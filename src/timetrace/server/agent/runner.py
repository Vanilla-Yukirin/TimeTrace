"""Tool-calling agent loop over the local LLM.

``AgentRunner.run()`` is an async generator yielding event dicts so the FastAPI
route can stream them as SSE. The loop:

  1. send the conversation + tool schemas to the local model (Qwen3 via
     LM Studio / any OpenAI-compatible endpoint)
  2. if the model asks for tool calls → execute them against the DB, feed
     results back, repeat
  3. otherwise → emit the final answer

Bounded by ``max_iterations`` so a confused model can't loop forever; on the
last turn we drop the tools so the model is forced to answer in prose.

Event shapes (all dicts, ``type`` discriminates):
  {"type":"step","phase":"tool_call","tool":..,"args":{..}}
  {"type":"step","phase":"tool_result","tool":..,"summary":".."}
  {"type":"reasoning","text":".."}                   # thinking delta (reasoning_content)
  {"type":"token","text":".."}                       # the answer
  {"type":"done","records_consulted":N,"model":..,"tools_used":[..],"usage":{prompt,completion,total}}
  {"type":"error","message":".."}
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog
from openai import AsyncOpenAI

from timetrace.server.agent import tools as agent_tools

if TYPE_CHECKING:
    from timetrace.common.config import VLMConfig
    from timetrace.server.db import Database

logger = structlog.get_logger(__name__)

_MAX_ITERATIONS = 6
_TIMEOUT_S = 180.0

_SYSTEM_PROMPT = (
    "你是 TimeTrace 的活动记录助手。TimeTrace 在本地记录用户在电脑上看过的"
    "窗口与截图，并由 AI 自动给每条记录打分类标签。\n\n"
    "你可以调用工具查询用户的真实活动数据，然后用中文简洁、口语化地回答。原则：\n"
    "- 回答前先用工具取真实数据，绝不凭空编造数字或内容。遇到“今天/最近/"
    "这几天”等时间词，用带 hours_back 的工具。\n"
    "- 工具返回的时长是秒，回答时换算成更易读的分钟 / 小时。\n"
    "- 找具体内容用 search_activity；看时间分布用 get_app_breakdown（按应用）"
    "或 get_category_stats（按分类）；要总览用 get_recent_activity。\n"
    "- 你可以用 apply_label 给某条记录改分类标签——这是你唯一的写权限，"
    "你不能删除或修改记录本身。\n"
    "- 数据不足以回答时，如实说明，不要硬凑。\n\n"
    "当前时间：{now}。"
)


def _chunk_delta(chunk: Any) -> Any:
    """Pull the delta off a streaming chunk, tolerating empty ``choices``.

    Some OpenAI-compatible servers emit keep-alive / usage-only chunks with an
    empty ``choices`` list; skip those rather than IndexError.
    """
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    return getattr(choices[0], "delta", None)


def _accumulate_usage(total: dict[str, int], chunk: Any) -> None:
    """Fold a streamed chunk's token usage (if present) into ``total``.

    With ``stream_options={"include_usage": True}`` an OpenAI-compatible server
    sends a final usage-only chunk (empty ``choices``). Some servers omit it, so
    this is best-effort — a turn with no usage chunk just leaves the total at 0.
    """
    u = getattr(chunk, "usage", None)
    if u is None:
        return
    total["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
    total["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
    total["total_tokens"] += getattr(u, "total_tokens", 0) or 0


def _summarize(name: str, result: dict) -> str:
    """One-line human summary of a tool result for the 'thinking' UI chip."""
    if not isinstance(result, dict):
        return "完成"
    if "error" in result:
        return f"出错：{result['error']}"
    if name == "apply_label":
        return f"已标为 {result.get('category_after')}"
    items = result.get("items")
    if isinstance(items, list):
        return f"{len(items)} 条结果"
    return "完成"


class AgentRunner:
    """One conversational turn of the tool-calling loop, bound to a DB + LLM."""

    def __init__(
        self,
        db: Database,
        vlm_cfg: VLMConfig,
        *,
        max_iterations: int = _MAX_ITERATIONS,
        system_prompt: str | None = None,
    ) -> None:
        self._db = db
        self._cfg = vlm_cfg
        self._max_iterations = max_iterations
        # None → the default chat persona. Callers (e.g. the report generator)
        # pass their own persona; we append the current time rather than
        # ``.format`` it so prompts containing literal ``{}`` (CSS) are safe.
        self._system_prompt = system_prompt
        self._client = AsyncOpenAI(base_url=vlm_cfg.base_url, api_key=vlm_cfg.api_key)

    async def aclose(self) -> None:
        await self._client.close()

    def _extra_body(self) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if self._cfg.disable_thinking:
            extra["extra_body"] = {"enable_thinking": False}
        return extra

    async def run(
        self,
        messages: list[dict],
        *,
        hours_back_hint: int | None = None,
    ) -> AsyncIterator[dict]:
        """Drive the loop, yielding event dicts. ``messages`` is the chat history
        ([{role, content}, ...]); the latest user message is last."""
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        if self._system_prompt is not None:
            system_content = f"{self._system_prompt}\n\n当前时间：{now}。"
        else:
            system_content = _SYSTEM_PROMPT.format(now=now)
        convo: list[dict] = [{"role": "system", "content": system_content}]
        convo.extend({"role": m["role"], "content": m["content"]} for m in messages)
        extra = self._extra_body()
        tools_used: list[str] = []
        records_consulted = 0
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for _ in range(self._max_iterations):
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            # Streamed tool-call fragments, keyed by their `index` so multiple
            # parallel calls accumulate into the right slot.
            tc_acc: dict[int, dict[str, Any]] = {}
            try:
                stream = await self._client.chat.completions.create(
                    model=self._cfg.model,
                    messages=convo,
                    tools=agent_tools.TOOL_SCHEMAS,
                    tool_choice="auto",
                    timeout=_TIMEOUT_S,
                    stream=True,
                    stream_options={"include_usage": True},
                    **extra,
                )
                async for chunk in stream:
                    _accumulate_usage(usage_total, chunk)
                    delta = _chunk_delta(chunk)
                    if delta is None:
                        continue
                    text = getattr(delta, "content", None)
                    if text:
                        content_parts.append(text)
                        yield {"type": "token", "text": text}
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        reasoning_parts.append(reasoning)
                        yield {"type": "reasoning", "text": reasoning}
                    for tcd in getattr(delta, "tool_calls", None) or []:
                        slot = tc_acc.setdefault(
                            getattr(tcd, "index", 0) or 0,
                            {"id": None, "name": None, "args": ""},
                        )
                        if getattr(tcd, "id", None):
                            slot["id"] = tcd.id
                        fn = getattr(tcd, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                slot["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                slot["args"] += fn.arguments
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent.llm_failed")
                yield {"type": "error", "message": f"调用 LLM 失败: {exc}"}
                return

            if not tc_acc:
                # No tool calls → this turn was the final answer, already
                # streamed token-by-token above. Fall back to reasoning_content
                # only if the model emitted nothing on `content`.
                if not content_parts and reasoning_parts:
                    yield {"type": "token", "text": "".join(reasoning_parts).strip()}
                yield {
                    "type": "done",
                    "records_consulted": records_consulted,
                    "model": self._cfg.model,
                    "tools_used": tools_used,
                    "usage": usage_total,
                }
                return

            ordered = [tc_acc[i] for i in sorted(tc_acc)]
            # Record the assistant's tool-call turn verbatim so the follow-up
            # tool messages reference valid tool_call_ids.
            convo.append(
                {
                    "role": "assistant",
                    "content": "".join(content_parts),
                    "tool_calls": [
                        {
                            "id": slot["id"] or f"call_{i}",
                            "type": "function",
                            "function": {
                                "name": slot["name"] or "",
                                "arguments": slot["args"] or "{}",
                            },
                        }
                        for i, slot in enumerate(ordered)
                    ],
                }
            )
            for i, slot in enumerate(ordered):
                name = slot["name"] or ""
                try:
                    args = json.loads(slot["args"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                tools_used.append(name)
                yield {"type": "step", "phase": "tool_call", "tool": name, "args": args}
                result = await agent_tools.dispatch_tool(self._db, name, args)
                if isinstance(result, dict) and isinstance(result.get("items"), list):
                    records_consulted += len(result["items"])
                yield {
                    "type": "step",
                    "phase": "tool_result",
                    "tool": name,
                    "summary": _summarize(name, result),
                }
                convo.append(
                    {
                        "role": "tool",
                        "tool_call_id": slot["id"] or f"call_{i}",
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        # Iteration budget exhausted while still tool-calling: force a prose
        # answer by dropping the tools on the final shot (still streamed).
        try:
            stream = await self._client.chat.completions.create(
                model=self._cfg.model,
                messages=convo,
                timeout=_TIMEOUT_S,
                stream=True,
                stream_options={"include_usage": True},
                **extra,
            )
            emitted = False
            reasoning_parts = []
            async for chunk in stream:
                _accumulate_usage(usage_total, chunk)
                delta = _chunk_delta(chunk)
                if delta is None:
                    continue
                text = getattr(delta, "content", None)
                if text:
                    emitted = True
                    yield {"type": "token", "text": text}
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_parts.append(reasoning)
                    yield {"type": "reasoning", "text": reasoning}
            if not emitted:
                yield {
                    "type": "token",
                    "text": "".join(reasoning_parts).strip() or "(已达到工具调用上限)",
                }
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent.final_answer_failed")
            yield {"type": "token", "text": f"(已达到工具调用上限，最终回答失败: {exc})"}
        yield {
            "type": "done",
            "records_consulted": records_consulted,
            "model": self._cfg.model,
            "tools_used": tools_used,
            "usage": usage_total,
        }
