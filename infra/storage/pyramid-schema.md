# 分层记忆金字塔 · Schema spec（L2/L3/L4 + signals）

> **状态**：设计草案（2026-06-02），**未实装**。配套 [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) 的存储层 spec。DDL 为 **sketch**（架构级，定字段语义与索引意图），落地时以 `server/db/sqlite.py::_SCHEMA` 实际实现为准。
>
> 现状 schema 见 [storage/schema.md](schema.md)（注意该页是 v1 快照）。本页只描述**新增**的派生层。

---

## **🗺️ 一句话**

在现有 `records` / `analysis_results`（L0/L1）之上，新增三张写时表 —— `episodes`（L2 会话）、`digests`（L3/L4 日/周/月上卷）、`signals`（派生行为信号）—— 外加 `records.episode_id` 一列与一张 `episodes_fts`。全部沿用现有 `_SCHEMA` literal + `_migrate()` 幂等 ALTER 的既有套路，**不引入迁移框架**。

---

## 0. 落地套路（锚定现状代码）

现状（[storage/schema.md](schema.md) 与代码核对）：

- 整套 schema 是 `src/timetrace/server/db/sqlite.py` 里的 `_SCHEMA` **字符串 literal**（约 30–204 行），每次 `init()` 用 `executescript()` 跑全部 `CREATE...IF NOT EXISTS`。**没有 .sql 文件、没有 ORM、没有迁移框架。**
- 加列走 `_migrate()`（约 295–367 行）：`PRAGMA table_info` 内省 → 缺了才 `ALTER TABLE ADD COLUMN`。已有三次先例（`client_record_id` / `screenshots.hash` / `analysis_results.text_embedding`）。
- 队列 = 状态列：`claim_next_task(kind)` 用 `UPDATE...RETURNING` 前缀 swap 领取，`reclaim_stale_tasks` 回收 stale lease。**新派生层的队列态直接复用这套**，不发明 job 系统。
- 时间戳一律用业务时钟 `records.ts_start`，**禁用 `updated_at`**（worker 会污染它）。

**新表落地方式**：

| 动作 | 怎么做 |
|---|---|
| 建 `episodes` / `digests` / `signals` / `episodes_fts` | 作为 `CREATE...IF NOT EXISTS` 追加进 `_SCHEMA` literal，`init()` 自动建 |
| 加 `records.episode_id` 列 | 进 `_migrate()`，`PRAGMA table_info(records)` 守卫的 ALTER（与三次先例同形）|
| 队列态 | `episodes.status` / `digests` 建任务复用 `claim_next_task(kind='pending_episode'/'pending_digest')` |
| 嵌入编解码 | 复用 `analysis_results.text_embedding` 的 packed float32 BLOB 约定 + `EmbeddingConfig.dim` |
| FTS | 复用 `records_fts` 的 trigram + `_fts_query` 转义 + BM25 CTE 形状 |

---

## 1. `schema_version`（Phase 1 顺手引入）

3+ 张新分层表会撑爆 ad-hoc `_migrate()` 的 `PRAGMA` 内省。Phase 1 引入一个**版本戳**（不是 Alembic）：

```sql
-- 复用现有 settings KV 表，不新建表
INSERT OR IGNORE INTO settings(key, value_json, updated_at)
VALUES ('schema_version', '1', <now_ms>);
```

未来的 ALTER 可按 `schema_version` 整数 gate，而非每次 `PRAGMA table_info`。**明确不在范围**：把已有三次 `_migrate` 迁移重写到版本号上 —— 那是 additive 之外的改动，留到真正需要时。这是廉价保险，符合"改动范围最小化"。

---

## 2. L2 · `episodes`（承重层）

一行 = 一段连贯 sitting（30–80 行/天）。摘要写一次后**冻结**，永不重摘。

```sql
CREATE TABLE IF NOT EXISTS episodes (
    id                TEXT PRIMARY KEY,
    ts_start          INTEGER NOT NULL,        -- 来自成员帧 ts_start（业务时钟），不是 updated_at
    ts_end            INTEGER NOT NULL,
    day_local         TEXT NOT NULL,           -- 'YYYY-MM-DD'，4AM 逻辑日切下计算（见 §5）
    primary_app       TEXT,
    category          TEXT,                    -- 成员帧 confidence 加权多数投票（见 §4）
    title             TEXT,                    -- ≤40 字，'调试 支付回调超时'
    summary           TEXT,                    -- ~150 token LLM 摘要，写一次冻结；VLM 宕时可为 NULL
    cues              TEXT,                    -- JSON array：保留的 git-reset/undo/error 字面线索
    tags              TEXT,                    -- JSON array：A-MEM 式跨链属性（可选）
    frame_count       INTEGER NOT NULL DEFAULT 0,
    duration_s        INTEGER NOT NULL DEFAULT 0,  -- MAX(0, ts_end-ts_start)，可 SUM
    summary_embedding BLOB,                    -- packed float32，dim = EmbeddingConfig.dim
    summary_embedding_model TEXT,
    status            TEXT NOT NULL DEFAULT 'pending_summary',  -- 复用队列状态机
    locked_at         INTEGER,                 -- claim lease
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL         -- 仅 bookkeeping，绝不作业务时间
);
CREATE INDEX IF NOT EXISTS idx_episodes_day    ON episodes(day_local, ts_start);
CREATE INDEX IF NOT EXISTS idx_episodes_cat_ts ON episodes(category, ts_start);
CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status) WHERE status LIKE 'pending_%';
```

