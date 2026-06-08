"""SQLite database initialisation and low-level data access.

aiosqlite uses a single shared connection for all coroutines. Concurrent access
from capture + multiple worker consumers + the API would interleave cursors
and `commit()` calls on that one connection, producing
``OperationalError: cannot commit transaction - SQL statements in progress``.

To prevent this we serialize every public method behind ``self._lock``. Reads
become sequential too, but at TimeTrace's volume (a few writes/sec, infrequent
queries) the throughput cost is negligible compared to the alternative of
multi-connection plumbing.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import aiosqlite
import structlog

from timetrace.common.models import CaptureContext

logger = structlog.get_logger(__name__)

_MAX_ORPHAN_BRIDGE_MS = 5 * 60 * 1000

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS records (
    id               TEXT PRIMARY KEY,
    client_record_id TEXT,
    ts_start         INTEGER NOT NULL,
    ts_end           INTEGER,
    event_type       TEXT NOT NULL DEFAULT 'heartbeat',
    app_name         TEXT NOT NULL DEFAULT '',
    process_name     TEXT NOT NULL DEFAULT '',
    window_title     TEXT NOT NULL DEFAULT '',
    url              TEXT,
    capture_reason   TEXT,
    status           TEXT NOT NULL DEFAULT 'captured',
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_ts_start  ON records(ts_start);
CREATE INDEX IF NOT EXISTS idx_records_app_ts    ON records(app_name, ts_start);
CREATE INDEX IF NOT EXISTS idx_records_status    ON records(status)
    WHERE status LIKE 'pending_%';
-- Partial unique index: enforces idempotency when client_record_id is supplied
-- but lets pre-P3a rows (NULL) coexist without conflicting with each other.
CREATE UNIQUE INDEX IF NOT EXISTS idx_records_client_record_id
    ON records(client_record_id) WHERE client_record_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS screenshots (
    id          TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL REFERENCES records(id),
    path        TEXT NOT NULL,
    thumb_path  TEXT,
    width       INTEGER,
    height      INTEGER,
    hash_sha256 TEXT,
    phash       BLOB,
    deleted_at  INTEGER,
    privacy_level TEXT NOT NULL DEFAULT 'normal',
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_screenshots_record  ON screenshots(record_id);
-- Partial UNIQUE: at most one row per (record_id, hash_sha256). Lets the ingest
-- route absorb outbox at-least-once replays without inserting a duplicate
-- screenshot row + double-feeding the pHash index. Pre-P3a rows with NULL
-- hash_sha256 are excluded so the migration is non-breaking.
CREATE UNIQUE INDEX IF NOT EXISTS idx_screenshots_record_hash
    ON screenshots(record_id, hash_sha256) WHERE hash_sha256 IS NOT NULL;

CREATE TABLE IF NOT EXISTS analysis_results (
    record_id           TEXT PRIMARY KEY REFERENCES records(id),
    vlm_desc            TEXT,
    vlm_model           TEXT,
    vlm_latency_ms      INTEGER,
    category_suggested  TEXT,
    category_final      TEXT,
    confidence          REAL,
    decision_trace      TEXT,
    error_code           TEXT,
    error_msg           TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    next_retry_at       INTEGER,
    status              TEXT NOT NULL DEFAULT 'pending_vlm',
    locked_at           INTEGER,
    updated_at          INTEGER NOT NULL,
    -- Text embedding of vlm_desc (packed float32 bytes). NULL until the
    -- worker's embedding stage fills it; backfill script can sweep older
    -- vlm_done rows. Dimensionality set by EmbeddingConfig.dim (768 for
    -- nomic-embed-v1.5); search-side numpy decode assumes float32.
    text_embedding      BLOB,
    text_embedding_model TEXT,
    -- Pipeline stage timestamps (epoch ms, nullable). queued_at = when (re)
    -- enqueued to pending_vlm; done_at = when transitioned to vlm_done. NULL on
    -- rows created before this migration (the moments are gone, no backfill) →
    -- the audit feed shows "—". Drive queue-wait / total-latency; reused by the
    -- memory-pyramid rollup later.
    queued_at           INTEGER,
    done_at             INTEGER
);

CREATE INDEX IF NOT EXISTS idx_analysis_status ON analysis_results(status);

CREATE TABLE IF NOT EXISTS categories (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    parent_id   TEXT REFERENCES categories(id),
    description TEXT,
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    is_hidden   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tags (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS record_tags (
    record_id   TEXT NOT NULL REFERENCES records(id),
    tag_id      TEXT NOT NULL REFERENCES tags(id),
    source      TEXT,
    confidence  REAL,
    PRIMARY KEY (record_id, tag_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    id              TEXT PRIMARY KEY,
    record_id       TEXT NOT NULL REFERENCES records(id),
    action          TEXT NOT NULL,
    category_before TEXT,
    category_after  TEXT,
    tags_before     TEXT,
    tags_after      TEXT,
    user_note       TEXT,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);

-- AI-generated insight dashboards ("看板"). One row per generation; the latest
-- per scope is what the UI shows. content is an HTML fragment authored by the
-- agent from real activity data. We keep history (no UPDATE) so regenerations
-- don't lose prior reports and a future view can diff them.
CREATE TABLE IF NOT EXISTS reports (
    id            TEXT PRIMARY KEY,
    scope         TEXT NOT NULL,
    period_start  INTEGER NOT NULL,
    period_end    INTEGER NOT NULL,
    format        TEXT NOT NULL DEFAULT 'html',
    content       TEXT NOT NULL,
    model         TEXT,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_scope_created ON reports(scope, created_at DESC);

-- Full-text search across record metadata + VLM description.
-- trigram tokenizer is CJK-friendly (no whitespace tokenization needed).
-- record_id is UNINDEXED — stored for JOIN but not searchable.
-- We populate this manually from insert_record + save_description, with a
-- migration backfill on cold start. No triggers (debug-friendlier).
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
    record_id UNINDEXED,
    window_title,
    app_name,
    process_name,
    url,
    vlm_desc,
    tokenize='trigram'
);

-- Login-system: admin user + browser sessions. Bearer tokens for
-- machine-to-machine live separately in ``~/.config/timetrace-server/tokens.json``
-- via ``ServerAuth`` (server/auth.py). Single-user by design — no ``role``
-- column, no ``user_id`` foreign keys elsewhere. See devlogs/infra/
-- archive-202605280400-login-system-design.md for the design rationale.
CREATE TABLE IF NOT EXISTS auth_users (
    username             TEXT PRIMARY KEY,
    password_hash        TEXT NOT NULL,
    password_must_change INTEGER NOT NULL DEFAULT 0,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL REFERENCES auth_users(username),
    created_at    INTEGER NOT NULL,
    last_seen_at  INTEGER NOT NULL,
    expires_at    INTEGER NOT NULL,
    user_agent    TEXT,
    ip            TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_username ON auth_sessions(username);
CREATE INDEX IF NOT EXISTS idx_sessions_expires  ON auth_sessions(expires_at);

-- Memory pyramid: time-window summary cascade (5min → 1h → 6h → day → week).
-- One self-similar table; `grain` distinguishes the level. Each non-empty
-- window is one row; higher grains are summary-of-summaries of the grain below.
-- See infra/storage/pyramid-schema.md + infra/architecture/episode-and-rollup-
-- pipeline.md. `metrics_json` is pure-SQL (no LLM). description/evaluation/
-- body_json/embedding are filled by the narrative stage; compression_ratio +
-- drill_down_hint are the precomputed "information scent" guiding drill-down;
-- source_hash + source_version drive re-emission (later slices). Window bounds
-- are derivable from records.ts_start, so NO foreign key on records is needed.
CREATE TABLE IF NOT EXISTS summaries (
    id                      TEXT PRIMARY KEY,
    grain                   TEXT NOT NULL,      -- '5min'|'1h'|'6h'|'day'|'week'
    scope_key               TEXT NOT NULL,      -- deterministic window key (idempotency)
    window_start            INTEGER NOT NULL,   -- epoch-ms, business clock (ts_start grid)
    window_end              INTEGER NOT NULL,
    day_local               TEXT NOT NULL,      -- 'YYYY-MM-DD' under the 4AM cut
    description             TEXT,               -- LLM narrative (narrative stage)
    evaluation              TEXT,               -- LLM appraisal (narrative stage)
    body_json               TEXT,               -- structured lists (raw_table/key_events/cues/...)
    metrics_json            TEXT,               -- pure-SQL aggregates, ms (summary/metrics.py)
    record_count            INTEGER NOT NULL DEFAULT 0,
    src_tokens              INTEGER,            -- information scent (narrative stage)
    out_tokens              INTEGER,
    compression_ratio       REAL,
    drill_down_hint         TEXT,
    redacted                INTEGER NOT NULL DEFAULT 0,  -- 1 once narrative is desensitized
    summary_embedding       BLOB,
    summary_embedding_model TEXT,
    status                  TEXT NOT NULL DEFAULT 'pending_summary',  -- queue state
    locked_at               INTEGER,            -- claim lease (narrative queue)
    source_version          INTEGER NOT NULL DEFAULT 0,  -- explicit invalidation counter
    source_hash             TEXT,               -- input fingerprint for re-emission
    computed_through_ts     INTEGER,            -- watermark (partial-coverage builds)
    created_at              INTEGER NOT NULL,
    updated_at              INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_summaries_grain_scope ON summaries(grain, scope_key);
CREATE INDEX IF NOT EXISTS idx_summaries_grain_ts ON summaries(grain, window_start);
CREATE INDEX IF NOT EXISTS idx_summaries_day ON summaries(day_local, grain);
CREATE INDEX IF NOT EXISTS idx_summaries_status ON summaries(status) WHERE status LIKE 'pending_%';

-- Keyword search over summary narratives (mirrors records_fts shape). Populated
-- by the narrative stage; empty until then.
CREATE VIRTUAL TABLE IF NOT EXISTS summaries_fts USING fts5(
    summary_id UNINDEXED, grain UNINDEXED, description, evaluation, tokenize='trigram'
);
"""

