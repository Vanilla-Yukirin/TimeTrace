"""NarrativeBuilder — the LLM stage of the pyramid (Phase 3).

Turns a FINALIZED metric summary into a structured narrative:
**流水账 (chronological account) + 重点提炼 (key points) + 评价 (appraisal)**.
Every grain gets one, generated only after its window closes — so the
single-threaded LM Studio is paced by window closure, not a burst.

Leaf (``5min``) narratives read the raw frame descriptions inside the window.
Higher grains are summary-of-summaries: they read their CHILDREN's narratives +
own metrics, never re-scanning frames. Output is structured (description /
evaluation / body_json); the builder estimates ``compression_ratio`` for the
drill-down "information scent".

The LLM is injected (``NarrativeLLM``) so the builder is unit-testable with a
mock and has no hard dependency on the box VLM endpoint.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Protocol

import structlog

from timetrace.server.summary.windows import CHILD_OF, GRAINS

if TYPE_CHECKING:
    from openai import AsyncOpenAI

    from timetrace.server.db import Database

logger = structlog.get_logger(__name__)

# Chars/token estimate (matches infra/storage/pyramid-schema.md's scent heuristic).
_CHARS_PER_TOKEN = 3.5

# Hard character budget for the leaf frame context, so a busy window can't
# overflow the model's input window (CJK tokenizes ~1 token/char). Only DESCRIBED
# frames are fed (no-description switch rows carry no narrative). With the box LM
# Studio reloaded at 16384 / parallel=1 there's ample input room; the real output
# constraint is ``max_tokens`` (an always-thinking model spends it on reasoning
# before content — keep it generous, see NarrativeBuilder.max_tokens).
_MAX_LEAF_CONTEXT_CHARS = 3000

# Per-grain output budget. Coarser windows legitimately produce a longer
# narrative (a summary-of-summaries spans more time), and the always-thinking
# model spends part of this budget on reasoning before any content — so a flat
# 3000 truncates the dense / aggregate windows mid-JSON. Give the coarse grains
# headroom. A uniform override can still be forced via NarrativeBuilder(
# max_tokens=...) for live tuning.
_GRAIN_MAX_TOKENS = {
    "5min": 4000,
    "1h": 6000,
    "6h": 7000,
    "day": 7000,
    "week": 7000,
}
_DEFAULT_MAX_TOKENS = 4000  # fallback for an unknown grain

_GRAIN_SCOPE = {
    "5min": "这 5 分钟",
    "1h": "这 1 小时",
    "6h": "这 6 小时段",
    "day": "这一天",
    "week": "这一周",
}

_SYSTEM_PROMPT = (
    "你是个人活动记忆的叙述助手。给你某个时间窗内这台电脑的活动"
    "（指标 + 逐帧描述或下层摘要），你生成一段结构化的中文总结。"
    "三部分都要，尽量详细、尽量具体：\n"
    "1. description（流水账）：按时间顺序客观叙述这段时间在做什么，"
    "落到具体的应用/文件/网页/话题。\n"
    "2. key_points（重点提炼）：3-6 条，"
    "提炼关键事件、产出、转折、卡点；每条一句话。\n"
    "3. evaluation（评价）：一到几句，"
    "中肯评价专注度/效率/性质，可点出深度工作、分心、返工、拖延等。\n"
    "口径很重要：这些是【本机捕获的活跃切片】，不是这段时间的全部；"
    "如实叙述，绝不脑补没出现的内容，信息不足就说不足。\n"
    "只输出一个 JSON 对象，键为 description(str)、"
    "key_points(list[str])、evaluation(str)，不要别的文字。"
)


class NarrativeLLM(Protocol):
    """Minimal text-completion surface the builder needs (mock-friendly)."""

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str: ...


class OpenAINarrativeLLM:
    """``NarrativeLLM`` backed by an OpenAI-compatible chat endpoint (LM Studio)."""

    def __init__(self, client: AsyncOpenAI, model: str, *, disable_thinking: bool = False) -> None:
        self._client = client
        self._model = model
        self._disable_thinking = disable_thinking

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        # Qwen3+ reasoning models otherwise burn the whole token budget on
        # <think> and return empty content — same opt-out the VLM client uses.
        extra = {"extra_body": {"enable_thinking": False}} if self._disable_thinking else {}
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
            **extra,
        )
        return resp.choices[0].message.content or ""


def _est_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def _fmt_hm(ms: int) -> str:
    return time.strftime("%H:%M", time.localtime(ms / 1000))


def _metrics_line(metrics: dict) -> str:
    cat = metrics.get("cat_ms") or {}
    top = sorted(cat.items(), key=lambda kv: -kv[1])[:6]
    cats = "、".join(f"{k} {round(v / 1000)}s" for k, v in top) or "（无）"
    apps = metrics.get("app_ms") or {}
    top_apps = sorted(apps.items(), key=lambda kv: -kv[1])[:6]
    app_s = "、".join(f"{k} {round(v / 1000)}s" for k, v in top_apps) or "（无）"
    active = round((metrics.get("active_ms") or 0) / 1000)
    return (
        f"分类时长: {cats}\n应用时长: {app_s}\n"
        f"活跃(切片之和): {active}s | 记录数: {metrics.get('record_count', 0)}"
    )


def _metrics_only_narrative(metrics: dict, n_src: int) -> dict:
    """Deterministic, LLM-free narrative for a window with nothing to describe
    (no described frames / no child narratives — e.g. a window_switch burst with
    no screenshots). Skipping the LLM here is what keeps the background loop from
    spinning forever retrying hopeless windows (empty content → pending → retry).
    Still carries the metric line so the row says something concrete.
    """
    metric_str = _metrics_line(metrics).replace("\n", "；")
    desc = f"本窗无可叙述的视觉记录（{n_src} 条活动，多为窗口切换/无截图）。指标：{metric_str}"
    body = {"key_points": [], "source_units": n_src, "metrics_only": True}
    return {
        "description": desc,
        "evaluation": "",
        "body_json": json.dumps(body, ensure_ascii=False),
        "src_tokens": 0,
        "out_tokens": _est_tokens(desc),
        "compression_ratio": None,
    }


def _strip_fences(s: str) -> str:
    """Drop a leading ```json / ``` line and a trailing ``` if present."""
    s = s.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
    if s.endswith("```"):
        s = s[: s.rfind("```")]
    return s.strip()


