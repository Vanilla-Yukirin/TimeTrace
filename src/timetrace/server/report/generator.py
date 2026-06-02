"""Generate an AI insight dashboard ("看板") for a time scope.

Reuses the chat agent's tool-calling loop (``AgentRunner``): the model calls the
read tools to pull the user's REAL recent activity, then writes a self-contained
HTML fragment that "抓特点" — the vibe of a NetEase year-in-review / QQ 群分析,
not KPI bars. The LLM call is expensive, so this runs on a schedule (see
``server/bootstrap.py``) plus an on-demand POST route.
"""

from __future__ import annotations

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
    "输出：只输出一段【自包含的 HTML 片段】"
    "（不要 markdown、不要代码围栏、不要 <html>/<body> 外壳），结构：\n"
    "1) 顶部一个总览卡片：本时段（已剔除休眠伪影后的）大致活跃时长 + 最花时间的应用/分类；\n"
    "2) 下面 2-4 个『特点 / 洞察』小卡片，每个一句话点出一个【有数据出处】的真实特点"
    "（如某段时间集中在某应用、某分类占比突出、沟通类很活跃）。"
    "可以风趣，但每句都要能在工具数据里找到依据。\n"
    "样式用内联 style，圆角卡片、半透明背景、继承文字颜色（适配深浅主题），"
    "可用 emoji，紧凑好看。时长把秒换算成分钟 / 小时。"
    "数据稀少、或大半是『尚未分类』遗留时，就幽默且诚实地说明现状，别硬编洞察。"
)


def _instruction(scope: str) -> str:
    label = SCOPE_LABELS.get(scope, "最近一段时间")
    return f"请为我生成一份『{label}』的活动近况洞察看板（HTML）。先查真实数据再写。"


def _clean_html(text: str) -> str:
    """Strip a leading ```html / trailing ``` fence the model may add anyway."""
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\n", "", t)
    t = re.sub(r"\n```$", "", t)
    return t.strip()


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
        html = _clean_html("".join(parts))
        report_id = await self._db.insert_report(
            scope=scope,
            period_start=start_ms,
            period_end=now_ms,
            fmt="html",
            content=html,
            model=self._cfg.model,
        )
        logger.info("report.saved", scope=scope, report_id=report_id, html_len=len(html))
        yield {
            "type": "report",
            "report": {
                "id": report_id,
                "scope": scope,
                "period_start": start_ms,
                "period_end": now_ms,
                "format": "html",
                "content": html,
                "model": self._cfg.model,
                "created_at": now_ms,
            },
        }
