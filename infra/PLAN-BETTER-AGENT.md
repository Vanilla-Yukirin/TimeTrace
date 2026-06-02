# PLAN · 更好的 Agent —— 分层记忆金字塔 + 薄路由器

> **状态**：设计草案（2026-06-02 起草），**未实装**。本文是激进重设计的总纲（single source of truth），三个支撑 spec 从这里链出。DDL / prompt 一律为 **sketch**（架构级），不是逐字 spec。
>
> **这不是 devlog**：放在 `infra/` wiki 是因为它是前瞻性活计划，会随推进更新；不进 `devlogs/`（那里只追加历史快照）。完工后可整理为 devlog 归档 + 翻新相关 wiki 子页。
>
> **支撑 spec**
> - [storage/pyramid-schema.md](storage/pyramid-schema.md) —— L2/L3/L4 三表 + signals 的 DDL sketch、迁移、幂等键、回填、删除传播
> - [architecture/episode-and-rollup-pipeline.md](architecture/episode-and-rollup-pipeline.md) —— 写时 builder 如何挂 worker 状态机与调度器
> - [architecture/thin-router-agent.md](architecture/thin-router-agent.md) —— agent 从 raw-reader 重塑为路由器 + 子 agent fallback

---

## **🗺️ 一页速读**

**今天的痛**：agent 在 **query-time 现读原始帧**。`search_activity` 一次最多吐 100 条未截断 `vlm_desc`（≈ 12–15K token），`get_recent_activity` 最多 200 条（≈ 28–32K token），`convo` 跨 6 轮不驱逐。两次大 read 就破了本地 Qwen3 的 ~50K 上下文。而一天 ~5000 帧 × ~100 token ≈ **500K token**，任何 read 路径都装不下一天。

**核心倒置**：把贵的「读」从 query-time 挪到 **write-time** 一次性摊销。数据建成金字塔，写入时从下往上逐层抬高，查询时 agent 从上往下按需下探。

```
            ┌─────────────────────────────────────────────┐
   L4 周/月  │  digests(grain=week/month)  叙事+metrics_json │  ~1/周 ~1/月
            ├─────────────────────────────────────────────┤
   L3 日    │  digests(grain=day) + signals  日叙事+派生信号 │  1 digest + 数条 signal /天
            ├─────────────────────────────────────────────┤
   L2 会话  │  episodes  一段"做某事"的摘要(~150 tok)+嵌入   │  30–80 /天   ← 承重层
            ├─────────────────────────────────────────────┤
   L1 帧义  │  analysis_results  vlm_desc + category + 嵌入  │  = L0
            ├─────────────────────────────────────────────┤
   L0 原始  │  records  时间轴单元(窗口/app/ts)             │  ~5000 /天（永不批量读）
            └─────────────────────────────────────────────┘
```

**薄路由器**：agent 不再是「吞数据的嘴」，而是 **route → retrieve → light-reason** 的路由器。给它一组按粒度/成本分层的工具，让它先分类问题、选最便宜够用的层、必要才下钻。罕见的「真得扫一大段原始帧」用 **子 agent map-reduce fallback**（隔离上下文，绝不进主 convo）。

**三个目标问题各走哪条路**：

| 问题 | 路由 | query-time LLM 读 token |
|---|---|---|
| 「我现在在做什么」 | 读**最新 1–3 条原始帧**（L0/L1，永远最新）+ 可选最近一条已闭合 episode | ~150 |
| 「这个月娱乐花了多少小时」 | `query_stats` 读月 digest 的 `metrics_json`（或求和日 digest） | **~0**（键值行，纯 SQL） |
| 「写代码时哪些坏习惯导致返工/回滚」 | `query_stats(signal_counts)` → 下钻被 flag 的少数 episode | ~700 |

没有一个目标问题会触达子 agent fallback。

---

## 1. 问题陈述与 token 数学

### 1.1 硬约束

- **数据规模**：~5000 records/天，每条 `vlm_desc` ~100+ token（中文截图描述，VLM 已经离线生成好了）。一天 ≈ **500K token**。
- **算力上限**：本地 LLM（Qwen3 经 LM Studio / OpenAI 兼容端点，跑在家用小主机 RTX 3080 20GB 上），实际可用上下文 **~50K**。
- **结论**：500K ≫ 50K。哪怕只想分析一天，原始帧也装不进上下文。这是不可回避的物理墙。

### 1.2 为什么"硬搬给子 agent 总结"不是答案

