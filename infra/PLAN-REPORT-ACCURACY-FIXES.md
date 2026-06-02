# PLAN：看板报告准确性修复 + per-app 覆盖 + Pyramid 落地排期

> 状态：Phase 0 已落地（本会话，纯代码、零 DB 写），Phase 1+ 待办。
> 综合来源：4 篇架构文档评审（[PLAN-BETTER-AGENT.md](PLAN-BETTER-AGENT.md) + [pyramid-schema](storage/pyramid-schema.md) + [episode-pipeline](architecture/episode-and-rollup-pipeline.md) + [thin-router](architecture/thin-router-agent.md)）+ 线上 box DB 只读取证 + 2 个功能设计 + 完备性批判（14-agent workflow）。
> **核心结论：看板报告不是因为 AI 不认识应用而出错，而是『数据标签 + Prompt + 死规则』三层 bug。** 三个廉价修复即可让报告准确；pyramid 是正确的长期方向，但必须 gate 在数据清洗之后。

---

## 0. 文档评审结论

四篇文档一致评级 **sound-with-gaps**：

- **优点**：write-time amortization 是「500K-token/天 vs ~50K 上下文」这堵墙的正确答案；对单用户本地优先 app 比例恰当（复用 `claim_next_task` / `_report_scheduler` / `TaskGroup` / settings KV，不另造 job 框架）；代码引用异常诚实准确（承重声明已逐条复核为真：空 `RuleSet()` @ `worker/loop.py`、init-only 时长 cap @ `sqlite.py:288/605`、`COALESCE(...,'uncategorized')` @ `tools.py`、`get_setting/set_setting`、flat-6 categories）。
- **致命 gap 是「高度不对」**：四个真实线上问题里，三个（NULL-vs-uncategorized、3063 未回填遗留、报告臆造）住在 pyramid 之下的 L0/L1 数据与报告 persona 层，pyramid 坐其上但不修；第四个（睡眠时长膨胀）被 pyramid **主动继承**——四篇都复用 `SUM(MAX(0, ts_end-ts_start))`（只防负不封顶），无一引用已存在的 `_cap_implausible_record_durations`（>5min 封顶）。提议的 SUM-invariant 甚至会把膨胀总数**认证成「正确」**。
- **判决**：建 pyramid，但 **gate 在 Phase 0 数据卫生之后**；先发 L2 episodes + day digest，推迟 L4 周/月 digest、deep_scan map-reduce、双 token-budget+SSE、第二个 summary_embedding 索引。Phase-3 schema fold 前先对齐 MCP/tool 计数 off-by-one 与 `tools.py` 过期 docstring。

---

## 1. 根因复盘（已对源码 + 线上 DB 逐条核对）

| # | 根因 | 证据 |
|---|------|------|
| 1 | **数据误标**：`get_category_stats` 用 `COALESCE(a.category_final,'uncategorized')` 把「真未分类（全库 0 条）」和「3063 条 NULL 遗留（pre-`26ccbd5` 的 worker 描述过但没调 `set_category_final`）」抹成同一个「未分类」桶（约占近 24h 记录 **72%**） | `tools.py`；线上：`category_final='uncategorized'` 全库 0 条、3063 条 NULL 带 `vlm_desc` |
| 2 | **时长膨胀**：聚合 clamp 只有 `MAX(0,...)` 无上限，**2014 条 ~9h 的 `window_switch` 睡眠记录**撑爆所有时长。真正的修复 `_cap_implausible_record_durations`（>5min 封顶）只在 `init()` 跑，常驻小主机重启间隙这些行一直 live | `sqlite.py:288,605`；`_MAX_ORPHAN_BRIDGE_MS=5*60*1000` |
| 3 | **Prompt 臆造**：`_REPORT_SYSTEM` 不教 6 分类、不区分 NULL/uncategorized、不提睡眠膨胀，反而显式叫模型找「摸鱼时刻/反差亮点」 | `report/generator.py` |
| 4 | **死规则**（次要）：`worker/loop.py` 每次 new 空 `RuleSet()`，确定性规则层（权重 2.0 > vlm 1.5）形同虚设，Vanish 被逐帧 VLM 抖动撕成 social=101 / work=21 / system=2 / NULL=190 | `worker/loop.py`；`engine.py` SOURCE_WEIGHTS |

