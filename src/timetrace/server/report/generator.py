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
    "你是 TimeTrace 的『近况洞察』分析师，风格像网易云年度报告 / QQ 群聊分析——"
    "幽默、有洞察、会抓特点，而不是干巴巴的 KPI。\n"
    "工作流：先调用工具（get_recent_activity / get_app_breakdown / "
    "get_category_stats）拿到用户这段时间的【真实】活动数据，再据此写报告；"
    "所有结论必须基于工具返回的真实数据，绝不编造。\n"
    "拿到数据后，只输出一段【自包含的 HTML 片段】（不要 markdown、不要代码围栏、"
    "不要 <html>/<body> 外壳），结构：\n"
    "1) 顶部一个总览卡片：本时段总活跃时长 + 最花时间的应用/分类；\n"
    "2) 下面 2-4 个『特点 / 洞察』小卡片，每个一句话点出一个有意思的特点"
    "（如专注时段、主线任务、摸鱼时刻、反差亮点）。\n"
    "样式用内联 style，圆角卡片、半透明背景、继承文字颜色（适配深浅主题），"
    "可用 emoji，紧凑好看。时长把秒换算成分钟 / 小时。数据稀少就幽默地说明，别硬编。"
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
