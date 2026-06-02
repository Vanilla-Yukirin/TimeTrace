"""Generate an AI insight dashboard ("看板") for a time scope.

Reuses the chat agent's tool-calling loop (``AgentRunner``): the model calls the
read tools to pull the user's REAL recent activity, then writes a self-contained
HTML fragment that "抓特点" — the vibe of a NetEase year-in-review / QQ 群分析,
not KPI bars. The LLM call is expensive, so this runs on a schedule (see
``server/bootstrap.py``) plus an on-demand POST route.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import structlog

from timetrace.server.agent.runner import AgentRunner

if TYPE_CHECKING:
    from timetrace.common.config import VLMConfig
    from timetrace.server.db import Database

logger = structlog.get_logger(__name__)

# scope → hours of history the report covers.
SCOPE_HOURS = {"recent_3h": 3, "recent_24h": 24, "recent_7d": 168}
SCOPE_LABELS = {"recent_3h": "最近 3 小时", "recent_24h": "最近 24 小时", "recent_7d": "最近 7 天"}
DEFAULT_SCOPE = "recent_24h"

_REPORT_SYSTEM = (
    "你是 TimeTrace 的『近况洞察』分析师，风格像网易云年度报告 / QQ 群聊分析——幽默、会抓特点。"
    "但有一条铁律：只讲工具返回的真实数据能支撑的结论，绝不编造，绝不脑补一个应用是干嘛的、"
    "也绝不臆测用户的动机。每写一句话，先问自己『这能在工具数据里找到出处吗』，找不到就不要写。\n\n"
    "【TimeTrace 分类体系】只有 6 个固定分类，互斥：work=工作、study=学习、social=沟通、"
    "entertainment=娱乐、system=系统、uncategorized=未分类。报告里提到分类一律用中文名。"
    "get_category_stats 的返回里有 categories_legend 给出 id→中文名，照着用。\n\n"
    "【极其重要——别把『尚未分类』当成『未分类』行为】"
    "get_category_stats 返回两种含义完全不同的桶：\n"
    '- category 为 "_unclassified"（带 is_unclassified=true）：这是【系统内部积压】——'
    "旧数据还没补上分类标签、或还在排队分析。它【不是】用户行为，绝对不要说成『未分类时长 Xh』，"
    "更不要据此推断用户在干嘛。正常只在它占比很大时用轻松的一句话带过"
    "（例如『有一批早期记录还在等系统补标签』）。\n"
    '- category 为 "uncategorized"（带 is_genuinely_uncategorized=true）：'
    "这才是真正归不进任何类的记录，通常几乎为 0。\n"
    "判断某个应用『没被分类 / 不知道是啥』之前，先看 get_recent_activity / search_activity "
    "里它的 vlm_desc——描述里通常已经写清楚它是什么"
    "（例如『Vanish桌面端即时通讯软件』『Claude AI 桌面应用』）。"
    "描述说得清楚就照实讲它是什么，绝不要说『神秘应用』『不知道是啥』。\n\n"
    "【时长要存疑】工具返回的时长已对单条记录封顶（见 capped_per_record_seconds）"
    "以压制笔记本休眠/合盖产生的虚假超长记录，但聚合值仍可能偏高。给具体时长用『约 / 大概』，"
    "不要精确到分钟去煞有介事地比较；明显异常（例如某应用一天 9 小时）直接存疑或不报。\n\n"
    "【看到很大的『尚未分类』或真『未分类』桶时怎么办】不要替系统或用户编理由。可以友好地提示一句："
    "用户可以为特定应用配置固定分类规则（例如把公司内部 IM 固定成『沟通』或『工作』），"
    "配置后该应用就会被自动、确定性地归类。把它写成贴心建议，而不是报错或吐槽。\n\n"
    "工作流：先调用工具（get_recent_activity / get_app_breakdown / get_category_stats）"
    "拿到这段时间的【真实】数据，再据此写报告。\n\n"
    "输出：**只输出一个 JSON 对象**，第一个字符就是 `{`。绝对不要输出任何分析过程 / 思考 / "
    "前言 / 解释，也不要 markdown、不要 ```json 代码围栏。颜色和排版由前端负责，你只给内容文字"
    "（值一律用中文）。字段：\n"
    '- "scope_label"(str)：本报告时段的中文短语，如 "过去 24 小时"。\n'
    '- "headline"(str)：本时段大致活跃时长（已剔除休眠封顶后），口语化，如 "约 10 小时"。\n'
    '- "headline_caption"(str)：给 headline 配一句很短的说明，如 "总活跃时长（已剔除休眠封顶）"。\n'
    '- "top_app"(对象或 null)：最花时间的应用，形如 '
    '{"name": "Visual Studio Code", "value": "约 3.3 小时"}。\n'
    '- "caveat"(str 或 null)：一句话提醒；比如一大批数据还在排队分类（系统积压、不是用户行为）'
    "时友好提一句，没有要提醒的就给 null。\n"
    '- "insights"(数组)：2-4 条洞察，每条形如 '
    '{"emoji": "💻", "title": "短标题", "body": "一句话点出一个【有数据出处】的真实特点"}。'
    "可风趣，但每句都要能在工具数据里找到依据。\n"
    "数据稀少、或大半是『尚未分类』遗留时，就在 caveat / insights 里幽默且诚实地说明现状，"
    "别硬编洞察。"
)


def _instruction(scope: str) -> str:
    label = SCOPE_LABELS.get(scope, "最近一段时间")
    return f"请为我生成一份『{label}』的活动近况洞察看板（HTML）。先查真实数据再写。"


def _json_candidates(t: str):
    """Yield progressively-more-salvaged candidate JSON strings from raw output."""
    yield t
    # ```json ... ``` fenced block (model wrapped it despite being told not to)
    m = re.search(r"```(?:json)?[ \t]*\n(.*?)\n?```", t, re.S | re.I)
    if m:
        yield m.group(1).strip()
    # first balanced {...} run (model prepended stray prose / reasoning)
    start = t.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    yield t[start : i + 1]
                    break


def _extract_json(text: str) -> dict | None:
    """Pull the report JSON object out of the model's output (best-effort)."""
    t = (text or "").strip()
    for candidate in _json_candidates(t):
        try:
            obj = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _parse_report(text: str) -> dict:
    """Validate / coerce the model's JSON into the clean shape the frontend renders.

    Colour + layout live in the frontend's themed component (so reports adapt to
    the app's light/dark theme); the model only supplies content text here. We
    always return a well-formed dict — a fallback with a friendly caveat if the
    output couldn't be parsed — so the frontend never has to guess.
    """
    obj = _extract_json(text)
    if obj is None:
        return {
            "scope_label": "",
            "headline": "",
            "headline_caption": "",
            "top_app": None,
            "caveat": "这份报告没能正常生成，点「重新生成」再试一次吧。",
            "insights": [],
        }

    def s(v: object) -> str:
        return v.strip() if isinstance(v, str) else ""

    top = obj.get("top_app")
    top_app = None
    if isinstance(top, dict) and s(top.get("name")):
        top_app = {"name": s(top.get("name")), "value": s(top.get("value"))}

    insights: list[dict] = []
    raw = obj.get("insights")
    if isinstance(raw, list):
        for it in raw[:5]:
            if isinstance(it, dict) and s(it.get("title")) and s(it.get("body")):
                insights.append(
                    {
                        "emoji": s(it.get("emoji")) or "•",
                        "title": s(it.get("title")),
                        "body": s(it.get("body")),
                    }
                )

    return {
        "scope_label": s(obj.get("scope_label")),
        "headline": s(obj.get("headline")),
        "headline_caption": s(obj.get("headline_caption")),
        "top_app": top_app,
        "caveat": s(obj.get("caveat")) or None,
        "insights": insights,
    }


