# 薄路由器 Agent · 工具分层 / 路由 / 子 agent fallback

> **状态**：设计草案（2026-06-02），**未实装**。配套 [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) 的 agent 运行时 spec。工具 schema / prompt 为 **sketch**。
>
> 现状 agent：单 OpenAI tool-loop（`server/agent/runner.py`，`max_iterations=6`，180s 超时，SSE 流式），5 个 raw-reader 工具（`server/agent/tools.py`）。本页把它重塑为路由器。

---

## **🗺️ 一句话**

agent 不再是「吞数据的嘴」，而是 **route → retrieve → light-reason** 的薄路由器。`AgentRunner` 循环**不变**，智能搬进系统 prompt + 一组按成本分层的工具（全部 token-bounded by construction）。罕见的大窗原始扫描走 `deep_scan` 子 agent map-reduce **fallback** —— 子 agent 是**单次 summarizer 调用、不是完整 AgentRunner**。所有工具仍在 `agent/tools.py` 单一源，web agent / MCP / ReportGenerator 共用。

---

## 0. 核心倒置（锚定现状）

今天每个 read 工具都在 **query-time 扫原始帧**：`search_activity` 最多 100 条**未截断** `vlm_desc`（≈ 12–15K tok/单次），`get_recent_activity` 最多 200 条截断（≈ 28–32K tok），结果跨 6 轮累进 `convo`、**不驱逐**。两次大 read 破 50K。MCP-only 的 `ask_agent` 同病（80 帧塞一个 prompt）。

所有被调研系统都拒绝这个（Dayflow chat 只读预合并 card；Rewind 是 index-first RAG；ActivityWatch 只返聚合）。倒置 = 两步协同：(1) 写时建好金字塔（见 [pipeline](episode-and-rollup-pipeline.md)）；(2) 5 个 raw-reader 换成 6 个分层工具，系统 prompt 逼模型走 cheap→expensive 阶梯。

---

## 1. 工具成本阶梯

6 个工具替换 5 个 raw-reader，全在 `server/agent/tools.py`（`_IMPLS` + `TOOL_SCHEMAS` + `dispatch_tool` 单一源，web Runner 自动点亮）。全部返回 `{items:[...], ...}` 契约，故 `runner.py::_summarize` + `records_consulted` + UI step-chip 继续工作。

```
TIER 0  query_stats      纯 SQL over digests.metrics_json / 现算 GROUP BY    ~0 LLM tok，几十行
TIER 1  get_digest       1–4 条预算好的 digest（叙事 + metrics）            ~0.5–4K
TIER 1  get_episodes     窗口内 episode 摘要（绝不返原始 vlm_desc）          上限 ~6K
TIER 2  search_episodes  对小 episode-摘要索引做语义/FTS（向量下沉 L2）      上限 ~6K
TIER 3  get_raw          逃生口：窄窗原始帧，按 token 估算硬上限             硬 cap ~10K
WRITE   apply_label      唯一写权限（不变：category_final + feedback）       trivial
（FALLBACK）deep_scan    大窗原始扫描，内部 map-reduce 子 agent，返 ≤2K       见 §3
```

系统 prompt 强制：**先试能答的最便宜层；上一层不够才下探；`get_raw` 需理由、绝不当第一刀。**

### 1.1 `query_stats` —— TIER 0，纯 SQL，零 LLM

所有时间/时长/app/类/计数问题的 fast-path。命中 `digests.metrics_json` 键值行（零 LLM），uncached 窗口才现算 `GROUP BY` over episodes（复用实战的时长 clamp）。**绝不碰 `vlm_desc`。**

```jsonc
{ "name":"query_stats",
  "description":"零重读地取聚合时长/计数。读预算好的 digest metrics（或聚合 episodes）。任何'多少小时/多少时间/哪个 app/多频繁'先用它。返回数字不返描述。",
  "parameters":{ "type":"object","properties":{
    "metric":{"enum":["seconds_by_category","seconds_by_app","episode_count","active_seconds","top_titles","signal_counts"]},
    "period":{"enum":["today","this_week","this_month","custom"]},
    "start_iso":{"type":"string"}, "end_iso":{"type":"string"},
    "filter_category":{"type":"string"}, "top_n":{"type":"integer","default":10,"maximum":50}
  }, "required":["metric","period"] } }
```
返回 `{items:[{key,seconds,share}...], total_seconds, source:"digest"|"live"|"mixed", computed_through_ts, period_start_iso, period_end_iso}`。**跨层组合**（"this week" 缝合）按 [pyramid-schema §6](../storage/pyramid-schema.md)：逐子区间取最高已 finalized 层、未上卷尾巴现算、`source:"mixed"` + watermark 让 agent 加 caveat。