最初的直觉是「给主 agent 一个调子 agent 的工具，每次提问就把 50K 硬搬给子 agent 出个总结，主 agent 再据此回答」。这把**最贵的读放在了 query-time，每次提问都重做一遍** —— 正是 token 吞吐恐怖的根。子 agent fan-out 应当是**兜底**，不是主循环（见 [thin-router-agent.md §子 agent fallback](architecture/thin-router-agent.md)）。

### 1.3 倒置后的成本

| 阶段 | 发生时机 | LLM 调用/天 |
|---|---|---|
| L1 VLM describe | 写时（每帧，沉没成本，本来就要跑） | ~（变化帧），未来 dedup 后 ~1500–3000 |
| L2 episode 摘要 | 写时（每段闭合一次，读自己 20–100 帧 ≈ 2–10K token） | **~50**（30–80） |
| L3 日 digest | 写时（summary-of-~50-summaries） | **1** |
| L4 周/月 digest | 写时（summary-of-summaries） | **~1/7 + ~1/30**（可忽略/天） |
| **query-time** | 读时 | 三个目标问题 **≈ 0 额外 LLM 读 token** |

一天**新增聚合成本** ≈ ~50 段摘要 + 1 次日 rollup（外加本来就要跑的逐帧 VLM）。query-time 读成本 ≈ 0。这就是 500K → ~0 的倒置。

> 详细预算表与回填数学见 [pyramid-schema.md §回填与预算](storage/pyramid-schema.md)。

---

## 2. 现状基线（已核对的代码事实）

> 这一节纠正了几处对现状的常见误判，后续设计都锚定真实代码。源头审计见三个支撑 spec。

- **没有 `analysis_tasks` 表**。worker 队列就是 `analysis_results.status` 列状态机（`pending_vlm`/`processing_vlm`/`vlm_done`/`error_final`），用 `claim_next_task(kind)` 的 `UPDATE...RETURNING` 前缀 swap 领取，`reclaim_stale_tasks` 回收。`analysis_tasks` 这个名字只在过期的 CLAUDE.md / devlog 散文里出现。**金字塔的新队列态直接复用这套 claim/lease/reclaim，不发明新 job 框架。**
- **零聚合/rollup 基础设施**。`get_app_breakdown` / `get_category_stats` 每次都对原始 `records`+`analysis_results` 现算 SQL GROUP BY。没有 episode、没有 digest、没有派生信号表。
- **per-frame `text_embedding` 存在**（`analysis_results.text_embedding` BLOB，packed float32），但 `vector_search` 是**全表 numpy cosine 扫描**、**没有暴露成 agent 工具**。5000 行/天会让全表扫描退化 —— 所以语义检索的主面要**下沉到 episode 级**（数十向量/天），per-frame 嵌入降为 fallback。
- **6 类扁平分类**：`work 工作 / study 学习 / social 沟通 / entertainment 娱乐 / system 系统 / uncategorized 未分类`。id 集与 `server/vlm/client.py::_CATEGORY_IDS` 镜像、与 `SKILL.md` 手抄，必须保持同步。`category_suggested` / `confidence`（硬编 1.0）/ `decision_trace` 三列 DDL 有、worker 不写 —— **`confidence` 建议复活**用于 rollup 信任门控。
- **单/双进程共用 `bootstrap.serve()`**。`main.py`（单进程）与 `server/cli.py`（双进程 server）都调它。双进程下 client 是哑客户端、本地无 DB。**所有派生层只能 server 侧计算。**
- **三处手写工具清单的 drift 风险**：`agent/tools.py::TOOL_SCHEMAS`、`mcp_layer/server.py` 的手写 `@mcp.tool()` 闭包、`skills/timetrace/SKILL.md`。加新工具时若不折叠，drift 必然复发。

---

## 3. Prior-art 对比

我们调研了四个开源/商业活动记忆系统 + 一组「LLM 处理超上下文数据」的工程范式。**它们无一例外地拒绝「query-time 读原始帧」**。