> VLM 从未困惑：`vlm_desc` 明写「Vanish桌面端即时通讯软件」「Claude AI 桌面应用」。困惑的是工具和 prompt。报告里「Vanish/Claude 躺在未分类因为 agent 不认识它们」是 **臆造**。

---

## 2. 排期

### Phase 0 — 数据卫生（✅ 已落地本会话，纯代码、零 DB 写、零迁移）

- **P0.1 ✅ `get_category_stats` 拆桶 + 时长封顶**（`server/agent/tools.py`）
  - SQL 拆桶：NULL（无 analysis 行 **或** `category_final IS NULL`）归 `_unclassified`（items 标 `is_unclassified` + `note`）；真 `uncategorized` 单独桶并标 `is_genuinely_uncategorized`。
  - 时长封顶：新增 `_clamped_dur_sql()`，单条记录 `> _MAX_PLAUSIBLE_RECORD_MS(5min)` 的跨度记 0（镜像 DB 侧 cap），负跨度 floor 到 0。`get_app_breakdown` 同步。
  - 返回新增 `capped_per_record_seconds` + `categories_legend`（6 id→中文）；修过期 docstring + tool schema 描述。
  - 测试：`test_get_category_stats_buckets_unclassified_not_uncategorized`、`test_get_app_breakdown_caps_sleep_inflated_spans`。
- **P0.2 ✅ 重写 `_REPORT_SYSTEM`**（`server/report/generator.py`，纯 prompt）
  - 教 flat-6；明确 `_unclassified`（系统内部积压）≠ `uncategorized`，前者不得当用户行为讲；先看 `vlm_desc` 再判断 app 是否「未知」；时长用「约/大概」、可疑 >1h 单条存疑；**删「摸鱼时刻/反差亮点」**；看到大 `_unclassified`/`uncategorized` 桶时引导用户去配 per-app 规则而非编理由；保留网易云口吻 + 「无数据出处不写」铁律。
- **P0.3 ✅ per-app 覆盖后端**（`server/settings/overrides.py` + worker 接线 + 路由，**空默认 = 零行为变化**）
  - 新模块 `overrides.py`：`load_overrides` / `validate_overrides` / `save_overrides` / `build_ruleset` / `find_note`。单一来源；settings key=`app_overrides`，形状 `{version, apps:{<token>:{category, note}}}`；category 限 6 个 id；note ≤300 字；note 查找按 substring 扫描（不是 dict 索引）。
  - `worker/loop.py`：每任务 `load_overrides` → `build_ruleset(...)` 取代空 `RuleSet()`（用户钉的分类确定性压过 VLM）+ `find_note(...)` 注入 VLM describe。
  - `vlm/client.py`：`describe(..., app_note=None)` 末尾追加用户提供的应用背景。
  - 路由 `routes/settings.py`：`GET/PUT /v1/settings/app-overrides`，gate 在 `business_deps`；非法 category/超量 → 400。
  - 测试：`tests/test_app_overrides.py`（模块 + 路由 + 「规则压过 VLM」整合）。

> Phase 0 全量 **467 passed**（新增 12 测试）、ruff check/format 全绿。需经 deploy 工作流部署到 box 才对线上报告生效。

### Phase 1 — 用户可配 UI + 历史回填（待办）