### 1.2 `get_episodes(A,B)` —— TIER 1，episode 摘要（替 `get_recent_activity`）

一行 ≈ title + 1–2 行 summary + 类 + app + 时长 ≈ ~60 tok，一天 ~50 段 ≈ ~3K（vs raw@200 的 28–32K）。

```jsonc
{ "name":"get_episodes",
  "description":"列出时间窗内的活动 EPISODE（合并的会话摘要，不是原始帧），时间序。一段=一次连贯 sitting。用于'我[某时/之间/今天]在做什么'。便宜：几十条一行摘要。",
  "parameters":{ "type":"object","properties":{
    "start_iso":{"type":"string"}, "end_iso":{"type":"string"},
    "category":{"type":"string"}, "limit":{"type":"integer","default":50,"maximum":120}
  }, "required":["start_iso","end_iso"] } }
```
返回 `{items:[{episode_id,ts_start_iso,ts_end_iso,primary_app,category,title,summary,frame_count,active_seconds}...], truncated:bool}`。硬 cap：输出 token 估算 ≤ `_EPISODE_BUDGET`(~6K)，超则返最近 N + `truncated:true` + 引导（缩窗 / 用 `query_stats` 取总量）。

### 1.3 `get_digest(level,period)` —— TIER 1，rollup 叙事 + metrics

```jsonc
{ "name":"get_digest",
  "description":"读预算好的日/周/月 digest：短叙事 + 结构化 metrics。用于'总结我的[一天/一周/一月]'，或下钻前的廉价上下文。返 1–4 行，不返原始帧。",
  "parameters":{ "type":"object","properties":{
    "level":{"enum":["day","week","month"]},
    "period":{"type":"string","description":"scope key '2026-06-02'/'2026-W23'/'2026-06' 或 'latest'"}
  }, "required":["level","period"] } }
```
返回 `{items:[{scope_key,level,period_start_iso,period_end_iso,summary,metrics}...], available:bool}`。digest 没建（`available:false`）→ 返能廉价现算的 metrics + 标注叙事 pending，**绝不 query-time 触发昂贵 build**。

### 1.4 `search_episodes(query)` —— TIER 2，语义/FTS over 小索引

缺失的 RAG 路径。语义（cosine over `episodes.summary_embedding`，**数百向量**非 per-frame 全表扫）+ 关键词（`episodes_fts` trigram + BM25 CTE）经现有 by-image 同款 **RRF 融合**。

```jsonc
{ "name":"search_episodes",
  "description":"跨全历史按语义或关键词找 episode。用于'我上次什么时候做 X''找我调试 Y 那次'。向量+关键词混合 over episode 摘要。返 top-k 摘要 + 时间邻居，不返原始帧。",
  "parameters":{ "type":"object","properties":{
    "query":{"type":"string"}, "start_iso":{"type":"string"}, "end_iso":{"type":"string"},
    "top_k":{"type":"integer","default":8,"maximum":20},
    "include_neighbors":{"type":"boolean","default":true}
  }, "required":["query"] } }
```
`include_neighbors` 实现 EM-LLM **两阶段检索**（相似 + 时间邻接）：拉每个命中前/后那段，"什么导致这次返工"才答得了。top_k≤20、短摘要 → 最坏 ~2.4K。**embserver 宕 → 降级 FTS5-only**（RRF 容一路缺失），不报错。

### 1.5 `get_raw(A,B)` —— TIER 3，逃生口，窄窗

唯一读 `vlm_desc` 的工具。下钻 / 引证。硬 gate：**按 token 估算 cap，不是行数 cap**（200 帧已破窗）。

```jsonc
{ "name":"get_raw",
  "description":"逃生口。读窄窗（≤~2h）原始帧描述。仅当 episode/digest 缺你要的精确细节（如某刻屏上的确切文本）才用。不用于宽问题——那些用 query_stats/get_episodes。输出硬 cap；过宽窗被拒。",
  "parameters":{ "type":"object","properties":{
    "start_iso":{"type":"string"}, "end_iso":{"type":"string"},
    "keyword":{"type":"string"}, "max_frames":{"type":"integer","default":40,"maximum":80}
  }, "required":["start_iso","end_iso"] } }
```
守卫：(1) 窗 > `_RAW_MAX_WINDOW_S`(~2h) 返 `{error:"窗口过宽，缩小或用 get_episodes"}`（`dispatch_tool` 永不抛 → 模型自纠）；(2) `vlm_desc` 经 `format_record_for_llm(desc_chars=...)` 截断（现 `search_activity` 没用这个）；(3) 运行态 token 估算 cap `_RAW_BUDGET`(~10K)，到顶停、`truncated:true`。这也是 §3 map-reduce 子 agent 内部调的入口。

