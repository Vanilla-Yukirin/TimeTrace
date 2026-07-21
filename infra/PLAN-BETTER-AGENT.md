# PLAN · 更好的 Agent —— 分层记忆金字塔 + 薄路由器

> **状态（2026-07-20 校准）**：**部分实装**。固定时间窗 `summaries`、指标级联、source_hash、rollup/narrate loop、`query_stats` 与 `search_summaries` 已上线；signals/episode、完整 thin-router、deep scan/子 agent fallback 仍是设计。本文保留原始设计推导，历史工具数和阶段描述不代表当前代码；当前待办以 [`devlogs/PLAN.md`](../devlogs/PLAN.md) 为准。
>
> **这不是 devlog**：放在 `infra/` wiki 是因为它是前瞻性活计划，会随推进更新；不进 `devlogs/`（那里只追加历史快照）。完工后可整理为 devlog 归档 + 翻新相关 wiki 子页。
>
> **设计演进**：承重层由早期的"语义 episode 变长切分"改为"**固定时间窗 tumbling 级联**"（5min→1h→6h→天→周），绕掉边界检测（γ）这个最大质量风险点；并新增**自描述结果**（每个工具结果自带 coverage / 压缩率 / 下钻提示 / next_tools）与 **MCP 多入口引导**。
>
> **支撑 spec**
> - [storage/pyramid-schema.md](storage/pyramid-schema.md) —— 统一 `summaries` 级联表 + signals 的 DDL sketch、压缩率/下钻提示/脱敏字段、迁移、幂等键、回填、删除传播
> - [architecture/episode-and-rollup-pipeline.md](architecture/episode-and-rollup-pipeline.md) —— 写时级联 builder 如何挂 worker 状态机与调度器
> - [architecture/thin-router-agent.md](architecture/thin-router-agent.md) —— agent 重塑为路由器 + 自描述结果信封 + MCP 引导 + 子 agent fallback

---

## **🗺️ 一页速读**

**今天的痛**：agent 在 **query-time 现读原始帧**。`search_activity` 一次最多吐 100 条未截断 `vlm_desc`（≈ 12–15K token），`get_recent_activity` 最多 200 条（≈ 28–32K token），`convo` 跨 6 轮不驱逐。两次大 read 就破了本地 Qwen3 的 ~50K 上下文。而一天 ~5000 帧 × ~100 token ≈ **500K token**，任何 read 路径都装不下一天。

**核心倒置**：把贵的「读」从 query-time 挪到 **write-time** 一次性摊销。数据建成**固定时间窗级联**金字塔，写入时从下往上逐层 summary-of-summaries 抬高（空窗跳过），查询时 agent 从上往下按需下探。一张自相似的 `summaries` 表用 `grain` 区分层级：

```
            ┌────────────────────────────────────────────────────────┐
  week      │  summaries(grain=week)   summary-of-7-days + metrics     │  ~1/周
            ├────────────────────────────────────────────────────────┤
  day       │  summaries(grain=day)    + signals  日叙事+派生信号       │  1/天
            ├────────────────────────────────────────────────────────┤
  6h        │  summaries(grain=6h)     summary-of-6×1h                 │  ~4/天
            ├────────────────────────────────────────────────────────┤
  1h        │  summaries(grain=1h)     summary-of-~12×5min             │  ~10–14/天
            ├────────────────────────────────────────────────────────┤
  5min ←承重 │  summaries(grain=5min)   ~30 帧→1 段摘要+压缩率+下钻提示   │  ~100–150/天
            ├────────────────────────────────────────────────────────┤
  L1 帧义   │  analysis_results  vlm_desc + category + 嵌入            │  = L0
            ├────────────────────────────────────────────────────────┤
  L0 原始   │  records  时间轴单元(窗口/app/ts)，窗由 ts_start 直接算    │  ~5000/天（永不批量读）
            └────────────────────────────────────────────────────────┘
```

每层摘要都**脱敏**（`redacted`）、按**严格 schema**（自然语言描述+评价 + 结构化列表）、并自带**压缩率 + 下钻提示**（"下钻能多得到什么/无新结论"）作为信息气味。

**薄路由器 + 自描述结果**：agent 不再是「吞数据的嘴」，而是 **route → retrieve → light-reason** 的路由器。给它一组按 grain/成本分层的工具，**每个工具结果自带信封**（盖到哪 coverage / 压缩率 / 下钻提示 / 下一步调谁 next_tools）牵着它走。罕见的「真得扫一大段原始帧」用 **子 agent map-reduce fallback**（隔离上下文，绝不进主 convo）。