- **P1.1 前端 Settings KV 编辑器**（待用户确认是否现在做；UI 审美归用户）
  - `AppOverridesSection.tsx`：每行 `[app 名] [分类下拉(6 builtin)] [说明] [−]` + `[+ 添加]` + 保存；react-query/apiFetch 模式照 `AccountSection`/`TokenManager`（**不是** `EmbeddingDiagnostics`，那是 localStorage-only），视觉 style 常量照 `EmbeddingDiagnostics`。
  - 分类 `<select>` **客户端硬过滤到 6 个 builtin id**（别信 `getCategories`，box 上还有旧 20-cat 残留）。
  - `api.ts` + `queryKeys.ts` 加 `getAppOverrides`/`putAppOverrides` + `appOverrides` key。后端路由已就绪。
- **P1.2 一次性遗留回填（⚠️ 需授权，见 §3）**
  - 对 `status=vlm_done AND category_final IS NULL` 的 3063 行：规则命中写 override 分类，否则保持 NULL 或从 `vlm_desc` 重新派生。**约束**：没有独立存的 VLM 分类列可恢复——非规则行只能拿 override 分类或维持 NULL，除非重调 VLM。做成「应用到历史」按钮 + 受影响条数预览，不要 auto-on-save。

### Phase 2 — Pyramid + thin-router（gate 在 Phase 0 之后，effort L）

- 先发 **L2 episodes + day digest**；推迟 L4 周/月、deep_scan map-reduce、双 token-budget SSE、第二个 summary_embedding 索引。
- 在 episode/digest builder 处**应用时长封顶 + NULL-aware 分类投票**，承重层不浇在脏混凝土上。
- CI 用 **capped SUM-invariant**（不是 raw）+ 补 mixed-stitching 测试。
- 整理 MCP/tool 计数 off-by-one 与过期 docstring 再做 Phase-3 fold。

---

## 3. 需要用户授权的操作（任何对生产 box DB 的写）

1. **回填 3063 条 NULL `category_final`**（P1.2）——先 tmp/哨兵日期 dry run，确认范围；非规则行只能 override 或维持 NULL。
2. **清理旧 20 类两级 taxonomy**（`DELETE FROM categories ...`）——先核对无记录引用旧 id；优先级低于报告准确性。`apply_label` 的 by-id 校验与规则下拉都应只认新 6 个 id。
3. **「应用到历史」若做成 save 时自动跑**——静默重写历史 `category_final`（含覆盖人工 `apply_label`）属生产写，必须显式触发 + 条数预览。

> 时长膨胀**已用 P0.1 读侧 clamp 解决，无需写 DB**；破坏性 `UPDATE records SET ts_end=ts_start WHERE ts_end-ts_start>300000` 或重启触发 init cap 是可选替代，需显式 OK。**禁止 ssh box 改 git/重启**，一律走 deploy 工作流。

---

## 4. 待办（用户提出）

- [ ] **前端看板分类配图（猫猫插画）重设计**：当前矢量插画被认为「一般（丑）」，后续重做。非阻塞，独立排期。

---

## 5. 风险备忘

- **子串匹配脚枪**：`RuleSet.match` 用 `rule['app'].lower() in app.lower()` 首个命中赢；短 key（'word'/'code'）会误伤。UI 要展示 resolved `app_name` 示例、提示是子串非精确。
- **`_MAX_PLAUSIBLE_RECORD_MS=5min` cap**：会低估「单条记录连续 >5min」的场景；但按 capture 设计（≤30s 心跳 + 180s idle 关闭）正常无单条 >5min，>5min 几乎只来自睡眠，故 cap 安全。若将来 capture 改为长记录需重审。
- **box 旧 20-cat 与 flat-6 并存**：`apply_label` by-id 校验会认旧 id，规则下拉直读 `get_categories` 会冒废弃项——读取处一律白名单过滤到 6 个。
- **返回形状变化**（多 `is_unclassified` / `categories_legend` / `capped_per_record_seconds` 等）对 Python 消费方 additive-safe（runner JSON 序列化整体、MCP 原样返回，无 parser 依赖）；前端两处 `get_category_stats` 引用只是工具名→标签显示映射，不消费返回形状。
