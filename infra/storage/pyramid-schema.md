# 分层记忆金字塔 · Schema spec（时间窗摘要级联 + signals）

> **状态**：设计草案（2026-06-02 起草，2026-06-05 改为时间窗级联），**未实装**。配套 [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) 的存储层 spec。DDL 为 **sketch**（架构级，定字段语义与索引意图），落地以 `server/db/sqlite.py::_SCHEMA` 实际实现为准。
>
> 现状 schema 见 [storage/schema.md](schema.md)（v1 快照）。本页只描述**新增**的派生层。

---

## **🗺️ 一句话**

在现有 `records` / `analysis_results`（L0/L1）之上，新增**一张自相似的级联摘要表 `summaries`** —— 同一张表用 `grain` 区分 `5min` / `1h` / `6h` / `day` / `week`（可扩 `month`），每层是下层的 summary-of-summaries，**空窗不建行**。外加一张 `signals`（派生行为信号）和 `summaries_fts`。全部沿用现有 `_SCHEMA` literal + `_migrate()` 套路，**不引入迁移框架**。

> **设计变更（vs 早期 episode 方案）**：承重层从"语义 episode 变长切分"改为"**固定时间窗 tumbling 级联**"。理由：固定窗死简单、确定、幂等（窗口即 key）、增量天然，**绕掉了边界检测（γ）这个最大质量风险点**。窗口可由 `ts_start` 直接算出 → **不需要 `records.episode_id` 外键列**，连那次 ALTER 都省了。"episode（一次 sitting）"降级为**可选派生视图**（在 `5min` 层上按 app/标题连续性分组），不再是地基。

---

## 0. 落地套路（锚定现状代码）

现状（与代码核对）：

- 整套 schema 是 `src/timetrace/server/db/sqlite.py` 里的 `_SCHEMA` **字符串 literal**（约 30–204 行），每次 `init()` 用 `executescript()` 跑全部 `CREATE...IF NOT EXISTS`。**没有 .sql 文件、没有 ORM、没有迁移框架。**
- 加列走 `_migrate()`（约 295–367 行）：`PRAGMA table_info` 内省 → 缺了才 `ALTER`。已有三次先例（`client_record_id` / `screenshots.hash` / `analysis_results.text_embedding`）。
- 队列 = 状态列：`claim_next_task(kind)` 用 `UPDATE...RETURNING` 前缀 swap 领取，`reclaim_stale_tasks` 回收 stale lease。**新派生层的摘要任务直接复用这套**，不发明 job 系统。
- 时间戳一律用业务时钟 `records.ts_start`，**禁用 `updated_at`**（worker 会污染它）。

**新表落地方式**：

| 动作 | 怎么做 |
|---|---|
| 建 `summaries` / `signals` / `summaries_fts` | 作为 `CREATE...IF NOT EXISTS` 追加进 `_SCHEMA` literal，`init()` 自动建 |
| 帧 → 5min 窗映射 | **不加列**：`window_start = floor((ts_start - cut_offset) / 5min)`，查询走现有 `idx_records_ts_start` 的范围扫 |
| 摘要任务队列 | `summaries.status` 复用 `claim_next_task(kind='pending_summary')` |
| 嵌入编解码 | 复用 `analysis_results.text_embedding` 的 packed float32 BLOB 约定 + `EmbeddingConfig.dim` |
| FTS | 复用 `records_fts` 的 trigram + `_fts_query` 转义 + BM25 CTE 形状 |

---

## 1. `schema_version`（Phase 1 顺手引入）

新分层表会撑爆 ad-hoc `_migrate()` 的 `PRAGMA` 内省。Phase 1 引入一个**版本戳**（不是 Alembic）：

```sql
INSERT OR IGNORE INTO settings(key, value_json, updated_at)
VALUES ('schema_version', '1', <now_ms>);   -- 复用现有 settings KV 表
```

未来 ALTER 按 `schema_version` 整数 gate。**明确不在范围**：重写已有三次迁移。廉价保险，符合"改动范围最小化"。

---

## 2. `summaries`（承重层 + 上卷层，一张自相似级联表）

一行 = 一个**时间窗**的摘要。`grain` 决定窗大小，每层喂下层的摘要（summary-of-summaries）。空窗（睡觉/离开）**不建行**。