**三个目标问题各走哪条路**：

| 问题 | 路由 | query-time LLM 读 token |
|---|---|---|
| 「我现在在做什么」 | 读**最新 1–3 条原始帧**（L0/L1，永远最新；当前窗未必已闭合成摘要） | ~150 |
| 「这个月娱乐花了多少小时」 | `query_stats` 读 `summaries(day/week).metrics_json`（纯 SQL 求和） | **~0**（键值行，纯 SQL） |
| 「写代码时哪些坏习惯导致返工/回滚」 | `query_stats(signal_counts)` → 按 `signals.evidence_json` 下钻被 flag 的少数窗摘要 | ~700 |

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
| `5min` 摘要 | 写时（每活跃窗一次，读自己 ~30 帧；空窗跳过） | **~100–150** |
| `1h`/`6h` 摘要 | 写时（summary-of-summaries，输入是下层摘要、极小） | **~14 + ~4** |
| `day`/`week` 摘要 | 写时（summary-of-summaries） | **1 + ~1/7**（可忽略/天） |
| **query-time** | 读时 | 三个目标问题 **≈ 0 额外 LLM 读 token** |

一天**新增聚合成本** ≈ ~120 个廉价小摘要调用（外加本来就要跑的逐帧 VLM）。比早期 episode 方案（~50）多，但每个 `5min` 调用输入极小、**完全确定性、无 γ 边界风险**，后台/夜间在 3080 上跑得动 —— 用"多一些廉价确定的小调用"换"砍掉最大质量风险点"。query-time 读成本 ≈ 0，这就是 500K → ~0 的倒置。

> 详细预算表与回填数学见 [pyramid-schema.md §回填与预算](storage/pyramid-schema.md)。

---

## 2. 现状基线（已核对的代码事实）

> 这一节纠正了几处对现状的常见误判，后续设计都锚定真实代码。源头审计见三个支撑 spec。

- **没有 `analysis_tasks` 表**。worker 队列就是 `analysis_results.status` 列状态机（`pending_vlm`/`processing_vlm`/`vlm_done`/`error_final`），用 `claim_next_task(kind)` 的 `UPDATE...RETURNING` 前缀 swap 领取，`reclaim_stale_tasks` 回收。`analysis_tasks` 这个名字只在过期的 CLAUDE.md / devlog 散文里出现。**金字塔的新队列态直接复用这套 claim/lease/reclaim，不发明新 job 框架。**
- **零聚合/rollup 基础设施**。`get_app_breakdown` / `get_category_stats` 每次都对原始 `records`+`analysis_results` 现算 SQL GROUP BY。没有任何分层摘要表、没有聚合/digest 表、没有派生信号表。
- **per-frame `text_embedding` 存在**（`analysis_results.text_embedding` BLOB，packed float32），但 `vector_search` 是**全表 numpy cosine 扫描**、**没有暴露成 agent 工具**。5000 行/天会让全表扫描退化 —— 所以语义检索的主面要**下沉到摘要级 `summaries.summary_embedding`**（数百向量/天），per-frame 嵌入降为 fallback。
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

## 4. 金字塔设计（时间窗级联 + signals）

> 完整 DDL sketch 见 [pyramid-schema.md](storage/pyramid-schema.md)；builder 接线见 [episode-and-rollup-pipeline.md](architecture/episode-and-rollup-pipeline.md)。这里只讲架构与取舍。

### 4.1 层栈（一张自相似 `summaries` 级联表）

| 层 grain | 表 | 一行装什么 | 行数/天 | 谁来建 |
|---|---|---|---|---|
| **L0** 原始帧 | `records`（已存在） | 时间轴单元：`ts_start/ts_end`、app/process/window/url、`status` | ~5000 | capture |
| **L1** 帧级语义 | `analysis_results`（已存在） | `vlm_desc`(~100 tok)、`category_final`、`confidence`（复活）、`text_embedding` | = L0 | worker VLM 阶段 |
| **`5min`** ← 承重 | `summaries`(新) | ~30 帧 → 1 段：`description`+`evaluation`+`body_json`、`cues`、压缩率、`drill_down_hint`、`redacted`、`summary_embedding` | ~100–150 | 级联 builder |
| **`1h`** | `summaries` | summary-of-~12×5min + `metrics_json` | ~10–14 | 级联 builder |
| **`6h`** | `summaries` | summary-of-6×1h | ~4 | 级联 builder |
| **`day`** + 信号 | `summaries`(grain=day) + `signals`(新) | 日叙事 + `metrics_json`（**纯 SQL，零 LLM**）+ N 条信号 | 1 + 数条 | nightly 级联 + signal detector |
| **`week`** | `summaries`(grain=week) | summary-of-7-days + 上卷 `metrics_json` | ~1/周 | 周 rollup |

