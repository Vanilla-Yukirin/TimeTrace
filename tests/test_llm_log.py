"""Unified LLM-request ledger: the timed wrapper + DB round-trip.

The wrapper is provider-agnostic (works on any object exposing
``chat.completions.create``), so a tiny fake client stands in for AsyncOpenAI.
"""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from timetrace.common.config import StorageConfig
from timetrace.server.db import Database
from timetrace.server.llm_log import LLMRequestLog, set_default_sink, timed_chat_completion


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    yield database
    await database.close()


class _FakeCompletions:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._resp


class _FakeClient:
    def __init__(self, resp=None, exc=None):
        self.chat = SimpleNamespace(completions=_FakeCompletions(resp, exc))


def _resp_with_usage():
    usage = SimpleNamespace(
        prompt_tokens=120,
        completion_tokens=40,
        total_tokens=160,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=25),
    )
    msg = SimpleNamespace(content="hello", reasoning_content="thinking…")
    return SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=msg)])


async def test_timed_completion_logs_ok_row_with_real_usage():
    captured: list[LLMRequestLog] = []

    async def sink(entry):
        captured.append(entry)

    client = _FakeClient(resp=_resp_with_usage())
    resp = await timed_chat_completion(
        client, caller="narrate", model="m1", sink=sink, prompt_chars=300, messages=[]
    )
    assert resp is not None
    assert len(captured) == 1
    e = captured[0]
    assert e.caller == "narrate" and e.status == "ok" and e.model == "m1"
    assert e.prompt_tokens == 120 and e.completion_tokens == 40 and e.reasoning_tokens == 25
    assert e.prompt_chars == 300
    assert e.completion_chars == len("hello") + len("thinking…")
    assert e.ts_end >= e.ts_start and e.duration_ms >= 0
    # the create() call got the model + passthrough kwargs
    assert client.chat.completions.calls[0]["model"] == "m1"


async def test_timed_completion_logs_error_and_reraises():
    captured: list[LLMRequestLog] = []

    async def sink(entry):
        captured.append(entry)

    client = _FakeClient(exc=RuntimeError("endpoint down"))
    with pytest.raises(RuntimeError, match="endpoint down"):
        await timed_chat_completion(client, caller="worker_vlm", model="m", sink=sink, messages=[])
    assert len(captured) == 1 and captured[0].status == "error"
    assert "endpoint down" in captured[0].error


async def test_no_sink_is_noop():
    set_default_sink(None)  # ensure no global sink leaks across tests
    client = _FakeClient(resp=_resp_with_usage())
    # must not raise even though nothing is logging
    resp = await timed_chat_completion(client, caller="x", model="m", messages=[])
    assert resp is not None


async def test_db_ledger_roundtrip_and_stats(db):
    rows = [
        LLMRequestLog("narrate", "m", 1000, 1500, 500, "ok", 100, 30, 10, 130, 200, 50),
        LLMRequestLog("worker_vlm", "m", 2000, 2400, 400, "ok", 80, 20, 0, 100, 90, 40),
        LLMRequestLog("narrate", "m", 3000, 3100, 100, "error", None, None, None, None, 10, None),
    ]
    for r in rows:
        await db.insert_llm_request(**asdict(r))

    all_rows = await db.query_llm_requests(limit=10)
    assert len(all_rows) == 3
    assert all_rows[0]["ts_start"] == 3000  # newest first

    only_narrate = await db.query_llm_requests(caller="narrate", limit=10)
    assert len(only_narrate) == 2 and all(r["caller"] == "narrate" for r in only_narrate)

    # keyset cursor: rows strictly older than ts_start=3000
    older = await db.query_llm_requests(before_ts=3000, limit=10)
    assert {r["ts_start"] for r in older} == {1000, 2000}

    stats = await db.llm_request_stats()
    assert stats["n"] == 3 and stats["n_error"] == 1
    assert stats["prompt_tokens"] == 180 and stats["completion_tokens"] == 50
