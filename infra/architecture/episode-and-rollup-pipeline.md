# 写时管线 · 时间窗摘要级联 / 信号检测

> **状态**：设计草案（2026-06-02 起草，2026-06-05 改为时间窗级联），**未实装**。配套 [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) 的写时管线 spec。算法/prompt 为 **sketch**。
>
> 现状 worker 见 [architecture/analysis-worker.md](analysis-worker.md)（v1 快照）。本页描述其上**新增**的派生层 builder。
>
> 文件名仍叫 `episode-and-rollup`（历史原因）；承重层已改为**固定时间窗级联**，"episode（一次 sitting）"是 `5min` 层上的可选派生分组，见 §6。

---

## **🗺️ 一句话**

金字塔的「贵 read 一次性算」全在 server 侧后台完成。一个**级联 rollup builder** 把 L1 帧逐层卷成 `5min → 1h → 6h → day → week` 摘要（每层喂下层、空窗跳过），外加 `signal detector`。全部挂现有 `bootstrap.serve()` 的 `asyncio.TaskGroup`，复用 worker 的 `claim_next_task(kind)` / `reclaim` / backoff 队列与 `_report_scheduler` 调度模板，**不发明新框架**。

---

## 0. 挂载点（锚定现状代码）

现状（与代码核对）：

- `bootstrap.serve()` 用一个 `asyncio.TaskGroup` 跑 `worker` / `reclaim` / `report_scheduler` 等；`_watch_quit` 持取消集合 `{"worker","reclaim","report_scheduler"} | extra_task_names`，统一路由 `quit_event` 优雅退出。
- `_report_scheduler` 是现成模板：**首延迟 + while-True + sleep + 吞异常 + 无 VLM 则退出**。
- `worker/loop.py::_handle_one` 是单帧状态机；`_embed_and_save` 是「best-effort 侧阶段：catch+log、永不传播、不阻塞 `vlm_done`」范式。
- **单/双进程共用 `serve()`**（`main.py` 与 `server/cli.py` 都调），双进程下 client 哑、无 DB → **派生层只能 server 侧**。

新 builder 接线：

| Builder | 挂法 | 队列态 |
|---|---|---|
| 级联 rollup builder | `tg.create_task(_rollup_loop(), name="rollup")`，名字加进 `_watch_quit` 取消集合 | `summaries.status='pending_summary'`，`claim_next_task(kind='pending_summary')` |
| signal detector | 挂在 `day` grain rollup 内（批量序列分析比 per-record 更合跨帧模式），best-effort 侧阶段 | 无独立队列 |

---

## 1. 级联 rollup —— tumbling 固定窗，每层喂下层

固定时间格，**确定性、无边界检测**。窗对齐到 4AM 逻辑日（见 [pyramid-schema §5](../storage/pyramid-schema.md)）。

```
L1 帧 ──(每 5min 窗)──▶ 5min 摘要 ──(每 12 个)──▶ 1h ──(每 6 个)──▶ 6h ──(每 4 个)──▶ day ──(每 7 天)──▶ week
       grain='5min'              grain='1h'        grain='6h'        grain='day'         grain='week'
```

### 1.1 何时跑 + 闭合条件

`_rollup_loop`（套 `_report_scheduler` 模板）周期扫各 grain 的**可闭合窗**：

- **`5min` 窗可闭合**当：`now > window_end` 且该窗内所有帧都 `vlm_done`（描述齐了）。
- **高层窗可闭合**当：`now > window_end` 且其所有**子窗**都已 `summary_done`（下层先齐）。
- **空窗跳过**：窗内无帧（睡觉/关机/离开）→ **不建行**，级联里自然出现 gap（查询侧靠 coverage 处理，见 [thin-router](thin-router-agent.md)）。
- **幂等**：窗口即 `scope_key`，`UNIQUE(grain, scope_key)` + UPSERT，调度重叠/crash 重放只覆盖同行不双计。
- **闭合后冻结**：已 `summary_done` 的窗不再重摘，除非 `source_version` 被 bump（删除传播 / 手工失效）。