**`5min` 是承重层**。它把 5000 帧坍缩成 ~100–150 段（确定性、固定窗）。再往上每层是 summary-of-summaries。**主语义面 = `summaries.summary_embedding`**（数百向量/天，非 5000），per-frame 嵌入降为 drill-down fallback。**窗口由 `ts_start` 直接算出（无 `episode_id` 外键、无该列迁移）。**

### 4.2 为什么改用固定时间窗（而非语义 episode）

早期方案用"语义 episode 变长切分"做承重层，边界靠 idle-gap + app 切换 +（Phase 1b）嵌入 topic-drift（γ surprise）。**问题**：γ 是整个设计最易翻车的旋钮（"边界调参是刀尖"），而承重层质量会**级联**到上面每一层 digest/signal —— 一旦切碎或切粘，全盘皆坏。

**固定时间窗 tumbling 级联**直接绕掉它：窗口是死的时间格（`5min`/`1h`/...），**确定性、无调参、天然幂等（窗即 key）、增量天然**。代价是 `5min` 块语义上不如 episode "一段任务"齐整，但**往上 `1h`/`6h`/`day` 再 summary 一次就重新粘合了**。"episode（一次 sitting）"降级为 `5min` 层上的**可选派生分组**（按 app/标题连续性，确定性 group-by，非地基），见 [pipeline §6](architecture/episode-and-rollup-pipeline.md)。

边界规则全部消失，取而代之是：**窗对齐到 4AM 逻辑日**、**空窗（睡眠/离开）不建行**、**闭合后冻结**（除非 `source_version` bump）、**空闲快路**（全锁屏窗直接产 "空闲/锁屏" 不调 LLM）。

### 4.3 rollup（数字走 SQL，叙事才用 LLM）

`5min` 窗喂自己 ~30 帧 `vlm_desc`（~3K tok→~150 tok）；高层喂下层那几条摘要（输入极小）。`metrics_json`（day/week 必有）全由**纯 SQL** 算（复用 `_clamped_dur_sql()` —— 含超 5min 封顶段），LLM 只写 `description`/`evaluation`，**有输出 token 上限**。每层 `body_json.child_scope_keys` 回链下层，防"摘要的摘要"放大幻觉。

**逻辑日 = 4AM–4AM（可配置）**；`day_local` 存 `TEXT 'YYYY-MM-DD'` 并保留源时区；`scope_key` 确定性（`5min`→`'…T18:25'`，`week`→ ISO-week `'2026-W23'`，不混日历周以免 `UNIQUE(grain,scope_key)` 跨月撞键）。

### 4.4 信息气味：压缩率 + 下钻提示（写时算，引导是否下探）

每条摘要写时算两个字段，让读它的 agent（尤其外部 MCP agent）判断要不要往下挖：

- **`compression_ratio = out/src`**（按字符数 /3.5 估，**零 LLM、确定性**）。越小=丢得越多=下钻**可能**越有料。
- **`drill_down_hint`**（写时预计算一行）：压缩率是会骗人的 proxy（30 帧"发呆"压成一句也是高压缩率，下钻啥也没有），所以配一句具体的"下钻可得/不可得什么"修正它，**主要由确定性统计得出**（对比本层 prose 省略了下层 `body_json` 的哪些字段）。

读取规则进工具 hint（见 §5b）：压缩率高且 `drill_down_hint` 列出你要的字段 → 下钻；说"无新结论" → 别浪费调用。

### 4.5 派生信号（"坏习惯/返工"的答案）

存为 `signals` 行、**写时算一次**，于是"什么坏习惯导致返工"是廉价的 `WHERE kind='rework'` 查询，永不实时扫月。

**v1 只做一处：`day` rollup 内的批量序列检测**（rework / context-switch storm / focus-vs-distraction / late-night 都是**跨帧序列模式**，单帧触发不了；per-frame 阶段 YAGNI 延后）。检测器读 **`summaries.body_json.cues` + 必要时 L1 文本**，因此 **摘要写时必须保留 git-reset/undo/error 字面线索**到 `cues`，让信号推理读摘要而非重扫 L0。遵循 `_embed_and_save` best-effort 纪律。基于**具体观测事件**（git reset / 重复编辑 / error-retry），不是软性 "distraction flag"。