| 系统 | 分层方式 | 写时摊销 | 语义检索 | TimeTrace 偷哪招 / 避哪坑 |
|---|---|---|---|---|
| **Dayflow**（Mac，SwiftUI，AI 后端可换） | 时间线 card = 一段"sitting"，默认**倾向合并**、时间是约束不是目标 | 所有贵 LLM read 在后台一次完成；recap 输出 token 上限 8192 | —（按天 scoped 取 card） | **偷**：card 合并规则、idle 段跳过 LLM、逻辑日 4AM–4AM、distractions[] 嵌在 work card 内。**坑**：LLM 发的相对时间戳易漂移（我们锚 `ts_start`）；merge-by-reprocess 会 thrash（我们 close-once 后冻结） |
| **screenpipe**（Rust，~16k★） | 事件驱动采集（切窗/点击/停顿/滚动才截），帧配 accessibility tree、拿不到才 OCR | 数据层与 agent 解耦；pipe 插件**从不直接碰 DB** | 本地可搜 DB + API | **偷**：变化触发采集（静态时 30→0.5 FPS，~95% 省）、结构化输出 generateObject+schema 让派生层字段可查、数据层/agent 解耦。**坑**：纯静态但有意义的工作（看视频/读文档）要 0.5 FPS 心跳兜底，否则时长少算 |
| **ActivityWatch**（无截图，纯 app/窗口/AFK） | event/bucket 模型，heartbeat 合并 | 摄入时就把分组算好（不是 per-query） | — | **偷**：duration 作为可 SUM 的存储列、heartbeat-coalesce（600 个 1s "还在 VSCode" 合成 1 个 600s 区间）、AFK/idle 作为 episode 边界。**坑**：只存 UTC 丢偏移导致 group-by-local-day 脆；noisy 标题（带时钟/计数）使精确相等 merge 失败爆事件量 → 标题归一化 |
| **Rewind / Recall**（端上时间线，海量历史） | 元数据先过滤，再碰向量/LLM | 增量索引；按时间窗检索 | index-first，top-k → 只送命中 | **偷**：metadata-first 检索、semantic_search 返回 top-k ID + 预算好的摘要（不返原始帧）、按 ID 引用。**坑**：嵌入仍会无限堆积 → per-frame 嵌入是"要老化/上卷"的层，不是"在其上做大规模查询"的层 |
| **范式**：RAPTOR / EM-LLM / MemGPT(Letta) / A-MEM / Anthropic 多 agent | 递归树摘要 / Bayesian-surprise 边界 / 分层记忆 / Zettelkasten 链接 / orchestrator-worker | 建树成本随文档线性、查询成本受 token 预算限 | 折叠树检索 | **偷**：RAPTOR「写时建树、查时受限」；EM-LLM 用嵌入 surprise 切边界（γ 旋钮）+ 两阶段检索（相似 + 时间邻接）；Anthropic「子 agent 是智能过滤器、返回 ≤2K」「effort-scaled fan-out」「多 agent ~15x token」；分层 merge 会**放大幻觉** → 每层保留源指针 |

**收敛出的统一原则**：把贵的 read 一次性放到 write-time，query-time 只做 route → retrieve → light-reason，外加一个**仅在真需要时**触发的子 agent map-reduce fallback。Dayflow / screenpipe / ActivityWatch / Rewind / RAPTOR / Anthropic 全部指向同一个形状。

---

## 4. 金字塔设计（L0 → L4 + signals）

> 完整 DDL sketch 见 [pyramid-schema.md](storage/pyramid-schema.md)；builder 接线见 [episode-and-rollup-pipeline.md](architecture/episode-and-rollup-pipeline.md)。这里只讲架构与取舍。

### 4.1 层栈

| 层 | 表 | 一行装什么 | 行数/天 | 谁来建 |
|---|---|---|---|---|
| **L0** 原始帧 | `records`（已存在） | 时间轴单元：`ts_start/ts_end`、app/process/window/url、`capture_reason`、`status` | ~5000 | capture |
| **L1** 帧级语义 | `analysis_results`（已存在）+ `records.episode_id`（新列） | `vlm_desc`(~100 tok)、`category_final`、`confidence`（复活）、`text_embedding` | = L0 | worker VLM 阶段 |
| **L2** 会话 episode | `episodes`（新） | 一段连贯 sitting：`ts_start/ts_end`、`day_local`、`primary_app`、`category`、`title`、`summary`(~150 tok)、`cues`、`frame_count`、`duration_s`、`summary_embedding` | **30–80** | episode builder（闭合时触发） |
| **L3** 日 digest + 信号 | `digests`(grain=day)（新）+ `signals`（新） | 日叙事(~300 tok) + `metrics_json`（按 category/app 秒数等，**纯 SQL，零 LLM**）+ N 条信号 | 1 + 数条 | nightly rollup |
| **L4** 周/月 | `digests`(grain=week/month)（新） | summary-of-summaries 叙事 + 上卷的 `metrics_json` | ~1/周 ~1/月 | 周/月 rollup |