### 1.2 一个窗怎么摘要

| grain | 输入 | LLM？ |
|---|---|---|
| `5min` | 窗内 ~30 帧的 `vlm_desc`（~3K tok in → ~150 tok out） | 是（本地 LLM 一次） |
| `1h`/`6h`/`day`/`week` | 下层那几条摘要的 `description`+`evaluation`（极小输入） | 是（summary-of-summaries，输入更小） |

- **空闲快路**：窗内全锁屏/无输入/pHash 相同 → 直接产 `description="空闲/锁屏"`、`status='summary_done'`、`compression_ratio` 标极高、`drill_down_hint="无新结论"`，**不调 LLM**。
- **数值走 SQL**：`metrics_json`（day/week 必有）由纯 SQL 算（复用 `_clamped_dur_sql()`），**不进 LLM**；LLM 只写 `description`/`evaluation` 叙事。
- **回链**：高层 `body_json.child_scope_keys` 存下层窗 key，防递归摘要幻觉、支持逐层下钻。
- **部分覆盖先建**：窗可在尚有帧 `pending_vlm` / 子层未齐时先按现有数据建，打 `computed_through_ts` 水位；晚到数据补齐后由 §1.4 的重发机制自动重算 —— "晚到"与"被改"是同一回事（输入变了）。

### 1.3 执行模型：它是一个 task/worker/队列，复用现有那套

rollup **就是一个 task**（有输入、有输出、输出入库、要排队），**复用现有 `claim_next_task(kind)` / lease(`locked_at`) / `reclaim_stale_tasks` / backoff**，不另造框架。与 VLM worker 的区别只有两点：

| | VLM worker（现有） | rollup worker（新 `_rollup_loop`） |
|---|---|---|
| 任务关系 | **扁平独立**的帧，纯抢占、谁先无所谓 | **依赖 DAG**：父窗须等所有子窗 `summary_done`（children-before-parent） |
| 触发 | 帧一来就排队 | 窗**闭合**（`now>window_end` 且子层齐）才"可建" |
| 调度 | 一个 kind 抢占消费 | **分层有序扫描**：每轮 `5min→1h→6h→day→week` 自底向上推一层 |
| 入库 | `analysis_results` UPSERT | `summaries` UPSERT（`(grain,scope_key)`，窗即幂等键） |

**"队列"是隐式的**：缺 `summary_done` 行的已闭合窗 = 待办；不需要单独的 job 表。`_rollup_loop` 每轮按 grain 由低到高扫"可建窗"，claim 上锁（`pending_summary`→`processing_summary`+`locked_at`）→ 摘要 → UPSERT（`summary_done`）。并发用 Semaphore 限（见 §8），写入走单锁 aiosqlite 连接，UPSERT 不冲突。崩溃残留的 `processing_*` lease 由 `reclaim_stale_tasks` 原样回收。

### 1.4 级联失效与重发（`source_hash` + 巡检 + 开机自检）

底层数据后期被改（`apply_label` 改类、帧 soft-delete、重跑 VLM）或晚到，上层须**自底向上自动重算**。机制 = **内容指纹 + 双路检测 + 自底向上波**：

- **每行存 `source_hash`** = 其输入的身份+版本哈希。`5min` 行 = 成员帧 `(id, content_version)` 哈希；高层行 = 子行 `(scope_key, source_hash)` 哈希。**输入没变 → 哈希不变 → 不重算**（省算力的闸门）。
- **检测两条路**：
  1. **即时（事件驱动）**：改动点（`apply_label` 写 `feedback` 处 / soft-delete / 重跑 VLM）直接把覆盖该帧的 `5min` 窗标 dirty（`status='pending_summary'` + `source_version` bump）。快、准。
  2. **兜底（巡检 + 开机自检）**：定时 + 启动时跑完整性扫描，重算每行 expected `source_hash` 与存的比对，失配则 re-queue。专抓即时路漏的（崩溃、直接改库、bug）。
