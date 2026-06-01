"""Tests for the agent tool-calling loop (runner.py) with a stubbed LLM client.

The model's actual tool-calling competence is verified live at deploy; here we
deterministically verify the LOOP wiring — assistant/tool message threading,
tool dispatch against the real DB, event emission, and the iteration-budget
fallback — without any network call.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from timetrace.common.config import StorageConfig, VLMConfig
from timetrace.common.models import CaptureContext
from timetrace.server.agent.runner import AgentRunner
from timetrace.server.db import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    yield database
    await database.close()


def _chunk(content=None, tool_calls=None, reasoning=None):
    """A single streamed chunk: choices[0].delta with content / tool_calls / reasoning."""
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _usage_chunk(prompt, completion, total):
    """A usage-only chunk (empty choices) as sent with stream_options.include_usage."""
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
        ),
    )


def _content(text):
    """A streamed response that is a plain prose answer (one content chunk)."""
    return [_chunk(content=text)]


def _tcd(index, call_id, name, arguments):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _tools(*calls):
    """A streamed response carrying tool calls.

    ``calls`` are ``(call_id, name, arguments)`` tuples; each becomes its own
    streamed chunk with an ascending ``index`` (mirrors how OpenAI streams
    parallel tool calls)."""
    return [
        _chunk(tool_calls=[_tcd(i, cid, name, args)]) for i, (cid, name, args) in enumerate(calls)
    ]


class _ScriptedClient:
    """Stand-in for AsyncOpenAI: pops a scripted *streamed* response per
    create() call. Each response is a list of chunks; create() returns an
    async iterator over them, matching ``stream=True`` semantics."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.closed = False

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        chunks = self._responses.pop(0)

        async def _stream():
            for c in chunks:
                yield c

        return _stream()

    async def close(self):
        self.closed = True


def _runner(db, responses):
    cfg = VLMConfig(base_url="http://localhost:1", api_key="test", model="m")
    runner = AgentRunner(db, cfg, max_iterations=3)
    runner._client = _ScriptedClient(responses)  # noqa: SLF001 — test seam
    return runner


async def _collect(runner, messages):
    return [ev async for ev in runner.run(messages)]


async def test_direct_answer_no_tools(db):
    runner = _runner(db, [_content("你好，有什么可以帮你的？")])
    events = await _collect(runner, [{"role": "user", "content": "hi"}])
    types = [e["type"] for e in events]
    assert "step" not in types
    assert any(e["type"] == "token" and "帮你" in e["text"] for e in events)
    assert events[-1]["type"] == "done"


async def test_tool_call_then_answer(db):
    await db.insert_record(
        CaptureContext(app_name="Cursor", process_name="cursor.exe", window_title="main.py"),
        reason="t",
    )
    responses = [
        _tools(("c1", "get_recent_activity", '{"hours_back": 24}')),
        _content("你最近在用 Cursor。"),
    ]
    runner = _runner(db, responses)
    events = await _collect(runner, [{"role": "user", "content": "我最近在干嘛"}])
    steps = [e for e in events if e["type"] == "step"]
    assert any(s["phase"] == "tool_call" and s["tool"] == "get_recent_activity" for s in steps)
    assert any(s["phase"] == "tool_result" for s in steps)
    done = events[-1]
    assert done["type"] == "done"
    assert "get_recent_activity" in done["tools_used"]
    assert done["records_consulted"] >= 1


async def test_apply_label_through_loop(db):
    rid = await db.insert_record(
        CaptureContext(app_name="Cursor", process_name="cursor.exe", window_title="main.py"),
        reason="t",
    )
    responses = [
        _tools(("c1", "apply_label", f'{{"record_id": "{rid}", "category": "work"}}')),
        _content("已标记。"),
    ]
    runner = _runner(db, responses)
    events = await _collect(runner, [{"role": "user", "content": "把它标成工作"}])
    assert any(
        e["type"] == "step" and e["phase"] == "tool_result" and "work" in e["summary"]
        for e in events
    )
    assert await db.get_category_final(rid) == "work"


async def test_iteration_budget_forces_final_answer(db):
    await db.insert_record(
        CaptureContext(app_name="Cursor", process_name="cursor.exe", window_title="main.py"),
        reason="t",
    )
    # max_iterations=1: one tool-calling round, then the loop exits and the
    # runner makes one final no-tools call to force prose.
    cfg = VLMConfig(base_url="http://localhost:1", api_key="test", model="m")
    runner = AgentRunner(db, cfg, max_iterations=1)
    runner._client = _ScriptedClient(  # noqa: SLF001
        [
            _tools(("c1", "get_recent_activity", "{}")),
            _content("（基于已查到的）你在用 Cursor。"),
        ]
    )
    events = await _collect(runner, [{"role": "user", "content": "我在干嘛"}])
    assert any(e["type"] == "token" and "Cursor" in e["text"] for e in events)
    assert events[-1]["type"] == "done"


async def test_llm_failure_emits_error(db):
    class _BoomClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kwargs):
            raise RuntimeError("connection refused")

        async def close(self):
            pass

    cfg = VLMConfig(base_url="http://localhost:1", api_key="test", model="m")
    runner = AgentRunner(db, cfg)
    runner._client = _BoomClient()  # noqa: SLF001
    events = await _collect(runner, [{"role": "user", "content": "hi"}])
    assert events[0]["type"] == "error"
    assert "connection refused" in events[0]["message"]


async def test_reasoning_streamed_as_events(db):
    # A thinking model emits reasoning_content deltas; they should surface as
    # their own `reasoning` events (not swallowed), alongside the answer tokens.
    runner = _runner(db, [[_chunk(reasoning="让我想想"), _chunk(content="你好")]])
    events = await _collect(runner, [{"role": "user", "content": "hi"}])
    assert any(e["type"] == "reasoning" and "想想" in e["text"] for e in events)
    assert any(e["type"] == "token" and "你好" in e["text"] for e in events)
    assert events[-1]["type"] == "done"


async def test_usage_accumulated_into_done(db):
    # The final usage-only chunk (stream_options.include_usage) is folded into
    # the done event so the UI can show per-message token cost.
    runner = _runner(db, [[_chunk(content="hi"), _usage_chunk(10, 5, 15)]])
    events = await _collect(runner, [{"role": "user", "content": "hi"}])
    done = events[-1]
    assert done["type"] == "done"
    assert done["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


async def test_usage_summed_across_tool_iterations(db):
    # Multiple LLM calls in one turn (tool round + final answer) → usage sums.
    await db.insert_record(
        CaptureContext(app_name="Cursor", process_name="cursor.exe", window_title="main.py"),
        reason="t",
    )
    responses = [
        [_chunk(tool_calls=[_tcd(0, "c1", "get_recent_activity", "{}")]), _usage_chunk(8, 2, 10)],
        [_chunk(content="你在用 Cursor。"), _usage_chunk(20, 6, 26)],
    ]
    runner = _runner(db, responses)
    events = await _collect(runner, [{"role": "user", "content": "我在干嘛"}])
    done = events[-1]
    assert done["type"] == "done"
    assert done["usage"] == {"prompt_tokens": 28, "completion_tokens": 8, "total_tokens": 36}