**L2 是承重层**。它把 5000 帧坍缩成 30–80 段（60–150× 缩减），一段"今天的 episode 级扫描"轻松进 50K。**主语义面 = `episodes.summary_embedding`**（数十向量/天），per-frame 嵌入降为 drill-down fallback。

### 4.2 L2 episode 分段（确定性边界优先）

episode = 一段连贯的 sitting（Dayflow：默认倾向合并，时间是约束不是目标）。**一律锚定真实帧 `ts_start`**，绝不信 LLM 发的相对时间戳。

**v1 边界规则（任一触发就闭合当前段）**：

1. **Idle gap** —— 连续帧间隔 > `IDLE_GAP_S`（默认 300s，对齐现有 `_MAX_ORPHAN_BRIDGE_MS`）。先做 `flood` 式小 gap 容忍（ActivityWatch 借鉴），避免 2s alt-tab 把 2h 会话碎成 40 段。
2. **App-cluster 切换** —— `primary_app` 切换且短期不回。归一化分组键（剥掉实时时钟/标签计数）、URL 归一到 canonical 域名（否则 noisy 标题爆段数）。
3. **Max-duration cap** —— `MAX_EPISODE_S`（默认 3600s）强制闭合，避免马拉松变成一个不可摘要的大块。

**Phase 1b（flag，默认 OFF）—— 嵌入 topic-drift**：连续帧 `text_embedding` cosine 距离超过 `T = mean(window) + γ·std(window)`（EM-LLM Bayesian-surprise）。捕捉"同 app 不同任务"（VSCode 项目 A→B）。**γ 是整个设计最易翻车的旋钮**（边界调参是刀尖），所以承重层先用确定性边界跑通、对真实数据验证质量，再开 γ。segmenter 签名一开始就接受 drift 输入、只是 `enabled=False`，开启零返工。modularity/conductance 精修 pass 完全延后（Phase 6+，"质量不行再说"）。

**何时跑**：事件触发、不批量。`bootstrap.serve()` 的 TaskGroup 加一个 `_episode_builder_loop`（套 `_report_scheduler` 模板），~60s 扫一次"开放边界"，当边界规则触发且成员帧都 `vlm_done` 时**闭合**该段。**闭合后冻结**，永不重摘（Dayflow thrash 教训；我们有权威帧时间戳，不需要像 Dayflow 那样反复 reconcile）。**空闲快路**：全锁屏/无输入/pHash 相同的段，直接产 `category='system'`、`summary="空闲/锁屏"`，**不调 LLM**。

### 4.3 L3/L4 rollup（数字走 SQL，叙事才用 LLM）

日 rollup 喂当天的 **episode 摘要**（不是原始帧）：~50 × 150 tok ≈ 7.5K，进得去。`metrics_json` 全由**纯 SQL** 算（按 category/app 秒数、episode_count、active_seconds、top titles），复用现有时长 clamp 表达式 `SUM(MAX(0, COALESCE(ts_end,ts_start)-ts_start))`；LLM 只生成 ~300 tok 叙事，**有输出 token 上限**。周/月 = summary-of-summaries（喂 7 条日叙事 → 1 条周叙事），**每层回链源 ID** 防"摘要的摘要"放大幻觉。

**逻辑日 = 4AM–4AM（可配置）**，不是午夜 UTC。`day_local` 存 `TEXT 'YYYY-MM-DD'`，并保留源时区；周 `scope_key` 用 ISO-week（`'2026-W23'`），不混日历周以免 `UNIQUE(grain, scope_key)` 跨月撞键。

### 4.4 派生信号（"坏习惯/返工"的答案）

存为 `signals` 行、**写时算一次**，于是"什么坏习惯导致返工"是廉价的 `WHERE kind='rework'` 查询，永不实时扫月。

**v1 只做一处：rollup 内的批量序列检测**（rework / context-switch storm / focus-vs-distraction / late-night 都是**跨帧序列模式**，单帧触发不了；per-frame 阶段延后，YAGNI）。检测器读 **L2 `episodes.cues` + 成员帧文本**，因此 **`episodes.cues` 必须在写时保留 git-reset/undo/error 的字面线索**，让信号推理读 L2 而非重扫 L0。遵循 `_embed_and_save` 的 best-effort 纪律（catch+log、永不传播、不阻塞状态机）。基于**具体观测事件**（git reset / 重复编辑 / error-retry），不是软性的"distraction flag"。

