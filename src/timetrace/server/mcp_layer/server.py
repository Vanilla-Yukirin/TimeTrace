"""TimeTrace MCP server — exposes activity context to external AI agents.

``build_mcp_server(db, vlm_cfg)`` returns a configured FastMCP instance;
``server/api/app.py::create_app`` mounts it at ``/mcp`` and drives its
session manager from the FastAPI lifespan (required even with
``stateless_http=True`` — see the comment inline). Same process, same port,
same SSH-tunnel as the REST API — no extra wiring for clients (Claude
Code / Desktop).

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
from mcp.server.transport_security import TransportSecuritySettings
from openai import AsyncOpenAI

from timetrace.server.agent import tools as agent_tools

if TYPE_CHECKING:
    from timetrace.common.config import VLMConfig
    from timetrace.server.db import Database

logger = structlog.get_logger(__name__)

# Context budget for ask_agent. Assumes LM Studio loaded the model with
# context ≥16K (`lms load <model> -c 16384` or via GUI). Default 4096
# overflows easily; user is expected to bump on deploy.
# 80 records × ~160 chars ≈ 13 KB ≈ ~5K tokens, leaves ~10K for prompt/answer.
_ASK_AGENT_MAX_RECORDS = 80
_ASK_AGENT_RECORD_DESC_CHARS = 160
_ASK_AGENT_TIMEOUT_S = 180.0

# DNS-rebinding allowlist for the streamable-HTTP transport. FastMCP silently
# turns DNS-rebinding protection ON whenever no transport_security is passed and
# the (default) bind host is 127.0.0.1, and then only honors localhost Host
# headers (see fastmcp/server.py). Behind nginx the Host arrives as the public
# domain, so that implicit default 421s every public request — local/tunnel
# (Host=localhost) kept working, public domain never did. Keep protection ON but
# re-grant the public domain explicitly. The bearer gate is the real boundary;
# this is defense-in-depth, not the lock.
_MCP_ALLOWED_HOSTS = [
    "timetrace.yukirin.me",  # public 443 → Host carries no port
    "timetrace.yukirin.me:*",  # explicit port, just in case
    "127.0.0.1:*",
    "localhost:*",
    "[::1]:*",
]
# Claude Code / MCP machine clients send no Origin (non-browser), so this only
# matters for a browser-based MCP tool (e.g. Inspector); mirror the host grant.
_MCP_ALLOWED_ORIGINS = [
    "https://timetrace.yukirin.me",
    "http://127.0.0.1:*",
    "http://localhost:*",
]


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
    # stateless_http=True: each HTTP request is independent (no session id /
    # event store). Trade-off is no resumable streams, but for our use case
    # (Claude Code invokes a tool, gets a JSON result, done) statelessness
    # is simpler. session_manager.run() still has to be driven from FastAPI
    # lifespan even in stateless mode (anyio task group).
    # json_response=True: plain JSON instead of SSE-streaming — debuggable
    # from curl + matches single-shot tool-call clients.
    # streamable_http_path="/": the FastMCP default is "/mcp", which collides
    # with our FastAPI mount point and ends up serving at /mcp/mcp/. Putting
    # the streamable handler at the mount root means clients connect to /mcp/
    # directly.
    mcp: FastMCP = FastMCP(
        "timetrace",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_MCP_ALLOWED_HOSTS,
            allowed_origins=_MCP_ALLOWED_ORIGINS,
        ),
    )
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
        start_iso: str | None = None,
        end_iso: str | None = None,
    ) -> dict:
        """Keyword search over activity records.

        Searches across window title, app name, process name, URL, and VLM
        description. ≥3 char queries use FTS5 trigram + BM25 ranking
        (CJK-friendly); shorter queries fall back to multi-field LIKE.
        Multi-word queries (whitespace-separated) match records containing ALL
        the words, in any order.

        Args:
            query: search keyword(s). Examples: "鸣潮", "judge replay history",
                "Code.exe", "github.com".
            limit: max records to return (default 20, cap 2000).
            hours_back: if set, limit to the last N hours. Otherwise all-time.
            start_iso/end_iso: absolute local-time window
                ('YYYY-MM-DD HH:MM:SS'); takes precedence over hours_back.
                Results are relevance-ranked — narrow the window + raise limit
                to pull more (no cursor; use get_recent_activity to page a full
                day chronologically).

        Returns:
            dict with ``items``, ``query`` echoed back, and ``count``.
        """
        return await agent_tools.search_activity(
            db, query, limit=limit, hours_back=hours_back,
            start_iso=start_iso, end_iso=end_iso,
        )

    @mcp.tool()
    async def get_recent_activity(
        hours_back: int = 24,
        limit: int = 50,
        start_iso: str | None = None,
        end_iso: str | None = None,
        cursor: str | None = None,
    ) -> dict:
        """Return a chronological snapshot of activity (oldest→newest in window).

        No keyword filter — use for an overview before digging in. To pull a
        FULL day, keep the window fixed and re-call with the returned
        ``next_cursor`` as ``cursor`` until it comes back null.

        Args:
            hours_back: time window in hours (default 24, cap 720 = 30 days).
            limit: max records per page (default 50, cap 2000).
            start_iso/end_iso: absolute local-time window
                ('YYYY-MM-DD HH:MM:SS'); takes precedence over hours_back.
            cursor: pagination cursor from a previous call's ``next_cursor``.

        Returns:
            dict with ``items``, ``count``, and ``next_cursor`` (null when the
            page wasn't full = no more records).
        """
        return await agent_tools.get_recent_activity(
            db, hours_back=hours_back, limit=limit,
            start_iso=start_iso, end_iso=end_iso, cursor=cursor,
        )

    @mcp.tool()
    async def get_app_breakdown(hours_back: int = 24, top_n: int = 20) -> dict:
        """Aggregate active duration per app over the last N hours.

        Sums (ts_end - ts_start) for each app. Records still open (no ts_end)
        are included in the ``records`` count but contribute 0 to
        ``total_seconds`` (SQL uses ``COALESCE(ts_end, ts_start)``), so the
        time totals only reflect closed sessions. Use this for "how much
        time did I spend in X".

        Args:
            hours_back: time window (default 24, cap 720).
            top_n: max apps to return, sorted by duration desc (default 20).
        """
        return await agent_tools.get_app_breakdown(db, hours_back=hours_back, top_n=top_n)

    @mcp.tool()
    async def get_category_stats(hours_back: int = 24, top_n: int = 20) -> dict:
        """Aggregate active duration per category (the AI-assigned label) over the
        last N hours. Use for "how is my time split across kinds of activity".
        Records not yet classified bucket under ``uncategorized``.

        Args:
            hours_back: time window (default 24, cap 720).
            top_n: max categories, sorted by duration desc (default 20, cap 50).
        """
        return await agent_tools.get_category_stats(db, hours_back=hours_back, top_n=top_n)

    @mcp.tool()
    async def apply_label(record_id: str, category: str, note: str | None = None) -> dict:
        """Set/replace a record's category label — the ONLY write tool.

        Read everything, label only: this cannot delete or modify a record or
        its screenshots. ``category`` accepts a category id (``work``) or
        its display name (``工作``); ``record_id`` comes from
        ``search_activity`` / ``get_recent_activity``. Returns an ``error`` field
        (not an exception) on unknown record/category.
        """
        return await agent_tools.apply_label(db, record_id, category, note=note)

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
        window_note = ""
        if not rows:
            # No data in the requested window. Fall back to the all-time DESC
            # query so a demo against a stale DB still returns the *most
            # recent* N records, not the oldest 80 (which is what the default
            # ASC sort would give and was the whole bug we're avoiding).
            rows = await db.query_records(
                start_ms=0,
                end_ms=now_ms,
                limit=_ASK_AGENT_MAX_RECORDS,
                order="desc",
            )
            if not rows:
                return {
                    "answer": (
                        "数据库里还没有任何活动记录。先启动 timetrace-client 采集一段时间再试。"
                    ),
                    "records_consulted": 0,
                    "model": vlm_cfg.model,
                }
            # Rows are DESC by ts_start, so rows[0] is newest, rows[-1] is oldest.
            # actual_hours reports how far back our context now reaches.
            oldest_ts = min(r["ts_start"] for r in rows)
            actual_hours = max(1, int((now_ms - oldest_ts) / 3600_000))
            window_note = (
                f"（请求窗口 {hours_back}h 内无数据，已自动扩窗到 ~{actual_hours}h 找到 "
                f"{len(rows)} 条历史记录用于回答）"
            )
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
        final_answer = answer.strip()
        if window_note:
            final_answer = f"{window_note}\n\n{final_answer}"
        return {
            "answer": final_answer,
            "records_consulted": len(rows),
            "model": vlm_cfg.model,
        }

    return mcp