### 1.6 `apply_label` —— WRITE，不变

原样保留：`category_final` + `feedback` 审计行。仍是 agent **唯一**写权限（"信任分析、只打标、绝不删/改记录"的产品原则）。派生层全由后台 job 写、agent 永不碰。

### 1.7 旧名兼容（锁定 T5）

`search_activity` / `get_recent_activity` **保留为薄 shim**，分别委托 `search_episodes` / `get_episodes`（都已 token-bounded）。**不硬改名** —— SKILL.md / MCP 闭包 / 前端 `TOOL_LABELS` 都按名硬引用，硬改名逼出多面破坏。旧名加一行 deprecation docstring；新显式名是 router prompt / SKILL.md 今后教的。`get_raw` 是真正新增。

---

## 2. 路由策略（系统 prompt 即分类器）

runner 仍单循环（最后一轮 drop tools 逼散文 —— 不变）。智能在系统 prompt（**append 注入不 `.format`**，避开 CSS 花括号碰撞 —— 现有约定）。

### 系统 prompt 骨架（sketch）

```
你是 TimeTrace 的活动记忆路由器。你靠读【预计算的摘要层】回答用户做过什么，几乎从不读原始帧。

数据层，由便宜到贵。永远先试能答的最便宜层：
  1. query_stats   —— 按 app/类的总量/计数/时长。零成本。任何"多少小时/多少/哪个 app/多频繁"用它。
  2. get_digest    —— 日/周/月叙事 + metrics。"总结我的<时段>"用它。
     get_episodes  —— 窗口内会话摘要时间序。"我<某时>在做什么"用它。
  3. search_episodes —— 跨全时段语义/关键词召回。"我什么时候做过 X"用它。
  4. get_raw       —— 最后手段。仅窄窗原始帧文本。仅当摘要层缺所问的确切细节。绝不是第一刀。

分类问题再路由：
  - "现在/正在"          -> 读最新少量原始帧（不是 episode：当前 sitting 可能还没闭合）。常一刀。
  - "X 花了多少时间"      -> query_stats。停。别为总量去读 episode。
  - "总结我的一天/一周"   -> get_digest。要细节才下钻 get_episodes。
  - "找/我何时做过 X"     -> search_episodes（语义）。get_raw 仅用于确认命中。
  - 行为("坏习惯/返工/回滚") -> query_stats(metric=signal_counts) + 在被 flag 的窗口 get_episodes。绝不扫月原始帧。

规则：
  - 偏好一次廉价调用。别串联 raw read。
  - get_raw 窗口须 ≤~2h。要宽 raw 扫描你做不到——用 episode/digest 总结，或说明限制。
  - 按 id 引用 episode/帧；别把长原始描述贴进答案。
  - 唯一可做的改动是 apply_label。绝不删/改记录。
```

### Worked traces

**「我现在在做什么」** → 读**最新 1–3 条原始帧**（L0/L1，永远最新）。**不是 episode** —— 当前 sitting 可能还没闭合、还没 episode 行；最新帧可能还 `pending_vlm`（VLM 没跑），则用 app/窗口元数据 + 最近一条**已描述**帧。1 调用 ~150 tok。
> **⚠️ 注意**：agent-runtime 初稿写的"right now → get_episodes(small window)"是**错的**（开放 episode 问题）。"现在"必须读最新原始帧，不等 episode 闭合。

**「这个月娱乐多少小时」** → `query_stats(seconds_by_category, this_month, filter=entertainment)` → 读月 digest `metrics_json` 键值行 → "约 43 小时（占活跃 18%）"。**1 调用，~0 LLM 读 token，没碰任何帧。** 这就是 500K→~0 倒置。

**「写代码哪些坏习惯导致返工/回滚」**（最易误扫月原始帧的行为问题）→ `query_stats(signal_counts, this_month)`（便宜，告诉模型 rework 是主信号）→ 下钻被 flag 的 2–3 窗口（`signals.evidence_json` 带 episode_id，或 `search_episodes("git revert reset rollback")`），episode `cues` 写时已保留 git/undo 线索 → light-reason ~10 条摘要 (~700 tok)："回滚多在 23:00 后（late_night）、常跟在一次 context_switch_storm 后……"。~3 廉价调用，**永不扫月**；跨帧模式检测写时已付一次（signal detector）。

