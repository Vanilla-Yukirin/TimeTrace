"""TimeTrace MCP server — exposes activity context to external AI agents.

Mounted on the main FastAPI app at ``/mcp`` via ``mount_mcp`` (called from
``server/api/app.py``). Single-process, same port, same SSH-tunnel as the
REST API — no extra wiring for the demo client (Claude Code / Desktop).

External agents see four tools:
  - ``search_activity`` — keyword search backed by FTS5 trigram + multi-field
    LIKE (server-side), returns the same shape /v1/records does
  - ``get_recent_activity`` — recent-N-hours snapshot, no keyword
  - ``get_app_breakdown`` — duration aggregates per app for a time window
  - ``ask_agent`` — internal Qwen3-35BA3B agent: takes a natural-language
    question, retrieves relevant records, asks the LLM to synthesize a
    natural-language answer. Single LLM round-trip (no tool-calling loop)
    keeps demo latency tight and is easier to debug.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog
from mcp.server.fastmcp import Context, FastMCP
from openai import AsyncOpenAI

if TYPE_CHECKING:
    from fastapi import FastAPI

    from timetrace.common.config import VLMConfig
    from timetrace.server.db import Database

logger = structlog.get_logger(__name__)

# Cap context fed to the LLM in ask_agent. ~6 KB per record × N must stay
# inside Qwen3.6's 4096-token window after prompt + answer headroom.
_ASK_AGENT_MAX_RECORDS = 60
_ASK_AGENT_RECORD_DESC_CHARS = 200
_ASK_AGENT_TIMEOUT_S = 90.0


def _ms_to_iso(ms: int | None) -> str:
    """epoch-ms → ISO 8601 local-tz, sortable + human-readable."""
    if ms is None:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000))


def _format_record_for_llm(r: dict) -> str:
    """Compact one-line text representation of a record for LLM consumption."""
    ts = _ms_to_iso(r.get("ts_start"))
    app = r.get("app_name") or ""
    title = r.get("window_title") or ""
    desc = r.get("vlm_desc") or ""
    if len(desc) > _ASK_AGENT_RECORD_DESC_CHARS:
        desc = desc[:_ASK_AGENT_RECORD_DESC_CHARS] + "..."
    duration_s: int | float = 0
    if r.get("ts_end") and r.get("ts_start"):
        duration_s = max(0, (r["ts_end"] - r["ts_start"]) / 1000)
    return f"{ts} | {app} | {title[:80]} | {int(duration_s)}s | {desc}"


def build_mcp_server(db: Database, vlm_cfg: VLMConfig | None) -> FastMCP:
    """Construct a FastMCP server wired to a Database and optional VLM endpoint.

    Tool implementations close over ``db`` (synchronous reads via the same
    aiosqlite lock that the REST API uses) and ``vlm_cfg`` (only for
    ``ask_agent``; if ``None``, the tool returns a degraded "VLM unavailable"
    response so the rest of the MCP surface still works).
    """
    mcp: FastMCP = FastMCP("timetrace")
    # Cache an OpenAI client per server (not per tool call) so the underlying
    # httpx connection pool gets reused across requests. None if VLM unset.
    chat_client: AsyncOpenAI | None = None
    if vlm_cfg is not None:
        chat_client = AsyncOpenAI(base_url=vlm_cfg.base_url, api_key=vlm_cfg.api_key)

    @mcp.tool()
    async def search_activity(
        query: str,
        limit: int = 20,
        hours_back: int | None = None,
    ) -> dict:
        """Keyword search over activity records.

        Searches across window title, app name, process name, URL, and VLM
        description. ≥3 char queries use FTS5 trigram + BM25 ranking
        (CJK-friendly); shorter queries fall back to multi-field LIKE.

        Args:
            query: search keyword. Examples: "鸣潮", "Visual Studio Code",
                "微信", "Weixin", "Code.exe", "github.com".
            limit: max records to return (default 20, cap 100).
            hours_back: if set, limit to the last N hours. Otherwise all-time.

        Returns:
            dict with ``items`` (list of records) and ``query`` echoed back.
            Each record has ``id``, ``ts_start``, ``ts_end``, ``app_name``,
            ``window_title``, ``vlm_desc``, ``thumb_path``.
        """
        limit = min(max(1, limit), 100)
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - hours_back * 3600 * 1000 if hours_back else 0
        rows = await db.query_records(
            start_ms=start_ms,
            end_ms=now_ms,
            limit=limit,
            keyword=query,
        )
        return {
            "query": query,
            "items": [
                {
                    "id": r["id"],
                    "ts_start_iso": _ms_to_iso(r.get("ts_start")),
                    "ts_end_iso": _ms_to_iso(r.get("ts_end")),
                    "app_name": r.get("app_name"),
                    "window_title": r.get("window_title"),
                    "vlm_desc": r.get("vlm_desc"),
                    "category": r.get("category_final"),
                }
                for r in rows
            ],
        }

    @mcp.tool()
    async def get_recent_activity(hours_back: int = 24, limit: int = 50) -> dict:
        """Return a chronological snapshot of recent activity.

        No keyword filter — use when the agent wants an overview before
        deciding what to dig into.

        Args:
            hours_back: time window in hours (default 24, cap 720 = 30 days).
            limit: max records (default 50, cap 200).
        """
        hours_back = min(max(1, hours_back), 720)
        limit = min(max(1, limit), 200)
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - hours_back * 3600 * 1000
        rows = await db.query_records(start_ms=start_ms, end_ms=now_ms, limit=limit)
        return {
            "hours_back": hours_back,
            "items": [
                {
                    "ts_start_iso": _ms_to_iso(r.get("ts_start")),
                    "app_name": r.get("app_name"),
                    "window_title": r.get("window_title"),
                    "vlm_desc": (r.get("vlm_desc") or "")[:200],
                }
                for r in rows
            ],
        }

    @mcp.tool()
    async def get_app_breakdown(hours_back: int = 24, top_n: int = 20) -> dict:
        """Aggregate active duration per app over the last N hours.

        Sums (ts_end - ts_start) for each app. Records without ts_end (still
        open) are skipped. Use this for "how much time did I spend in X".

        Args:
            hours_back: time window (default 24, cap 720).
            top_n: max apps to return, sorted by duration desc (default 20).
        """
        hours_back = min(max(1, hours_back), 720)
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - hours_back * 3600 * 1000
        # Use raw SQL — query_records doesn't aggregate.
        async with db.lock:
            async with db.conn.execute(
                """SELECT app_name,
                          COUNT(*) AS records,
                          SUM(COALESCE(ts_end, ts_start) - ts_start) AS total_ms
                   FROM records
                   WHERE ts_start BETWEEN ? AND ?
                     AND app_name != ''
                   GROUP BY app_name
                   ORDER BY total_ms DESC
                   LIMIT ?""",
                (start_ms, now_ms, top_n),
            ) as cur:
                rows = await cur.fetchall()
        items = [
            {
                "app_name": r["app_name"],
                "records": r["records"],
                "total_seconds": int((r["total_ms"] or 0) / 1000),
            }
            for r in rows
        ]
        total_seconds = sum(it["total_seconds"] for it in items)
        return {
            "hours_back": hours_back,
            "total_seconds": total_seconds,
            "items": items,
        }

    @mcp.tool()
    async def ask_agent(question: str, hours_back: int = 24, ctx: Context | None = None) -> dict:
        """Answer a natural-language question by retrieving + reasoning over
        recent activity.

        Pipeline (single LLM round-trip for low latency):
          1. Fetch recent activity (last ``hours_back`` hours, up to
             ``_ASK_AGENT_MAX_RECORDS`` records)
          2. Compact each record to one line
          3. Ask the local Qwen3-35BA3B to read the timeline + answer

        Args:
            question: natural-language question, e.g.
                "我昨天写了多久代码", "我今天在哪些代码仓库花了时间".
            hours_back: time window for context retrieval (default 24).

        Returns:
            dict with ``answer`` (str), ``records_consulted`` (int),
            and ``model`` (str) for transparency.
        """
        if chat_client is None or vlm_cfg is None:
            return {
                "answer": "LLM endpoint not configured (set TIMETRACE_VLM_* env vars).",
                "records_consulted": 0,
                "model": None,
            }
        hours_back = min(max(1, hours_back), 720)
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - hours_back * 3600 * 1000
        rows = await db.query_records(
            start_ms=start_ms,
            end_ms=now_ms,
            limit=_ASK_AGENT_MAX_RECORDS,
        )
        if not rows:
            return {
                "answer": f"过去 {hours_back} 小时没有活动记录。",
                "records_consulted": 0,
                "model": vlm_cfg.model,
            }
        timeline = "\n".join(_format_record_for_llm(r) for r in rows)
        system = (
            "你是 TimeTrace 的活动记录助手。基于下面的活动时间线，"
            "用中文简洁回答用户问题。如果数据不足以回答，明确指出。"
            "涉及时长统计时，将秒数换算为更易读的分钟或小时。"
        )
        user = (
            f"=== 活动时间线（最近 {hours_back} 小时，共 {len(rows)} 条）===\n"
            f"格式: 时间 | 应用 | 窗口标题 | 持续秒 | 画面描述\n\n"
            f"{timeline}\n\n"
            f"=== 用户问题 ===\n{question}"
        )
        extra: dict[str, Any] = {}
        if vlm_cfg.disable_thinking:
            extra["extra_body"] = {"enable_thinking": False}
        try:
            resp = await chat_client.chat.completions.create(
                model=vlm_cfg.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                timeout=_ASK_AGENT_TIMEOUT_S,
                **extra,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("ask_agent.llm_failed")
            return {
                "answer": f"调用 LLM 失败: {exc}",
                "records_consulted": len(rows),
                "model": vlm_cfg.model,
            }
        msg = resp.choices[0].message
        # LM Studio routes Qwen3+ output to reasoning_content; vanilla OpenAI
        # uses content. Same fallback as vlm/client.py::_extract_message_content.
        answer = getattr(msg, "content", None) or getattr(msg, "reasoning_content", None) or ""
        return {
            "answer": answer.strip(),
            "records_consulted": len(rows),
            "model": vlm_cfg.model,
        }

    return mcp


def mount_mcp(app: FastAPI, db: Database, vlm_cfg: VLMConfig | None) -> None:
    """Mount the MCP server on a FastAPI app at /mcp (streamable HTTP transport).

    External MCP clients (Claude Code / Desktop) connect by configuring this
    URL in their settings. Streamable HTTP is the modern MCP transport
    (replaces SSE) and works over plain HTTP + SSH tunnel.
    """
    mcp = build_mcp_server(db, vlm_cfg)
    # FastMCP exposes its ASGI app via streamable_http_app(); mount at /mcp.
    app.mount("/mcp", mcp.streamable_http_app())
    logger.info("mcp.mounted", path="/mcp")
