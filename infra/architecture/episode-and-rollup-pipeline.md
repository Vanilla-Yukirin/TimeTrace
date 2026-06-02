# 写时管线 · Episode 分段 / Rollup / 信号检测

> **状态**：设计草案（2026-06-02），**未实装**。配套 [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) 的写时管线 spec。算法/prompt 为 **sketch**。
>
> 现状 worker 见 [architecture/analysis-worker.md](analysis-worker.md)（v1 快照）。本页描述在它之上**新增**的派生层 builder。

---

## **🗺️ 一句话**

金字塔的「贵 read 一次性算」全在 server 侧后台完成。三个新 builder —— **episode builder**（L0/L1 → L2）、**digest rollup**（L2 → L3/L4）、**signal detector**（读 L2 cues → `signals`）—— 都挂在现有 `bootstrap.serve()` 的 `asyncio.TaskGroup`，复用 worker 的 `claim_next_task(kind)` / `reclaim` / backoff 队列机制与 `_report_scheduler` 调度模板，**不发明新框架**。

---

## 0. 挂载点（锚定现状代码）

现状（与代码核对）：

- `bootstrap.serve()` 用一个 `asyncio.TaskGroup` 跑 `worker` / `reclaim` / `report_scheduler` 等任务；`_watch_quit` 持有一个取消集合 `{"worker","reclaim","report_scheduler"} | extra_task_names`，统一路由 `quit_event` 优雅退出。
- `_report_scheduler` 是现成模板：**首延迟 + while-True + sleep + 吞异常 + 无 VLM 则退出**。
- `worker/loop.py::_handle_one` 是单帧状态机；`_embed_and_save` 是「best-effort 侧阶段，catch+log、永不传播、不阻塞 `vlm_done`」的范式。
- **单/双进程共用 `serve()`**（`main.py` 与 `server/cli.py` 都调它），双进程下 client 哑、无 DB → **派生层只能 server 侧**。

新 builder 接线：

| Builder | 挂法 | 队列态 |
|---|---|---|
| episode builder | `tg.create_task(_episode_builder_loop(), name="episode_builder")`，名字加进 `_watch_quit` 取消集合 | `episodes.status` = `pending_summary`，`claim_next_task(kind='pending_summary')` |
| digest rollup | `tg.create_task(_digest_scheduler(), name="digest_scheduler")`（套 `_report_scheduler` 模板，跑 day/week/month grain） | `digests` UPSERT（幂等键，无需 claim）；摘要任务可走 `pending_digest` |
| signal detector | **挂在 nightly digest job 内**（批量序列分析比 per-record 更合跨帧模式），best-effort 侧阶段 | 无独立队列，rollup 内顺带 |

---

## 1. Episode 分段（L0/L1 → L2）

episode = 一段连贯 sitting。目标 **30–80 段/天**（60–150× 缩减）。一律锚定真实帧 `ts_start`，绝不信 LLM 发的相对时间戳。

### 1.1 v1 确定性边界（任一触发即闭合当前段）

按 [PLAN §4.2](../PLAN-BETTER-AGENT.md) 锁定：**v1 只用确定性边界**，承重层不押在难调的 γ 上。

1. **Idle gap** —— 连续帧间隔 > `IDLE_GAP_S`（默认 300s，对齐现有 `_MAX_ORPHAN_BRIDGE_MS` = 5min）。**先做 `flood` 式小 gap 容忍**（ActivityWatch 借鉴）：2s alt-tab 不该把 2h 会话碎成 40 段。
2. **App-cluster 切换** —— `primary_app` 切换且短期不回（debounce flicker）。**归一化分组键**：剥掉标题里的实时时钟 / 标签计数（否则 noisy 标题让精确相等失败、爆段数）；URL 归一到 canonical 小写域名（无协议）。
3. **Max-duration cap** —— 强制闭合 `MAX_EPISODE_S`（默认 3600s / 60min），马拉松不该变成一个不可摘要的大块。

### 1.2 Phase 1b · 嵌入 topic-drift（flag，默认 OFF）

连续帧 `text_embedding` cosine 距离超 `T = mean(window) + γ·std(window)`（EM-LLM Bayesian-surprise）捕捉"同 app 不同任务"（VSCode 项目 A→B）。**γ 是全设计最易翻车的旋钮**（边界调参是刀尖、依赖 embserver 在线），所以：

- segmenter 签名**一开始就接受** drift 输入，只是 `drift_enabled=False` 默认 —— 开启零返工。
- 先用确定性边界跑通、对真实数据验证 episode 质量（golden-day fixture），**再**开 γ。
- modularity / conductance 精修 pass **完全延后**（Phase 6+，"质量不行再说"）。

### 1.3 何时跑 —— 事件触发、闭合后冻结

`_episode_builder_loop` ~60s 扫一次「开放边界」：上一闭合 episode 之后那串连续的、`vlm_done` 完成的帧。当某边界规则触发**且**成员帧都 `vlm_done` 时，**闭合**该段（写 `episodes` 行 + 摘要 + 回填 `records.episode_id`）。