- **传播是自底向上的波**：`5min` 重算 → 其 `source_hash` 变 → 父 `1h` 发现"子哈希对不上" → re-queue → 重算 → `6h` 失配 → … 一路到 `week`。**不必一次遍历全树**：每轮巡检把波推一层、几轮收敛（或即时路里 eager 往上标一条 dirty 链）。两种都**幂等**（标已 pending 的行无副作用）。
- **爆炸半径有界**：哈希闸门保证只有真覆盖到改动的窗才重算 —— 改 1 帧 = 重算 5 行（各 grain 1 个），不是重算全史。
- **防重算风暴**：批量改动（重分类一整月）触发大量重算 → 复用回填的 token-bucket 限速 + 离峰跑（见 §8）。

> 本质是**增量物化视图**（incremental materialized view）/ RAPTOR 重嵌入语义，落在 SQLite + 现有 worker 队列上。`source_hash` 与 `source_version` 同存：`source_version` 是显式失效计数（删除传播 bump 它），`source_hash` 是内容自动比对（巡检兜底）。

---

## 2. 严格结构化输出（每条摘要必须按 schema）

摘要**不是自由文本**，是 `VLMClient.generate` + 严格 `json_schema`（screenpipe：派生层一律 generateObject + schema，字段才可查）。既要原始数据、也要聚合、也要自然语言 —— 三块互相约束，是压住幻觉的主手段。

```jsonc
{
  // ① 原始事实表（仅 5min 层带；如实记录，不臆测。是廉价 drill-down 索引 + 审计依据）
  "raw_table": [ {"ts":"17:02:10","app":"Code.exe","title":"outbox.py","one_line":"编辑 _drain()"}, ... ],
  // ② 聚合（程序预算好喂进去，模型照抄校验、不自己数）
  "apps":        [{"name":"Code.exe","seconds":210}, ...],
  "categories":  [{"id":"work","seconds":260}],
  // ③ 自然语言（只能引用 ①② 出现过的东西，尽量减少推测）
  "description": "string ~120 tok —— 这段在做什么，严格陈述可见事实",
  "evaluation":  "string ~40 tok —— 据上表评价：专注/碎片化/返工/摸鱼/空闲，不外推",
  "key_events":  ["17:02 切到终端跑 pytest", "17:05 git reset --hard"],
  "cues":        ["git reset --hard","Ctrl+Z×12"],   // 返工/回滚字面线索（承重）
  "child_scope_keys": ["2026-06-04T17:00", ...]       // 回链下层
}
```

**prompt 纪律（压幻觉的关键）**，写死三条：**① 先逐条列 `raw_table` —— 如实记录、不臆测；② `apps`/`categories` 秒数由程序纯 SQL 预聚合后喂入，模型只照抄/校验、不自己归纳；③ `description`/`evaluation` 只能引用 ①② 里出现过的内容，尽量减少推测。** 先列事实、再据事实评价的两段式，把外推空间挤掉。`5min` 层输入是几百帧内、完全吃得下，所以"奢侈"地把 `raw_table` 也带上（高层不带 `raw_table`，只 summary-of-summaries）。

`description`/`evaluation` 落 `summaries` 同名列；其余进 `body_json`。`apps`/`categories` 的秒数**以 SQL 算的为准**（LLM 填的仅作交叉校验，见 [PLAN §9 评估](../PLAN-BETTER-AGENT.md)）。

---

## 3. 信息气味：压缩率 + 下钻提示（写时算）

承接你的设计点，两个字段在摘要写时就算好（详见 [pyramid-schema §4](../storage/pyramid-schema.md)）：

- **`compression_ratio = out_tokens/src_tokens`**：纯字符数估算（`chars/3.5`），**零 LLM**、确定性。
- **`drill_down_hint`**：写时预计算一行"下钻可得/不可得什么"。**主要靠确定性统计**——对比本层 `description` 省略了下层 `body_json` 的哪些结构化字段（逐段时间线？报错原文？git 时刻？），拼成提示。压缩率是 proxy（高压缩可能只是 idle 冗余），`drill_down_hint` 是修正它的具体信号。