```sql
CREATE TABLE IF NOT EXISTS summaries (
    id                TEXT PRIMARY KEY,
    grain             TEXT NOT NULL,           -- '5min'|'1h'|'6h'|'day'|'week'（可扩 'month'）
    scope_key         TEXT NOT NULL,           -- 确定性窗 key，见 §5
    window_start      INTEGER NOT NULL,        -- 窗起 epoch-ms，由 ts_start 对齐算出（业务时钟）
    window_end        INTEGER NOT NULL,
    day_local         TEXT NOT NULL,           -- 'YYYY-MM-DD'，4AM 逻辑日（见 §5），便于按天 group

    -- 结构化摘要主体（严格 schema，见 pipeline §结构化输出）
    description       TEXT,                    -- 自然语言：这段在做什么
    evaluation        TEXT,                    -- 自然语言：评价/判断（专注?摸鱼?返工?）
    body_json         TEXT,                    -- 结构化列表：apps/categories/durations/key_events/cues/timestamps
    metrics_json      TEXT,                    -- 纯 SQL 可 SUM 数值（day/week 必有；低 grain 可选），见 §3

    -- 信息气味（引导下钻，全部写时算）
    src_tokens        INTEGER,                 -- 下层喂进来的估算 token（字符数/3.5，确定性）
    out_tokens        INTEGER,                 -- 本摘要估算 token
    compression_ratio REAL,                    -- out/src，越小=丢得越多=下钻可能越有料
    drill_down_hint   TEXT,                    -- 写时预计算一行："下钻可得 X / 不可得 Y"，见 §4

    -- 隐私
    redacted          INTEGER NOT NULL DEFAULT 1,  -- 1=本行已脱敏，可安全经 MCP 外发/导出

    -- 检索
    summary_embedding BLOB,                    -- packed float32，dim = EmbeddingConfig.dim
    summary_embedding_model TEXT,

    -- 队列 / 增量 / 失效（重发机制见 episode-and-rollup-pipeline.md §1.4）
    status            TEXT NOT NULL DEFAULT 'pending_summary',  -- 复用队列状态机
    locked_at         INTEGER,                 -- claim lease
    source_version    INTEGER NOT NULL DEFAULT 0,  -- 显式失效计数：删除传播/手工失效时 bump
    source_hash       TEXT,                    -- 输入身份+版本指纹：巡检比对自动触发重算
    computed_through_ts INTEGER,               -- watermark：下层已折入到此刻为止（支持部分覆盖先建）
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL         -- 仅 bookkeeping，绝不作业务时间
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_summaries_grain_scope ON summaries(grain, scope_key); -- 幂等 UPSERT 键
CREATE INDEX IF NOT EXISTS idx_summaries_grain_ts  ON summaries(grain, window_start);
CREATE INDEX IF NOT EXISTS idx_summaries_day       ON summaries(day_local, grain);
CREATE INDEX IF NOT EXISTS idx_summaries_status    ON summaries(status) WHERE status LIKE 'pending_%';
```

**级联关系（每层喂下层；空窗跳过）**：

| grain | 窗大小 | 喂自 | 活跃行数/天（估） |
|---|---|---|---|
| `5min` | 5 分钟 | 该窗内的 L1 帧 `vlm_desc`（~30 帧 / 窗 @10s 关键帧） | ~100–150（去掉睡眠/离开的空窗） |
| `1h` | 1 小时 | 该小时内的 ~12 个 `5min` 行 | ~10–14 |
| `6h` | 6 小时 | 该段内的 6 个 `1h` 行 | ~4 |
| `day` | 1 天（4AM 切） | 当天 4 个 `6h` 行 | 1 |
| `week` | ISO 周 | 7 个 `day` 行 | ~1/7 |

**为什么不需要 `records.episode_id`**：窗口是固定时间格，`5min` 行覆盖哪些帧由 `window_start ≤ records.ts_start < window_end` 决定，范围查询走现有 `idx_records_ts_start`。无外键、无回填该列、无迁移。`evidence`（要下钻到帧）= 存窗口边界，按 ts 范围拉。

**level 之间的回链**：高层 `body_json` 保留 `child_scope_keys: [...]`（下层窗 key 列表），防"摘要的摘要"放大幻觉、支持逐层下钻（RAPTOR / hierarchical-merge pitfalls）。