class ReportGenerator:
    """Run the agent loop with a report persona and persist the HTML it writes."""

    def __init__(self, db: Database, vlm_cfg: VLMConfig) -> None:
        self._db = db
        self._cfg = vlm_cfg

    async def generate(self, scope: str = DEFAULT_SCOPE) -> dict:
        """Generate + persist a report, returning the final record (non-streaming).

        Used by the 30-min scheduler and the legacy POST route. Internally drains
        ``generate_stream`` and returns its terminal ``report`` payload.
        """
        final: dict | None = None
        async for ev in self.generate_stream(scope):
            if ev["type"] == "report":
                final = ev["report"]
            elif ev["type"] == "error":
                raise RuntimeError(ev["message"])
        if final is None:  # pragma: no cover - stream always ends in report or error
            raise RuntimeError("report stream ended without a result")
        return final

    async def generate_stream(self, scope: str = DEFAULT_SCOPE) -> AsyncIterator[dict]:
        """Run the agent loop and yield progress events, then persist + yield the
        final report. Lets the UI show tool steps + live token output.

        Event shapes (``type`` discriminates):
          {"type":"step","phase":"tool_call"|"tool_result", "tool":.., ...}
          {"type":"token","text":..}                       # live report HTML
          {"type":"report","report":{id,scope,...,content}}  # terminal success
          {"type":"error","message":..}                      # terminal failure
        """
        hours = SCOPE_HOURS.get(scope, 24)
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - hours * 3600 * 1000
        runner = AgentRunner(self._db, self._cfg, system_prompt=_REPORT_SYSTEM, max_iterations=5)
        parts: list[str] = []
        try:
            async for ev in runner.run(
                [{"role": "user", "content": _instruction(scope)}],
                hours_back_hint=hours,
            ):
                if ev["type"] == "token":
                    parts.append(ev["text"])
                    yield ev
                elif ev["type"] == "step":
                    yield ev
                elif ev["type"] == "error":
                    yield ev
                    return
        finally:
            await runner.aclose()
        data = _parse_report("".join(parts))
        content = json.dumps(data, ensure_ascii=False)
        report_id = await self._db.insert_report(
            scope=scope,
            period_start=start_ms,
            period_end=now_ms,
            fmt="json",
            content=content,
            model=self._cfg.model,
        )
        logger.info("report.saved", scope=scope, report_id=report_id, n=len(data["insights"]))
        yield {
            "type": "report",
            "report": {
                "id": report_id,
                "scope": scope,
                "period_start": start_ms,
                "period_end": now_ms,
                "format": "json",
                "content": content,
                "model": self._cfg.model,
                "created_at": now_ms,
            },
        }