| 信号 `kind` | 检测（读 cues + L1 文本） | 存的指标 |
|---|---|---|
| `rework` / `forced_rollback` | `git reset/revert/--hard` 线索；同文件名跨多个非连续窗反复编辑；undo 风暴 | severity = 回滚次数；`evidence_json` = scope_keys + 线索串 |
| `context_switch_storm` | 每小时 `metrics_json.switch_count` 超阈 | switches/hour |
| `focus_block` vs `distraction` | 连续同类窗 run 长度；娱乐打断工作的 <5min 窗 | 专注分钟 / 分心分钟 |
| `late_night` | `window_start` 落夜间窗 | count/duration |

`entertainment_time` **不是信号**，就是 `metrics_json.cat_seconds.entertainment`。

> **⚠️ 6 类 taxonomy 对 entertainment/rework 不足**：扁平 6 类把所有休闲塞进一个桶，且窗 category 走多数投票 —— "边写代码边看 YouTube" 会被判成单一类、丢掉重叠。**不扩 6 类**（它们与 VLM enum / SKILL.md 同步），而是在摘要层引入 **distraction/打断概念**（娱乐 <5min 打断工作窗），让 agent 区分「主类娱乐时长」（干净）vs「含打断的总休闲」（更全）。`rework` 没有类、是 `signals.kind`，但它依赖 git/undo 线索先进了 `vlm_desc` —— **L1 describe prompt 可能需轻量提示"记录版本控制/终端/错误对话框文本"**，否则 `cues` 抓不到不存在的东西。

---

## 5. Thin-router agent 设计

> 完整工具 JSON schema、路由 persona、前端协同清单见 [thin-router-agent.md](architecture/thin-router-agent.md)。

### 5.1 工具成本阶梯

`AgentRunner` 循环不变（`max_iterations=6`、最后一轮 drop tools 逼出散文）。智能搬进**系统 prompt** + **更便宜的工具**。6 个工具替换 5 个 raw-reader，全部 token-bounded **by construction**，全在 `server/agent/tools.py`（`_IMPLS`+`TOOL_SCHEMAS`+`dispatch_tool`）单一源：

```
TIER 0  query_stats       纯 SQL over summaries(day/week).metrics_json / 现算    ~0 LLM tok
TIER 1  get_summary       指定 grain 的级联摘要（week→day→6h→1h→5min 由粗到细）  by-construction 限界
TIER 2  search_summaries  对小摘要索引做语义/FTS（向量下沉到摘要级）            上限 ~6K
TIER 3  get_raw           逃生口：窄窗原始帧（未脱敏，鉴权后），token 硬上限     硬 cap
WRITE   apply_label       唯一写权限（不变：category_final + feedback）          trivial
```

系统 prompt 强制：**先试能答的最便宜层；grain 由粗到细下探；`get_raw` 需理由、绝不当第一刀。**

- `query_stats`：所有"多少小时/几次/哪个 app"问题的 fast-path，命中 `summaries(day/week).metrics_json` 键值行（零 LLM），uncached 窗口才现算。**绝不碰 `vlm_desc`。**
- `get_summary(grain, A, B)`：一个工具吃 `grain` 读任意层。"总结我这周/这天" = `get_summary('week'|'day')` 一刀；要细节按结果里的 `drill_down_hint` 换 `'1h'`/`'5min'`。
- 旧名 `search_activity` / `get_recent_activity` / `get_digest` / `get_app_breakdown` / `get_category_stats` **保留为薄 shim** 委托新工具（不硬改名 —— SKILL.md / MCP / 前端 label 都按名硬引用，硬改名会逼出多面破坏）；旧名加一行 deprecation docstring。`get_raw` 是真正新增的逃生口。
- 跨层组合（"this week" 缝合）：`query_stats` 必须**读时跨层组合** —— 每个子区间用最高的已 finalized grain，未上卷尾巴 fall back 低 grain（再到帧），用 `computed_through_ts` watermark 缝合，返回 `source:"mixed"` + watermark 让 agent 加 caveat。这是最易静默多算/少算的地方。

### 5.2 三个 worked trace

