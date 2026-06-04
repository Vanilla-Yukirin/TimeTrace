# 薄路由器 Agent · 工具分层 / 自描述结果 / MCP 引导 / 子 agent fallback

> **状态**：设计草案（2026-06-02 起草，2026-06-05 改为时间窗级联 + 自描述结果），**未实装**。配套 [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) 的 agent 运行时 spec。工具 schema / prompt 为 **sketch**。
>
> 现状 agent：单 OpenAI tool-loop（`server/agent/runner.py`，`max_iterations=6`，180s 超时，SSE 流式），5 个 raw-reader 工具（`server/agent/tools.py`）。本页把它重塑为路由器。

---

## **🗺️ 一句话**

agent 不再是「吞数据的嘴」，而是 **route → retrieve → light-reason** 的薄路由器，读[时间窗摘要级联](../storage/pyramid-schema.md)而非原始帧。三件关键设计：(1) **每个工具结果自带信封** —— coverage（盖到哪/什么没生成）+ compression（压缩率）+ drill_down（下钻能多得啥）+ next_tools（下一步调谁），让 agent 哪怕没读文档也被牵着走；(2) **MCP 多入口引导**外部 agent；(3) 罕见大窗原始扫描走 `deep_scan` **fallback**（子 agent = 单次 summarizer，非完整 AgentRunner）。所有工具在 `agent/tools.py` 单一源，web agent / MCP / ReportGenerator 共用。

---

## 0. 核心倒置（锚定现状）

今天每个 read 工具都在 **query-time 扫原始帧**：`search_activity` 最多 100 条**未截断** `vlm_desc`（≈12–15K tok/单次），`get_recent_activity` 最多 200 条（≈28–32K tok），跨 6 轮累进 `convo`、**不驱逐** → 两次大 read 破 50K。MCP-only `ask_agent` 同病（80 帧塞一 prompt）。倒置 = 写时建好级联（见 [pipeline](episode-and-rollup-pipeline.md)），query-time 只 route → retrieve → light-reason。

---

## 1. 工具成本阶梯（读级联，不读原始帧）

`AgentRunner` 循环**不变**（最后一轮 drop tools 逼散文）。智能在系统 prompt + 更便宜的工具，全在 `agent/tools.py`（`_IMPLS`+`TOOL_SCHEMAS`+`dispatch_tool` 单一源）。

```
TIER 0  query_stats        纯 SQL over summaries(day/week).metrics_json / 现算    ~0 LLM tok
TIER 1  get_summary        指定 grain 的级联摘要（week→day→6h→1h→5min 由粗到细）  by-construction 限界
TIER 2  search_summaries   语义/FTS over 摘要嵌入（数百向量，非全表 per-frame）   上限 ~6K
TIER 3  get_raw            逃生口：窄窗原始帧，按 token 估算硬上限（且鉴权后）     硬 cap ~10K
WRITE   apply_label        唯一写权限（不变：category_final + feedback）          trivial
（FALLBACK）deep_scan      大窗原始扫描，内部 map-reduce 子 agent，返 ≤2K          见 §4
```

系统 prompt 强制：**先试能答的最便宜层；grain 由粗到细下探；`get_raw` 需理由、绝不当第一刀。**

### 1.1 `query_stats` —— TIER 0，纯 SQL，零 LLM

所有时间/时长/app/类/计数问题的 fast-path。命中 `summaries(day/week).metrics_json` 键值行（零 LLM），uncached 窗口才现算（复用 `_clamped_dur_sql()`）。**绝不碰 `vlm_desc`。** 跨层组合（"this week" 缝合）逐子区间取最高已 finalized grain、未上卷尾巴 fall back 低 grain（再到帧），按 `computed_through_ts` 缝合，返回 `source:"mixed"` + watermark（见 [pyramid-schema §6](../storage/pyramid-schema.md)）。

### 1.2 `get_summary(grain, A, B)` —— TIER 1，级联摘要

