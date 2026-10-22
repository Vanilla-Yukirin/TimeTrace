"""Deployment header contract for the HTTPS gateway's nonstandard port."""

import re
from pathlib import Path

import pytest

TEMPLATE = (
    Path(__file__).resolve().parents[1] / "deploy/nginx-timetrace.yukirin.me.conf"
).read_text(encoding="utf-8")
LOCATIONS = re.findall(r"location\s+([^{}]+)\{([^{}]+)\}", TEMPLATE)
PROXIES = [(name.strip(), body) for name, body in LOCATIONS if "proxy_pass" in body]


@pytest.mark.parametrize("location,body", PROXIES, ids=[name for name, _ in PROXIES])
def test_proxy_preserves_external_authority(location, body):
    # Each location sets its own headers, so checking only /v1/ misses the
    # more specific streaming routes. MCP alone has a deliberate allowlist map.
    host = "$timetrace_mcp_host" if location == "/mcp/" else "$http_host"
    assert re.search(r"proxy_set_header\s+Host\s+" + re.escape(host) + r";", body)
    assert re.search(r"proxy_set_header\s+X-Forwarded-Host\s+\$http_host;", body)
