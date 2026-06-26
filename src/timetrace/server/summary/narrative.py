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

    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
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


def _parse_narrative(raw: str) -> dict:
    """Best-effort extract the JSON object; fall back to raw-as-description."""
    s = raw.strip()
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
    return {"description": s, "key_points": [], "evaluation": ""}


class NarrativeBuilder:
    """Generates the narrative for a finalized cascade row via an injected LLM."""

    def __init__(self, db: Database, llm: NarrativeLLM, *, max_tokens: int = 1200) -> None:
        self._db = db
        self._llm = llm
        self._max_tokens = max_tokens

    async def build_one(self, summary: dict) -> dict:
        """Build {description, evaluation, body_json, src/out_tokens, compression_ratio}.

        Pure compute — the caller persists it. Leaf reads frames; higher grains
        read child narratives (summary-of-summaries).
        """
        grain = summary["grain"]
        metrics = json.loads(summary.get("metrics_json") or "{}")
        if grain == "5min":
            context, n_src = await self._leaf_context(summary)
        else:
            context, n_src = await self._rollup_context(summary)

        scope = _GRAIN_SCOPE.get(grain, grain)
        span = f"{_fmt_hm(summary['window_start'])}–{_fmt_hm(summary['window_end'])}"
        user = (
            f"时间窗：{scope}（{summary.get('day_local', '')} {span}，grain={grain}）\n\n"
            f"【指标】\n{_metrics_line(metrics)}\n\n"
            f"【{'逐帧描述' if grain == '5min' else '下层摘要'}】（共 {n_src} 条）\n{context}"
        )
        raw = await self._llm.complete(
            system=_SYSTEM_PROMPT, user=user, max_tokens=self._max_tokens
        )
        parsed = _parse_narrative(raw)

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

    async def _leaf_context(self, summary: dict) -> tuple[str, int]:
        """Chronological frame lines (ts | app | title | vlm_desc) for a 5min window."""
        async with self._db.lock:
            async with self._db.conn.execute(
                """SELECT r.ts_start, r.app_name, r.window_title, a.vlm_desc, a.category_final
                   FROM records r LEFT JOIN analysis_results a ON a.record_id = r.id
                   WHERE r.ts_start >= ? AND r.ts_start < ?
                   ORDER BY r.ts_start""",
                (summary["window_start"], summary["window_end"]),
            ) as cur:
                rows = await cur.fetchall()
        lines = []
        for r in rows:
            desc = (r["vlm_desc"] or "").strip().replace("\n", " ")
            cat = r["category_final"] or "?"
            lines.append(
                f"{_fmt_hm(r['ts_start'])} | {r['app_name'] or ''} | "
                f"{(r['window_title'] or '')[:60]} | [{cat}] {desc[:200]}"
            )
        return ("\n".join(lines) or "（无逐帧描述）"), len(rows)

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

    async def narrate_range(
        self, start_ms: int, end_ms: int, now_ms: int, *, per_grain_limit: int = 500
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for grain in GRAINS:  # fine→coarse: parents see freshly-written children
            rows = await self._db.get_pending_summaries(
                grain, start_ms, end_ms, now_ms, per_grain_limit
            )
            for row in rows:
                out = await self._builder.build_one(row)
                await self._db.save_summary_narrative(row["id"], **out)
            counts[grain] = len(rows)
            if rows:
                logger.info("narrative.grain_done", grain=grain, n=len(rows))
        return counts