替 `get_recent_activity` + `get_digest`。一个工具吃 `grain` 参数读任意层：

```jsonc
{ "name":"get_summary",
  "description":"读时间窗摘要。grain 由粗到细：week→day→6h→1h→5min。先用粗 grain 拿全貌，按结果里的 drill_down/compression 决定是否换细 grain 下钻。绝不返原始帧。",
  "parameters":{ "type":"object","properties":{
    "grain":{"enum":["week","day","6h","1h","5min"]},
    "start_iso":{"type":"string"}, "end_iso":{"type":"string"},
    "category":{"type":"string","description":"可选过滤一个 6 类"}
  }, "required":["grain","start_iso","end_iso"] } }
```

返回每个窗一个 cell（含自描述信封，见 §2）。grain 越细返回越多 cell → 由 §3 的 token cap 约束。"总结我这周/这天" = `get_summary('week'|'day')` 一刀；要细节按 `drill_down_hint` 换 `'1h'`/`'5min'`。

### 1.3 `search_summaries(query)` —— TIER 2，语义/FTS over 小索引

缺失的 RAG 路径。语义（cosine over `summaries.summary_embedding`，**数百向量**非全表 per-frame）+ 关键词（`summaries_fts` trigram + BM25 CTE）经现有 by-image 同款 **RRF 融合**。`include_neighbors` 实现 EM-LLM 两阶段检索（相似 + 时间邻接）。**embserver 宕 → 降级 FTS5-only**，不报错。返回命中 cell + 信封。

### 1.4 `get_raw(A, B)` —— TIER 3，逃生口（鉴权后、窄窗）

唯一读**未脱敏** `vlm_desc` 的工具。硬 gate：窗 > `_RAW_MAX_WINDOW_S`(~2h) 返 `{error:"窗口过宽，用 get_summary"}`（`dispatch_tool` 永不抛 → 自纠）；按 **token 估算 cap**（不是行数）`_RAW_BUDGET`(~10K)；`vlm_desc` 经 `format_record_for_llm(desc_chars=...)` 截断。**因为读未脱敏原始数据，只在本地 + `require_principal` 后可达**，经 MCP 外发受同样鉴权约束。这也是 §4 map-reduce 子 agent 内部调的入口。

### 1.5 `apply_label` / 旧名 shim

`apply_label` 原样保留（唯一写权限）。`search_activity`/`get_recent_activity`/`get_app_breakdown`/`get_category_stats`/`get_digest` **保留为薄 shim**委托新工具（不硬改名 —— SKILL.md/MCP/前端 label 按名硬引用），各加一行 deprecation docstring。

---

## 2. 自描述结果信封（你的第一点：结果自带说明）

> 每个 read 工具结果套一层信封，让 agent **一眼看到"盖到哪、什么没生成、丢了多少、值不值得下钻、下一步调谁"**——信息在结果里，不依赖 agent 预先读文档。

```jsonc
{ "cells": [
    { "scope_key":"2026-06-04T17", "grain":"1h",
      "description":"...", "evaluation":"...", "metrics":{...},
      "compression": {"src_tokens":3000,"out_tokens":150,"ratio":0.05},   // 信息气味
      "drill_down":  "下钻 5min 可得 12 段逐段时间线 + 2 次 git reset 时刻 + 报错原文；本层已含各 app 总时长。" },
    ...
  ],
  "coverage": {                                  // 你的第一点：完整性说明
    "computed_through":"2026-06-04 18:00",
    "missing":"本周三 18:00 之后尚未上卷，已用 1h 层现算近似",
    "pending":["weekly digest 未生成","signals 未检测该区间"],
    "empty_windows":["02:00–09:00 无数据（睡眠/关机）"]
  },
  "hint": "这是 1h 粒度。要更细调 get_summary(grain=5min)；要逐帧确切文本调 get_raw(窄窗,需鉴权)；要纯时长用 query_stats。",
  "next_tools": ["get_summary(grain=5min)","get_raw","query_stats"] }
```