- **闭合后冻结**：永不重摘 lookback 窗外的 episode（Dayflow 教训：merge-by-reprocess 会 thrash、反复重花 token）。我们有权威帧时间戳，close-once 即可，无需像 Dayflow 那样 reconcile LLM 漂移的时间范围。
- **空闲快路**：全锁屏 / 无输入 / pHash 相同的段，直接产 `category='system'`、`summary="空闲/锁屏"`、`status='summary_done'`，**不调 LLM**（Dayflow idle bypass）。

---

## 2. Episode 摘要 prompt（L1 → L2，sketch）

读自己 20–100 帧成员的 `vlm_desc`（每条 ~100 tok → 2–10K tok，舒服进 50K）。复用 `AgentRunner` 换 persona（`ReportGenerator` 已证明的模式）或直接一次 `VLMClient.generate` + 严格 `json_schema`（screenpipe：派生层一律用结构化输出 generateObject + schema，字段才可查）。输出：

```jsonc
{
  "title":   "≤40 字，'调试 支付回调超时'",
  "summary": "~150 token，这一段在做什么，一段话",
  "category": "work|study|social|entertainment|system|uncategorized",  // 成员帧 confidence 加权多数
  "tags":    ["可选，跨链属性"],
  "cues":    ["git reset --hard", "Ctrl+Z×12", "rollback"]  // 承重：保留返工/回滚线索
}
```

prompt 显式指令：*"若观察到 git reset/revert、重复 undo（Ctrl+Z 风暴）、同文件反复编辑、error→retry 循环，把字面线索串列进 `cues`。"* `cues` 让 signal 检测读 L2 而非重扫 L0。保留成员 `record_id`（经 `episode_id` FK）使摘要可审计、支持时间邻接下钻、缓解分层 merge 幻觉。

> **⚠️ 依赖**：`cues` 抓不到 `vlm_desc` 里根本没提的东西。若 git/终端/错误文本未进帧描述，**L1 describe prompt 可能需轻量提示**"记录版本控制/终端/错误对话框文本"。这是 Phase 4 的前置依赖，不是 episode builder 自己能解的。

---

## 3. Rollup（L2 → L3/L4，数字走 SQL、叙事才用 LLM）

`_digest_scheduler`（套 `_report_scheduler` 模板）跑 day / week / month grain。

- **`metrics_json` 纯 SQL**：按 category/app 秒数、episode_count、active_seconds、switch_count、top_titles，复用现有时长 clamp 表达式 `SUM(MAX(0, COALESCE(ts_end,ts_start)-ts_start))`。**零 LLM token。**
- **叙事 LLM**：日 rollup 喂当天 ~50 条 episode **摘要**（不是原始帧）≈ 7.5K tok in → ~300 tok 叙事 out，**有输出 token 上限**（Dayflow recap 上限 8192）。
  - map-reduce（并行/快）适合聚合路径；**iterative-refine（顺序/连贯）**适合"我这一天的故事"叙事 —— 二选一按需。
- **周/月 = summary-of-summaries**：喂 7 条日叙事 → 1 条周叙事。**每层回链源 episode/day ID**，绝不让 L4 变成"摘要的摘要的"无依据复述（分层 merge 放大幻觉）。
- **逻辑日 4AM–4AM**（见 [pyramid-schema.md §5](../storage/pyramid-schema.md)）；周 `scope_key` 用 ISO-week。
- **增量 + 幂等**：`digests.computed_through_ts` watermark 只折增量；`UNIQUE(grain, scope_key)` + UPSERT 保证调度重叠 / crash 重放不双计。

---

## 4. 信号检测（读 L2 cues → `signals`）

**v1 单处：rollup 内的批量序列检测**。rework / context-switch storm / focus-vs-distraction / late-night 都是**跨帧序列模式**，单帧触发不了 —— per-frame 阶段买不到什么、还多一条会误改热 worker 循环的代码路径，YAGNI 延后。

检测器读 **L2 `episodes.cues` + 成员帧文本**，套 `_embed_and_save` best-effort 纪律（catch+log、永不传播、不阻塞）。基于**具体观测事件**，不是软性 distraction flag（Dayflow 坑：同一 LLM 发 distraction flag 对 rework 太软）。

| `kind` | 检测 | 存 |
|---|---|---|
| `rework` / `forced_rollback` | `git reset/revert/--hard` 线索；同文件名跨 ≥N 非连续 episode 反复编辑；undo 风暴 | `severity`=回滚次数，`evidence_json`=episode_ids+cues |
| `context_switch_storm` | 每小时 category/app 转换数 > 阈（`metrics_json.switch_count`） | switches/hour |
| `focus_block` vs `distraction` | 连续同类 run 长；娱乐打断工作的 <5min 段（Dayflow distractions[]） | 专注/分心分钟 |
| `late_night` | `ts_start` 落夜间窗的 episode | count/duration |
| `idle_gap` | 活跃流中 > 阈的 gap | duration |