```
1h 行：compression_ratio=0.04，drill_down_hint="下钻 5min 可得 12 段逐段时间线 + 2 次 git reset 时刻 + 报错原文；本层已含各 app 总时长。"
1h 行（摸鱼）：compression_ratio=0.02，drill_down_hint="无新结论：95% 单一视频播放，逐帧无差异。"
```

---

## 4. 脱敏（`redacted` 列）

从 `5min` 层起每层摘要在生成时**脱敏**（抹密码/token/私信/身份证等），`redacted=1`。越往上越粗、敏感细节越少。于是**摘要层默认可安全经 MCP 外发 / 导出**，原始帧（`get_raw`）留本地 + 鉴权后才出。脱敏规则放 `common/config.py`，复用现有隐私黑名单思路（见 [privacy/strategy.md](../privacy/strategy.md)）。承接 P4。

---

## 5. 信号检测（读摘要 cues → `signals`）

**v1 单处：`day` grain rollup 内的批量序列检测**。rework / context-switch storm / focus-vs-distraction / late-night 都是**跨帧序列模式**，单帧触发不了 → per-frame 阶段 YAGNI 延后。

检测器读 **`summaries.body_json.cues` + 必要时 L1 文本**，套 `_embed_and_save` best-effort 纪律（catch+log、永不传播、不阻塞）。基于**具体观测事件**，不是软性 distraction flag（Dayflow 坑）。

| `kind` | 检测 | 存 |
|---|---|---|
| `rework` / `forced_rollback` | `git reset/revert/--hard` 线索；同文件名跨多个非连续窗反复编辑；undo 风暴 | `severity`=回滚次数，`evidence_json`=scope_keys+cues |
| `context_switch_storm` | 每小时 `metrics_json.switch_count` > 阈 | switches/hour |
| `focus_block` vs `distraction` | 连续同类窗 run 长；娱乐打断工作的 <5min 窗 | 专注/分心分钟 |
| `late_night` | `window_start` 落夜间窗 | count/duration |
| `idle_gap` | 活跃流中 > 阈的 gap（级联里的空窗） | duration |

> distraction 概念在摘要层实现（娱乐 <5min 打断工作窗），让 agent 区分"主类娱乐时长"（干净）vs"含打断的总休闲"（更全）—— 不扩 6 类 taxonomy。详见 [PLAN §4.4 ⚠️](../PLAN-BETTER-AGENT.md)。

> **⚠️ cues 的前置依赖**：`cues` 抓不到 `vlm_desc` 里根本没提的东西。若 git/终端/报错文本未进帧描述，**L1 describe prompt 需轻量提示**"记录版本控制/终端/错误对话框文本"。这是 Phase 4 前置依赖。

---

## 6. 可选派生视图：episode（一次 sitting）

时间窗砍掉了 γ 边界风险，但若要"按一次任务"看（"我那次调支付调了多久"），可在 `5min` 层上做**廉价派生分组**：相邻 `5min` 窗若 `primary_app` + 主类连续就归一段，遇切换断开。这是**确定性的 group-by**（不是嵌入 surprise 检测），可即时算或物化成一张轻量 `episodes_view`。**非地基、非 v1**，质量不行也不拖累统计/rollup。

---

## 7. 降级契约（VLM / embserver 宕机）

> 关键韧性：新层加了对 VLM / embserver 的新依赖，每层宕机行为必须明确，否则一次模型宕机冻住整个金字塔、"这个月多少小时"悄悄停更。

| 子系统宕 | 5min/上卷摘要 | metrics_json | search（语义） |
|---|---|---|---|
| **VLM 宕** | 窗照常**闭合占位**（`status='pending_summary'`、`description=NULL` 待回填）；**metrics 照算** | 照常（纯 SQL） | 不受影响 |
| **embserver 宕** | 摘要文本照写，`summary_embedding` 留空待回填 | 照常 | 降级 **FTS5-only**（RRF 容一路缺失），不报错 |
| **两者都在** | 全功能 | 全功能 | 向量+FTS 融合 |