def _read_json_string(s: str) -> str:
    """Read a JSON string body up to the next unescaped quote (or end if the
    stream was truncated mid-value). ``s`` starts right after the opening quote.
    """
    out: list[str] = []
    i = 0
    escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            out.append(escapes.get(s[i + 1], s[i + 1]))
            i += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _salvage_fields(s: str) -> dict:
    """Recover fields from a truncated / unparseable JSON blob.

    The model writes ``description`` first, so on a max_tokens truncation the
    description usually survives (possibly clipped) while later fields are gone.
    Pull whatever landed instead of dumping the raw blob into ``description``.
    """
    desc = ""
    m = re.search(r'"description"\s*:\s*"', s)
    if m:
        desc = _read_json_string(s[m.end() :])
    ev = ""
    me = re.search(r'"evaluation"\s*:\s*"', s)
    if me:
        ev = _read_json_string(s[me.end() :])
    kps: list[str] = []
    mk = re.search(r'"key_points"\s*:\s*\[(.*?)\]', s, re.DOTALL)
    if mk:
        kps = re.findall(r'"((?:[^"\\]|\\.)*)"', mk.group(1))
    return {"description": desc.strip(), "key_points": kps, "evaluation": ev.strip()}


def _parse_narrative(raw: str) -> dict:
    """Best-effort extract the structured fields.

    First try a clean JSON parse (after stripping code fences); on failure
    (truncated output is the common one) salvage the description / key_points /
    evaluation that did land, rather than dumping the raw ```json blob.
    """
    s = _strip_fences(raw)
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        try:
            obj = json.loads(s[a : b + 1])
            return {
                "description": str(obj.get("description") or "").strip(),
                "key_points": [str(x) for x in (obj.get("key_points") or [])],
                "evaluation": str(obj.get("evaluation") or "").strip(),
            }
        except (json.JSONDecodeError, TypeError):
            pass
    salvaged = _salvage_fields(s)
    if salvaged["description"]:
        return salvaged
    return {"description": s, "key_points": [], "evaluation": ""}