---

## 3. 子 agent map-reduce fallback

**FALLBACK，不是主循环。** 多 agent ~15x token，仅留给"真得扫一段 50K 装不下、且金字塔层答不了"的罕见 query。三个目标问题**到不了这里**。

### 触发（确定性，非 agent 推理）

新工具 `deep_scan(start,end,question)` 内部按窗口 token 估算决定：

```
deep_scan 触发 map-reduce 当且仅当
   estimated_raw_tokens(window) > _SINGLE_PASS_BUDGET (~40K)   // ≈ frame_count × ~140
否则只 get_raw 一次（单 pass，无需子 agent）。
```
90min 窗(~300 帧 ≈42K) 临界 → 1–2 子 agent；7 天 raw(~35K 帧 ≈5M tok) → 多子 agent。常见 query 根本到不了 `deep_scan`（router 用 `query_stats`/`get_episodes`/`get_digest` 答了）。

### Fan-out / reduce（Anthropic orchestrator-worker，隔离上下文）

1. **Partition**：把 `[A,B]` 切成 N 个**非重叠**时间片，每片原始帧 ≈ `_SUBAGENT_BUDGET`(~40K)。非重叠关键 —— 子 agent 不能中途协调，orchestrator 必须预切干净。N **effort-scaled**：1 片(单 pass)/2–4(一天)/10+(一周)。
2. **Map**：`asyncio.gather` + `Semaphore(2–3)`（对齐 `WorkerConfig.vlm_concurrency`，别打爆单 3080）并发。每片：`get_raw(片)` → **单次受约束 `chat.completions.create(tools=None)`**（带 question + 该片原始帧）→ 返 **≤2K token 压缩 finding**（带源 episode/帧 ID）。每个子 agent 烧自己的上下文在原始帧上；**orchestrator 永不见它们**。
3. **Reduce**：orchestrator concat N 个 finding（N×~2K，7 天 ≈14K 仍 < 50K），直接推理或一次 reduce 调用。每个 finding 留源 ID → reduced 答案有依据、可审计。

### 锁定 T1 —— 子 agent 是单次 summarizer，不是完整 AgentRunner

> 三个 lens 在这里有冲突；**采纳 agent-runtime 的方案。** map worker 的活是"读这片预切原始帧 + question → 返 ≤2K 有依据 finding"，需要零 tool-calling、零 6 轮循环、零 SSE。每片 spawn 完整 `AgentRunner` 会把 180s 超时 + 迭代开销 × N，并在每个子里重引入无界 `convo` 风险。

实现：

- `deep_scan` 是 `_IMPLS` 普通工具，`dispatch_tool` 像别的工具 await 它，**父循环不变**。
- 内部跑**专用轻量 summarizer**（单次 `chat.completions.create`），复用 runner 持有的同一 `VLMConfig`/openai client（比 `ReportGenerator` 更轻 —— 无流式循环）。
- 并发用裸 `asyncio`（`gather`+`Semaphore`），**无需 Celery/Workflow 引擎** —— FastAPI+asyncio 原生扛这种有界 fan-out；它是 per-request 工具，不是 TaskGroup 任务。
- 它**不返 `items`** → 不 bump `records_consulted`；返 `{subagents:N, frames_scanned:M, finding:.., evidence_ids:[..]}`，另设 telemetry + `subagent` SSE 事件（§5）。
- 守 `dispatch_tool` 永不抛契约：子 agent 失败返部分 reduce + `{degraded:true}`，不抛。

orchestrator = 现有 `AgentRunner`（它把 `deep_scan` 当工具调）；只有 **worker** 轻量。这也让 `deep_scan` 可 MCP 暴露（返紧凑 dict）。

---

## 4. token 预算双重防御

> 循环必须**无法**超预算，不只是被劝阻（更大窗不是解药 —— context rot；by-construction 限界）。