| 信号 `kind` | 检测（读 L2 cues + L1 文本） | 存的指标 |
|---|---|---|
| `rework` / `forced_rollback` | `git reset/revert/--hard` 线索；同文件名跨 ≥N 非连续 episode 反复编辑；undo 风暴 | severity = 回滚次数；`evidence_json` = episode_ids + 线索串 |
| `context_switch_storm` | 每小时 category/app 转换数超阈 | switches/hour |
| `focus_block` vs `distraction` | 连续同类 run 长度；娱乐打断工作的 <5min 段（Dayflow distractions[]） | 专注分钟 / 分心分钟 |
| `late_night` | `ts_start` 落在夜间窗的 episode | count/duration |

`entertainment_time` **不是信号**，就是 `metrics_json` 里 `SUM(duration) WHERE category='entertainment'`。

> **⚠️ 6 类 taxonomy 对 entertainment/rework 不足**：扁平 6 类把所有休闲塞进一个桶，且 episode category 走多数投票 —— "边写代码边看 YouTube" 90 分钟会被判成单一类、丢掉重叠。**不扩 6 类**（它们与 VLM enum / SKILL.md 同步），而是在 **L2 引入 distraction/打断概念**（娱乐 <5min 打断工作段），让 agent 能区分「主类娱乐时长」（干净）vs「含打断的总休闲」（更全）。`rework` 没有类、是 `signals.kind`，但它依赖 git/undo 线索先进了 `vlm_desc` —— **L1 describe prompt 可能需轻量提示"记录版本控制/终端/错误对话框文本"**，否则 L2 的 `cues` 抓不到不存在的东西。

---

## 5. Thin-router agent 设计

> 完整工具 JSON schema、路由 persona、前端协同清单见 [thin-router-agent.md](architecture/thin-router-agent.md)。

### 5.1 工具成本阶梯

`AgentRunner` 循环不变（`max_iterations=6`、最后一轮 drop tools 逼出散文）。智能搬进**系统 prompt** + **更便宜的工具**。6 个工具替换 5 个 raw-reader，全部 token-bounded **by construction**，全在 `server/agent/tools.py`（`_IMPLS`+`TOOL_SCHEMAS`+`dispatch_tool`）单一源：

```
TIER 0  query_stats      纯 SQL over digests.metrics_json / 现算 GROUP BY    ~0 LLM tok
TIER 1  get_digest       1–4 条预算好的 digest（叙事+metrics）              ~0.5–4K
TIER 1  get_episodes     窗口内 episode 摘要（绝不返原始 vlm_desc）          上限 ~6K
TIER 2  search_episodes  对小 episode-摘要索引做语义/FTS（向量下沉到 L2）    上限 ~6K
TIER 3  get_raw          逃生口：窄窗原始帧，按 token 估算硬上限             硬 cap
WRITE   apply_label      唯一写权限（不变：category_final + feedback）       trivial
```

系统 prompt 强制：**先试能答的最便宜层；上一层不够才下探；`get_raw` 需理由、绝不当第一刀。**

- `query_stats`：所有"多少小时/几次/哪个 app"问题的 fast-path，命中 `metrics_json` 键值行（零 LLM），uncached 窗口才现算 GROUP BY over episodes。**绝不碰 `vlm_desc`。**
- 旧名 `search_activity` / `get_recent_activity` **保留为薄 shim**，分别委托 `search_episodes` / `get_episodes`（不硬改名 —— SKILL.md / MCP / 前端 label 都按名硬引用，硬改名会逼出多面破坏）；旧名加一行 deprecation docstring。`get_raw` 是真正新增的预算化逃生口。
- 跨层组合（"this week" 缝合）：`query_stats` 必须**读时跨层组合** —— 每个子区间用最高的已 finalized rollup，未上卷的尾巴 fall back 到现算 episode（再到帧），用 `computed_through_ts` watermark 缝合，返回 `source:"mixed"` + watermark 让 agent 加 caveat。这是最易静默多算/少算的地方。

### 5.2 三个 worked trace