# Minimum keyword length where trigram FTS5 can match. Below this, we fall back
# to multi-field LIKE so 2-char queries like "VS" / "鸣潮" still hit.
_FTS_MIN_LEN = 3

# Flat, intent-based 6-category taxonomy. Kept deliberately small + mutually
# exclusive so the VLM can pick exactly one reliably. The id is the stored
# value (category_final / VLM enum); the name is the Chinese display label.
# NOTE: the same id set is mirrored as the VLM output enum in
# server/vlm/client.py (_CATEGORY_IDS) — keep the two in sync.
_BUILTIN_CATEGORIES = [
    ("work", "工作", None),
    ("study", "学习", None),
    ("social", "沟通", None),
    ("entertainment", "娱乐", None),
    ("system", "系统", None),
    ("uncategorized", "未分类", None),
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return str(uuid.uuid4())


def _fts_query(keyword: str) -> str:
    """Build a safe FTS5 MATCH expression from a user keyword.

    FTS5 treats double-quoted strings as literal phrases, so wrapping the
    whole keyword (after escaping internal ``"`` to ``""``) sidesteps the
    "syntax error near ..." class of bug for inputs containing FTS5
    operators (``AND``/``OR``/``NEAR``/``-``/``:``/``.``/etc.) or punctuation.

    The result is a single phrase, so for trigram tokenizer this means
    "match any contiguous substring matching this string" — which is what
    casual users expect (vs. boolean keyword AND).
    """
    return '"' + keyword.replace('"', '""') + '"'


def _processing_to_pending(status: str) -> str:
    """Map an in-flight worker status back to its pending queue.

    Accepts both the current ``processing_<base>`` form and the legacy
    ``processing_pending_<base>`` form so historic rows from earlier builds
    don't get permanently orphaned.
    """
    if status.startswith("processing_pending_"):
        return "pending_" + status[len("processing_pending_") :]
    if status.startswith("processing_"):
        return "pending_" + status[len("processing_") :]
    return status


class SqliteDatabase:
    """SQLite-backed implementation of the Database surface.

    Today this is the only Database implementation; PostgresDatabase lands at P5.
    Until then `timetrace.server.db.Database` is exported as an alias for this
    class so type annotations and construction can continue to share a name.
    """

    def __init__(self, storage_cfg: Any) -> None:
        self._cfg = storage_cfg
        self._conn: aiosqlite.Connection | None = None
        # Serializes access to the shared aiosqlite connection (single-thread
        # SQLite worker) so concurrent cursors + commits don't trample each other.
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        async with self._lock:
            self._cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self._cfg.db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.executescript(_SCHEMA)
            await self._conn.commit()
            await self._migrate()
            healed = await self._close_open_records_before(_now_ms(), tail_ts=None)
            if healed:
                logger.info("database.heal_open_records_on_start", count=healed)
            capped = await self._cap_implausible_record_durations(_now_ms())
            if capped:
                logger.info("database.cap_implausible_record_durations", count=capped)
            # _seed_categories' commit also flushes the heal UPDATE above.
            await self._seed_categories()
            await self._seed_schema_version()
            logger.info("database.init", path=str(self._cfg.db_path))

    async def _migrate(self) -> None:
        """Idempotent schema migrations for databases created by older versions.

        Caller must hold ``self._lock``.
        """
        async with self._conn.execute("PRAGMA table_info(screenshots)") as cur:
            cols = {row["name"] for row in await cur.fetchall()}
        if "phash" not in cols:
            await self._conn.execute("ALTER TABLE screenshots ADD COLUMN phash BLOB")
            await self._conn.commit()
            logger.info("database.migrate", added_column="screenshots.phash")

        async with self._conn.execute("PRAGMA table_info(records)") as cur:
            record_cols = {row["name"] for row in await cur.fetchall()}
        if "client_record_id" not in record_cols:
            await self._conn.execute("ALTER TABLE records ADD COLUMN client_record_id TEXT")
            await self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_records_client_record_id "
                "ON records(client_record_id) WHERE client_record_id IS NOT NULL"
            )
            await self._conn.commit()
            logger.info("database.migrate", added_column="records.client_record_id")

        # Embedding columns on analysis_results — added in embedding-pipeline
        # Phase 1. Existing rows get NULL until backfill (Phase 2) sweeps.
        async with self._conn.execute("PRAGMA table_info(analysis_results)") as cur:
            ar_cols = {row["name"] for row in await cur.fetchall()}
        if "text_embedding" not in ar_cols:
            await self._conn.execute("ALTER TABLE analysis_results ADD COLUMN text_embedding BLOB")
            await self._conn.commit()
            logger.info("database.migrate", added_column="analysis_results.text_embedding")
        if "text_embedding_model" not in ar_cols:
            await self._conn.execute(
                "ALTER TABLE analysis_results ADD COLUMN text_embedding_model TEXT"
            )
            await self._conn.commit()
            logger.info(
                "database.migrate",
                added_column="analysis_results.text_embedding_model",
            )
        # Pipeline stage timestamps — added for the audit feed (queue-wait /
        # total-latency) + reused by the memory-pyramid rollup. Existing rows
        # stay NULL (the moments already passed); only new records get values.
        if "queued_at" not in ar_cols:
            await self._conn.execute("ALTER TABLE analysis_results ADD COLUMN queued_at INTEGER")
            await self._conn.commit()
            logger.info("database.migrate", added_column="analysis_results.queued_at")
        if "done_at" not in ar_cols:
            await self._conn.execute("ALTER TABLE analysis_results ADD COLUMN done_at INTEGER")
            await self._conn.commit()
            logger.info("database.migrate", added_column="analysis_results.done_at")

        # Backfill the (record_id, hash_sha256) UNIQUE for DBs created before
        # the dedup work in P3a-cleanup. Idempotent; the index uses IF NOT EXISTS.
        await self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_screenshots_record_hash "
            "ON screenshots(record_id, hash_sha256) WHERE hash_sha256 IS NOT NULL"
        )
        await self._conn.commit()

        # Backfill records_fts for DBs created before the FTS column landed.
        # The VIRTUAL TABLE itself is created by _SCHEMA (idempotent with
        # IF NOT EXISTS); we only need to populate it if it's empty AND the
        # records table already has data. Subsequent inserts/updates keep it
        # in sync via insert_record + save_description.
        async with self._conn.execute("SELECT COUNT(*) FROM records_fts") as cur:
            fts_count = (await cur.fetchone())[0]
        async with self._conn.execute("SELECT COUNT(*) FROM records") as cur:
            record_count = (await cur.fetchone())[0]
        if fts_count == 0 and record_count > 0:
            await self._conn.execute(
                """INSERT INTO records_fts
                       (record_id, window_title, app_name, process_name, url, vlm_desc)
                   SELECT r.id,
                          COALESCE(r.window_title, ''),
                          COALESCE(r.app_name, ''),
                          COALESCE(r.process_name, ''),
                          COALESCE(r.url, ''),
                          COALESCE(a.vlm_desc, '')
                   FROM records r
                   LEFT JOIN analysis_results a ON a.record_id = r.id"""
            )
            await self._conn.commit()
            logger.info("database.migrate.fts_backfill", rows=record_count)

    async def _seed_categories(self) -> None:
        """Insert built-in categories if they don't exist yet.

        Caller must hold ``self._lock``.
        """
        for cat_id, name, parent_id in _BUILTIN_CATEGORIES:
            await self._conn.execute(
                """INSERT OR IGNORE INTO categories (id, name, parent_id, is_builtin)
                   VALUES (?, ?, ?, 1)""",
                (cat_id, name, parent_id),
            )
        await self._conn.commit()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not initialised"
        return self._conn

    @property
    def lock(self) -> asyncio.Lock:
        """Public connection lock for callers that bypass Database methods.

        External code that touches ``db.conn`` directly (route helpers, the
        pHash loader, tests that prep state) must wrap their access in
        ``async with db.lock:`` so it cannot interleave with concurrent
        commits issued from other coroutines.
        """
        return self._lock

    # ------------------------------------------------------------------ #
    # Records                                                              #
    # ------------------------------------------------------------------ #

    async def insert_record(
        self,
        ctx: CaptureContext,
        reason: str,
        event_type: str = "heartbeat",
        *,
        client_record_id: str | None = None,
        ts_start: int | None = None,
    ) -> str:
        record_id = _new_id()
        now = _now_ms()
        # ts_start is the *business* time (when the activity happened on the
        # client). In single-process capture it equals now; the two-process
        # ingest path passes the client's capture clock so a backed-up outbox
        # drain doesn't restamp records with the (much later) server-receive
        # time — which previously made ts_end < ts_start for every replayed
        # record. created_at/updated_at stay on the server clock (bookkeeping).
        ts_start_value = ts_start if ts_start is not None else now
        async with self._lock:
            healed = await self._close_open_records_before(now, tail_ts=now)
            if healed:
                logger.info("database.heal_open_records_on_insert", count=healed)
                # Commit the heal independently of the INSERT below so that a
                # client_record_id UNIQUE violation doesn't roll back the heal
                # work and leave a long-open transaction holding it pending.
                await self.conn.commit()
            try:
                await self.conn.execute(
                    """INSERT INTO records
                       (id, client_record_id, ts_start, event_type, app_name,
                        process_name, window_title, url, capture_reason,
                        status, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record_id,
                        client_record_id,
                        ts_start_value,
                        event_type,
                        ctx.app_name,
                        ctx.process_name,
                        ctx.window_title,
                        ctx.url,
                        reason,
                        "captured",
                        now,
                        now,
                    ),
                )
                # Mirror into records_fts. vlm_desc is filled by save_description
                # later (worker pipeline); start empty.
                await self.conn.execute(
                    """INSERT INTO records_fts
                       (record_id, window_title, app_name, process_name, url, vlm_desc)
                       VALUES (?, ?, ?, ?, ?, '')""",
                    (
                        record_id,
                        ctx.window_title or "",
                        ctx.app_name or "",
                        ctx.process_name or "",
                        ctx.url or "",
                    ),
                )
                await self.conn.commit()
            except aiosqlite.IntegrityError:
                # Drop the failed INSERT's implicit transaction so the next
                # caller starts clean. (Without this the lock acquired by the
                # next method would re-enter an already-open transaction.)
                await self.conn.rollback()
                raise
        return record_id

    async def find_record_by_client_id(self, client_record_id: str) -> dict | None:
        """Return the record carrying the given client-generated id, if any."""
        async with self._lock:
            async with self.conn.execute(
                "SELECT * FROM records WHERE client_record_id = ?",
                (client_record_id,),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def ingest_or_get_record(
        self,
        ctx: CaptureContext,
        reason: str,
        event_type: str,
        client_record_id: str,
        ts_start: int | None = None,
    ) -> tuple[str, bool]:
        """Insert a record with the given client_record_id, or return the
        existing one if a row already carries this id.

        Returns ``(record_id, was_new)``. Uses the ``client_record_id``
        UNIQUE INDEX as the atomic check — a parallel ingest losing the race
        catches the IntegrityError and re-reads the winner.

        ``ts_start`` is the client's capture clock (the wire contract says the
        server does not rewrite it); passed through so replayed outbox entries
        keep their real activity time instead of the server-receive time.
        """
        try:
            rid = await self.insert_record(
                ctx,
                reason=reason,
                event_type=event_type,
                client_record_id=client_record_id,
                ts_start=ts_start,
            )
            return rid, True
        except aiosqlite.IntegrityError:
            existing = await self.find_record_by_client_id(client_record_id)
            if existing is None:
                # Should be impossible: UNIQUE violation but row missing on re-read.
                # Re-raise so the caller doesn't silently corrupt state.
                raise
            return existing["id"], False

    async def close_record(self, record_id: str, ts_end: int | None = None) -> bool:
        """Set ts_end on a record (e.g. when window switches away).

        When `ts_end` is None the server clock is used; HttpBackend passes the
        client's clock so the time the user actually stopped using the window
        is honoured rather than the time the server happened to receive the
        close call.

        Returns True iff a row was updated. Routes that need to surface a 404
        for an unknown id check this — silently returning False used to let
        a stale HttpBackend close calls go nowhere with no signal.
        """
        now = _now_ms()
        ts_end_value = ts_end if ts_end is not None else now
        async with self._lock:
            cur = await self.conn.execute(
                "UPDATE records SET ts_end=?, updated_at=? WHERE id=?",
                (ts_end_value, now, record_id),
            )
            rowcount = cur.rowcount
            await cur.close()
            await self.conn.commit()
        return rowcount > 0

    async def _close_open_records_before(
        self,
        ts_cutoff: int,
        *,
        tail_ts: int | None,
    ) -> int:
        """Close any orphaned `records.ts_end IS NULL` rows older than `ts_cutoff`.

        For each open row, ts_end is set to the next plausible boundary:
          1. MIN(next.ts_start), if it is within `_MAX_ORPHAN_BRIDGE_MS`.
          2. Otherwise the `tail_ts` fallback, if it is within that same
             threshold.
          3. Otherwise the row's own ts_start, giving zero-duration. Old
             crash-orphans then render as points rather than day-spanning bars.

        Deliberately does NOT use ``records.updated_at`` as a fallback: the
        worker bumps it on transition / save_description, so an old orphan
        whose VLM completes long after the original capture would get an
        incorrectly-late ts_end — exactly the cross-day-bar bug we're fixing.

        Caller must hold ``self._lock`` AND is responsible for committing.
        Returns the number of rows updated.
        """
        now = _now_ms()
        if tail_ts is None:
            sql = """UPDATE records
                     SET ts_end = COALESCE(
                             (SELECT MIN(n.ts_start) FROM records n
                              WHERE n.ts_start > records.ts_start
                                AND n.ts_start - records.ts_start <= ?),
                             records.ts_start
                         ),
                         updated_at = ?
                     WHERE ts_end IS NULL AND ts_start < ?"""
            params: tuple = (_MAX_ORPHAN_BRIDGE_MS, now, ts_cutoff)
        else:
            sql = """UPDATE records
                     SET ts_end = COALESCE(
                             (SELECT MIN(n.ts_start) FROM records n
                              WHERE n.ts_start > records.ts_start
                                AND n.ts_start - records.ts_start <= ?),
                             CASE
                                 WHEN ? - records.ts_start <= ? THEN ?
                                 ELSE records.ts_start
                             END
                         ),
                         updated_at = ?
                     WHERE ts_end IS NULL AND ts_start < ?"""
            params = (
                _MAX_ORPHAN_BRIDGE_MS,
                tail_ts,
                _MAX_ORPHAN_BRIDGE_MS,
                tail_ts,
                now,
                ts_cutoff,
            )

        cur = await self.conn.execute(sql, params)
        rowcount = cur.rowcount
        await cur.close()
        return rowcount

    async def _cap_implausible_record_durations(self, now: int) -> int:
        """Collapse impossible record spans left by older orphan-heal logic.

        Capture normally emits at least one heartbeat every 30 seconds while
        active, and idle transitions close the current record after 180 seconds.
        A single record spanning more than `_MAX_ORPHAN_BRIDGE_MS` is therefore
        a stale boundary artifact, not a trustworthy activity duration.

        Caller must hold ``self._lock`` AND is responsible for committing.
        Returns the number of rows updated.
        """
        cur = await self.conn.execute(
            """UPDATE records
               SET ts_end = ts_start,
                   updated_at = ?
               WHERE ts_end IS NOT NULL
                 AND ts_end - ts_start > ?""",
            (now, _MAX_ORPHAN_BRIDGE_MS),
        )
        rowcount = cur.rowcount
        await cur.close()
        return rowcount

    async def mark_pending(self, record_id: str) -> None:
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """INSERT OR IGNORE INTO analysis_results
                   (record_id, status, updated_at, queued_at)
                   VALUES (?, 'pending_vlm', ?, ?)""",
                (record_id, now, now),
            )
            await self.conn.execute(
                "UPDATE records SET status='pending_vlm', updated_at=?"
                " WHERE id=? AND status='captured'",
                (now, record_id),
            )
            await self.conn.commit()

    async def requeue_skipped_for_vlm(self, record_id: str) -> bool:
        """Re-enqueue a record short-circuited to ``vlm_done`` with no image.

        Ingest delivers a record's metadata first and its screenshot seconds
        later as a separate call (HttpBackend's record-then-screenshot pattern
        + the 1.5s capture delay + outbox/network lag → a 2–30s gap). The
        1s-poll worker routinely claims the record before the screenshot lands,
        finds ``screenshot_path`` empty, and parks it in ``vlm_done`` with no
        description (``worker.vlm_skipped_no_image``). When the screenshot
        finally arrives we flip that exact terminal state back to ``pending_vlm``
        so the worker re-describes + classifies it WITH the image.

        Gated on ``vlm_desc IS NULL``: the success path always writes
        ``vlm_desc`` before ``vlm_done``, so only the no-image skip leaves
        ``vlm_done`` with a NULL description — a genuinely-described record is
        never disturbed. Idempotent (no-op once re-enqueued / already described).
        Returns True iff a row was re-enqueued.
        """
        now = _now_ms()
        async with self._lock:
            async with self.conn.execute(
                """UPDATE analysis_results
                   SET status='pending_vlm', locked_at=NULL, next_retry_at=NULL,
                       queued_at=?, updated_at=?
                   WHERE record_id=? AND status='vlm_done' AND vlm_desc IS NULL""",
                (now, now, record_id),
            ) as cur:
                changed = cur.rowcount > 0
            if changed:
                await self.conn.execute(
                    "UPDATE records SET status='pending_vlm', updated_at=?"
                    " WHERE id=? AND status='vlm_done'",
                    (now, record_id),
                )
            await self.conn.commit()
        return changed

    async def query_records(
        self,
        start_ms: int,
        end_ms: int,
        limit: int = 200,
        cursor: str | None = None,
        app_name: str | None = None,
        apps: list[str] | None = None,
        categories: list[str] | None = None,
        keyword: str | None = None,
        order: str = "asc",
    ) -> list[dict]:
        """Query records over a time window with optional filters.

        ``order`` controls ts_start direction. Behavior depends on keyword:
        - no keyword / short-keyword LIKE: pure ts_start sort in given direction
        - FTS5 keyword (≥3 chars): primary key is BM25 relevance (most-relevant
          first, non-negotiable — keyword search is about relevance). ``order``
          becomes the secondary sort: same-score rows are broken by ts_start
          in the requested direction. Default 'asc' preserves timeline-view
          semantics that the frontend depends on; callers wanting "most recent
          N" pass 'desc'.
        """
        order_dir = "DESC" if order.lower() == "desc" else "ASC"

        conditions = ["r.ts_start BETWEEN ? AND ?"]
        where_params: list[Any] = [start_ms, end_ms]

        if cursor is not None:
            conditions.append("r.ts_start > (SELECT ts_start FROM records WHERE id=?)")
            where_params.append(cursor)

        if app_name:
            conditions.append("r.app_name = ?")
            where_params.append(app_name)

        if apps:
            placeholders = ",".join("?" * len(apps))
            conditions.append(f"r.app_name IN ({placeholders})")
            where_params.extend(apps)

        if categories:
            placeholders = ",".join("?" * len(categories))
            conditions.append(f"a.category_final IN ({placeholders})")
            where_params.extend(categories)

        # Keyword strategy: trigram-FTS5 MATCH for ≥3-char queries (CJK-friendly,
        # BM25-ranked), multi-field LIKE for <3 chars (so "VS" / "鸣潮" still
        # work — trigram requires 3+ chars to match). Both routes search the
        # same 5 fields: window_title, app_name, process_name, url, vlm_desc.
        use_fts = False
        fts_match: str | None = None
        if keyword:
            kw = keyword.strip()
            if len(kw) >= _FTS_MIN_LEN:
                use_fts = True
                # Quote keyword as a phrase so FTS5 doesn't choke on punctuation
                # or treat AND/OR/NEAR as operators.
                fts_match = _fts_query(kw)
            else:
                # Short keyword: multi-field LIKE so 1-2 char queries still hit.
                conditions.append(
                    "(r.window_title LIKE ? ESCAPE '\\' "
                    "OR r.app_name LIKE ? ESCAPE '\\' "
                    "OR r.process_name LIKE ? ESCAPE '\\' "
                    "OR r.url LIKE ? ESCAPE '\\' "
                    "OR a.vlm_desc LIKE ? ESCAPE '\\')"
                )
                escaped = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                like = f"%{escaped}%"
                where_params.extend([like, like, like, like, like])

        where = " AND ".join(conditions)

        # BM25 path uses a CTE so bm25() is called in the same query level
        # as the FTS5 MATCH (FTS5 aux functions only work in that context;
        # the old subquery-without-MATCH form silently returned a constant
        # and the supposed "BM25 ranking" was actually insertion order).
        # The CTE is also INNER-JOINed back to records, which doubles as the
        # FTS filter (replaces the old `r.id IN (...)` form).
        if use_fts:
            sql = """
                WITH fts_hits AS (
                    SELECT record_id, bm25(records_fts) AS rank_score
                    FROM records_fts WHERE records_fts MATCH ?
                )
                SELECT r.*, a.vlm_desc, a.category_final, a.confidence,
                       s.thumb_path, s.image_path,
                       (SELECT COUNT(*) FROM screenshots
                        WHERE record_id = r.id AND deleted_at IS NULL
                       ) AS screenshot_count
                FROM records r
                JOIN fts_hits h ON h.record_id = r.id
                LEFT JOIN analysis_results a ON a.record_id = r.id
                LEFT JOIN (
                    SELECT record_id, MIN(thumb_path) AS thumb_path,
                           MIN(path) AS image_path
                    FROM screenshots
                    WHERE deleted_at IS NULL
                    GROUP BY record_id
                ) s ON s.record_id = r.id
                WHERE {where}
                ORDER BY h.rank_score ASC, r.ts_start {direction}
                LIMIT ?
            """.format(where=where, direction=order_dir)
            params: list[Any] = [fts_match, *where_params, limit]
        else:
            sql = """
                SELECT r.*, a.vlm_desc, a.category_final, a.confidence,
                       s.thumb_path, s.image_path,
                       (SELECT COUNT(*) FROM screenshots
                        WHERE record_id = r.id AND deleted_at IS NULL
                       ) AS screenshot_count
                FROM records r
                LEFT JOIN analysis_results a ON a.record_id = r.id
                LEFT JOIN (
                    SELECT record_id, MIN(thumb_path) AS thumb_path,
                           MIN(path) AS image_path
                    FROM screenshots
                    WHERE deleted_at IS NULL
                    GROUP BY record_id
                ) s ON s.record_id = r.id
                WHERE {where}
                ORDER BY r.ts_start {direction}
                LIMIT ?
            """.format(where=where, direction=order_dir)
            params = [*where_params, limit]

        async with self._lock:
            async with self.conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def query_audit_records(
        self,
        start_ms: int = 0,
        end_ms: int = 9_999_999_999_999,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[dict]:
        """Audit-log feed: newest-first records joined with their analysis row.

        Distinct from :meth:`query_records` (timeline view) on purpose:
        - DESC by ``(ts_start, id)`` so the newest capture is first and ties are
          stable (``query_records``' ASC cursor ``r.ts_start > (...)`` is tie-
          unsafe and wrong-direction for an audit feed).
        - Selects the analysis state-machine columns (``a.status``, retries,
          error, vlm_latency, locked_at) plus a screenshot-lag/count subquery so
          the route can derive per-record status + latencies. ``r.status`` is
          NOT the source of truth for pipeline state (``claim_next_task`` does
          not mirror ``processing_vlm`` onto ``records``); ``a.status`` is — so
          both are returned, aliased apart.

        ``cursor`` is the compound keyset ``"{ts_start}_{id}"`` of the last row
        of the previous page (ts_start is an int, id a hyphenated uuid → the
        first ``_`` splits them unambiguously).
        """
        conditions = ["r.ts_start BETWEEN ? AND ?"]
        params: list[Any] = [start_ms, end_ms]

        if cursor:
            cur_ts_raw, _, cur_id = cursor.partition("_")
            try:
                cur_ts = int(cur_ts_raw)
            except ValueError:
                cur_ts = None
            if cur_ts is not None and cur_id:
                # Strict "older than the cursor row" in DESC order.
                conditions.append("(r.ts_start < ? OR (r.ts_start = ? AND r.id < ?))")
                params.extend([cur_ts, cur_ts, cur_id])

        where = " AND ".join(conditions)
        sql = f"""
            SELECT
                r.id, r.client_record_id, r.event_type, r.capture_reason,
                r.app_name, r.process_name, r.window_title, r.url,
                r.ts_start, r.ts_end, r.created_at, r.updated_at,
                r.status                AS record_status,
                a.status                AS analysis_status,
                a.category_final, a.category_suggested, a.confidence,
                a.retry_count, a.next_retry_at, a.error_code, a.error_msg,
                a.vlm_latency_ms, a.vlm_model, a.locked_at,
                a.queued_at, a.done_at, a.decision_trace,
                a.updated_at            AS analysis_updated_at,
                (a.vlm_desc IS NOT NULL) AS has_desc,
                length(a.vlm_desc)       AS desc_chars,
                (SELECT MIN(created_at) FROM screenshots
                   WHERE record_id = r.id AND deleted_at IS NULL) AS first_shot_at,
                (SELECT COUNT(*) FROM screenshots
                   WHERE record_id = r.id AND deleted_at IS NULL) AS screenshot_count
            FROM records r
            LEFT JOIN analysis_results a ON a.record_id = r.id
            WHERE {where}
            ORDER BY r.ts_start DESC, r.id DESC
            LIMIT ?
        """
        params.append(limit)

        async with self._lock:
            async with self.conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_apps(self) -> list[dict]:
        """Return distinct app names with record counts, most-used first."""
        async with self._lock:
            async with self.conn.execute(
                """SELECT app_name AS name, COUNT(*) AS count
                   FROM records
                   WHERE app_name != ''
                   GROUP BY app_name
                   ORDER BY count DESC, name ASC"""
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_screenshots_with_records(self, screenshot_ids: list[str]) -> list[dict]:
        """Fetch rich metadata for the given screenshot ids (used by image search)."""
        if not screenshot_ids:
            return []
        placeholders = ",".join("?" * len(screenshot_ids))
        async with self._lock:
            async with self.conn.execute(
                f"""SELECT s.id AS screenshot_id, s.record_id, s.thumb_path,
                           r.ts_start, r.ts_end, r.app_name, r.window_title, r.url,
                           a.vlm_desc, a.category_final, a.confidence
                    FROM screenshots s
                    JOIN records r ON r.id = s.record_id
                    LEFT JOIN analysis_results a ON a.record_id = s.record_id
                    WHERE s.id IN ({placeholders}) AND s.deleted_at IS NULL""",
                screenshot_ids,
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Screenshots                                                          #
    # ------------------------------------------------------------------ #

    async def insert_screenshot(
        self,
        record_id: str,
        path: str,
        thumb_path: str | None,
        width: int,
        height: int,
        hash_sha256: str,
        privacy_level: str = "normal",
        phash: bytes | None = None,
    ) -> tuple[str, bool]:
        """Insert a screenshot row; returns ``(screenshot_id, was_new)``.

        Idempotent on ``(record_id, hash_sha256)``: if the same screenshot was
        already inserted for this record (outbox at-least-once replay) we
        return the existing id with ``was_new=False`` so the caller can skip
        side-effects like phash_index.insert.
        """
        screenshot_id = _new_id()
        now = _now_ms()
        async with self._lock:
            try:
                await self.conn.execute(
                    """INSERT INTO screenshots
                       (id, record_id, path, thumb_path, width, height,
                        hash_sha256, phash, privacy_level, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        screenshot_id,
                        record_id,
                        path,
                        thumb_path,
                        width,
                        height,
                        hash_sha256,
                        phash,
                        privacy_level,
                        now,
                    ),
                )
                await self.conn.commit()
                return screenshot_id, True
            except aiosqlite.IntegrityError:
                # Duplicate (record_id, hash_sha256) — read back the id we already have.
                await self.conn.rollback()
                async with self.conn.execute(
                    "SELECT id FROM screenshots WHERE record_id=? AND hash_sha256=?",
                    (record_id, hash_sha256),
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    # Should be impossible: UNIQUE violation but row missing on re-read.
                    raise
                return row["id"], False

    async def get_record_ts_start(self, record_id: str) -> int | None:
        """Return `records.ts_start` for the given record, or None if missing."""
        async with self._lock:
            async with self.conn.execute(
                "SELECT ts_start FROM records WHERE id=?", (record_id,)
            ) as cur:
                row = await cur.fetchone()
        return row["ts_start"] if row else None

    async def get_screenshots_for_record(self, record_id: str) -> list[dict]:
        async with self._lock:
            return await self._get_screenshots_for_record_unlocked(record_id)

    async def _get_screenshots_for_record_unlocked(self, record_id: str) -> list[dict]:
        # Explicit column list — `phash` BLOB is intentionally excluded so the
        # row dict stays JSON-serializable when surfaced via the API.
        async with self.conn.execute(
            """SELECT id, record_id, path, thumb_path, width, height,
                      hash_sha256, deleted_at, privacy_level, created_at
               FROM screenshots WHERE record_id=? AND deleted_at IS NULL""",
            (record_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Analysis worker                                                      #
    # ------------------------------------------------------------------ #

    async def claim_next_task(self, kind: str) -> dict | None:
        """Atomically claim the next task in `kind` state and flip it to processing.

        ``kind`` is the queue name (e.g. ``pending_vlm``); the in-flight status
        is ``processing_<base>`` where ``<base>`` strips the ``pending_`` prefix.
        That way ``reclaim_stale_tasks`` can map ``processing_vlm`` back to
        ``pending_vlm`` cleanly with a simple prefix swap.

        Single UPDATE...WHERE...IN(SELECT)...RETURNING avoids the race that the
        old SELECT-then-UPDATE form had under multiple concurrent workers.
        """
        now = _now_ms()
        base = kind.removeprefix("pending_")
        processing = f"processing_{base}"
        async with self._lock:
            async with self.conn.execute(
                """UPDATE analysis_results
                   SET status = ?, locked_at = ?, updated_at = ?
                   WHERE record_id = (
                       SELECT record_id FROM analysis_results
                       WHERE status = ? AND (next_retry_at IS NULL OR next_retry_at <= ?)
                       ORDER BY updated_at ASC
                       LIMIT 1
                   ) AND status = ?
                   RETURNING record_id, retry_count""",
                (processing, now, now, kind, now, kind),
            ) as cur:
                row = await cur.fetchone()
            await self.conn.commit()
        if row is None:
            return None
        return {"record_id": row["record_id"], "retry_count": row["retry_count"]}

    async def get_record_meta(self, record_id: str) -> dict | None:
        """Return record metadata + first non-deleted screenshot path for VLM.

        Returns:
            {"record_id", "window_title", "app_name", "url", "screenshot_path"}
            or None if missing. app_name/url feed the category rule hook.
        """
        async with self._lock:
            async with self.conn.execute(
                """SELECT r.window_title AS window_title,
                          r.app_name AS app_name,
                          r.url AS url,
                          (SELECT path FROM screenshots
                           WHERE record_id = r.id AND deleted_at IS NULL
                           ORDER BY created_at ASC LIMIT 1) AS screenshot_path
                   FROM records r
                   WHERE r.id = ?""",
                (record_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return {
            "record_id": record_id,
            "window_title": row["window_title"],
            "app_name": row["app_name"],
            "url": row["url"],
            "screenshot_path": row["screenshot_path"],
        }

    async def mark_error_retryable(
        self,
        record_id: str,
        error_msg: str,
        retry_count: int,
        next_retry_at: int,
    ) -> None:
        """Bounce a task back to pending with backoff so it's re-claimed later."""
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """UPDATE analysis_results
                   SET status='pending_vlm',
                       error_msg=?,
                       retry_count=?,
                       next_retry_at=?,
                       locked_at=NULL,
                       queued_at=?,
                       updated_at=?
                   WHERE record_id=?""",
                (error_msg, retry_count, next_retry_at, now, now, record_id),
            )
            await self.conn.execute(
                "UPDATE records SET status='pending_vlm', updated_at=? WHERE id=?",
                (now, record_id),
            )
            await self.conn.commit()

    async def save_description(
        self,
        record_id: str,
        desc: str,
        *,
        vlm_model: str | None = None,
        vlm_latency_ms: int | None = None,
    ) -> None:
        now = _now_ms()
        async with self._lock:
            async with self.conn.execute(
                "UPDATE analysis_results "
                "SET vlm_desc=?, vlm_model=?, vlm_latency_ms=?, updated_at=? "
                "WHERE record_id=?",
                (desc, vlm_model, vlm_latency_ms, now, record_id),
            ) as cur:
                rows_updated = cur.rowcount
            # Sync vlm_desc into FTS index so subsequent searches can hit it.
            # UPDATE is a no-op on records inserted before the FTS column landed;
            # the migration backfill handles those.
            await self.conn.execute(
                "UPDATE records_fts SET vlm_desc = ? WHERE record_id = ?",
                (desc, record_id),
            )
            await self.conn.commit()
        if rows_updated == 0:
            logger.warning("database.save_description.not_found", record_id=record_id)

    async def save_text_embedding(self, record_id: str, vec: bytes, model: str) -> None:
        """Write a packed-float32 text embedding to ``analysis_results``.

        Worker calls this after a successful ``save_description`` + transition
        to ``vlm_done``. Idempotent — re-running just overwrites. Failures
        here must never propagate up to the worker loop (caller catches).
        """
        now = _now_ms()
        async with self._lock:
            async with self.conn.execute(
                "UPDATE analysis_results "
                "SET text_embedding=?, text_embedding_model=?, updated_at=? "
                "WHERE record_id=?",
                (vec, model, now, record_id),
            ) as cur:
                rows_updated = cur.rowcount
            await self.conn.commit()
        if rows_updated == 0:
            logger.warning("database.save_text_embedding.not_found", record_id=record_id)

    async def fetch_rows_needing_text_embedding(
        self, limit: int, exclude_ids: set[str] | None = None
    ) -> list[dict]:
        """Backfill candidates: have a non-empty vlm_desc but no text_embedding.

        ``exclude_ids`` lets the worker's backfill sweep skip poison rows that
        already failed this run so they don't re-block the queue head. The set
        is expected to stay tiny (real failures are rare), so the NOT IN clause
        is cheap.
        """
        clause = "vlm_desc IS NOT NULL AND vlm_desc != '' AND text_embedding IS NULL"
        params: list[Any] = []
        if exclude_ids:
            placeholders = ",".join("?" * len(exclude_ids))
            clause += f" AND record_id NOT IN ({placeholders})"
            params.extend(exclude_ids)
        params.append(limit)
        async with self._lock:
            async with self.conn.execute(
                f"SELECT record_id, vlm_desc FROM analysis_results WHERE {clause} LIMIT ?",
                params,
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def vector_search(
        self,
        query_vec: bytes,
        limit: int,
        start_ms: int = 0,
        end_ms: int = 9_999_999_999_999,
    ) -> list[tuple[str, float]]:
        """Cosine-rank records by text_embedding similarity to ``query_vec``.

        Returns ``[(record_id, score), ...]`` sorted by score desc (most
        similar first), capped at ``limit``. Only rows with a non-NULL
        text_embedding within the time window participate.

        Pure numpy over the BLOB column — no embedding client needed here; the
        caller embeds the query text and passes the packed float32 bytes. At
        our scale (hundreds–thousands of rows × 768 dims) the full scan is
        sub-10ms, so no ANN index yet.
        """
        import numpy as np  # noqa: PLC0415 — localized; keeps DB module import-light

        async with self._lock:
            async with self.conn.execute(
                "SELECT a.record_id, a.text_embedding "
                "FROM analysis_results a "
                "JOIN records r ON r.id = a.record_id "
                "WHERE a.text_embedding IS NOT NULL "
                "  AND r.ts_start BETWEEN ? AND ?",
                (start_ms, end_ms),
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            return []

        q = np.frombuffer(query_vec, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        scored: list[tuple[str, float]] = []
        for r in rows:
            v = np.frombuffer(r["text_embedding"], dtype=np.float32)
            if v.shape != q.shape:
                # dim mismatch (model changed mid-DB) — skip rather than crash
                continue
            vn = float(np.linalg.norm(v))
            if vn == 0.0:
                continue
            scored.append((r["record_id"], float(np.dot(q, v) / (qn * vn))))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    async def transition(self, record_id: str, new_status: str) -> None:
        now = _now_ms()
        async with self._lock:
            if new_status == "vlm_done":
                # Stamp completion time on the terminal-success state (real
                # describe OR no-image skip). A re-enqueue→vlm_done overwrites it
                # with the latest completion — what total-latency wants.
                await self.conn.execute(
                    "UPDATE analysis_results SET status=?, updated_at=?, done_at=? "
                    "WHERE record_id=?",
                    (new_status, now, now, record_id),
                )
            else:
                await self.conn.execute(
                    "UPDATE analysis_results SET status=?, updated_at=? WHERE record_id=?",
                    (new_status, now, record_id),
                )
            await self.conn.execute(
                "UPDATE records SET status=?, updated_at=? WHERE id=?",
                (new_status, now, record_id),
            )
            await self.conn.commit()

    async def mark_error_final(self, record_id: str, error_msg: str) -> None:
        """Park a task in `error_final` and mirror the status onto the record.

        Mirroring keeps `records.status` (driving the timeline / API view) in
        sync with `analysis_results.status` (driving the worker state machine);
        otherwise a row that exhausts retries can show `pending_vlm` or
        `processing_vlm` in the UI forever.
        """
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """UPDATE analysis_results
                   SET status='error_final', error_msg=?, locked_at=NULL, updated_at=?
                   WHERE record_id=?""",
                (error_msg, now, record_id),
            )
            await self.conn.execute(
                "UPDATE records SET status='error_final', updated_at=? WHERE id=?",
                (now, record_id),
            )
            await self.conn.commit()

    # ------------------------------------------------------------------ #
    # Feedback                                                             #
    # ------------------------------------------------------------------ #

    async def insert_feedback(
        self,
        record_id: str,
        action: str,
        category_before: str | None,
        category_after: str | None,
        tags_before: str | None = None,
        tags_after: str | None = None,
        user_note: str | None = None,
    ) -> str:
        feedback_id = _new_id()
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """INSERT INTO feedback
                   (id, record_id, action, category_before, category_after,
                    tags_before, tags_after, user_note, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    feedback_id,
                    record_id,
                    action,
                    category_before,
                    category_after,
                    tags_before,
                    tags_after,
                    user_note,
                    now,
                ),
            )
            # Update category_final in analysis_results when user edits
            if action == "edit" and category_after:
                await self.conn.execute(
                    """UPDATE analysis_results SET category_final=?, updated_at=?
                       WHERE record_id=?""",
                    (category_after, now, record_id),
                )
            await self.conn.commit()
        return feedback_id

    # ------------------------------------------------------------------ #
    # Categories                                                           #
    # ------------------------------------------------------------------ #

    async def get_categories(self, include_hidden: bool = False) -> list[dict]:
        query = "SELECT * FROM categories"
        if not include_hidden:
            query += " WHERE is_hidden=0"
        query += " ORDER BY name"
        async with self._lock:
            async with self.conn.execute(query) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Settings                                                             #
    # ------------------------------------------------------------------ #

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        async with self._lock:
            async with self.conn.execute(
                "SELECT value_json FROM settings WHERE key=?", (key,)
            ) as cur:
                row = await cur.fetchone()
        return row["value_json"] if row else default

    async def set_setting(self, key: str, value_json: str) -> None:
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """INSERT OR REPLACE INTO settings (key, value_json, updated_at)
                   VALUES (?, ?, ?)""",
                (key, value_json, now),
            )
            await self.conn.commit()

    # ------------------------------------------------------------------ #
    # Reports (AI-generated insight dashboards)                            #
    # ------------------------------------------------------------------ #

    async def insert_report(
        self,
        scope: str,
        period_start: int,
        period_end: int,
        fmt: str,
        content: str,
        model: str | None = None,
    ) -> str:
        """Store one generated report. History is kept (append-only)."""
        report_id = _new_id()
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """INSERT INTO reports
                   (id, scope, period_start, period_end, format, content, model, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (report_id, scope, period_start, period_end, fmt, content, model, now),
            )
            await self.conn.commit()
        return report_id

    async def get_latest_report(self, scope: str) -> dict | None:
        """Return the most recently generated report for ``scope``, or None.

        Ties on created_at (same-ms inserts in tests) break by rowid so "latest"
        is always the last-inserted row.
        """
        async with self._lock:
            async with self.conn.execute(
                """SELECT id, scope, period_start, period_end, format, content, model, created_at
                   FROM reports WHERE scope=?
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (scope,),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # Housekeeping                                                         #
    # ------------------------------------------------------------------ #

    async def reclaim_stale_tasks(self, timeout_ms: int = 300_000) -> int:
        """Reset tasks stuck in processing_* back to pending_*.

        Called periodically to recover from worker crashes.
        Returns the number of tasks reclaimed.

        Handles both the current ``processing_<base>`` shape and the legacy
        ``processing_pending_<base>`` shape that older builds wrote — both map
        back to ``pending_<base>`` so an orphaned row is never lost.
        """
        now = _now_ms()
        cutoff = now - timeout_ms
        async with self._lock:
            async with self.conn.execute(
                """SELECT record_id, status FROM analysis_results
                   WHERE status LIKE 'processing_%' AND locked_at <= ?""",
                (cutoff,),
            ) as cur:
                rows = await cur.fetchall()

            count = 0
            for row in rows:
                pending_status = _processing_to_pending(row["status"])
                # Refresh queued_at so queue_wait_ms measures the post-reclaim
                # wait, not the crash window — matches the other re-enqueue paths
                # (mark_error_retryable / requeue_skipped_for_vlm) and the
                # "(re)enqueued to pending_vlm" contract on the column.
                await self.conn.execute(
                    "UPDATE analysis_results"
                    " SET status=?, locked_at=NULL, queued_at=?, updated_at=? WHERE record_id=?",
                    (pending_status, now, now, row["record_id"]),
                )
                count += 1

            if count:
                await self.conn.commit()
                logger.info("database.reclaim_stale_tasks", count=count)
        return count

    # ------------------------------------------------------------------ #
    # Memory pyramid: summary cascade (5min → 1h → 6h → day → week)        #
    # ------------------------------------------------------------------ #

    async def _seed_schema_version(self) -> None:
        """Stamp the schema version — a marker, NOT a migration framework.

        Future ALTERs can gate on this integer instead of re-introspecting
        ``PRAGMA table_info`` every boot. ``INSERT OR IGNORE`` so an existing
        stamp wins; we never downgrade or rewrite past migrations onto it.
        Caller holds ``self._lock``.
        """
        await self.conn.execute(
            "INSERT OR IGNORE INTO settings(key, value_json, updated_at) "
            "VALUES ('schema_version', '1', ?)",
            (_now_ms(),),
        )
        await self.conn.commit()

    async def upsert_summary(
        self,
        *,
        grain: str,
        scope_key: str,
        window_start: int,
        window_end: int,
        day_local: str,
        metrics_json: str,
        record_count: int = 0,
        status: str = "pending_summary",
    ) -> str:
        """Insert/replace a cascade row keyed by ``(grain, scope_key)``.

        Idempotent: re-running the rollup over the same window overwrites the
        same row (the UNIQUE index makes ``window ⇒ key`` collapse on conflict),
        so schedule overlap / crash replay never double-counts. ``id`` and
        ``created_at`` survive updates; ``updated_at`` is bookkeeping only.

        Only the metric/identity columns are written here — the narrative
        columns (description / evaluation / body_json / *_tokens /
        compression_ratio / drill_down_hint / summary_embedding) are owned by
        the LLM narrative stage and left untouched on a metrics rebuild.
        Returns the row id (new on insert, existing on update).
        """
        now = _now_ms()
        new_id = _new_id()
        async with self._lock:
            async with self.conn.execute(
                """INSERT INTO summaries
                       (id, grain, scope_key, window_start, window_end, day_local,
                        metrics_json, record_count, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(grain, scope_key) DO UPDATE SET
                       window_start = excluded.window_start,
                       window_end   = excluded.window_end,
                       day_local    = excluded.day_local,
                       metrics_json = excluded.metrics_json,
                       record_count = excluded.record_count,
                       -- Phase 1: status is always 'pending_summary' so this is a
                       -- no-op. Phase 2 (narrative consumer): gate this reset on a
                       -- source_hash change, else a pure metrics rebuild would
                       -- re-queue an already-narrativized row and lose progress.
                       status       = excluded.status,
                       updated_at   = excluded.updated_at
                   RETURNING id""",
                (
                    new_id,
                    grain,
                    scope_key,
                    window_start,
                    window_end,
                    day_local,
                    metrics_json,
                    record_count,
                    status,
                    now,
                    now,
                ),
            ) as cur:
                row = await cur.fetchone()
            await self.conn.commit()
        return row["id"] if row else new_id

    async def get_summary(self, grain: str, scope_key: str) -> dict | None:
        """Fetch a single cascade row, or None."""
        async with self._lock:
            async with self.conn.execute(
                "SELECT * FROM summaries WHERE grain = ? AND scope_key = ?",
                (grain, scope_key),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def get_summaries_in_range(
        self, grain: str, start_ms: int, end_ms: int
    ) -> list[dict]:
        """Cascade rows of ``grain`` whose ``window_start`` ∈ ``[start_ms, end_ms)``.

        Half-open + ordered by ``window_start`` so the rollup builder can group a
        grain's children into their parent windows deterministically.
        """
        async with self._lock:
            async with self.conn.execute(
                "SELECT * FROM summaries "
                "WHERE grain = ? AND window_start >= ? AND window_start < ? "
                "ORDER BY window_start",
                (grain, start_ms, end_ms),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_category_final(self, record_id: str) -> str | None:
        """Return analysis_results.category_final for a record, or None."""
        async with self._lock:
            async with self.conn.execute(
                "SELECT category_final FROM analysis_results WHERE record_id=?",
                (record_id,),
            ) as cur:
                row = await cur.fetchone()
        return row["category_final"] if row else None

    async def set_category_final(
        self,
        record_id: str,
        category: str,
        *,
        category_suggested: str | None = None,
        confidence: float | None = None,
        decision_trace: str | None = None,
    ) -> None:
        """Authoritatively set ``category_final``, creating the analysis row if
        the worker hasn't processed this record yet.

        Used by the agent's ``apply_label`` tool: a user / AI can label a record
        that has no VLM analysis row. We seed the row as ``vlm_done`` on first
        touch so the worker queue (which claims ``pending_vlm``) won't re-process
        and clobber the manual label; rows that already exist keep their status.

        The worker passes ``category_suggested`` (the VLM's raw pick before any
        rule override), ``confidence`` and ``decision_trace`` (the vote breakdown
        from ``decide_category``). Manual labels pass none of these → the
        ``COALESCE`` keeps any existing VLM trace instead of nulling it, so a
        user relabel still shows "VLM said X, you set Y".
        """
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """INSERT INTO analysis_results
                       (record_id, category_final, category_suggested, confidence,
                        decision_trace, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'vlm_done', ?)
                   ON CONFLICT(record_id) DO UPDATE SET
                       category_final = excluded.category_final,
                       category_suggested =
                           COALESCE(excluded.category_suggested, category_suggested),
                       confidence = COALESCE(excluded.confidence, confidence),
                       decision_trace =
                           COALESCE(excluded.decision_trace, decision_trace),
                       updated_at = excluded.updated_at""",
                (record_id, category, category_suggested, confidence, decision_trace, now),
            )
            await self.conn.commit()

    async def get_record_by_id(self, record_id: str) -> dict | None:
        """Return a single record with analysis results and screenshots list."""
        async with self._lock:
            async with self.conn.execute(
                """SELECT r.*, a.vlm_desc, a.category_final, a.confidence,
                          a.status AS analysis_status
                   FROM records r
                   LEFT JOIN analysis_results a ON a.record_id = r.id
                   WHERE r.id = ?""",
                (record_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            result = dict(row)
            result["screenshots"] = await self._get_screenshots_for_record_unlocked(record_id)
        return result

    # ------------------------------------------------------------------ #
    # Login-system: users + sessions                                       #
    # ------------------------------------------------------------------ #
    #
    # These are intentionally typed as low-level CRUD methods, NOT a UserStore
    # facade — same shape as ``insert_record`` etc.. The auth logic
    # (bcrypt verify, rate limiting, session minting) lives in
    # ``server/users.py`` so each layer is independently testable.

    async def get_user(self, username: str) -> dict | None:
        async with self._lock:
            async with self.conn.execute(
                "SELECT username, password_hash, password_must_change, "
                "created_at, updated_at FROM auth_users WHERE username=?",
                (username,),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def count_users(self) -> int:
        async with self._lock:
            async with self.conn.execute("SELECT COUNT(*) FROM auth_users") as cur:
                return (await cur.fetchone())[0]

    async def insert_user(
        self,
        username: str,
        password_hash: str,
        *,
        must_change: bool,
    ) -> None:
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO auth_users (username, password_hash, "
                "password_must_change, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, 1 if must_change else 0, now, now),
            )
            await self.conn.commit()

    async def update_user_password(
        self,
        username: str,
        password_hash: str,
        *,
        must_change: bool,
    ) -> None:
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                "UPDATE auth_users SET password_hash=?, password_must_change=?, "
                "updated_at=? WHERE username=?",
                (password_hash, 1 if must_change else 0, now, username),
            )
            await self.conn.commit()

    async def insert_session(
        self,
        session_id: str,
        username: str,
        *,
        expires_at: int,
        user_agent: str | None,
        ip: str | None,
    ) -> None:
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO auth_sessions (id, username, created_at, last_seen_at, "
                "expires_at, user_agent, ip) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, username, now, now, expires_at, user_agent, ip),
            )
            await self.conn.commit()

    async def get_session(self, session_id: str) -> dict | None:
        async with self._lock:
            async with self.conn.execute(
                "SELECT id, username, created_at, last_seen_at, expires_at, "
                "user_agent, ip FROM auth_sessions WHERE id=?",
                (session_id,),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def touch_session(self, session_id: str) -> None:
        """Bump ``last_seen_at`` on each authenticated request (cheap audit trail)."""
        async with self._lock:
            await self.conn.execute(
                "UPDATE auth_sessions SET last_seen_at=? WHERE id=?",
                (_now_ms(), session_id),
            )
            await self.conn.commit()

    async def delete_session(self, session_id: str) -> None:
        async with self._lock:
            await self.conn.execute("DELETE FROM auth_sessions WHERE id=?", (session_id,))
            await self.conn.commit()

    async def delete_sessions_except(self, username: str, keep_session_id: str) -> int:
        """Revoke every session for ``username`` except the given one. Returns # deleted.

        Called after a password change so other devices get kicked while the
        device that just changed the password stays logged in.
        """
        async with self._lock:
            cur = await self.conn.execute(
                "DELETE FROM auth_sessions WHERE username=? AND id<>?",
                (username, keep_session_id),
            )
            await self.conn.commit()
            return cur.rowcount or 0

    async def purge_expired_sessions(self) -> int:
        """Drop sessions whose ``expires_at`` has passed. Returns # purged.

        Called periodically from the reclaim loop. Lazy-check in ``get_session``
        still rejects expired ones in the request path so this is cleanup, not
        a security gate.
        """
        async with self._lock:
            cur = await self.conn.execute(
                "DELETE FROM auth_sessions WHERE expires_at < ?", (_now_ms(),)
            )
            await self.conn.commit()
            return cur.rowcount or 0

    async def prune_sessions_over_cap(self, username: str, keep_newest: int) -> int:
        """Keep only the ``keep_newest`` most-recent sessions for ``username``.

        Called after each login so a single user's 30-day sessions can't grow
        unbounded (and a stale captured session ages out faster). Ranks by
        ``created_at`` desc and deletes everything past the cap. Returns # deleted.
        """
        if keep_newest <= 0:
            return 0
        async with self._lock:
            cur = await self.conn.execute(
                """DELETE FROM auth_sessions
                   WHERE username = ?
                     AND id NOT IN (
                       SELECT id FROM auth_sessions
                       WHERE username = ?
                       ORDER BY created_at DESC
                       LIMIT ?
                     )""",
                (username, username, keep_newest),
            )
            await self.conn.commit()
            return cur.rowcount or 0

    async def close(self) -> None:
        async with self._lock:
            if self._conn:
                await self._conn.close()
                self._conn = None