1. **per-tool 输出 cap（主防御）**：每 read 工具按 **token 估算**（不是行数）cap 自己输出 —— `query_stats`/`get_digest` 设计上小；`get_episodes` ~6K；`search_episodes` ~2.4K；`get_raw` 窗 ≤2h 且 ~10K 且 `vlm_desc[:200]`；`deep_scan` 返 ≤2K（reduce）。token 估算 helper（CJK-aware `~chars/3.5`）gate 每工具序列化循环。没有单工具能返 > ~10K → `convo` 稳在 50K。
2. **runner turn-budget gate（次防御）**：累计已 append 到 `convo` 的工具结果估算 token。下次 dispatch 前若 `cumulative + 下一工具上限 > _TURN_BUDGET`(~40K)，runner **拒绝再 read、提前逼散文**（复用 iteration-exhaustion 的 drop-`tools=` 机制），发 `budget` SSE。
3. **无静默增长**：工具现返**小**预聚合结果，单调增长 by-construction 有界（每轮 ≤10K，6 轮 ≤60K，#2 gate 在那之前切）。可选 compaction 备着但基本不需要。`deep_scan` 的隔离就是它的 compaction —— 原始帧从不进父 `convo`。

---

## 5. SSE 协议 + 前端协同（每加一项要全改、否则静默 no-op）

前端 `agentApi.ts` 是**封闭判别联合**：新工具走通用 `tool` 块（`ToolChip` 自动支持），但新**事件类型**被 `postSSE` 解析却被 `AgentPage.applyEvent` 的显式 if 链**静默忽略**。所以必须协同。

| 事件 | 形状 | 为什么 |
|---|---|---|
| `step` | 不变 `{phase, tool, args/summary}` | 新工具免费搭车 |
| `budget`（新） | `{type:'budget', used_tokens, limit, action:'forced_answer'|'notice'}` | 让 §4 提前/部分作答可见 |
| `subagent`（新） | `{type:'subagent', phase:'fanout'|'reduce', n, frames_scanned}` | 让罕见 `deep_scan` 的 30s 停顿可读，非神秘 |
| `done` | 扩 `episodes_consulted`、`subagents`（`deep_scan` 不返 `items` 不 bump `records_consulted`） | 分别报分层 telemetry |

**协同 edit-set 清单**（任一遗漏 = 静默 no-op）：

1. `agentApi.ts`：`AgentEvent` union 加 `budget`+`subagent`，扩 `done` payload。
2. `AgentPage.tsx::applyEvent`：加 `budget`（内联系统注记）/`subagent`（fan-out 进度块）分支。
3. `agentSessions.ts`：加新 `AssistantBlock` kind（`'subagent'`/`'notice'`），跨 localStorage 会话 reload 持久。
4. `TurnView.tsx::TOOL_LABELS`：加 6 个新工具中文 label（`query_stats`→"统计"、`get_episodes`→"活动片段"、`get_digest`→"摘要"、`search_episodes`→"语义检索"、`get_raw`→"原始帧"、`deep_scan`→"深度扫描"）。
5. `runner.py::_summarize`：加分支让每工具 chip 显示有意义文本（`query_stats`→"N 项统计"、`get_digest`→"<period> 摘要"、`deep_scan`→"扫描 M 帧/N 子任务"），而非通用"完成"。

`postSSE<T>` reader 本身事件无关、chat+reports 复用 → 新流式免费搭车，只改 consumer。

---

## 6. ReportGenerator：读 digest 不扫原始帧

今天 `ReportGenerator` 复用 `AgentRunner` + report persona、调**同样的 raw-reader** over `recent_7d=168h` 窗 —— 定时报告带着和 chat 同样的无界上下文风险、还被 168h 放大。倒置：

- **报告变成对预计算 L3/L4 digest 的薄渲染**。report persona 改调 `get_digest`/`query_stats`/`search_episodes`，**绝不 `get_raw`**。周报推理 ~7 条日 digest + 1 条周 digest（几 K tok），不是 168h 帧。贵 read 早在 nightly rollup 付过。
- **保留 `reports` 表为渲染 sink**（HTML，append-only，`get_latest_report`），但改 **从 `digests` 表 read**（`reports`=渲染层、`digests`=结构化源）。无需新报告路由；`SCOPE_HOURS` 可加 digest-aligned scope（`daily`/`weekly`/`monthly` → digest `level`）。
- report SSE 不变（`step|token|report|error`）。persona 现用廉价工具，`budget` 事件几乎用不到。
- 30min `_report_scheduler` 模板**复制**出 nightly `_rollup_loop`（见 [pipeline](episode-and-rollup-pipeline.md)），report scheduler 只从既存 digest 重渲染。

---

## 7. MCP 平价 —— 设计掉 #1 drift 风险