class NarrativeBuilder:
    """Generates the narrative for a finalized cascade row via an injected LLM."""

    def __init__(self, db: Database, llm: NarrativeLLM, *, max_tokens: int | None = None) -> None:
        self._db = db
        self._llm = llm
        # None → smart per-grain budget (_GRAIN_MAX_TOKENS); an int forces that
        # value uniformly across grains (live tuning via the CLI flag).
        self._max_tokens_override = max_tokens

    async def build_one(self, summary: dict) -> dict:
        """Build {description, evaluation, body_json, src/out_tokens, compression_ratio}.

        Pure compute — the caller persists it. Leaf reads frames; higher grains
        read child narratives (summary-of-summaries).
        """
        grain = summary["grain"]
        metrics = json.loads(summary.get("metrics_json") or "{}")
        if grain == "5min":
            context, n_src, n_desc = await self._leaf_context(summary)
            if n_desc == 0:
                # No describable frames — don't spend the GPU (or the loop's
                # retries) on a window that can only ever come back empty.
                return _metrics_only_narrative(metrics, n_src)
        else:
            context, n_src = await self._rollup_context(summary)
            if n_src == 0:
                return _metrics_only_narrative(metrics, n_src)

        scope = _GRAIN_SCOPE.get(grain, grain)
        span = f"{_fmt_hm(summary['window_start'])}–{_fmt_hm(summary['window_end'])}"
        user = (
            f"时间窗：{scope}（{summary.get('day_local', '')} {span}，grain={grain}）\n\n"
            f"【指标】\n{_metrics_line(metrics)}\n\n"
            f"【{'逐帧描述' if grain == '5min' else '下层摘要'}】（共 {n_src} 条）\n{context}"
        )
        budget = self._max_tokens_override or _GRAIN_MAX_TOKENS.get(grain, _DEFAULT_MAX_TOKENS)
        raw = await self._llm.complete(system=_SYSTEM_PROMPT, user=user, max_tokens=budget)
        parsed = _parse_narrative(raw)
        if not parsed["description"]:
            # Empty content — the always-thinking model burned the whole budget on
            # reasoning before any answer (thinking length is large and varies run
            # to run). Fail loudly so the row stays pending and a re-run retries it,
            # instead of being silently marked 'narrated' with nothing in it.
            raise ValueError("empty narrative content (model returned no usable text)")

        body = {"key_points": parsed["key_points"], "source_units": n_src}
        out_text = (
            parsed["description"] + " " + parsed["evaluation"] + " ".join(parsed["key_points"])
        )
        src_tokens = _est_tokens(context)
        out_tokens = _est_tokens(out_text)
        return {
            "description": parsed["description"],
            "evaluation": parsed["evaluation"],
            "body_json": json.dumps(body, ensure_ascii=False),
            "src_tokens": src_tokens,
            "out_tokens": out_tokens,
            "compression_ratio": round(out_tokens / src_tokens, 4) if src_tokens else None,
        }

    async def _leaf_context(self, summary: dict) -> tuple[str, int, int]:
        """Chronological frame lines for a 5min window. Only frames WITH a VLM
        description are fed (they carry the story); no-description window_switch
        rows are dropped from the context but still counted in ``n_src``. Capped
        to ``_MAX_LEAF_CONTEXT_CHARS`` to stay inside the model context.

        Returns ``(context, n_total, n_described)`` — the caller skips the LLM
        entirely when ``n_described`` is 0.
        """
        async with self._db.lock:
            async with self._db.conn.execute(
                """SELECT r.ts_start, r.app_name, r.window_title, a.vlm_desc, a.category_final
                   FROM records r LEFT JOIN analysis_results a ON a.record_id = r.id
                   WHERE r.ts_start >= ? AND r.ts_start < ?
                   ORDER BY r.ts_start""",
                (summary["window_start"], summary["window_end"]),
            ) as cur:
                rows = await cur.fetchall()
        described = [r for r in rows if (r["vlm_desc"] or "").strip()]
        lines, used, shown = [], 0, 0
        for r in described:
            desc = (r["vlm_desc"] or "").strip().replace("\n", " ")
            cat = r["category_final"] or "?"
            line = (
                f"{_fmt_hm(r['ts_start'])} | {r['app_name'] or ''} | "
                f"{(r['window_title'] or '')[:50]} | [{cat}] {desc[:140]}"
            )
            if used + len(line) > _MAX_LEAF_CONTEXT_CHARS and lines:
                break
            lines.append(line)
            used += len(line)
            shown += 1
        if shown < len(described):
            lines.append(
                f"…（窗内共 {len(rows)} 条、{len(described)} 条有描述，上下文截断到前 "
                f"{shown}；调大 LM Studio 上下文可全量）"
            )
        return ("\n".join(lines) or "（无逐帧描述）"), len(rows), len(described)

    async def _rollup_context(self, summary: dict) -> tuple[str, int]:
        """Child-grain narratives feeding a higher window (summary-of-summaries)."""
        child = CHILD_OF[summary["grain"]]
        children = await self._db.get_summaries_in_range(
            child, summary["window_start"], summary["window_end"]
        )
        lines = []
        for c in children:
            span = f"{_fmt_hm(c['window_start'])}–{_fmt_hm(c['window_end'])}"
            desc = (c["description"] or "").strip().replace("\n", " ")
            lines.append(f"[{span}] {desc[:400]}" if desc else f"[{span}] （下层暂无叙述，仅指标）")
        return ("\n".join(lines) or "（无下层摘要）"), len(children)