- **「我现在在做什么」**：路由 → 读最新 1–3 条原始帧（**不是摘要** —— 当前窗可能还没闭合；最新帧可能还 `pending_vlm` 没描述，则用 app/窗口元数据 + 最近一条已描述帧）。1 调用，~150 token。
- **「这个月娱乐多少小时」**：路由 → `query_stats(seconds_by_category, this_month, filter=entertainment)` → 读 `summaries(day/week).metrics_json` → "约 43 小时（占活跃 18%）"。**1 调用，~0 LLM 读 token，没碰任何帧。**
- **「写代码哪些坏习惯导致返工」**：`query_stats(signal_counts, this_month)` → 按 `signals.evidence_json` 的 scope_key 用 `get_summary('1h', 那几段)`（写时 `cues` 已留 git/undo 线索）→ light-reason ~10 条窗摘要 → "回滚多在 23:00 后、常跟在一次 context_switch_storm 之后……"。~3 廉价调用，**永不扫月**。

### 5.3 token 预算双重防御

1. **per-tool 输出 cap（主防御）**：每个 read 工具按 **token 估算**（不是行数）cap 自己的输出。没有单个工具能返回 > ~10K，于是 `convo` = 系统 prompt + 几个小结果，稳在 50K 内。
2. **runner turn-budget gate（次防御）**：累计 `convo` 已用估算 token，若 `cumulative + 下一工具上限 > _TURN_BUDGET`（~40K），runner **拒绝再 read、提前逼出散文**（复用 iteration-exhaustion 的 drop-tools 机制），发 `budget` SSE 事件让 UI 显示"已达上下文预算，据已有上下文作答"。

### 5b. 自描述结果信封 + MCP 引导（让任何 agent 自己走完工作流）

> 这一节回应两个关键需求：**结果要自带"说明"**（盖到哪、什么没生成），以及 **外部 MCP agent 默认不知道能深挖**。解法是把引导塞进结果本身、再加 MCP 多入口。

**每个 read 工具结果套一层信封**（详见 [thin-router-agent.md §2](architecture/thin-router-agent.md)）：

```jsonc
{ "cells":[ { "scope_key":"…T17","grain":"1h","description":"…",
             "compression":{"src_tokens":3000,"out_tokens":150,"ratio":0.05},
             "drill_down":"下钻 5min 可得 12 段时间线 + 2 次 git reset 时刻 + 报错原文；本层已含各 app 时长。" } ],
  "coverage":{ "computed_through":"…18:00", "missing":"周三 18:00 后未上卷，已用 1h 现算近似",
               "pending":["weekly 未生成","signals 未检测该区间"], "empty_windows":["02:00–09:00 无数据"] },
  "hint":"要更细调 get_summary(grain=5min)；要逐帧确切文本调 get_raw(窄窗,需鉴权)；要纯时长用 query_stats。",
  "next_tools":["get_summary(grain=5min)","get_raw","query_stats"] }
```

- `coverage` = **完整性说明**：把水位 / 未生成层 / 空窗显式讲出来，agent 不把"还没算完"误当"就这么多"。
- `compression` + `drill_down` = **信息气味**（§4.4）：引导是否下探。
- `hint` + `next_tools` = **工具互相指派**：把下一步塞进结果，不依赖 agent 预读文档。

**MCP 四入口引导**（详见 [thin-router-agent.md §5](architecture/thin-router-agent.md)）：① server `instructions`（握手时一段路由说明）；② resource `timetrace://guide`（可 fetch 的只读使用指南）；③ 结果内 `hint`/`next_tools`（最有效，裸连也被牵着走）；④ SKILL.md（Claude Code 自动读，从 `TOOL_SCHEMAS` 生成）。**全从 `TOOL_SCHEMAS` 单一源生成**，避免三处手抄 drift。

---

## 6. 子 agent map-reduce fallback

**这是兜底，不是主循环。** 多 agent ~15x token，仅保留给"真得扫一段 50K 装不下、且金字塔层答不了"的罕见 query。三个目标问题都**到不了这里**。

- **触发**：不靠 agent 推理决定，而是一个新工具 `deep_scan(start,end,question)` **内部按窗口 token 估算**决定：`estimated_raw_tokens(window) > _SINGLE_PASS_BUDGET(~40K)` 才 fan-out，否则单次 `get_raw`。
- **fan-out/reduce**（Anthropic orchestrator-worker，隔离上下文）：把窗口切成 **N 个非重叠**时间片（每片原始帧 ≈ ~40K）；`asyncio.gather` + `Semaphore(2–3)`（对齐 `WorkerConfig.vlm_concurrency`，别打爆单个 3080 端点）并发 map；每个 map = **单次受约束 `chat.completions.create(tools=None)`**（**不是**完整 `AgentRunner`，避免 N×6 轮开销 + N 个无界 convo），返回 ≤2K token 压缩 finding（带源 ID）；orchestrator concat N 个 finding（N×~2K，7 天 ≈ 14K 仍 < 50K）reduce。
- **实现**：`deep_scan` 是 `_IMPLS` 里的普通工具，`dispatch_tool` 像别的工具一样 await 它，父循环不变；子 summarizer 复用 runner 持有的同一 `VLMConfig`/openai client。它不返回 `items` 故不 bump `records_consulted`，另设 telemetry 字段 + `subagent` SSE 事件让这罕见的 30s 停顿可见。失败返回部分 reduce + `{degraded:true}`，不抛（守 `dispatch_tool` 永不抛契约）。