- **「我现在在做什么」**：路由 → 读最新 1–3 条原始帧（**不是 episode** —— 当前 sitting 可能还没闭合、还没 episode 行；最新帧可能还 `pending_vlm` 没描述，则用 app/窗口元数据 + 最近一条已描述帧）。1 调用，~150 token。
- **「这个月娱乐多少小时」**：路由 → `query_stats(seconds_by_category, this_month, filter=entertainment)` → 读月 digest `metrics_json` → "约 43 小时（占活跃 18%）"。**1 调用，~0 LLM 读 token，没碰任何帧。**
- **「写代码哪些坏习惯导致返工」**：`query_stats(signal_counts, this_month)` → 下钻被 flag 的 2–3 个 rework 窗口（`signals.evidence_json` 带 episode_id，或 `search_episodes("git revert reset rollback")`）→ light-reason ~10 条 episode 摘要 → "回滚多在 23:00 后、常跟在一次 context_switch_storm 之后……"。~3 廉价调用，**永不扫月**。

### 5.3 token 预算双重防御

1. **per-tool 输出 cap（主防御）**：每个 read 工具按 **token 估算**（不是行数）cap 自己的输出。没有单个工具能返回 > ~10K，于是 `convo` = 系统 prompt + 几个小结果，稳在 50K 内。
2. **runner turn-budget gate（次防御）**：累计 `convo` 已用估算 token，若 `cumulative + 下一工具上限 > _TURN_BUDGET`（~40K），runner **拒绝再 read、提前逼出散文**（复用 iteration-exhaustion 的 drop-tools 机制），发 `budget` SSE 事件让 UI 显示"已达上下文预算，据已有上下文作答"。

---

## 6. 子 agent map-reduce fallback

**这是兜底，不是主循环。** 多 agent ~15x token，仅保留给"真得扫一段 50K 装不下、且金字塔层答不了"的罕见 query。三个目标问题都**到不了这里**。

- **触发**：不靠 agent 推理决定，而是一个新工具 `deep_scan(start,end,question)` **内部按窗口 token 估算**决定：`estimated_raw_tokens(window) > _SINGLE_PASS_BUDGET(~40K)` 才 fan-out，否则单次 `get_raw`。
- **fan-out/reduce**（Anthropic orchestrator-worker，隔离上下文）：把窗口切成 **N 个非重叠**时间片（每片原始帧 ≈ ~40K）；`asyncio.gather` + `Semaphore(2–3)`（对齐 `WorkerConfig.vlm_concurrency`，别打爆单个 3080 端点）并发 map；每个 map = **单次受约束 `chat.completions.create(tools=None)`**（**不是**完整 `AgentRunner`，避免 N×6 轮开销 + N 个无界 convo），返回 ≤2K token 压缩 finding（带源 ID）；orchestrator concat N 个 finding（N×~2K，7 天 ≈ 14K 仍 < 50K）reduce。
- **实现**：`deep_scan` 是 `_IMPLS` 里的普通工具，`dispatch_tool` 像别的工具一样 await 它，父循环不变；子 summarizer 复用 runner 持有的同一 `VLMConfig`/openai client。它不返回 `items` 故不 bump `records_consulted`，另设 telemetry 字段 + `subagent` SSE 事件让这罕见的 30s 停顿可见。失败返回部分 reduce + `{degraded:true}`，不抛（守 `dispatch_tool` 永不抛契约）。

---

## 7. Schema 变更概览

> 完整 DDL sketch、索引、幂等键、回填见 [pyramid-schema.md](storage/pyramid-schema.md)。

- 新表 `episodes` / `digests` / `signals` + `episodes_fts`，全走 `CREATE...IF NOT EXISTS` 进 `_SCHEMA` literal（`init()` 的 `executescript` 自动建）。
- `records.episode_id` 加列走 `_migrate()` 的 `PRAGMA table_info` 守卫 ALTER（与 phash / client_record_id / text_embedding 三次先例同形）。
- **Phase 1 顺手引入 `schema_version`**（`settings` 里一个整数戳，不是 Alembic）：未来 ALTER 按版本号 gate 的廉价保险，不重写已有迁移。
- **幂等**：episode 用确定性 key、`digests` 用 `UNIQUE(grain, scope_key)` + UPSERT，应对调度重叠 / crash replay 的 at-least-once（像 outbox 一样可能重放）。`digests.computed_through_ts` 是增量建日的 watermark。
- `digests` 与现有 `reports` 表**分工**：`digests` = 结构化可查的源；`reports` = 渲染层（HTML），改为**从 `digests` 读**而非重扫原始帧。

---

## 8. 写时管线挂载

> 完整接线见 [episode-and-rollup-pipeline.md](architecture/episode-and-rollup-pipeline.md)。