- **`coverage`**：把 `computed_through_ts` 水位、未生成的层、空窗显式讲出来 → agent 不把"还没算完"误当"就这么多"。
- **`compression` + `drill_down`**（你的第三点的写时产物，见 [pyramid-schema §4](../storage/pyramid-schema.md)）：压缩率是廉价 proxy，`drill_down` 是修正它的具体信号。**系统 prompt 教 agent 读它**：压缩率高且 `drill_down` 列出了你要的字段 → 换细 grain 下钻；`drill_down` 说"无新结论" → 别浪费一次调用。
- **`hint` + `next_tools`**（你的第二点）：工具与工具互相指派，把下一步显式塞进结果。

---

## 3. token 预算双重防御

1. **per-tool 输出 cap（主防御）**：每 read 工具按 **token 估算**（CJK-aware `~chars/3.5`）cap 输出，不是行数。`get_summary` 粗 grain 天然小；细 grain 多 cell 时 emit 到 `_SUMMARY_BUDGET`(~6K) 截断 + `coverage.truncated`；`get_raw` 窗≤2h 且 ~10K 且 `vlm_desc[:200]`；`deep_scan` 返 ≤2K。没有单工具能返 >~10K → `convo` 稳在 50K。
2. **runner turn-budget gate（次防御）**：累计 `convo` 估算 token，若 `cumulative + 下一工具上限 > _TURN_BUDGET`(~40K)，runner **拒绝再 read、提前逼散文**（复用 iteration-exhaustion 的 drop-`tools=`），发 `budget` SSE。
3. **无静默增长**：工具返小预聚合结果，单调增长 by-construction 有界；`deep_scan` 隔离即 compaction，原始帧从不进父 `convo`。

---

## 4. 子 agent map-reduce fallback

**FALLBACK，不是主循环。** 多 agent ~15x token，仅留给"真得扫一段 50K 装不下、且级联层答不了"的罕见 query。三个目标问题**到不了这里**。

- **触发（确定性）**：工具 `deep_scan(start,end,question)` 内部按 `estimated_raw_tokens(window) > _SINGLE_PASS_BUDGET(~40K)` 才 fan-out，否则单次 `get_raw`。
- **fan-out/reduce**（Anthropic orchestrator-worker，隔离上下文）：切成 N 个**非重叠**时间片（每片 ≈~40K）；`asyncio.gather`+`Semaphore(2–3)`（对齐 `WorkerConfig.vlm_concurrency`）并发；每片 = **单次 `chat.completions.create(tools=None)`**（**不是**完整 `AgentRunner`，避免 N×6 轮开销 + N 个无界 convo）→ 返 ≤2K finding（带源 scope_key/ts）；orchestrator concat reduce（N×~2K，7 天 ≈14K < 50K）。
- **实现**：`deep_scan` 是 `_IMPLS` 普通工具，`dispatch_tool` 像别的工具 await，父循环不变；子 summarizer 复用同一 `VLMConfig`/openai client；裸 `asyncio`，无需 Celery/Workflow 引擎；不返 `items` → 不 bump `records_consulted`，另设 telemetry + `subagent` SSE；失败返部分 reduce + `{degraded:true}`，不抛。

---

## 5. MCP 引导：让外部 agent 自己读到工作流（你的第二点）

外部 agent（Claude Code/Desktop）默认不知道"我还能深挖"。MCP 协议给四个引导面，**全用上、互相指**：

| 入口 | 机制 | 内容 |
|---|---|---|
| **server `instructions`** | MCP `initialize` 握手返回的 server 级说明字段 | 一段话："这是分层活动记忆。先 `query_stats`/`get_summary(grain=week/day)`，按结果 `drill_down`/`compression` 决定是否换细 grain 或 `get_raw`。" |
| **resource `timetrace://guide`** | MCP **resource**（外部 agent 可主动 fetch 的只读文档） | 等价于本页的路由阶梯 + 自描述信封说明，让 agent 按需读全 |
| **结果内 `hint`/`next_tools`** | 嵌在每个工具返回里（§2） | **最有效**：不需 agent 预读任何文档，结果一步步牵着走 |
| **SKILL.md** | Claude Code 装 skill 时自动读 | 从 `TOOL_SCHEMAS` 生成，与上面同源 |