最大 drift（findings）：加工具到 `agent/tools.py` 自动点亮 web agent，但 MCP 是 **6 个手写 `@mcp.tool()` 闭包**（`mcp_layer/server.py`）须手动同步，SKILL.md 是**第三**份手抄清单。激进重设计折叠它们：

1. **MCP 从 `TOOL_SCHEMAS` 生成**：`build_mcp_server` 里循环——对每个 `agent_tools.TOOL_SCHEMAS` 条目注册一个委托 `dispatch_tool(db,name,args)` 的 `@mcp.tool()` wrapper。MCP 再不会偏离 web agent —— 一个注册源、两个 consumer。（`apply_label` 写确认 UX 不同可留 bespoke；`deep_scan` 返紧凑 dict 也可 MCP 暴露。）
2. **杀掉反模式 `ask_agent`**：MCP-only、绕 `AgentRunner`、80 帧塞一 prompt、重复 `format_record_for_llm`。金字塔里它被严格 dominate —— 重指向同套分层工具（`query_stats`/`get_episodes`/`search_episodes`），删除（或保留则重实现在 `get_episodes`+`get_digest` 上、绝不 `get_raw`）。
3. **删孤儿 `server/mcp_layer/tools.py`**（死代码、零 live import），按 `## **⚠️ …**` 约定修 CLAUDE.md stub 表 / `mcp-layer.md` "4 tools" 散文。
4. **SKILL.md 工具段从 `TOOL_SCHEMAS` 生成**（或加测试断言 SKILL.md 提及每个 `_IMPLS` 名 + 6 类）。新工具自动传到外部 agent onboarding。

净效果：**一个真源（`TOOL_SCHEMAS`+`dispatch_tool`）**喂 web agent / MCP / ReportGenerator / (生成的) SKILL.md，无手抄、无 drift。

---

## 8. 处置决定（别再 re-litigate）

- `hours_back_hint` **死参数**：两 caller 都传、`run()` 从不读。**端到端删除**（route 字段 + 前端 `opts.hoursBack` + runner 参数）——路由现用显式工具参数定窗。
- 旧工具名 = 薄 shim（§1.7）。
- "现在在做什么" 读最新原始帧、不读 episode（§2 worked trace 1 ⚠️）。

---

## 9. 新旧对照

| 关注点 | 旧（raw-at-query） | 新（薄路由器 over 金字塔） |
|---|---|---|
| 工具 | 5 raw-reader（≤100/≤200 帧） | 6 分层 + `deep_scan` fallback |
| "这个月娱乐多少小时" 成本 | 扫一月帧（≫50K） | 一条 `query_stats` 行，~0 LLM tok |
| 贵 read 发生在 | 每次 query | 一次，写时（episode/digest/signal builder） |
| 语义检索 | 未暴露的全表 per-frame 扫 | `search_episodes` over 小 `summary_embedding` 索引 + RRF |
| 上下文安全 | `convo` 无界、不驱逐 | per-tool token cap + runner turn-budget gate；子 agent 隔离原始扫描 |
| 大窗原始扫描 | 不可能/破窗 | `deep_scan` map-reduce fallback（隔离 ≤40K 子上下文 → ≤2K finding） |
| 报告 | 每 30min 扫 168h 原始帧 | 对预计算 digest 薄渲染 |
| MCP/SKILL drift | 3 份手抄清单 | 全从 `TOOL_SCHEMAS` 生成 |
| 写面 | 仅 `apply_label` | 不变——派生层由后台 job 写、agent 永不碰 |

---

## 相关文件

- `src/timetrace/server/agent/tools.py`（6 工具 + token helper）· `runner.py`（系统 prompt / turn-budget gate / `_summarize` / `budget`+`subagent` SSE / `deep_scan` summarizer）
- `src/timetrace/server/report/generator.py`（persona 改 `get_digest`/`query_stats`）· `server/mcp_layer/server.py`（从 `TOOL_SCHEMAS` 生成；删 `ask_agent`）· `mcp_layer/tools.py`（删）
- `skills/timetrace/SKILL.md`（从 `TOOL_SCHEMAS` 生成）
- `frontend/src/lib/agentApi.ts` · `agentSessions.ts` · `pages/AgentPage.tsx` · `components/agent/TurnView.tsx`
- [PLAN-BETTER-AGENT.md](../PLAN-BETTER-AGENT.md) · [storage/pyramid-schema.md](../storage/pyramid-schema.md) · [episode-and-rollup-pipeline.md](episode-and-rollup-pipeline.md)