- 新 builder 都是 `bootstrap.serve()` TaskGroup 里 `tg.create_task(..., name="episode_builder")`，名字加进 `_watch_quit` 的取消集合。**单/双进程同时生效**（共用 `serve()`），client 保持哑、派生层纯 server 侧。
- 新队列态 `pending_episode` / `pending_digest` 复用 `claim_next_task(kind)` / `reclaim_stale_tasks` / backoff，**不建新 job 框架**。
- signal 检测器套 `_embed_and_save` 的 best-effort 侧阶段范式（catch+log，永不传播）。
- **降级契约**（关键）：VLM 挂了，episode 照常**闭合**（边界+时长+类投票都不需要 LLM），只让 `summary`/`summary_embedding` 等回填；否则一次 VLM 宕机会冻住整个金字塔、"这个月多少小时"悄悄停更。`search_episodes` 在 embserver 挂时降级到 FTS5-only（RRF 已能容一路缺失）。**`query_stats`/`get_digest` 的 metrics 既不依赖 VLM 也不依赖 embserver（纯 SQL）—— 这是关键韧性属性**：模型全宕时廉价聚合路径照常工作。调度器的"无 VLM 退出"守卫**不得**禁用纯 metrics 的 rollup。
- **并发预算**：episode builder / digest rollup / `_embed_and_save` / live VLM worker / `deep_scan` fan-out 都打同一个 3080 端点。给**交互 agent 优先级**，后台 rollup 低并发/夜间跑（nightly 已离峰；~60s 的 episode builder 是争用风险点）。

---

## 9. 评估：怎么知道它是对的

> 这是三个 lens 收敛时一起漏掉、但**最关键**的一节。它们都优化成本、默认正确性会跟来 —— 不会。

- **数值层 by-construction 可查 —— 作为硬 CI 不变式**：某天 `SUM(episodes.duration_s)` == 当天原始帧 `SUM(MAX(0, ts_end-ts_start))`（含 idle-gap 容忍）；`digest.metrics_json[cat]` == 现算 GROUP BY over episodes。廉价、确定，专抓 at-least-once rollup 重放的双计。
- **叙事层要 grounding 检查，不止 vibe**：每条 digest/episode 摘要保留源 ID；加轻量断言——摘要里点名的 app/类必须真出现在成员里（cheap string-overlap，防编造内容）。LLM-as-judge 留给人工抽查，不进 CI。
- **golden-day 哨兵 fixture**：一个手标的合成日（哨兵日期、tmp 隔离目录，不污染真实"今天"），已知 episode 边界 + 已知各类小时数，端到端断言。这是 γ / prompt 变更时的回归锚。

---

## 10. 分阶段计划（每阶段独立可交付）

> 每阶段的 files-to-touch / 新测试 / 验收标准详见三个支撑 spec。

| Phase | 目标 | 关键交付 | 验收锚 |
|---|---|---|---|
| **1** ← **最高杠杆第一步** | L2 episodes（承重层） | `episode/segmenter.py`（确定性边界）+ `episode/builder.py` + `episodes` 表 + `_episode_builder_loop` + **SUM-invariant 测试 + golden-day fixture** + `schema_version` | tmp 合成日跑完 episodes 行数 ≈ 段数（数十非数千）、`frame_count` 求和==覆盖帧数、重复跑幂等、SUM-invariant 绿、261 现存测试全绿 |
| **1b** | 嵌入 drift 边界 | segmenter 加 γ-drift 分支（flag 默认 OFF） | 开 flag 后"同 app 不同任务"被切开，关 flag 行为不变 |
| **2** | 日/周/月 digest | `digest/rollup.py` + `digests` 表 + `_digest_scheduler` + `get_digest` 工具 + reports 改读 digest | 合成月 `get_digest('month',...)` 的 `metrics_json` entertainment 秒数 == 跨 episode SQL 求和；该路径**不调任何 VLM/LLM** |
| **3** | thin-router | 6 工具分层 + `search_episodes`（FTS+向量下沉 L2+RRF）+ 旧名 shim + **MCP 折叠注册** + **SKILL.md 同步测试** + 前端事件/label 协同 | 跨 3 月哨兵数据"找那次调试"被召回；单 turn token < 5K；MCP 工具集 == web 工具集（测试强制） |
| **4** | 派生信号 | `signals/detectors.py` + `signals` 表 + `get_signals` 工具 + 复活 `confidence` + episode summary 保留 git/undo 线索 | 合成"改代码→`git reset --hard`→重改"序列触发 rework signal、`evidence_json` 回链 episode_id；该问答不扫原始帧 |
| **5** | 子 agent fallback | `agent/mapreduce.py` + `get_raw`（token-budget cap）+ `deep_scan` + 可选 `subagent` SSE | 故意绕层的"逐帧找窗口"query：主 convo 从不持有原始帧、各子 agent 返压缩结果、orchestrator 合成；fan-out 仅在 deep-scan tier 触发 |

