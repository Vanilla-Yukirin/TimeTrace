import asyncio
import io

import httpx
import pytest
from PIL import Image

from timetrace.client.activity_journal import ActivityJournal
from timetrace.client.capture.recorder import ScreenshotRecorder
from timetrace.client.capture.settings import ScreenshotSettings
from timetrace.client.capture.source import WindowInfo
from timetrace.client.controller import ClientController
from timetrace.client.core.config import ClientConfig
from timetrace.client.core.endpoints import EndpointSelector
from timetrace.client.core.outbox import Outbox
from timetrace.client.core.outbox_backend import OutboxBackend
from timetrace.client.local_api import create_local_app


class Pixels:
    hwnd = 1
    idle_seconds = 0
    at = 1000.0
    fail = False

    def wall_time(self):
        return self.at

    def active_window(self):
        return WindowInfo("Editor", "editor.exe", "private title", self.hwnd, 1)

    def pixels(self, hwnd):
        if self.fail:
            raise OSError("mock capture failure")
        return Image.new("RGB", (24, 16), (50 * hwnd, 0, 0))


def make_recorder(tmp_path, **settings):
    cfg = ClientConfig(capture=ScreenshotSettings(**settings))
    outbox = Outbox(tmp_path / "outbox")
    source = Pixels()
    events = []
    recorder = ScreenshotRecorder(
        cfg,
        OutboxBackend(outbox),
        source=source,
        on_event=lambda kind, detail: events.append(kind),
        on_capture=lambda _reason: None,
    )
    return recorder, source, outbox, events


async def frames(outbox):
    items = [item async for item in outbox.iter_pending()]
    return [item for item in items if item.payload["kind"] == "ingest"]


async def test_smart_capture_delays_switch_and_deduplicates_unchanged_frame(tmp_path):
    recorder, source, outbox, events = make_recorder(
        tmp_path,
        switch_capture_delay_s=1,
        min_capture_interval_s=3,
        max_capture_interval_s=4,
        deduplicate=True,
        image_format="webp",
    )
    await recorder.tick(0)
    assert await frames(outbox) == []
    await recorder.tick(1)
    source.hwnd = 2
    await recorder.tick(1.5)
    await recorder.tick(2.5)
    assert len(await frames(outbox)) == 1
    await recorder.tick(4)
    await recorder.tick(8)
    recorded = await frames(outbox)
    assert len(recorded) == 2
    assert "skip" in events
    for frame in recorded:
        assert frame.payload["app_name"] == "Editor"
        assert frame.payload["ts_start"] == 1_000_000
        assert frame.payload["image_format"] == "webp"
        assert Image.open(io.BytesIO(frame.image_bytes)).format == "WEBP"
        assert Image.open(io.BytesIO(frame.thumb_bytes)).format == "JPEG"


async def test_interval_capture_ignores_idle_and_dedup_but_honors_pause(tmp_path):
    recorder, source, outbox, _ = make_recorder(
        tmp_path, strategy="interval", interval_s=2, deduplicate=True, image_format="png"
    )
    source.idle_seconds = 999
    await recorder.tick(0)
    await recorder.tick(1.9)
    assert await frames(outbox) == []
    await recorder.tick(2)
    await recorder.tick(4)
    recorder.config.privacy.paused = True
    await recorder.tick(6)
    recorded = await frames(outbox)
    assert len(recorded) == 2
    assert Image.open(io.BytesIO(recorded[0].image_bytes)).format == "PNG"


async def test_failed_and_disabled_image_capture_never_queue_metadata_only(tmp_path):
    recorder, source, outbox, events = make_recorder(tmp_path, switch_capture_delay_s=0)
    source.fail = True
    await recorder.tick(0)
    assert "error" in events
    recorder.config.privacy.store_images = False
    await recorder.tick(3)
    assert await outbox.pending_count() == 0


async def test_dashboard_settings_and_event_cursor_keep_file_layer_and_csrf(tmp_path, monkeypatch):
    path = tmp_path / "client.toml"
    cfg = ClientConfig()
    cfg.save(path)
    cfg.server.auth_token = "env-only-secret"
    original = cfg.save_dashboard_settings
    monkeypatch.setattr(cfg, "save_dashboard_settings", lambda **kwargs: original(path, **kwargs))
    journal = ActivityJournal(tmp_path / "activity.sqlite3")
    for idx in range(5):
        journal.emit("capture", str(idx))
    await journal._flush()
    controller = ClientController(
        cfg, Outbox(tmp_path / "outbox"), EndpointSelector([]), asyncio.Event(), journal=journal
    )
    transport = httpx.ASGITransport(app=create_local_app(controller, port=8764, csrf_token="csrf"))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8764") as client:
        assert (await client.get("/")).status_code == 200
        headers = {"Origin": "http://127.0.0.1:8764", "X-TimeTrace-CSRF": "csrf"}
        assert (
            await client.patch("/api/settings/theme", json={"theme": "dark"})
        ).status_code == 403
        response = await client.patch(
            "/api/settings/theme", json={"theme": "dark"}, headers=headers
        )
        assert response.json() == {"theme": "dark"}
        assert "env-only-secret" not in path.read_text(encoding="utf-8")
        assert ClientConfig.load_or_default(path).control.theme == "dark"
        bad = await client.patch("/api/capture/settings", json={"interval_s": 0}, headers=headers)
        assert bad.status_code == 422
        newest = (await client.get("/api/events?limit=2")).json()
        assert [item["id"] for item in newest["items"]] == [5, 4]
        assert newest["more"]
        older = (await client.get("/api/events?before=4&limit=2")).json()
        assert [item["id"] for item in older["items"]] == [3, 2]
        newer = (await client.get("/api/events?after=3")).json()
        assert [item["id"] for item in newer["items"]] == [4, 5]


@pytest.mark.parametrize("tag,available", [("v0.10.0", True), ("v0.1.1", False)])
async def test_update_compares_numeric_versions(monkeypatch, tag, available):
    from timetrace.client import updates

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"tag_name": tag}))
    )
    monkeypatch.setattr(updates.httpx, "AsyncClient", lambda **_kwargs: client)
    assert (await updates.check_for_update())["available"] is available