---

## 7. Schema 变更概览

> 完整 DDL sketch、索引、幂等键、回填见 [pyramid-schema.md](storage/pyramid-schema.md)。

- 新表 `summaries`（自相似级联，`grain` 区分 5min/1h/6h/day/week）+ `signals` + `summaries_fts`，全走 `CREATE...IF NOT EXISTS` 进 `_SCHEMA` literal（`init()` 的 `executescript` 自动建）。
- **不需要 `records.episode_id`**：窗口由 `ts_start` 直接算出（`window_start = floor((ts_start-cut)/grain)`），范围查询走现有 `idx_records_ts_start` —— 连那次 ALTER 都省了。
- **Phase 1 顺手引入 `schema_version`**（`settings` 里一个整数戳，不是 Alembic）：未来 ALTER 按版本号 gate 的廉价保险，不重写已有迁移。
- **幂等**：`summaries` 用 `UNIQUE(grain, scope_key)`（窗口即 key）+ UPSERT，应对调度重叠 / crash replay 的 at-least-once（像 outbox 一样可能重放）。`computed_through_ts` 是增量上卷的 watermark。
- 新增字段：`compression_ratio` / `drill_down_hint`（信息气味，§4.4）、`redacted`（脱敏，§12）、`source_version`（删除传播）、`body_json`（结构化列表）。
- `summaries`(day/week) 与现有 `reports` 表**分工**：`summaries` = 结构化可查的源；`reports` = 渲染层（HTML），改为**从 `summaries` 读**而非重扫原始帧。

---

## 8. 写时管线挂载

> 完整接线见 [episode-and-rollup-pipeline.md](architecture/episode-and-rollup-pipeline.md)。

- 级联 builder 是 `bootstrap.serve()` TaskGroup 里 `tg.create_task(_rollup_loop(), name="rollup")`，名字加进 `_watch_quit` 的取消集合。**单/双进程同时生效**（共用 `serve()`），client 保持哑、派生层纯 server 侧。
- 摘要任务队列态 `pending_summary` 复用 `claim_next_task(kind)` / `reclaim_stale_tasks` / backoff，**不建新 job 框架**。与 VLM worker 的唯一区别：rollup 是**依赖 DAG**（父窗等子窗 `summary_done`），故 `_rollup_loop` 做**分层有序扫描**（每轮 5min→1h→6h→day→week 自底向上），"队列"是隐式的（缺 `summary_done` 行的已闭合窗 = 待办）。
- **重发 / 级联失效**：底层被改（`apply_label`/soft-delete/重跑 VLM）或晚到 → 上层自底向上自动重算。`source_hash`（内容指纹）+ 改动点即时标 dirty + **定时巡检 + 开机自检**兜底；自底向上波传播、哈希闸门保证爆炸半径有界（改 1 帧 ≈ 重算 5 行）；批量改动用 token-bucket 限速。本质是增量物化视图。详见 [episode-and-rollup-pipeline.md §1.3–1.4](architecture/episode-and-rollup-pipeline.md)。
- signal 检测器套 `_embed_and_save` 的 best-effort 侧阶段范式（catch+log，永不传播）。
- **降级契约**（关键）：VLM 挂了，窗照常**闭合占位**（开窗 + metrics 都不需要 LLM），只让 `description`/`summary_embedding` 待回填；否则一次 VLM 宕机会冻住整个金字塔、"这个月多少小时"悄悄停更。`search_summaries` 在 embserver 挂时降级 FTS5-only（RRF 已能容一路缺失）。**`query_stats`/`get_summary` 的 metrics 既不依赖 VLM 也不依赖 embserver（纯 SQL）—— 关键韧性属性**：模型全宕时廉价聚合路径照常。调度器"无 VLM 退出"守卫**不得**禁用纯 metrics 的 rollup。
- **并发预算**：`5min` rollup / 上卷 / `_embed_and_save` / live VLM worker / `deep_scan` fan-out 都打同一个 3080 端点。给**交互 agent 优先级**，上卷夜间低并发（已离峰；活跃时段的 `5min` rollup 是主争用点 —— 限并发或可滞后几分钟批量补）。

