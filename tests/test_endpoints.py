"""Multi-endpoint failover (P1): config schema, EndpointSelector, and
HttpBackend's per-request base_url_provider."""

from __future__ import annotations

import httpx
import pytest

from timetrace.client.core.backend import BackendError, HttpBackend
from timetrace.client.core.config import ClientConfig, EndpointSection, ServerSection
from timetrace.client.core.endpoints import EndpointSelector
from timetrace.common.models import CaptureContext

# --------------------------------------------------------------------------- #
# Config schema                                                                 #
# --------------------------------------------------------------------------- #


def test_legacy_single_url_synthesizes_one_endpoint():
    s = ServerSection(url="https://example.test")
    eps = s.all_endpoints()
    assert len(eps) == 1
    assert eps[0].url == "https://example.test"
    assert eps[0].enabled
    assert s.enabled_endpoints() == eps


def test_endpoints_array_used_when_present_and_filters_disabled():
    s = ServerSection(
        url="https://ignored",
        endpoints=[
            EndpointSection(name="lan", url="http://192.168.1.2:8765"),
            EndpointSection(name="pub", url="https://pub", enabled=False),
        ],
    )
    assert [e.name for e in s.all_endpoints()] == ["lan", "pub"]
    assert [e.name for e in s.enabled_endpoints()] == ["lan"]


def test_config_load_parses_endpoints_array(tmp_path):
    p = tmp_path / "client.toml"
    p.write_text(
        '[server]\nauth_token = "t"\n\n'
        '[[server.endpoints]]\nname = "a"\nurl = "http://a"\nenabled = true\n\n'
        '[[server.endpoints]]\nname = "b"\nurl = "http://b"\nenabled = false\n',
        encoding="utf-8",
    )
    cfg = ClientConfig.load_or_default(p)
    assert [(e.name, e.enabled) for e in cfg.server.endpoints] == [("a", True), ("b", False)]


def test_config_roundtrip_endpoints_including_ssh(tmp_path):
    cfg = ClientConfig()
    cfg.server.endpoints = [
        EndpointSection(name="lan", url="http://192.168.1.2:8765"),
        EndpointSection(
            name="jp",
            url="http://127.0.0.1:19765",
            type="ssh",
            ssh_host="jp",
            remote_port=18765,
        ),
    ]
    p = cfg.save(tmp_path / "client.toml")
    eps = ClientConfig.load_or_default(p).server.endpoints
    assert [e.name for e in eps] == ["lan", "jp"]
    assert eps[1].type == "ssh"
    assert eps[1].ssh_host == "jp"
    assert eps[1].remote_port == 18765
    assert eps[0].type == "http"  # http endpoint round-trips without ssh fields


# --------------------------------------------------------------------------- #
# EndpointSelector                                                              #
# --------------------------------------------------------------------------- #


def _probe_for(healthy: set[str]):
    async def probe(url: str) -> bool:
        return url in healthy

    return probe


def _eps(*specs):
    return [EndpointSection(name=n, url=u, enabled=e) for (n, u, e) in specs]


async def test_selector_picks_first_healthy_in_priority_order():
    sel = EndpointSelector(
        _eps(("lan", "u_lan", True), ("pub", "u_pub", True)),
        probe=_probe_for({"u_lan", "u_pub"}),
    )
    chosen = await sel.select()
    assert chosen is not None and chosen.name == "lan"
    assert sel.current_url() == "u_lan"


async def test_selector_falls_back_when_top_down():
    sel = EndpointSelector(
        _eps(("lan", "u_lan", True), ("pub", "u_pub", True)),
        probe=_probe_for({"u_pub"}),
    )
    chosen = await sel.select()
    assert chosen is not None and chosen.name == "pub"


async def test_selector_none_when_all_down():
    sel = EndpointSelector(_eps(("lan", "u_lan", True)), probe=_probe_for(set()))
    assert await sel.select() is None
    assert sel.current_url() is None


async def test_selector_skips_disabled_even_if_healthy():
    sel = EndpointSelector(
        _eps(("lan", "u_lan", False), ("pub", "u_pub", True)),
        probe=_probe_for({"u_lan", "u_pub"}),
    )
    chosen = await sel.select()
    assert chosen is not None and chosen.name == "pub"


async def test_selector_sticky_keeps_current_on_probe_miss():
    """A transient all-probe-miss must NOT blackhole current to None — keep the
    last-good endpoint so the send's own retry decides liveness."""
    healthy = {"u_pub"}

    async def probe(url: str) -> bool:
        return url in healthy

    sel = EndpointSelector(_eps(("lan", "u_lan", True), ("pub", "u_pub", True)), probe=probe)
    assert (await sel.select()).name == "pub"  # pub healthy → current=pub
    healthy.clear()  # everything transiently down
    kept = await sel.select()
    assert kept is not None and kept.name == "pub"  # sticky: still pub, not None
    assert sel.current_url() == "u_pub"


async def test_selector_none_when_all_down_and_no_current():
    """With no current to keep, all-down → None (a real 'nowhere to send')."""
    sel = EndpointSelector(_eps(("lan", "u_lan", True)), probe=_probe_for(set()))
    assert await sel.select() is None


async def test_selector_upgrades_back_to_higher_priority():
    healthy = {"u_pub"}

    async def probe(url: str) -> bool:
        return url in healthy

    sel = EndpointSelector(_eps(("lan", "u_lan", True), ("pub", "u_pub", True)), probe=probe)
    first = await sel.select()
    assert first is not None and first.name == "pub"  # lan down → pub
    healthy.add("u_lan")  # lan recovers
    second = await sel.select()
    assert second is not None and second.name == "lan"  # upgrades back


# --------------------------------------------------------------------------- #
# HttpBackend base_url_provider                                                 #
# --------------------------------------------------------------------------- #

_CTX = CaptureContext(app_name="a", process_name="p", window_title="w")


async def test_backend_provider_builds_absolute_url():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200, json={"record_id": "r", "was_new": True, "server_received_at": 0}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HttpBackend(client=client, base_url_provider=lambda: "http://lan:8765")
    await backend.submit_record(_CTX, reason="t")
    assert captured["url"] == "http://lan:8765/v1/ingest/record"
    await client.aclose()


async def test_backend_provider_none_raises_so_entry_stays_pending():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    )
    backend = HttpBackend(client=client, base_url_provider=lambda: None)
    with pytest.raises(BackendError, match="no healthy endpoint"):
        await backend.submit_record(_CTX, reason="t")
    await client.aclose()