可选 `summaries_fts`（FTS5 trigram，复用 `records_fts` 形状）让摘要可关键词检索：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS summaries_fts USING fts5(
    summary_id UNINDEXED, grain UNINDEXED, description, evaluation, tokenize='trigram'
);
```

---

## 3. `metrics_json`（纯 SQL，可 SUM）

`day` / `week` 行必带 `metrics_json`（低 grain 可选）。全部由**纯 SQL** 算（零 LLM）：

```jsonc
{
  "cat_seconds":   {"work": 28800, "entertainment": 5400, ...},  // 6 类各秒数
  "app_seconds":   {"Code.exe": 21600, "chrome.exe": 7200, ...},
  "active_seconds": 39600,
  "switch_count":  31,                 // category/app 转换数（context_switch_storm 用）
  "top_titles":    ["调试 outbox 队列", ...]
}
```

时长一律走 `server/agent/tools.py::_clamped_dur_sql()`（不是裸 `MAX(0, ...)`：还有一层超 `_MAX_PLAUSIBLE_RECORD_MS`（5min）封顶 `CASE WHEN span > cap THEN 0 ELSE MAX(0, span) END`，照抄时别漏封顶段）。

`summaries`(day/week) vs 现有 `reports` 表分工：`summaries` = **结构化可查的源**（数字可 `SUM`、可被 `query_stats` 直读）；`reports`（append-only HTML）= **渲染层**，改为从 `summaries` read 而非重扫原始帧（见 [thin-router-agent.md §ReportGenerator](../architecture/thin-router-agent.md)）。

---

## 4. 信息气味：压缩率 + 下钻提示（引导 agent 是否下探）

两个字段写时算好，让读到摘要的 agent（尤其外部 MCP agent）自己判断要不要往下挖：

- **`compression_ratio = out_tokens / src_tokens`**（确定性，按字符数 /3.5 估，**不耗 LLM**）。越小 = 这层丢弃越多 = 下钻**可能**越有料。
- **`drill_down_hint`**（写时预计算一行）：压缩率是 proxy、会骗人（30 帧"还在 VSCode 发呆"压成一句也是高压缩率，但下钻啥也没有），所以配一句具体的"下钻能/不能多得到什么"来修正它。**主要由确定性统计得出**（对比本层 prose 省略了下层 `body_json` 的哪些结构化字段）：

```
drill_down_hint 示例：
  "下钻 1h→5min 可得：12 个 5min 单元的逐段时间线 + 3 段报错原文 + 2 次 git reset 时刻；本层已含各 app 总时长。"
  "下钻无新结论：本段 95% 为单一 idle/锁屏，逐帧无差异。"
