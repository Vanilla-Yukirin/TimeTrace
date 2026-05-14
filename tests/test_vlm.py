"""Unit tests for VLM client + health gate (no network)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from timetrace.common.config import VLMConfig
from timetrace.server.vlm.client import (
    VLMClient,
    VLMError,
    format_description,
)
from timetrace.server.vlm.health import GateState, VLMHealthGate

# --------------------------------------------------------------------------- #
# VLMConfig.from_env                                                            #
# --------------------------------------------------------------------------- #


def test_vlm_config_from_env_returns_none_when_key_missing(monkeypatch):
    monkeypatch.delenv("TIMETRACE_VLM_API_KEY", raising=False)
    assert VLMConfig.from_env() is None


def test_vlm_config_from_env_returns_none_when_key_blank(monkeypatch):
    monkeypatch.setenv("TIMETRACE_VLM_API_KEY", "  ")
    assert VLMConfig.from_env() is None


def test_vlm_config_from_env_returns_config_when_key_present(monkeypatch):
    monkeypatch.setenv("TIMETRACE_VLM_API_KEY", "sk-test")
    monkeypatch.setenv("TIMETRACE_VLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("TIMETRACE_VLM_MODEL", "test-model")
    monkeypatch.delenv("TIMETRACE_VLM_DISABLE_THINKING", raising=False)
    cfg = VLMConfig.from_env()
    assert cfg is not None
    assert cfg.api_key == "sk-test"
    assert cfg.base_url == "https://example.test/v1"
    assert cfg.model == "test-model"
    assert cfg.disable_thinking is False  # default off — vanilla OpenAI safe


def test_vlm_config_disable_thinking_truthy_values(monkeypatch):
    monkeypatch.setenv("TIMETRACE_VLM_API_KEY", "sk-test")
    for value in ("true", "True", "1", "yes", "on"):
        monkeypatch.setenv("TIMETRACE_VLM_DISABLE_THINKING", value)
        assert VLMConfig.from_env().disable_thinking is True, f"expected True for {value!r}"
    for value in ("", "false", "0", "no"):
        monkeypatch.setenv("TIMETRACE_VLM_DISABLE_THINKING", value)
        assert VLMConfig.from_env().disable_thinking is False, f"expected False for {value!r}"


# --------------------------------------------------------------------------- #
# format_description                                                            #
# --------------------------------------------------------------------------- #


def test_format_description_concatenates_three_sections():
    out = format_description(
        {
            "keywords": ["VSCode", "main.py"],
            "summary": "在写代码",
            "description": "用户正在 VSCode 里编辑 Python 文件",
        }
    )
    assert "用户正在 VSCode 里编辑 Python 文件" in out
    assert "摘要：在写代码" in out
    assert "关键词：VSCode、main.py" in out


def test_format_description_skips_missing_fields():
    out = format_description({"keywords": [], "summary": "", "description": "完整描述"})
    assert out.strip() == "完整描述"


# --------------------------------------------------------------------------- #
# VLMClient.describe                                                            #
# --------------------------------------------------------------------------- #


def _fake_completion(content: str):
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _make_client_with_mock(mock_create: AsyncMock, *, disable_thinking: bool = False) -> VLMClient:
    cfg = VLMConfig(
        base_url="https://x",
        api_key="sk-test",
        model="m",
        disable_thinking=disable_thinking,
    )
    client = VLMClient(cfg)
    client._client.chat.completions.create = mock_create  # type: ignore[attr-defined]
    return client


async def test_vlm_client_describe_parses_response():
    mock = AsyncMock(
        return_value=_fake_completion(
            '{"keywords": ["a", "b"], "summary": "s", "description": "d"}'
        )
    )
    client = _make_client_with_mock(mock)
    img = Image.new("RGB", (8, 8), (10, 20, 30))
    result = await client.describe(img, window_title="t")
    assert result == {"keywords": ["a", "b"], "summary": "s", "description": "d"}

    kwargs = mock.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    # default config does NOT send extra_body so vanilla OpenAI doesn't 400
    assert "extra_body" not in kwargs


async def test_vlm_client_describe_prompt_enforces_noun_phrase_and_softens_title_caveat():
    """The prompt must enforce a noun-phrase opening (no declarative meta-narration),
    list explicit forbidden patterns and a positive example, and still mark
    window_title as auxiliary but not call it 'inaccurate'.
    """
    mock = AsyncMock(
        return_value=_fake_completion('{"keywords": [], "summary": "s", "description": "d"}')
    )
    client = _make_client_with_mock(mock)
    img = Image.new("RGB", (8, 8), (0, 0, 0))
    await client.describe(img, window_title="微信")

    messages = mock.call_args.kwargs["messages"]
    text_parts = [part["text"] for part in messages[0]["content"] if part.get("type") == "text"]
    full_prompt = "\n".join(text_parts)

    # Grammar-level constraint: noun-phrase opening, declarative ban.
    assert "名词性短语" in full_prompt
    assert "陈述句" in full_prompt

    # Forbidden patterns must be enumerated explicitly (covers user-reported regressions).
    for forbidden in ("该截图", "画面显示", "这是", "展示了", "正在"):
        assert forbidden in full_prompt, f"prompt missing forbidden pattern: {forbidden}"

    # Positive example present.
    assert "正例" in full_prompt

    # Window-title caveat softened.
    assert "仅供辅助参考" in full_prompt
    assert "可能不准确" not in full_prompt
    assert "微信" in full_prompt  # window_title was actually injected


async def test_vlm_client_describe_sends_extra_body_when_thinking_disabled():
    mock = AsyncMock(
        return_value=_fake_completion('{"keywords": [], "summary": "s", "description": "d"}')
    )
    client = _make_client_with_mock(mock, disable_thinking=True)
    img = Image.new("RGB", (8, 8), (0, 0, 0))
    await client.describe(img)
    kwargs = mock.call_args.kwargs
    assert kwargs["extra_body"] == {"enable_thinking": False}


async def test_vlm_client_heartbeat_omits_extra_body_by_default():
    mock = AsyncMock(return_value=_fake_completion("2"))
    client = _make_client_with_mock(mock)
    await client.heartbeat()
    assert "extra_body" not in mock.call_args.kwargs


async def test_vlm_client_heartbeat_sends_extra_body_when_thinking_disabled():
    mock = AsyncMock(return_value=_fake_completion("2"))
    client = _make_client_with_mock(mock, disable_thinking=True)
    await client.heartbeat()
    assert mock.call_args.kwargs["extra_body"] == {"enable_thinking": False}


async def test_vlm_client_describe_raises_on_invalid_json():
    mock = AsyncMock(return_value=_fake_completion("this is not json"))
    client = _make_client_with_mock(mock)
    img = Image.new("RGB", (8, 8), (0, 0, 0))
    with pytest.raises(VLMError, match="non-JSON"):
        await client.describe(img)


async def test_vlm_client_describe_raises_on_missing_field():
    mock = AsyncMock(
        return_value=_fake_completion('{"keywords": [], "summary": "s"}')  # missing description
    )
    client = _make_client_with_mock(mock)
    img = Image.new("RGB", (8, 8), (0, 0, 0))
    with pytest.raises(VLMError, match="description"):
        await client.describe(img)


async def test_vlm_client_describe_raises_on_non_list_keywords():
    mock = AsyncMock(
        return_value=_fake_completion('{"keywords": "x", "summary": "s", "description": "d"}')
    )
    client = _make_client_with_mock(mock)
    img = Image.new("RGB", (8, 8), (0, 0, 0))
    with pytest.raises(VLMError, match="keywords"):
        await client.describe(img)


async def test_vlm_client_describe_wraps_network_error():
    mock = AsyncMock(side_effect=RuntimeError("connection refused"))
    client = _make_client_with_mock(mock)
    img = Image.new("RGB", (8, 8), (0, 0, 0))
    with pytest.raises(VLMError, match="connection refused"):
        await client.describe(img)


async def test_vlm_client_heartbeat_returns_true_on_2():
    mock = AsyncMock(return_value=_fake_completion("答案是 2"))
    client = _make_client_with_mock(mock)
    assert await client.heartbeat() is True


async def test_vlm_client_heartbeat_returns_false_on_other():
    mock = AsyncMock(return_value=_fake_completion("我不知道"))
    client = _make_client_with_mock(mock)
    assert await client.heartbeat() is False


async def test_vlm_client_heartbeat_returns_false_on_exception():
    mock = AsyncMock(side_effect=RuntimeError("boom"))
    client = _make_client_with_mock(mock)
    assert await client.heartbeat() is False


# --------------------------------------------------------------------------- #
# VLMHealthGate                                                                  #
# --------------------------------------------------------------------------- #


class _FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _make_gate(*, heartbeat_ok: bool = True, fail=3, recover=3, interval=30.0, clock=None):
    cfg = VLMConfig(base_url="https://x", api_key="k", model="m")
    client = VLMClient(cfg)
    client.heartbeat = AsyncMock(return_value=heartbeat_ok)  # type: ignore[assignment]
    gate = VLMHealthGate(
        client,
        fail_threshold=fail,
        recover_threshold=recover,
        probe_interval_s=interval,
        clock=clock or _FakeClock(),
    )
    return gate, client


async def test_health_gate_starts_healthy():
    gate, _ = _make_gate()
    assert gate.state == GateState.HEALTHY
    assert await gate.acquire() is True


async def test_health_gate_opens_after_consecutive_failures():
    gate, _ = _make_gate()
    await gate.report_failure()
    await gate.report_failure()
    assert gate.state == GateState.HEALTHY
    await gate.report_failure()
    assert gate.state == GateState.SLEEPING
    assert await gate.acquire() is False


async def test_health_gate_success_resets_failure_counter():
    gate, _ = _make_gate()
    await gate.report_failure()
    await gate.report_failure()
    await gate.report_success()
    await gate.report_failure()
    await gate.report_failure()
    assert gate.state == GateState.HEALTHY  # 2 failures < threshold


async def test_health_gate_recovers_after_consecutive_heartbeats():
    clock = _FakeClock(0.0)
    gate, client = _make_gate(heartbeat_ok=True, clock=clock)
    # Trip into SLEEPING
    for _ in range(3):
        await gate.report_failure()
    assert gate.state == GateState.SLEEPING

    # First probe: not yet at next_probe_at (initial cooldown applies)
    assert await gate.acquire() is False

    # Advance past first probe interval → should run heartbeat (success #1)
    clock.t = 100.0
    assert await gate.acquire() is False  # 1/3
    clock.t = 200.0
    assert await gate.acquire() is False  # 2/3
    clock.t = 300.0
    # 3rd success transitions to HEALTHY and acquire returns True
    assert await gate.acquire() is True
    assert gate.state == GateState.HEALTHY


async def test_health_gate_heartbeat_failure_resets_recovery_counter():
    clock = _FakeClock(0.0)
    gate, client = _make_gate(heartbeat_ok=True, clock=clock)
    for _ in range(3):
        await gate.report_failure()

    clock.t = 100.0
    await gate.acquire()  # success 1
    # Now flip heartbeat to fail
    client.heartbeat = AsyncMock(return_value=False)  # type: ignore[assignment]
    clock.t = 200.0
    await gate.acquire()
    # Recovery counter was reset; flip back to ok
    client.heartbeat = AsyncMock(return_value=True)  # type: ignore[assignment]
    clock.t = 300.0
    assert await gate.acquire() is False  # success 1 again, not yet 3
    clock.t = 400.0
    assert await gate.acquire() is False  # 2
    clock.t = 500.0
    assert await gate.acquire() is True  # 3 → recovered


async def test_health_gate_acquire_paces_probes():
    """Within one probe_interval, only one probe should fire across many acquires."""
    clock = _FakeClock(0.0)
    gate, client = _make_gate(heartbeat_ok=False, clock=clock)
    for _ in range(3):
        await gate.report_failure()

    clock.t = 100.0  # past the initial cooldown
    # Many concurrent-ish acquires but clock doesn't advance
    for _ in range(10):
        await gate.acquire()
    # heartbeat called only once because next_probe_at is now > current
    assert client.heartbeat.call_count == 1