**帧 → episode 回链**（1:N，列在 `records` 上，比 junction 表干净）：

```sql
-- 进 _migrate()：ALTER TABLE records ADD COLUMN episode_id TEXT REFERENCES episodes(id);
CREATE INDEX IF NOT EXISTS idx_records_episode ON records(episode_id);
```

**队列态**：`status` 取 `pending_summary` / `processing_summary` / `summary_done`，`claim_next_task(kind='pending_summary')` 的前缀 swap 与 `reclaim_stale_tasks` 原样可用。

**`cues` 为什么是承重字段**：signal 检测必须读 L2 而非重扫 L0。episode 摘要时若观察到 `git reset/revert`、Ctrl+Z 风暴、同文件反复编辑、error→retry，把字面线索串塞进 `cues`。`evidence` 仍保留成员 `record_id`（经 `episode_id` FK）保证可审计、可时间邻接下钻。

可选后续：`episodes_fts`（FTS5 trigram，复用 `records_fts` 形状），让 episode 摘要可关键词检索：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    episode_id UNINDEXED, title, summary, primary_app, tokenize='trigram'
);
```

---

## 3. L3/L4 · `digests`（结构化、可查；区别于 `reports` 的 HTML）

```sql
CREATE TABLE IF NOT EXISTS digests (
    id                  TEXT PRIMARY KEY,
    grain               TEXT NOT NULL,         -- 'day' | 'week' | 'month'
    scope_key           TEXT NOT NULL,         -- '2026-06-02' / '2026-W23'(ISO周) / '2026-06'
    period_start        INTEGER NOT NULL,
    period_end          INTEGER NOT NULL,
    summary             TEXT,                  -- LLM 叙事(~300 tok)，summary-of-summaries
    metrics_json        TEXT NOT NULL,         -- 纯 SQL 算，零 LLM：见下
    summary_embedding   BLOB,
    source_version      INTEGER NOT NULL DEFAULT 0,  -- bump 触发失效/重算（删除传播用）
    computed_through_ts INTEGER,               -- watermark：episodes 已折入到此刻为止
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL
);
-- 幂等 UPSERT 键：同 grain 同 scope 只一行
CREATE UNIQUE INDEX IF NOT EXISTS idx_digests_grain_scope ON digests(grain, scope_key);
```

`metrics_json` 形状（全部纯 SQL，复用 `get_app_breakdown` 的时长 clamp 表达式 `SUM(MAX(0, COALESCE(ts_end,ts_start)-ts_start))`）：

```jsonc
{
  "cat_seconds":   {"work": 28800, "entertainment": 5400, ...},  // 6 类各秒数
  "app_seconds":   {"Code.exe": 21600, "chrome.exe": 7200, ...},
  "episode_count": 47,
  "active_seconds": 39600,
  "switch_count":  31,                 // category/app 转换数（context_switch_storm 用）
  "top_titles":    ["调试 outbox 队列", ...]
}
```

`digests` vs `reports` 分工：`digests` = **结构化可查的源**（数字、可 `SUM`、可被 `query_stats` 直读）；`reports`（现有表，append-only HTML）= **渲染层**，改为从 `digests` read 而非重扫原始帧（见 [thin-router-agent.md §ReportGenerator](../architecture/thin-router-agent.md)）。

---

## 4. `signals`（派生行为信号）

```sql
CREATE TABLE IF NOT EXISTS signals (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,         -- 'rework'|'forced_rollback'|'context_switch_storm'|
                                         -- 'focus_block'|'distraction'|'late_night'|'idle_gap'
    ts            INTEGER NOT NULL,
    window_start  INTEGER,
    window_end    INTEGER,
    episode_id    TEXT REFERENCES episodes(id),
    severity      REAL,
    detail        TEXT,
    evidence_json TEXT,                  -- {record_ids:[], episode_ids:[], cues:[]} —— 下钻锚
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_kind_ts ON signals(kind, ts);
CREATE INDEX IF NOT EXISTS idx_signals_episode ON signals(episode_id);
```

检测逻辑（批量序列检测、读 L2 cues + L1 文本）见 [episode-and-rollup-pipeline.md §signals](../architecture/episode-and-rollup-pipeline.md)。`entertainment_time` **不是** signal，是 `metrics_json` 里的 `cat_seconds.entertainment`。

**复活 `analysis_results.confidence`**（现 DDL 有、worker 硬编 1.0）：worker 写真实 per-frame 分类置信度，episode `category` = 成员帧 confidence 加权多数投票，digest/signal 据此门控信任。

---

## 5. 时间口径（锁定决策）

- **逻辑日 4AM–4AM（可配置 cut-hour，默认 4）**：`day_local` 存 `TEXT 'YYYY-MM-DD'`，由 `ts_start` 在 cut 下计算。类型钉死 TEXT（干净 `GROUP BY day_local`、可读、可排序）。cut-hour 放 `common/config.py` 与其它金字塔旋钮一起。**保留源时区/偏移**（ActivityWatch 坑：只存 UTC 丢偏移使 group-by-local-day 脆）。
- **周 `scope_key` 用 ISO-week**（`'2026-W23'`），**不混日历周**，否则 `UNIQUE(grain, scope_key)` 会跨月撞键。

---

## 6. 幂等、watermark 与跨层组合

- **幂等键**：episode 用确定性 key（如成员帧首尾 `record_id` + day）；`digests` 用 `UNIQUE(grain, scope_key)` + UPSERT。rollup job 像 outbox 一样可能因调度重叠 / crash 而 at-least-once 重放，确定性键 + UPSERT 保证重放**不双计**。
- **watermark**：`digests.computed_through_ts` 记录"已折入的 episodes 截止 ts"，支撑增量建日（新 episode 闭合后只补增量）。
- **跨层组合（"this week" 缝合）**：read 时一个区间可能横跨「已 finalized 的 digest 行」+「今天还没上卷的 episode」+「当前还没闭合的 live 帧」。`query_stats` 必须**逐子区间取最高已 finalized 层**、未上卷尾巴 fall back 现算 episode（再到帧），按 `computed_through_ts` 缝合，返回 `source:"mixed"` + watermark。这是最易静默多算/少算的地方，幂等键**保护不了**跨层组合（它只保护重放）。

---

## 7. 回填与预算

### 回填存量原始帧

仿 `_backfill_embeddings` 的一次性 sweep（poison-row skip + endpoint-down abort after 5 连败）：按天（4AM 切）以 `ts_start` 序走历史 `records`，对**已存在的 `vlm_desc`** 跑分段（无新 VLM 帧调用 —— 描述早已生成）、摘要每段（~50 LLM/天）、再 nightly-rollup 每天（1 digest）+ 周/月。确定性键保证可重入。

- **newest-day-first**：近期查询最快可用，历史在后台慢慢填。
- **限速**：token bucket，别在用户活跃时打满 3080。
- **coverage 暴露**：回填期工具返回 `coverage`/`computed_through_ts`，agent 说"只统计到 X 为止"而非返空/错。
- **验证先行**：先在 tmp 隔离目录 + 哨兵日期跑一遍核对（SUM-invariant），不污染真实数据。

### 稳态预算（证明 ~50 + ~1，不是 5000）

| 阶段 | LLM 调用/天 | 说明 |
|---|---|---|
| L1 VLM describe | ~（变化帧）~1500–3000（未来 dedup 后） | 写时每帧一次，沉没成本，**永不重读** |
| L2 episode 摘要 | ~50 | 每段闭合一次，读自己 20–100 帧；idle 段跳过 LLM |
| L3 日 digest | 1 | summary-of-~50-summaries，~7.5K in/~300 out（有上限） |
| L4 周/月 | ~1/7 + ~1/30 | summary-of-summaries；纯 metric 上卷是 SQL |
| **query-time** | **0** | 三个目标问题不读原始帧 |

---

## 8. 删除传播（承接 P4 隐私，承认而非展开）

帧/截图 soft-delete（`screenshots.deleted_at`）后，摘要过它的 episode/digest/signal 行变 stale、可能仍留被删内容（且已嵌进 `summary_embedding`，更难擦）。

**最小传播故事**：被删帧所属 episode 标记 `source_version` bump → 触发该 episode 重摘要（或标记 `summary=NULL` 待重建）→ 其所在 day/week/month digest `source_version` bump → 重 rollup 丢弃。本节为 P4 留 TODO，实现可延后；但 `source_version` 字段从 Phase 2 就先留好，避免事后加列。

---

## 相关文件

- [storage/schema.md](schema.md) —— 现状 schema（v1 快照）
- `src/timetrace/server/db/sqlite.py` —— `_SCHEMA` literal、`_migrate()`、`claim_next_task`、`reclaim_stale_tasks`、`vector_search`
- `src/timetrace/common/config.py` —— `EmbeddingConfig.dim`、新增金字塔旋钮（`IDLE_GAP_S`/`MAX_EPISODE_S`/`γ`/cut-hour）
- [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) · [episode-and-rollup-pipeline.md](../architecture/episode-and-rollup-pipeline.md) · [thin-router-agent.md](../architecture/thin-router-agent.md)