效果：外部 agent 哪怕"裸连"，也会被 `instructions` 定调、被结果 `hint` 牵引、可 fetch `guide` 读全 —— 自己走完 route→drill 工作流。

---

## 6. MCP 平价 —— 设计掉 #1 drift 风险

最大 drift：加工具到 `agent/tools.py` 自动点亮 web agent，但 MCP 是 **6 个手写 `@mcp.tool()` 闭包**须手抄，SKILL.md 是**第三**份。折叠：

1. **MCP 从 `TOOL_SCHEMAS` 生成**：`build_mcp_server` 循环注册委托 `dispatch_tool(db,name,args)` 的 wrapper。一个注册源、两个 consumer，永不 drift。（`apply_label` 可留 bespoke；`deep_scan` 返紧凑 dict 也可暴露。）顺手设 server `instructions` + 注册 `timetrace://guide` resource（§5）。
2. **杀 `ask_agent`**（MCP-only、80 帧塞 prompt、重复 formatter）：重指向 `query_stats`/`get_summary`/`search_summaries`，删除（或重实现在 `get_summary` 上、绝不 `get_raw`）。
3. **删孤儿 `mcp_layer/tools.py`**（死代码），按 `## **⚠️ …**` 约定修 CLAUDE.md stub 表 / `mcp-layer.md` "4 tools" 散文。
4. **SKILL.md 从 `TOOL_SCHEMAS` 生成**（或测试断言覆盖每个 `_IMPLS` 名 + 6 类）。

净效果：**一个真源（`TOOL_SCHEMAS`+`dispatch_tool`）**喂 web agent / MCP / ReportGenerator / SKILL.md，无手抄、无 drift。

---

## 7. 路由 persona + worked traces

系统 prompt（append 注入不 `.format`，避 CSS 花括号碰撞）让模型分类问题 → 选最便宜 grain → 按结果信封决定下探：

```
你是 TimeTrace 的活动记忆路由器。靠读【时间窗摘要级联】回答，几乎从不读原始帧。
层由便宜到贵：query_stats(零成本统计) → get_summary(grain 由粗到细) → search_summaries(语义) → get_raw(窄窗,需鉴权,最后手段)。
每个结果带 coverage/compression/drill_down/next_tools：
  - 要总量/时长 → query_stats，停，别读摘要。
  - 要"在做什么/总结" → get_summary 先用粗 grain；看 drill_down：列出你要的字段且压缩率高才换细 grain；说"无新结论"就别下钻。
  - 要"何时做过 X" → search_summaries。
  - "现在/正在" → 读最新少量原始帧（不是摘要：当前窗可能还没闭合）。
  - 行为(坏习惯/返工) → query_stats(signal_counts) + 在被 flag 窗 get_summary。绝不扫月原始帧。
按 id/scope_key 引用；别贴长原始描述。唯一可改的是 apply_label。
```

- **「我现在在做什么」** → 读**最新 1–3 条原始帧**（不是摘要——当前窗未闭合；最新帧可能还 `pending_vlm`，则用 app/窗口元数据 + 最近一条已描述帧）。1 调用 ~150 tok。
  > **⚠️** 早期初稿写的"right now → get_summary(small window)"是**错的**（开放窗问题）。"现在"必须读最新原始帧。
- **「这个月娱乐多少小时」** → `query_stats(seconds_by_category, this_month, filter=entertainment)` → 读月 `metrics_json` → "约 43 小时（占活跃 18%）"。**1 调用，~0 LLM 读 token。**
- **「写代码哪些坏习惯导致返工」** → `query_stats(signal_counts)` → 按 `signals.evidence_json` 的 scope_key 用 `get_summary('1h', 那几段)`（`cues` 写时已留 git/undo 线索）→ light-reason ~10 cell (~700 tok)。**永不扫月**。