> distraction 概念在 **L2 层**实现（娱乐 <5min 打断工作段），让 agent 区分"主类娱乐时长"（干净）vs"含打断的总休闲"（更全）—— 不扩 6 类 taxonomy。详见 [PLAN §4.4 ⚠️](../PLAN-BETTER-AGENT.md)。

---

## 5. 降级契约（VLM / embserver 宕机）

> 关键韧性：新层加了对 VLM / embserver 的**新**依赖，每层宕机行为必须明确，否则一次模型宕机会冻住整个金字塔、"这个月多少小时"悄悄停更。

| 子系统宕 | episode builder | digest rollup | search_episodes |
|---|---|---|---|
| **VLM 宕** | episode **照常闭合**（边界+时长+类投票都不需 LLM），`summary`/`summary_embedding` 留 `pending_summary` 待回填 | `metrics_json` **照常算**（纯 SQL），叙事待回填 | 不受影响 |
| **embserver 宕** | episode 闭合，`summary_embedding` 留空待回填 | metrics + 叙事都不受影响 | 降级到 **FTS5-only**（RRF 已容一路缺失），不报错 |
| **两者都在** | 全功能 | 全功能 | 向量 + FTS 融合 |

`query_stats` / `get_digest` 的 metrics **既不依赖 VLM 也不依赖 embserver**（纯 SQL）—— 这是金字塔的核心韧性属性：模型全宕时廉价聚合路径照常工作。**调度器的"无 VLM 退出"守卫不得禁用纯 metrics 的 rollup**（现 `_report_scheduler` 模板在无 VLM 时整体退出 —— 新 builder 要拆分：分段/metrics 无 VLM 也跑，只摘要/嵌入阶段 gate 在 VLM/embserver 上）。

---

## 6. 并发预算（单 3080 端点争用）

episode builder（~60s 循环）、digest rollup（nightly）、`_embed_and_save`、live VLM worker、`deep_scan` fan-out **全打同一个 LM Studio 端点**。

- **交互 agent 优先**：用户问"现在在做什么"不该被后台 rollup 拖慢。
- 后台 rollup **低并发 / 夜间跑**（nightly 已离峰）。
- **episode builder 的 ~60s 循环是主要争用风险点** —— 它跑在用户活跃时段。摘要阶段限并发（如复用 `WorkerConfig.vlm_concurrency` 的 Semaphore），或在检测到交互请求时让路。
- `deep_scan` fan-out 用 `Semaphore(2–3)` 对齐 `vlm_concurrency`（见 [thin-router-agent.md §子 agent](thin-router-agent.md)）。

---

## 7. Phase 落地映射（files-to-touch）

| Phase | 新增文件 | 改动文件 | 新测试 |
|---|---|---|---|
| **1** | `server/episode/segmenter.py`（边界纯函数）、`server/episode/builder.py`（builder） | `db/sqlite.py`（`episodes`+`episodes_fts`+`episode_id` ALTER + `upsert_episode`/`link_records_to_episode`/`fetch_records_for_segmentation`）、`bootstrap.py`（`_episode_builder_loop`+取消集合）、`common/config.py`（阈值/γ/cut-hour 段）、`settings`（`schema_version`） | `test_episode_segmenter.py`（flood/gap/drift 纯函数，哨兵日期）、`test_episode_builder.py`（tmp DB 真实分段 + UPSERT 幂等）、**SUM-invariant + golden-day** |
| **2** | `server/digest/rollup.py` | `db/sqlite.py`（`digests`+`upsert_digest`/`get_digest`/`get_digests_in_range`）、`bootstrap.py`（`_digest_scheduler`）、`agent/tools.py`（`get_digest`）、`report/generator.py`（改读 digest） | `test_digest_rollup.py`（metrics 与直接 GROUP BY 一致、UPSERT 不双计）、`test_agent_tools_digest.py` |
| **4** | `server/signals/detectors.py` | `db/sqlite.py`（`signals`+`insert_signal`/`get_signals`）、`episode/builder.py`（cues 保留）、`bootstrap.py`（信号挂 nightly）、worker 写真实 `confidence` | `test_signals_detectors.py`（合成 rework 序列触发、纯娱乐不触发、evidence 回链）、`test_agent_tools_signals.py` |

公共约定（贯穿）：业务时间锚 `ts_start` 禁用 `updated_at`；4AM 逻辑日；幂等键 + UPSERT；源指针回链；`tmp_path` 真实 SQLite + 哨兵假日期不污染"今天"；新测试文件名沿用 `tests/test_*.py`；不破 261 现存测试。

---

## 相关文件

- [architecture/analysis-worker.md](analysis-worker.md) —— 现状 worker 状态机（v1 快照）
- `src/timetrace/server/bootstrap.py` · `server/worker/loop.py` · `server/db/sqlite.py`
- [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) · [storage/pyramid-schema.md](../storage/pyramid-schema.md) · [thin-router-agent.md](thin-router-agent.md)
