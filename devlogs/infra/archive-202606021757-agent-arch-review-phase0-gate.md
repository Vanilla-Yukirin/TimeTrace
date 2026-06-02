# 隔壁 agent 4 篇架构文档评审：14-agent workflow 判 sound-with-gaps + Phase 0 gate

**日期：** 2026-06-02
**目标：** 评审隔壁 agent 起草的"更好的 agent"架构书（金字塔 L0–L4 + 瘦路由 agent，共 4 篇），用多 agent workflow 对抗式深读 + 完备性批判，并把结论对照线上 box DB 真因落到"该不该建、怎么排期"。

---

## 背景

隔壁 agent 意识到现有 agent（`server/agent/runner.py` 单 tool-loop，query-time 现读原始帧、两次大 read 破本地 50K 上下文）的问题后，起草了 4 篇架构文档：

- [infra/PLAN-BETTER-AGENT.md](../../infra/PLAN-BETTER-AGENT.md)（总纲：L0→L4 金字塔 + 薄路由器 + token 数学 + prior-art + 分期）
- [infra/storage/pyramid-schema.md](../../infra/storage/pyramid-schema.md)（episodes/digests/signals DDL + 迁移/幂等/回填）
- [infra/architecture/episode-and-rollup-pipeline.md](../../infra/architecture/episode-and-rollup-pipeline.md)（写时 builder + 分段 + 降级契约）
- [infra/architecture/thin-router-agent.md](../../infra/architecture/thin-router-agent.md)（分层工具 + 路由 persona + 子 agent + MCP fold）

用户要主 agent review。恰逢同期只读取证 box DB 发现看板报告 4 个真因（见 backend 同期归档），故评审重点不只是"设计是否优雅"，而是"它解没解线上真问题"。

---

## 操作步骤

### 1. 先 inline + Explore 摸清现状

确认现有 agent = `runner.py` 6 轮 5 工具 tool-loop，无 episode/digest/派生信号；`server/report/generator.py::_REPORT_SYSTEM` 是看板报告 persona；`server/rules/engine.py::RuleSet` 存在但 worker 永远传空；`settings` 表存在但空 + `get_setting/set_setting` 已实现但无 HTTP 路由。

### 2. 跑一个 14-agent Workflow（~890K token）

结构：4 篇逐篇深读 reviewer（schema 化输出 verdict/strengths/gaps/codebase_mismatches/grounding_check/over_engineering）→ 1 个完备性 critic（跨文档不一致 + 最大风险 + 全篇都漏 + "让明早报告准确的最小集"）→ 2 个功能设计（per-app 覆盖 + 看板 prompt）各带对抗式可行性校验 → 1 个综合。每个 reviewer 都被要求对着真实源码复核承重声明，并对照 box DB 真因打分。

### 3. 评审结论

**四篇一致 `sound-with-gaps`。** 优点是真优点（write-time amortization 是 token 墙正解、对单用户比例恰当、代码引用异常诚实且逐条复核为真）；但 4 个线上真问题里，3 个住在金字塔之下的 L0/L1 数据 + 报告 persona 层（它坐其上不修），第 4 个（时长膨胀）被**主动继承**——四篇都复用 `SUM(MAX(0,...))`（只防负不封顶）、无一引用已有的 `_cap_implausible_record_durations`，**提议的 SUM-invariant 还会把膨胀总数认证成"正确"**。

四篇通病（critic 锁定）：① 时长封顶被继承且被 CI 认证；② NULL≠uncategorized 没人区分、会冻结进 digest 永久化；③ 3063 遗留没 L1 `category_final` 回填路径；④ 报告臆造源头 persona 没碰。

codebase 不符抽样：thin-router 反复说 MCP 是"6 个手写闭包"，实际 `mcp_layer/server.py` 注册 **7 个**（多一个 `ask_agent`），且文件 docstring 还停在"four tools"。

过度设计：L4 周/月 digest、deep_scan 子 agent map-reduce、双 token-budget+SSE 对单用户偏重，应最低优先级或砍。

### 4. critic 的判决 → Phase 0 gate

> 建金字塔，但 gate 在 Phase 0 数据卫生之后：① 跑已有时长 cap、② 回填 NULL 分类、③ 给 split 应用种 RuleSet、④ 重写报告 persona。**这四件每件约一天、都不需要金字塔，正好是让报告准确的最小集。**

主 agent 当晚就把这四件 Phase 0 全做了并部署（见 backend 同期归档），所以评审要求的"前置数据卫生"已落地。

### 5. 排期决策（用户拍板）

用户：金字塔（进一步架构落地）**先不做**——多天工程、明早演示来不及，且 critic 明说"建在未清洗数据上会自信地错"。给 4 篇文档补评审修正也**先不动**，记进 PLAN、排到**周三（2026-06-03）演示之后再开始**，演示前冻结。

---

## 遇到的问题与解决

### 问题1：评审标准容易停在"设计是否优雅"

**解决：** 把 box DB 真因作为 ground truth 喂进每个 reviewer 的 prompt，强制它判"解没解真问题"。结果四篇 grounding_check 全是 partial/weak——优雅但高度不对。

### 问题2：金字塔会不会"自信地错"

**现象：** 承重层（episode/digest）若建在 NULL 污染 + 9h 睡眠膨胀 + 旧 20 类残留的数据上，会把脏数据冻结进 frozen summary / UPSERT digest，比原始行更难修，且 agent 以 ~0 token 信任它。
**解决：** Phase 0 gate——先清 L0/L1 再盖。用户采纳，排到演示后。

---

## 知识清单

- **评审要对照真数据而非只看设计**：把生产 DB 真因喂进 reviewer，能立刻暴露"优雅但高度不对"——解的是 token 问题，不是线上的数据问题。
- **金字塔的危险=自信地错**：写时聚合层会把当下脏数据永久化且被下游高置信信任；承重层必须 gate 在数据卫生之后。
- **SUM-invariant 的反作用**：若不变量断言 `episode.duration == 原始 SUM(MAX(0,...))`，它会把膨胀总数认证成"正确"，该 fail 的 bug 反而 pass → 应改 capped SUM-invariant。
- **诚实的代码引用值得肯定**：四篇逐条复核承重声明（claim_next_task prefix-swap、_report_scheduler 模板、空 RuleSet、settings KV、full-table vector_search）全为真，是设计文档里少见的严谨——问题在高度不在事实。
- **多 agent 评审的产出**：reviewer 用 schema 强约束输出 + critic 做跨文档收口 + 综合 agent 出 planning markdown，主 agent 据此落 Phase 0 并排期，比单 agent 通读更不易漏。

---

## 待办 / 遗留

- [ ] 【周三 2026-06-03 演示之后】给 4 篇架构文档补评审修正（4 个通病 + "Phase 0 已完成"前置），并启动 pyramid Phase 1（L2 episodes + 日 digest + capped SUM-invariant 测试）。详见 [infra/PLAN-REPORT-ACCURACY-FIXES.md](../../infra/PLAN-REPORT-ACCURACY-FIXES.md) §2（演示前冻结）。
- [ ] Phase-3 MCP fold 前先对齐 MCP/tool 计数 off-by-one（实际 7 含 ask_agent）与过期 docstring。