```

读取规则（写进工具 hint，见 [thin-router-agent.md](../architecture/thin-router-agent.md)）：压缩率高且 `drill_down_hint` 列出了你要的字段 → 下钻；`drill_down_hint` 说"无新结论" → 别浪费一次调用。

---

## 5. 时间口径（锁定决策）

- **逻辑日 4AM–4AM（可配置 cut-hour，默认 4）**：`day_local` 存 `TEXT 'YYYY-MM-DD'`，由 `ts_start` 在 cut 下算。类型钉死 TEXT。cut-hour 放 `common/config.py` 与其它金字塔旋钮一起。**保留源时区/偏移**（ActivityWatch 坑：丢偏移使 group-by-local-day 脆）。
- **`scope_key` 确定性格式**（也是 `UNIQUE(grain, scope_key)` 幂等键）：
  - `5min` → `'2026-06-04T18:25'`（对齐到 5 分钟格）
  - `1h` → `'2026-06-04T18'`
  - `6h` → `'2026-06-04T2'`（当天第 0/1/2/3 段，按 4AM 切）
  - `day` → `'2026-06-04'`
  - `week` → `'2026-W23'`（**ISO-week**，不混日历周以免跨月撞键）

---

## 6. 幂等、watermark 与跨层组合

- **幂等键**：`UNIQUE(grain, scope_key)` + UPSERT。rollup job 像 outbox 一样可能因调度重叠 / crash 而 at-least-once 重放；窗口即 key，重放**只覆盖同一行、不双计**。
- **watermark**：`computed_through_ts` 记录"已折入的下层截止 ts"，支撑增量（下层新窗闭合后只补增量）。
- **跨层组合（"this week" 缝合）**：read 时一个区间可能横跨「已 finalized 的 `week`/`day` 行」+「今天还没上卷的 `1h`/`5min` 行」+「当前还没闭合的 live 帧」。`query_stats` 必须**逐子区间取最高已 finalized grain**、未上卷尾巴 fall back 到低 grain（再到帧），按 `computed_through_ts` 缝合，返回 `source:"mixed"` + watermark。这是最易静默多算/少算处，幂等键**保护不了**跨层组合（它只保护重放）。
- **重发 / 级联失效**：底层被改或晚到 → 上层自底向上自动重算。`source_hash`（内容指纹，巡检/开机自检比对）+ `source_version`（显式失效计数，删除传播 bump）双机制；改动点即时标 dirty + 定时巡检兜底；哈希闸门保证爆炸半径有界（改 1 帧 ≈ 重算 5 行）。完整机制见 [episode-and-rollup-pipeline.md §1.4](../architecture/episode-and-rollup-pipeline.md)。

---

## 7. 脱敏（`redacted` 列，承接 P4 隐私）

> 这套级联给了 P4 一个干净的隐私边界：**原始帧留本地 + 鉴权后才出（`get_raw`）；摘要层逐层脱敏，默认可安全经 MCP 外发 / 导出。**

- 从 `5min` 层起，每层摘要在生成时**脱敏**（抹掉密码/token/私信/身份证号等敏感串），`redacted=1`。越往上 grain 越粗、敏感细节越少。
- `get_raw`（逃生口）读未脱敏原始帧，**只在本地 + `require_principal` 后**可达，绝不经无鉴权路由外发。
- 删除传播：帧 soft-delete（`screenshots.deleted_at`）后，覆盖它的 `5min` 行 `source_version` bump → 重摘要 → 其 `1h/6h/day/week` 祖先链 `source_version` bump → 重 rollup 丢弃。`source_version` 字段 Phase 1 就先留好，避免事后加列。

---

## 8. 信号（`signals`）

```sql
CREATE TABLE IF NOT EXISTS signals (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,         -- 'rework'|'forced_rollback'|'context_switch_storm'|
                                         -- 'focus_block'|'distraction'|'late_night'|'idle_gap'
    ts            INTEGER NOT NULL,
    window_start  INTEGER,
    window_end    INTEGER,
    scope_key     TEXT,                  -- 关联的 summaries 窗（替代早期 episode_id）
    severity      REAL,
    detail        TEXT,
    evidence_json TEXT,                  -- {record_ts_range, scope_keys:[], cues:[]} —— 下钻锚
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_kind_ts ON signals(kind, ts);
```

检测逻辑（批量序列检测、读 `summaries.body_json.cues` + L1 文本）见 [episode-and-rollup-pipeline.md §signals](../architecture/episode-and-rollup-pipeline.md)。`entertainment_time` **不是** signal，是 `metrics_json.cat_seconds.entertainment`。

**复活 `analysis_results.confidence`**（现 DDL 有、worker 硬编 1.0）：worker 写真实 per-frame 分类置信度；`5min` 行 `category` = 成员帧 confidence 加权多数投票；digest/signal 据此门控信任。

---

## 9. 回填与预算

### 回填存量原始帧

仿 `_backfill_embeddings` 一次性 sweep（poison-row skip + endpoint-down abort after 5 连败）：按天（4AM 切）走历史 `records`，对**已存在的 `vlm_desc`**（无新 VLM 帧调用）逐格建 `5min` 摘要 → 上卷 `1h`/`6h`/`day`/`week`。确定性窗 key 保证可重入。

- **newest-day-first**：近期查询最快可用，历史后台慢填。
- **限速**：token bucket，别在用户活跃时打满 3080。
- **coverage 暴露**：回填期工具返回 `coverage`/`computed_through_ts`，agent 说"只统计到 X 为止"而非返空/错。
- **验证先行**：tmp 隔离目录 + 哨兵日期跑 SUM-invariant，不污染真实数据。

### 稳态预算

| 阶段 | LLM 调用/天 | 说明 |
|---|---|---|
| L1 VLM describe | ~（变化帧），未来 dedup 后 ~1500–3000 | 写时每帧一次，沉没成本，永不重读 |
| `5min` 摘要 | ~100–150 | 每活跃窗一次，读自己 ~30 帧；空窗跳过 |
| `1h` / `6h` 摘要 | ~14 + ~4 | summary-of-summaries，输入是下层摘要、极小 |
| `day` / `week` | 1 + ~1/7 | 同上 |
| **query-time** | **0** | 三个目标问题不读原始帧 |

> 比早期 episode 方案的摘要调用更多（~120 vs ~50），但每个 `5min` 调用输入极小（~30 帧）、且**完全确定性、无 γ 风险**，后台/夜间在 3080 上跑得动。这是用"多一些廉价确定的小调用"换"砍掉最大质量风险点"。

---

## 相关文件

- [storage/schema.md](schema.md) —— 现状 schema（v1 快照）
- `src/timetrace/server/db/sqlite.py` —— `_SCHEMA` literal、`_migrate()`、`claim_next_task`、`reclaim_stale_tasks`、`vector_search`
- `src/timetrace/server/agent/tools.py` —— `_clamped_dur_sql()`（时长 clamp 单一源）
- `src/timetrace/common/config.py` —— `EmbeddingConfig.dim`、新增金字塔旋钮（grain 阈值 / cut-hour / 脱敏规则）
- [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) · [episode-and-rollup-pipeline.md](../architecture/episode-and-rollup-pipeline.md) · [thin-router-agent.md](../architecture/thin-router-agent.md)