**冷启回填贯穿全程**：仿 `_backfill_embeddings` 一次性 sweep（poison-row skip + endpoint-down abort），**newest-day-first**（近期查询最快可用）、幂等可重入、限速（token bucket，别在用户活跃时打满 3080）。回填期间工具暴露 `coverage`/`computed_through_ts`，让 agent 说"我只统计到 X 为止"，而不是返回空/错聚合。

---

## 11. 拓扑安全性

- 所有 builder 挂 `bootstrap.serve()` TaskGroup，`main.py` 与 `server/cli.py` 共用 → **单/双进程自动同时生效**；client 哑（无 DB），派生层纯 server 侧。不动 `common/protocol.py` / ingest 路由。
- 全部在 `feature/refactor-split` 上做；部署走 `gh workflow run deploy.yml --ref feature/refactor-split`，**`origin/main` legacy 不动**直到重构完工。新表对 Linux / 未来 Postgres seam 无害（`Database` 别名 P5 升 Protocol 时一并带上）。

---

## 12. 隐私（承接 P4，明确而非展开）

> 三个 lens 全部漏了隐私 —— 但本项目是字面意义的录屏记忆层，且 roadmap 的 **P4 = 客户端隐私管线**。新层**聚合并浓缩了敏感数据**。

- episodes / digests / signals 继承与原始帧相同的 **local-only + `require_principal`** 姿态，**绝不**进未鉴权的 `/skill` 或任何 frontend-facing 路由。
- `summary` 是 LLM 生成的明文 prose、且嵌进了 `summary_embedding`（事后更难擦除）；`signals.evidence_json`（git 线索、文件名）在聚合态比单帧**更敏感**。为 P4 标注。
- **删除传播**：帧/截图 soft-delete（`deleted_at`）后，摘要过它的 episode/digest/signal 行变 stale、可能仍留被删内容。定义传播故事（至少：TODO + `source_version` bump 触发重 rollup 丢弃它）。本节是**承认**，实现可延后。

---

## 13. 风险与开放问题

### 风险排名

1. **承重层质量级联（最高）**：episode 切错（γ 刀尖）或摘要幻觉，上面每层 digest/signal 都继承且无廉价察觉途径。→ 缓解：确定性边界先行、SUM-invariant 硬测试、每层留源 ID、golden-day fixture。
2. **stale-doc / drift 复发**：已有三处手写工具清单 + 过期 CLAUDE.md stub 表。加 5–6 工具不折叠注册就重造 drift。→ 缓解：MCP 从 `TOOL_SCHEMAS` 生成 + SKILL.md 覆盖测试，**在 Phase 3 内做、不拖"以后"**。
3. **后台 rollup 抢占交互路径（单 3080）**：用户在 nightly rollup 时问"现在在做什么"会变慢。→ 交互优先级 + 后台低并发。
4. **跨层查询误算**（"this week" 缝合）：最坏的 bug——貌似合理的错数字。→ `computed_through_ts` watermark 缝合 + `source:"mixed"`。
5. **回填打满主机 / 非幂等重放双计小时**。→ 幂等键 + 限速 + newest-first。
6. **VLM/embserver 宕机冻住金字塔**。→ episode 无摘要也照常闭合；纯 metrics 路径不依赖模型。
7. **隐私面扩张未被承认**：敏感 prose 的嵌入事后难擦。→ §12。

### 开放问题

- γ 默认值与开启时机；distraction 阈值（<5min？）；`deep_scan` 触发的 token 阈；周 `scope_key` 口径（ISO-week 已定）。
- `hours_back_hint` 死参数：两个 caller 都传、`run()` 从不读。**决定：端到端删除**（route 字段 + 前端 `opts.hoursBack` + runner 参数），因为路由现在用显式工具参数定窗。

> **⚠️ 过期标注待补**：本计划落地后，`CLAUDE.md` 的"仍然存在的桩代码"表里 `mcp_layer/tools.py` 桩、`infra/architecture/mcp-layer.md` 的"4 tools"散文需按 `## **⚠️ 一句话标题**` 约定加过期标注或指向纠正。