`query_stats`/`get_summary(metrics)` **既不依赖 VLM 也不依赖 embserver**（纯 SQL）—— 金字塔核心韧性：模型全宕时廉价聚合路径照常。**调度器"无 VLM 退出"守卫不得禁用纯 metrics 的 rollup**（现 `_report_scheduler` 模板无 VLM 整体退出 → 新 builder 要拆分：开窗/metrics 无 VLM 也跑，只 LLM 叙事/嵌入阶段 gate 在 VLM/embserver 上）。

---

## 8. 并发预算（单 3080 端点争用）

`5min` rollup（活跃时段高频）、上卷（夜间）、`_embed_and_save`、live VLM worker、`deep_scan` fan-out 全打同一个 LM Studio 端点。

- **交互 agent 优先**：用户问"现在在做什么"不该被后台 rollup 拖慢。
- 上卷 `day`/`week` **夜间低并发**（已离峰）。
- **`5min` rollup 是主要争用点**（跑在用户活跃时段）：限并发（复用 `WorkerConfig.vlm_concurrency` 的 Semaphore），或检测到交互请求时让路。`5min` 摘要可**滞后几分钟**批量补，不必实时。
- `deep_scan` fan-out 用 `Semaphore(2–3)` 对齐 `vlm_concurrency`（见 [thin-router-agent.md §子 agent](thin-router-agent.md)）。

---

## 9. Phase 落地映射（files-to-touch）

| Phase | 新增文件 | 改动文件 | 新测试 |
|---|---|---|---|
| **1** | `server/summary/rollup.py`（级联 builder + 窗对齐 + 严格 schema + 压缩率/下钻提示 + 脱敏 hook） | `db/sqlite.py`（`summaries`+`summaries_fts`+`upsert_summary`/`get_summaries`/`fetch_frames_for_window`）、`bootstrap.py`（`_rollup_loop`+取消集合）、`common/config.py`（grain/cut-hour/脱敏规则）、`settings`（`schema_version`） | `test_rollup_cascade.py`（窗对齐+空窗跳过+UPSERT 幂等+压缩率确定性）、`test_summary_schema.py`（json_schema 约束）、**SUM-invariant + golden-day** |
| **2** | —（`day`/`week` grain 在同一 builder 内开） | `agent/tools.py`（`query_stats`/`get_summary`）、`report/generator.py`（改读 `summaries`） | `test_query_stats.py`（month=Σdaily metrics、跨层缝合 `source:mixed`）、`test_agent_tools_summary.py` |
| **3** | — | `agent/tools.py`（`search_summaries` FTS+向量+RRF）、旧名 shim、**MCP 折叠注册 + instructions/resource**、SKILL.md 同步测试、前端事件/label 协同 | `test_search_summaries.py`、`test_mcp_tools.py`（MCP 集==`_IMPLS`）、`test_skill_doc_sync.py` |
| **4** | `server/signals/detectors.py` | `db/sqlite.py`（`signals`+`insert_signal`/`get_signals`）、`summary/rollup.py`（cues 保留）、worker 写真实 `confidence` | `test_signals_detectors.py`（rework 序列触发、纯娱乐不触发、evidence 回链） |
| **5** | `server/agent/mapreduce.py` | `agent/tools.py`（`get_raw` token-cap + `deep_scan`）、前端可选 `subagent` 事件 | `test_agent_mapreduce.py`（非重叠切分+reduce+主 convo token 上界） |

公共约定（贯穿）：业务时间锚 `ts_start` 禁用 `updated_at`；4AM 逻辑日；窗口即幂等键 + UPSERT；回链 `child_scope_keys`；`tmp_path` 真实 SQLite + 哨兵假日期不污染"今天"；新测试沿用 `tests/test_*.py`；不破 261 现存测试。

---

## 相关文件

- [architecture/analysis-worker.md](analysis-worker.md) —— 现状 worker 状态机（v1 快照）
- `src/timetrace/server/bootstrap.py` · `server/worker/loop.py` · `server/db/sqlite.py` · `server/agent/tools.py::_clamped_dur_sql()`
- [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) · [storage/pyramid-schema.md](../storage/pyramid-schema.md) · [thin-router-agent.md](thin-router-agent.md)