class NarrativeCascade:
    """Drives narrative generation bottom-up over finalized windows in a range.

    Walks grains fine→coarse, so by the time a parent is narrated its children's
    fresh narratives already exist (summary-of-summaries). Idempotent: only
    narrates rows still at ``status='pending_summary'`` — a re-emission (source
    change) resets a row back to pending, so it gets re-narrated next run.
    """

    def __init__(self, db: Database, builder: NarrativeBuilder) -> None:
        self._db = db
        self._builder = builder

    async def _has_pending_children(
        self, grain: str, window_start: int, window_end: int, now_ms: int
    ) -> bool:
        """True if a finalized child window of this parent is still un-narrated.

        Defers a coarse window until its children are narrated, so it's a real
        summary-of-summaries and not a premature metrics paraphrase. Without this,
        a backlog drain (per-grain limit caps each tick) narrates a 1h window
        while most of its 5min children are still pending — and since a parent's
        source_hash doesn't change when a child gains a narrative, that thin
        narrative would never self-correct.
        """
        child = CHILD_OF.get(grain)
        if child is None:  # 5min — no children to wait on
            return False
        kids = await self._db.get_summaries_in_range(child, window_start, window_end)
        return any(k["status"] == "pending_summary" and k["window_end"] <= now_ms for k in kids)

    async def narrate_range(
        self,
        start_ms: int,
        end_ms: int,
        now_ms: int,
        *,
        per_grain_limit: int = 500,
        force: bool = False,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for grain in GRAINS:  # fine→coarse: parents see freshly-written children
            if force:
                # re-narrate any finalized window in range, ignoring status
                rows = [
                    r
                    for r in await self._db.get_summaries_in_range(grain, start_ms, end_ms)
                    if r["window_end"] <= now_ms
                ][:per_grain_limit]
            else:
                rows = await self._db.get_pending_summaries(
                    grain, start_ms, end_ms, now_ms, per_grain_limit
                )
            done, deferred = 0, 0
            for row in rows:
                if await self._has_pending_children(
                    grain, row["window_start"], row["window_end"], now_ms
                ):
                    # Children not yet narrated — defer; a later tick picks it up
                    # once the finer grain has drained (keeps the drain bottom-up).
                    deferred += 1
                    continue
                try:
                    out = await self._builder.build_one(row)
                    await self._db.save_summary_narrative(row["id"], **out)
                    done += 1
                except Exception:  # noqa: BLE001
                    # One bad window (LLM error, context overflow, …) shouldn't
                    # abort the whole pass; it stays pending and retries next run.
                    logger.warning(
                        "narrative.window_failed",
                        grain=grain,
                        scope=row.get("scope_key"),
                        exc_info=True,
                    )
            counts[grain] = done
            if done or deferred:
                logger.info("narrative.grain_done", grain=grain, n=done, deferred=deferred)
        return counts