---

## 8. SSE 协议 + 前端协同（每加一项要全改，否则静默 no-op）

前端 `agentApi.ts` 是**封闭判别联合**：新工具走通用 `tool` 块自动支持，但新**事件类型**被 `applyEvent` 静默忽略。

| 事件 | 形状 | 为什么 |
|---|---|---|
| `step` | 不变 | 新工具免费搭车 |
| `budget`（新） | `{used_tokens,limit,action}` | 让 §3 提前/部分作答可见 |
| `subagent`（新） | `{phase:'fanout'|'reduce',n,frames_scanned}` | 让罕见 `deep_scan` 30s 停顿可读 |
| `done` | 扩 `summaries_consulted`、`subagents` | `deep_scan` 不返 `items` 不 bump `records_consulted` |

**协同 edit-set 清单**（任一遗漏 = 静默 no-op）：① `agentApi.ts` union+`done` payload；② `AgentPage.tsx::applyEvent` 加 `budget`/`subagent` 分支；③ `agentSessions.ts` 加 `AssistantBlock` kind；④ `TurnView.tsx::TOOL_LABELS` 加新工具中文 label（`query_stats`→"统计"、`get_summary`→"摘要"、`search_summaries`→"语义检索"、`get_raw`→"原始帧"、`deep_scan`→"深度扫描"）；⑤ `runner.py::_summarize` 加分支（如 `get_summary`→"N 段摘要"、`deep_scan`→"扫描 M 帧/N 子任务"）。

---

## 9. ReportGenerator：读 summaries 不扫原始帧

今天 `ReportGenerator` 复用 `AgentRunner` + report persona 调 raw-reader over `recent_7d=168h` → 同 chat 的无界上下文风险被 168h 放大。倒置：

- 报告 persona 改调 `get_summary(grain=day/week)`/`query_stats`，**绝不 `get_raw`**。周报推理 ~7 条 day + 1 条 week 摘要（几 K tok），不是 168h 帧。贵 read 早在级联 rollup 付过。
- 保留 `reports` 表为渲染 sink，但改 **从 `summaries` read**（`reports`=渲染层、`summaries`=结构化源）。
- report SSE 不变；30min `_report_scheduler` 只从既存 `summaries` 重渲染（级联 builder 是另一条 `_rollup_loop` 任务，见 [pipeline](episode-and-rollup-pipeline.md)）。

---

## 10. 处置决定（别再 re-litigate）

- `hours_back_hint` **死参数**（两 caller 都传、`run()` 不读）：**端到端删除**——路由现用显式工具参数定窗。
- 旧工具名 = 薄 shim（§1.5）。
- "现在在做什么" 读最新原始帧、不读摘要（§7 ⚠️）。
- 子 agent = 单次 summarizer，非完整 AgentRunner（§4）。

---

## 相关文件

- `src/timetrace/server/agent/tools.py`（工具 + token helper + 自描述信封 + `_clamped_dur_sql`）· `runner.py`（系统 prompt / turn-budget gate / `_summarize` / `budget`+`subagent` SSE / `deep_scan` summarizer）
- `server/report/generator.py`（persona 改 `get_summary`/`query_stats`）· `server/mcp_layer/server.py`（从 `TOOL_SCHEMAS` 生成 + `instructions` + `timetrace://guide` resource；删 `ask_agent`）· `mcp_layer/tools.py`（删）
- `skills/timetrace/SKILL.md`（从 `TOOL_SCHEMAS` 生成）
- `frontend/src/lib/agentApi.ts` · `agentSessions.ts` · `pages/AgentPage.tsx` · `components/agent/TurnView.tsx`
- [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) · [storage/pyramid-schema.md](../storage/pyramid-schema.md) · [episode-and-rollup-pipeline.md](episode-and-rollup-pipeline.md)