---

## 9. 评估：怎么知道它是对的

> 这是三个 lens 收敛时一起漏掉、但**最关键**的一节。它们都优化成本、默认正确性会跟来 —— 不会。

- **数值层 by-construction 可查 —— 作为硬 CI 不变式**：某天 `summaries(grain=day).metrics_json[cat]` == 当天原始帧的纯 SQL `GROUP BY`（用 `_clamped_dur_sql()`）；高 grain `metrics_json` == 下层求和。廉价、确定，专抓 at-least-once rollup 重放的双计。
- **叙事层要 grounding 检查，不止 vibe**：每条摘要保留 `child_scope_keys` 源指针；加轻量断言——摘要里点名的 app/类必须真出现在成员里（cheap string-overlap，防编造内容）。LLM-as-judge 留给人工抽查，不进 CI。
- **golden-day 哨兵 fixture**：一个手标的合成日（哨兵日期、tmp 隔离目录，不污染真实"今天"），已知各窗内容 + 已知各类小时数，端到端断言。这是 prompt / 脱敏规则变更时的回归锚。

---

## 10. 分阶段计划（每阶段独立可交付）

> 每阶段的 files-to-touch / 新测试 / 验收标准详见三个支撑 spec。

| Phase | 目标 | 关键交付 | 验收锚 |
|---|---|---|---|
| **1** ← **最高杠杆第一步** | `5min` 承重层 + 上卷骨架 | `summary/rollup.py`（窗对齐 + 严格 schema + 压缩率/下钻提示 + 脱敏 hook）+ `summaries` 表 + `_rollup_loop` + **SUM-invariant 测试 + golden-day fixture** + `schema_version` | tmp 合成日跑完各 grain 行数符合预期（`5min` 数十/百、空窗跳过）、metrics 求和逐层一致、重复跑幂等、SUM-invariant 绿、261 现存测试全绿 |
| **2** | 日/周聚合 + 查询 | `summaries`(day/week) grain 在同一 builder 开 + `query_stats`/`get_summary` 工具 + reports 改读 `summaries` | 合成月 `query_stats(...,this_month)` 的 entertainment 秒数 == 原始帧纯 SQL 求和；该路径**不调任何 VLM/LLM**；跨层 "this week" 返 `source:"mixed"` + watermark |
| **3** | thin-router + 自描述 + MCP | 工具分层 + `search_summaries`（FTS+向量+RRF）+ 旧名 shim + **结果信封(coverage/compression/drill/next_tools)** + **MCP 折叠注册 + `instructions` + `timetrace://guide` resource** + **SKILL.md 同步测试** + 前端事件/label 协同 | 跨 3 月哨兵数据"找那次调试"被召回；单 turn token < 5K；MCP 工具集 == web 工具集（测试强制）；裸连外部 agent 靠结果 hint 走完 route→drill |
| **4** | 派生信号 | `signals/detectors.py` + `signals` 表 + `get_signals` 工具 + 复活 `confidence` + 摘要保留 git/undo `cues` | 合成"改代码→`git reset --hard`→重改"序列触发 rework signal、`evidence_json` 回链 scope_key；该问答不扫原始帧 |
| **5** | 子 agent fallback | `agent/mapreduce.py` + `get_raw`（token-budget cap）+ `deep_scan` + 可选 `subagent` SSE | 故意绕层的"逐帧找窗口"query：主 convo 从不持有原始帧、各子 agent 返压缩结果、orchestrator 合成；fan-out 仅在 deep-scan tier 触发 |
| **6+**（按需） | episode 派生视图 / dedup gate | `5min` 上的确定性 app-连续分组 view；capture 端 pHash dedup gate（拓扑敏感，单独 workstream） | 质量不行不拖累统计层；dedup 砍写时 VLM 成本 |

**冷启回填贯穿全程**：仿 `_backfill_embeddings` 一次性 sweep（poison-row skip + endpoint-down abort），**newest-day-first**（近期查询最快可用）、幂等可重入（窗即 key）、限速（token bucket，别在用户活跃时打满 3080）。回填期间工具暴露 `coverage`/`computed_through_ts`，让 agent 说"我只统计到 X 为止"，而不是返回空/错聚合。

---

## 11. 拓扑安全性

