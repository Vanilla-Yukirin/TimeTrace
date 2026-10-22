"""TimeTrace MCP server — exposes activity context to external AI agents.

``build_mcp_server(db, vlm_cfg, embedding)`` returns a configured FastMCP instance;
``server/api/app.py::create_app`` mounts it at ``/mcp`` and drives its
session manager from the FastAPI lifespan (required even with
``stateless_http=True`` — see the comment inline). Same process, same port,
same SSH-tunnel as the REST API — no extra wiring for clients (Claude
Code / Desktop).

External agents see these tools:
  - ``search_activity`` — keyword + semantic retrieval (RRF), shared with the
    web search page and the web agent (``server/retrieval.py``)
  - ``get_recent_activity`` — recent-N-hours snapshot, no keyword
  - ``get_latest_activity`` — latest captured observation per device, not presence
  - ``get_activity_overview`` — per-device coarse-to-fine summary fallback + coverage
  - ``get_app_breakdown`` / ``get_category_stats`` — duration aggregates
  - ``search_summaries`` — the memory-pyramid narrative layer (per-window
    流水账/重点/评价), fold coarse→fine; the efficient "what did I do" path
  - ``list_devices`` — capture devices (id / name / owner description) for
    the ``device_id`` scope the read tools accept
  - ``apply_label`` — the only write tool (set a record's category)
  - ``ask_agent`` — the server-side agent (same tool loop as the web chat):
    summaries first, then stats / retrieval, then a synthesized answer

Time zone: callers may send ``X-Timezone`` (IANA); see ``server/tz.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from timetrace.server.agent import tools as agent_tools
from timetrace.server.agent.runner import AgentRunner

if TYPE_CHECKING:
    from timetrace.common.config import VLMConfig
    from timetrace.server.db import Database
    from timetrace.server.embedding.client import EmbeddingClient

logger = structlog.get_logger(__name__)

# ask_agent runs the shared tool loop; fewer turns than the web chat keeps an
# MCP call (which blocks the calling agent) bounded on the single shared GPU.
_ASK_AGENT_MAX_ITER = 4

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


def build_mcp_server(
    db: Database, vlm_cfg: VLMConfig | None, embedding: EmbeddingClient | None = None
) -> FastMCP:
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

    @mcp.tool()
    async def search_activity(
        query: str,
        limit: int = 20,
        hours_back: int | None = None,
        start_iso: str | None = None,
        end_iso: str | None = None,
        device_id: str | None = None,
    ) -> dict:
        """Find activity records by content — keyword AND meaning.

        Fuses a keyword channel (FTS5 trigram + BM25 over window title, app,
        process, URL and the AI description; LIKE for <3-char queries) with a
        semantic channel (text-embedding cosine, when an embedding endpoint is
        configured) via RRF — the same retrieval as the web search page. Also
        returns ``related_summaries``: narrated pyramid windows that mention the
        query, to drill into with ``search_summaries``.

        Args:
            query: keywords or a short description. Examples: "鸣潮",
                "judge replay history", "在看 SQLite 文档".
            limit: max records to return (default 20, cap 2000).
            hours_back: if set, limit to the last N hours. Otherwise all-time.
            start_iso/end_iso: absolute window ('YYYY-MM-DD HH:MM:SS' in the
                request's X-Timezone, else the server zone); wins over hours_back.
            device_id: optional device scope from ``list_devices`` (or
                ``_unknown_device``); omitted = all devices.

        Returns:
            dict with relevance-ranked ``items`` (each with ``matched_by``),
            ``semantic_channel`` status and ``related_summaries``.
        """
        return await agent_tools.search_activity(
            db,
            query,
            limit=limit,
            hours_back=hours_back,
            start_iso=start_iso,
            end_iso=end_iso,
            device_id=device_id,
            embedding=embedding,
        )

    @mcp.tool()
    async def get_recent_activity(
        hours_back: int = 24,
        limit: int = 50,
        start_iso: str | None = None,
        end_iso: str | None = None,
        cursor: str | None = None,
        device_id: str | None = None,
        order: str = "asc",
    ) -> dict:
        """Return a chronological snapshot of activity (oldest→newest in window).

        This is a PAGE, not the latest observation. Use get_latest_activity
        for latest per-device records; use get_activity_overview for summaries.
        A page's last timestamp does NOT mean capture stopped then. Follow
        next_cursor while has_more=true. Cursors freeze the original window;
        keep device_id and order unchanged. No data never proves user absence.

        Args:
            hours_back: time window in hours (default 24, cap 720 = 30 days).
            limit: max records per page (default 50, cap 2000).
            start_iso/end_iso: absolute local-time window
                ('YYYY-MM-DD HH:MM:SS'); takes precedence over hours_back.
            cursor: pagination cursor from a previous call's ``next_cursor``.
            order: asc (default, oldest first) or desc (newest first).

        Returns:
            dict with ``items``, ``count``, ``order``, fixed window bounds,
            ``has_more`` and opaque ``next_cursor`` (null iff no further page).
        """
        return await agent_tools.get_recent_activity(
            db,
            hours_back=hours_back,
            limit=limit,
            start_iso=start_iso,
            end_iso=end_iso,
            cursor=cursor,
            device_id=device_id,
            order=order,
        )

    @mcp.tool()
    async def get_latest_activity(device_id: str | None = None) -> dict:
        """Latest captured observation PER DEVICE, without paging or an LLM.

        Use for "what was I doing most recently?". Includes capture age,
        original server receipt time, last device contact, and analysis status.
        Recent upload may be replaying old captures. Missing/old records or
        empty descriptions NEVER prove the user is offline. presence=unknown.
        Omit device_id for all devices, including legacy unknown-device data.
        """
        return await agent_tools.get_latest_activity(db, device_id=device_id)

    @mcp.tool()
    async def get_activity_overview(
        period: str = "today",
        start_iso: str | None = None,
        end_iso: str | None = None,
        device_id: str | None = None,
        max_grain: str = "day",
        limit: int = 50,
    ) -> dict:
        """First choice for "what did I do today/this week?", PER DEVICE.

        Uses existing summaries only, no LLM. Selects disjoint closed, narrated
        windows coarse→fine (week/day/6h/1h/5min). Pending/open/missing parents
        fall back to ready children, retaining uncovered ranges and counts.
        No automatic global-summary fallback for missing historical device rows.
        Follow fallback_hint on unavailable/gaps: unfiltered requests may read
        global search_summaries as global context only; device-filtered requests
        must keep that filter and page through raw records instead.
        period=today/this_week/this_month/custom; named periods start at civil
        midnight in the caller's zone. custom needs both ISO bounds (<=31 days).
        max_grain defaults to day. limit is PER DEVICE (50 default, 2000 max).
        truncated=true: narrow the range. Uncovered is not inactive/offline.
        For detail, search_summaries at drill_down_grain with the item bounds.
        """
        return await agent_tools.get_activity_overview(
            db,
            period=period,
            start_iso=start_iso,
            end_iso=end_iso,
            device_id=device_id,
            max_grain=max_grain,
            limit=limit,
        )

    @mcp.tool()
    async def get_app_breakdown(
        hours_back: int = 24,
        top_n: int = 20,
        period: str | None = None,
        start_iso: str | None = None,
        end_iso: str | None = None,
        device_id: str | None = None,
    ) -> dict:
        """Aggregate captured active duration per app, descending.

        Per-record spans are clamped (sleep/lid artifacts contribute 0). Use for
        "how much time did I spend in X".

        Args:
            period: today / this_week / this_month / custom — civil periods in
                the request zone with a 4AM day cut. Wins over hours_back; use
                it for "today", not hours_back=24.
            start_iso/end_iso: bounds for period='custom'.
            hours_back: rolling window in hours when no period (default 24).
            top_n: max apps (default 20, cap 50).
        """
        return await agent_tools.get_app_breakdown(
            db,
            hours_back=hours_back,
            top_n=top_n,
            period=period,
            start_iso=start_iso,
            end_iso=end_iso,
            device_id=device_id,
        )

    @mcp.tool()
    async def get_category_stats(
        hours_back: int = 24,
        top_n: int = 20,
        period: str | None = None,
        start_iso: str | None = None,
        end_iso: str | None = None,
        device_id: str | None = None,
    ) -> dict:
        """Aggregate captured active duration per category (the AI-assigned
        label). ``_unclassified`` is a not-yet-analyzed backlog, distinct from
        the genuine ``uncategorized`` category.

        Args:
            period: today / this_week / this_month / custom (wins over hours_back).
            start_iso/end_iso: bounds for period='custom'.
            hours_back: rolling window in hours when no period (default 24).
            top_n: max categories (default 20, cap 50).
        """
        return await agent_tools.get_category_stats(
            db,
            hours_back=hours_back,
            top_n=top_n,
            period=period,
            start_iso=start_iso,
            end_iso=end_iso,
            device_id=device_id,
        )

    @mcp.tool()
    async def search_summaries(
        grain: str = "day",
        period: str = "today",
        start_iso: str | None = None,
        end_iso: str | None = None,
        query: str | None = None,
        limit: int = 50,
        device_id: str | None = None,
        window_match: str = "overlap",
    ) -> dict:
        """Read the narrative pyramid: per-window 流水账 / 重点 / 评价.

        Exact-grain lookup. For automatic fallback over incomplete/open parent
        windows use get_activity_overview. Check availability/window_closed and
        has_more: empty/pending/truncated results do not establish inactivity.

        Read pre-written narratives at an exact granularity, then fold/drill.
        Use get_activity_overview first for broad "what did I do" questions,
        particularly today when coarse windows are not finalized yet.

        Folding workflow:
          1. Overview: grain='day' (or 'week'), period='this_week'
          2. Pick an interesting window from the results
          3. Drill: call again with grain=<the returned drill_down_grain> and
             period='custom' + that window's window_start_iso / window_end_iso
          4. Repeat down to grain='5min' for frame-level detail

        Args:
            grain: 5min / 1h / 6h / day / week (overview: day or week).
            period: today / this_week / this_month / custom.
            start_iso/end_iso: custom-period bounds (local 'YYYY-MM-DD HH:MM:SS');
                when drilling, pass the parent window's start/end here.
            query: optional full-text filter over the narratives (relevance-ranked).
            device_id: read one device's own pyramid (from ``list_devices``);
                omitted = global view, composed as one section per device.
            limit: max windows (default 50, cap 2000).
            window_match: overlap (default) or contained (full window inside bounds).
                For overview fallback, preserve custom bounds and use contained;
                if empty, descend to finer grains with the SAME bounds. Never
                attribute an extends_outside_period narrative to this period.

        Returns:
            dict with ``items`` (each: description, key_points, evaluation,
            top_categories, window_start_iso/end_iso), ``drill_down_grain``, and a
            ``note``. Durations are a captured-activity FLOOR, not total time.
        """
        return await agent_tools.search_summaries(
            db,
            grain=grain,
            period=period,
            start_iso=start_iso,
            end_iso=end_iso,
            query=query,
            limit=limit,
            device_id=device_id,
            window_match=window_match,
        )

    @mcp.tool()
    async def query_stats(
        metric: str = "seconds_by_category",
        period: str = "today",
        start_iso: str | None = None,
        end_iso: str | None = None,
        filter_category: str | None = None,
        top_n: int = 20,
        device_id: str | None = None,
    ) -> dict:
        """Aggregate captured time/count over a named civil period.

        Reads a precomputed pyramid row when the period is exactly a closed
        day/week (all devices), otherwise aggregates live. ``active_seconds`` is
        the wall-clock union (parallel devices counted once);
        ``seconds_by_device`` splits time per device.

        Args:
            metric: seconds_by_category / seconds_by_app / seconds_by_device /
                active_seconds / record_count.
            period: today / this_week / this_month / custom (request zone, 4AM cut).
            start_iso/end_iso: bounds for period='custom' (end exclusive).
            filter_category: optional single category id.
            top_n: max items (default 20, cap 50).
            device_id: optional device scope from ``list_devices``.
        """
        return await agent_tools.query_stats(
            db,
            metric=metric,
            period=period,
            start_iso=start_iso,
            end_iso=end_iso,
            filter_category=filter_category,
            top_n=top_n,
            device_id=device_id,
        )

    @mcp.tool()
    async def list_devices() -> dict:
        """List the user's capture devices: ``device_id``, display name and the
        owner's description. Use it to map "办公电脑"/"家里那台" to a
        ``device_id`` for the other tools' ``device_id`` scope."""
        return await agent_tools.list_devices(db)

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
        """Answer a natural-language question with the server-side agent.

        Runs the same tool-calling loop as the web chat: it reads the summary
        pyramid first, pulls stats for named periods, and searches records by
        keyword + meaning before answering. Slower than one direct tool call
        (several LLM turns on the shared GPU), so prefer the specific tools when
        you know what to fetch.

        Args:
            question: natural-language question, e.g.
                "我昨天写了多久代码", "我今天在哪些代码仓库花了时间".
            hours_back: hint for the time range the question is about (default 24).

        Returns:
            dict with ``answer`` (str), ``records_consulted`` (int),
            ``tools_used`` and ``model`` for transparency.
        """
        if vlm_cfg is None:
            return {
                "answer": "LLM endpoint not configured (set TIMETRACE_VLM_* env vars).",
                "records_consulted": 0,
                "model": None,
            }
        hours_back = min(max(1, hours_back), 720)
        runner = AgentRunner(db, vlm_cfg, embedding=embedding, max_iterations=_ASK_AGENT_MAX_ITER)
        prompt = f"{question}\n\n（问题涉及的时间范围提示：大约最近 {hours_back} 小时）"
        parts: list[str] = []
        done: dict = {}
        try:
            async for ev in runner.run(
                [{"role": "user", "content": prompt}], hours_back_hint=hours_back
            ):
                if ev["type"] == "token":
                    parts.append(ev["text"])
                elif ev["type"] == "done":
                    done = ev
                elif ev["type"] == "error":
                    return {"answer": ev["message"], "records_consulted": 0, "model": vlm_cfg.model}
        finally:
            await runner.aclose()
        return {
            "answer": "".join(parts).strip(),
            "records_consulted": done.get("records_consulted", 0),
            "tools_used": done.get("tools_used", []),
            "model": vlm_cfg.model,
        }

    return mcp
