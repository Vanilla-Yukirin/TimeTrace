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
from timetrace.server.tz import format_ms, view_tz, view_tz_name

if TYPE_CHECKING:
    from timetrace.common.config import VLMConfig
    from timetrace.server.db import Database
    from timetrace.server.embedding.client import EmbeddingClient

logger = structlog.get_logger(__name__)

_MAX_ITERATIONS = 6
_TIMEOUT_S = 180.0

_SYSTEM_PROMPT = (
    "你是 TimeTrace 的活动记录助手。TimeTrace 在本地记录用户在（一台或多台）电脑上"
    "看过的窗口与截图，由 AI 给每条记录写描述、打分类，并按 5min→1h→6h→day→week "
    "逐层生成叙述摘要（金字塔）。\n\n"
    "你可以调用工具查询用户的真实活动数据，然后用中文简洁、口语化地回答。工作方式：\n"
    "1. 先摘要、后下钻：问『某段时间在做什么』时，先用 get_activity_overview 读分设备"
    "叙述总览；粗层未完成会自动补细层。检查 coverage、summary_state、truncated。"
    "unavailable 或存在覆盖缺口时按 fallback_hint 继续：未指定设备可查全局 search_summaries"
    "（custom、相同起止、不传 device_id），仅作全局背景；指定设备则保持筛选逐页读取原始记录。"
    "不能把全局摘要归给某设备，也不能因设备摘要缺失就结束查询。"
    "需要细节再用 search_summaries 按 drill_down_grain 和窗口起止下钻，最后才看原始记录。\n"
    "2. 统计时长/占比用 query_stats（今天/本周/本月）或带 period 的 "
    "get_app_breakdown / get_category_stats。『今天』『本周』一律传 period，"
    "不要用 hours_back 近似；hours_back 只用于『最近 N 小时』这类滚动窗口。\n"
    "3. 找具体内容（某个文件、网页、话题什么时候出现过）用 search_activity，"
    "它同时按关键词和语义匹配，结果按相关度排序。\n"
    "4. 问最近在做什么用 get_latest_activity，区分采集时间、入库时间与分析进度。"
    "get_recent_activity 只在逐条翻页时使用，默认最早在前；has_more=true 不能把页尾当最新。\n"
    "原则：\n"
    "- 回答前先用工具取真实数据，绝不凭空编造数字或内容；数据不足就如实说明。\n"
    "- 没记录、截图间隔、描述为空、摘要待生成都不能推断用户离线；在线状态没有可靠证据。\n"
    "- 工具返回的时长是秒，回答时换算成分钟 / 小时；这些时长是『捕获到的活跃下限』，"
    "不是总时间。多台设备并行时，按分类/应用相加的时长可能超过实际经过时间。\n"
    "- 时间都按用户所在时区（见下方当前时间）理解和表述。\n"
    "- 你可以用 apply_label 给某条记录改分类标签——这是你唯一的写权限，"
    "你不能删除或修改记录本身。\n\n"
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
        embedding: EmbeddingClient | None = None,
    ) -> None:
        self._db = db
        self._embedding = embedding
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

    async def _device_context(self) -> str:
        """Name + owner description of each device, so phrases like "办公电脑"
        can be mapped to a device_id scope without an extra tool call."""
        try:
            devices = (await agent_tools.list_devices(self._db))["items"]
        except Exception:  # noqa: BLE001 — context is a hint, never fatal
            logger.warning("agent.device_context_failed", exc_info=True)
            return ""
        active = [d for d in devices if not d["revoked"]]
        if len(active) < 2:
            return ""
        lines = [
            f"- {d['name']}（device_id={d['device_id']}）"
            + (f"：{d['description']}" if d["description"] else "")
            for d in active
        ]
        return (
            "\n\n用户有多台设备，数据默认是全部设备合在一起；问题指向某台设备时，"
            "把对应 device_id 传给工具：\n" + "\n".join(lines)
        )

    async def run(
        self,
        messages: list[dict],
        *,
        hours_back_hint: int | None = None,
    ) -> AsyncIterator[dict]:
        """Drive the loop, yielding event dicts. ``messages`` is the chat history
        ([{role, content}, ...]); the latest user message is last."""
        now = f"{format_ms(int(time.time() * 1000), tz=view_tz())}（{view_tz_name()}）"
        if self._system_prompt is not None:
            system_content = f"{self._system_prompt}\n\n当前时间：{now}。"
        else:
            system_content = _SYSTEM_PROMPT.format(now=now)
        system_content += await self._device_context()
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
                result = await agent_tools.dispatch_tool(
                    self._db, name, args, embedding=self._embedding
                )
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