- 所有 builder 挂 `bootstrap.serve()` TaskGroup，`main.py` 与 `server/cli.py` 共用 → **单/双进程自动同时生效**；client 哑（无 DB），派生层纯 server 侧。不动 `common/protocol.py` / ingest 路由。
- **当前分支规则覆盖原计划**：实现进入 `main`，等待该 SHA 的 CI 全绿后再 fast-forward `deploy`；不要复制早期 `feature/refactor-split` / `--ref` 部署命令。新表仍需保持 Linux / 未来 Postgres seam 无害。

---

## 12. 隐私（承接 P4，明确而非展开）

> 本项目是字面意义的录屏记忆层，且 roadmap 的 **P4 = 客户端隐私管线**。时间窗级联其实给了 P4 一个**更干净的隐私边界**：摘要层逐层脱敏、原始帧留本地鉴权后才出。

- **脱敏内建（`redacted` 列）**：从 `5min` 层起每层摘要生成时脱敏（抹密码/token/私信/身份证等），越往上越粗。于是**摘要层默认可安全经 MCP 外发 / 导出**；`get_raw` 读未脱敏原始帧，**只在本地 + `require_principal` 后**可达。这正好契合"外部 agent 拿脱敏摘要、原始帧不出机"。
- `summaries` / `signals` 继承 **local-only + `require_principal`**，**绝不**进未鉴权的 `/skill` 或任何 frontend-facing 路由。`summary_embedding` 含敏感 prose、事后更难擦 —— 故脱敏要在**嵌入之前**做。
- **删除传播**：帧/截图 soft-delete（`deleted_at`）后，覆盖它的 `5min` 行 `source_version` bump → 重摘要 → `1h/6h/day/week` 祖先链 bump → 重 rollup 丢弃。`source_version` 字段 Phase 1 先留好。本节是**承认 + 留钩子**，完整实现随 P4。

---

## 13. 风险与开放问题

### 风险排名

1. **摘要质量 / 幻觉（最高）**：固定时间窗已**消掉了边界切错（γ）这个原最大风险**，但 `5min` 摘要本身仍可能幻觉，且会级联到上层。→ 缓解：严格 schema、SUM-invariant 硬测试、每层留 `child_scope_keys` 源指针、grounding 字符串重叠断言、golden-day fixture。
2. **跨层查询误算**（"this week" 缝合）：貌似合理的错数字，最坏的 bug。→ `computed_through_ts` watermark 缝合 + `source:"mixed"` caveat。
3. **stale-doc / drift 复发**：已有三处手写工具清单 + 过期 CLAUDE.md stub 表。加工具不折叠注册就重造 drift。→ MCP 从 `TOOL_SCHEMAS` 生成 + SKILL.md 覆盖测试，**Phase 3 内做、不拖"以后"**。
4. **后台 rollup 抢占交互路径（单 3080）**：`5min` rollup 跑在活跃时段，用户问"现在在做什么"会变慢。→ 交互优先级 + `5min` 限并发/可滞后批量补。
5. **回填打满主机 / 非幂等重放双计小时**。→ 窗即幂等键 + 限速 + newest-first。
6. **VLM/embserver 宕机冻住金字塔**。→ 窗无摘要也照常闭合占位；纯 metrics 路径不依赖模型。
7. **脱敏遗漏 / 敏感 prose 进了嵌入**：事后难擦。→ 脱敏在嵌入之前；§12。
8. **`5min` 摘要调用量（~120/天）抢 3080**：比 episode 方案多。→ 夜间/离峰批量 + 限并发；这是换"无 γ 风险"的代价。

### 开放问题

- ~~grain 阶梯~~ **已定**：`5min / 1h / 6h / day / week` —— 不加 month、保留 6h。
- 仍待定：`5min` 窗大小与关键帧间隔的实际比值；distraction 阈值（<5min？）；`deep_scan` 触发的 token 阈；脱敏规则集（复用现有隐私黑名单到什么程度）；巡检频率与开机自检的范围（全量扫 vs 近 N 天）。
- `hours_back_hint` 死参数：两个 caller 都传、`run()` 从不读。**决定：端到端删除**（route 字段 + 前端 `opts.hoursBack` + runner 参数），因为路由现在用显式工具参数定窗。

> **⚠️ 过期标注待补**：本计划落地后，`CLAUDE.md` 的"仍然存在的桩代码"表里 `mcp_layer/tools.py` 桩、`infra/architecture/mcp-layer.md` 的"4 tools"散文需按 `## **⚠️ 一句话标题**` 约定加过期标注或指向纠正。
