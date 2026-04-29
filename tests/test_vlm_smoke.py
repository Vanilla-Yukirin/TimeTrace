"""Smoke tests against a real VLM endpoint.

These verify protocol compatibility (HTTP 200, JSON shape, extra_body acceptance).
Skipped automatically when TIMETRACE_VLM_API_KEY is missing — `uv run pytest`
will skip them silently. Run them manually after upgrading the OpenAI SDK,
swapping the endpoint, or changing the model:

    uv run pytest tests/test_vlm_smoke.py -v
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv
from PIL import Image

# Load .env before reading the key so a freshly-cloned repo with .env populated
# but env not exported still picks it up.
load_dotenv()


pytestmark = pytest.mark.skipif(
    not os.getenv("TIMETRACE_VLM_API_KEY", "").strip(),
    reason="needs real VLM credentials in .env (TIMETRACE_VLM_API_KEY)",
)


def _make_client():
    # Imported lazily so module collection doesn't require openai when skipped.
    from timetrace.vlm.client import VLMClient, VLMConfig  # noqa: PLC0415

    cfg = VLMConfig.from_env()
    assert cfg is not None
    return VLMClient(cfg)


async def test_smoke_chat_endpoint_reachable():
    """Bare chat completion: confirms base_url + api_key + model are all accepted.

    `extra_body` is only included when ``TIMETRACE_VLM_DISABLE_THINKING=true``;
    otherwise we omit it so vanilla api.openai.com doesn't 400 with
    "unknown parameter: extra_body". This mirrors what VLMClient.describe()
    does at runtime.
    """
    client = _make_client()
    try:
        resp = await client._client.chat.completions.create(
            model=client.model,
            messages=[{"role": "user", "content": "1+1=? 直接给数字"}],
            timeout=15.0,
            **client._extra_kwargs(),
        )
        content = resp.choices[0].message.content or ""
        assert isinstance(content, str)
        assert "2" in content
    finally:
        await client.aclose()


async def test_smoke_describe_returns_structured_json():
    """describe() against a synthesized image must yield 3-field dict with right types."""
    client = _make_client()
    try:
        img = Image.new("RGB", (256, 256), (220, 220, 220))
        result = await client.describe(img)
        assert isinstance(result, dict)
        assert isinstance(result["keywords"], list)
        assert isinstance(result["summary"], str) and result["summary"].strip()
        assert isinstance(result["description"], str) and result["description"].strip()
    finally:
        await client.aclose()


async def test_smoke_describe_handles_window_title():
    client = _make_client()
    try:
        img = Image.new("RGB", (128, 128), (50, 50, 50))
        result = await client.describe(img, window_title="测试窗口")
        assert "summary" in result and "description" in result
    finally:
        await client.aclose()


async def test_smoke_heartbeat_returns_true():
    client = _make_client()
    try:
        assert await client.heartbeat() is True
    finally:
        await client.aclose()


async def test_smoke_invalid_key_raises():
    """A clearly bogus API key must surface as VLMError, not pass silently."""
    from timetrace.vlm.client import VLMClient, VLMConfig, VLMError  # noqa: PLC0415

    real = VLMConfig.from_env()
    assert real is not None
    bad_cfg = VLMConfig(base_url=real.base_url, api_key="sk-invalid-test-key", model=real.model)
    client = VLMClient(bad_cfg)
    try:
        img = Image.new("RGB", (64, 64), (0, 0, 0))
        with pytest.raises(VLMError):
            await client.describe(img)
    finally:
        await client.aclose()
