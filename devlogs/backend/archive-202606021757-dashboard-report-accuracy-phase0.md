# 看板报告准确性 Phase 0：诊断"未分类臆造" + 拆桶/时长封顶/防臆造 prompt + per-app 覆盖（含前端编辑器）

**日期：** 2026-06-02
**目标：** 用户看板报告把 Vanish/Claude 说成"未分类的神秘应用"。只读取证线上 box 找真因，落地一批纯代码/数据卫生修复让报告准确，并把"per-app 知识/分类覆盖"做成可配置（后端 + 前端 KV 编辑器）。结论：报告出错不是 AI 不认识应用，而是数据标签 + Prompt + 死规则三层 bug。

---

## 背景

用户给了两段看板报告原文，里面把 `Vanish(4096秒)` 和 `Claude(684秒)` 归为"隐藏 BOSS·未分类"，并臆造"你的 AI 助手和隐私工具才是真正的神秘玩家"。用户的直觉是"agent 不知道 Vanish（公司内部 IM）是什么"，想在前端设置里加一个 prompt/键值对设置（key=软件名、value=该应用的定义和规则，像 PaaS 环境变量那样带 +/- 按钮）。

但只读翻 box DB 后发现报告的前提本身是错的（见下），真因完全不同。

---

## 操作步骤

### 1. 只读取证线上 box（`GTi13-Ultra-2v4G` → `121.43.33.13:10089`）

box 上 `sqlite3` CLI 不存在 → 改用 Python stdlib `sqlite3.connect("file:...?mode=ro", uri=True)` 只读查询。关键发现（recent 24h，DB `/home/vanilla/TimeTraceData/db/timetrace.db`）：

- `analysis_results.status` 全部 4239 行 = `vlm_done`，**零积压**。VLM 早就描述完了。
- `category_final='uncategorized'` **全库 0 条**——没有任何记录是真正"未分类"。
- 但 **3063 条 `category_final IS NULL`**（带 `vlm_desc` + analysis 行），是**旧 worker（今天 commit `26ccbd5` 之前）描述了却从没调 `set_category_final` 的遗留**，约占近期 **72%**。
- 报告里那个"未分类 3h15m"其实就是这个 NULL 遗留桶——`get_category_stats` 用 `COALESCE(category_final,'uncategorized')` 把它误标成了 uncategorized。
- **Vanish**：已分类的 social=101/work=21/system=2，NULL=190；`vlm_desc` 明写"Vanish桌面端即时通讯软件"还读出群名。VLM 早认得它是 IM，只是被逐帧抖动**撕裂**成 social/work。
- **Claude**：work=36/system=5/NULL=41；`vlm_desc`="Claude AI 桌面应用"。
- 附带 bug：**2014 条 `ts_end-ts_start > 1h`、全 ~9h、全 `event_type=window_switch`** = 笔记本休眠/合盖伪影，把所有时长撑爆。

→ 报告的"Vanish/Claude 躺在未分类因为 agent 不认识它们"是**臆造**。真因：① NULL 遗留被误标 uncategorized；② 时长膨胀；③ prompt 鼓励编故事；④ 死规则（空 RuleSet）导致 Vanish 分类撕裂。

### 2. 跑 14-agent workflow 评审隔壁 agent 的 4 篇架构文档（详见 infra 同期归档）

结论 sound-with-gaps，且 critic 给出"让明早报告准确的最小集"=正好是下面 Phase 0 四件事，都不需要 pyramid。

### 3. P0.1 修 `server/agent/tools.py`（拆桶 + 时长封顶）

- `get_category_stats` SQL 拆桶：`CASE WHEN a.record_id IS NULL OR a.category_final IS NULL THEN '_unclassified' ELSE a.category_final END`。items 里 `_unclassified` 标 `is_unclassified` + `note`（系统内部积压，非用户行为），真 `uncategorized` 标 `is_genuinely_uncategorized`。
- 新增 `_clamped_dur_sql(prefix)`：单条记录跨度 `> _MAX_PLAUSIBLE_RECORD_MS(5min)` 记 0、负跨度 floor 到 0——**镜像 DB 侧 `_cap_implausible_record_durations`（`db/sqlite.py:605`）到读路径**（DB 那个只在 init 跑，常驻机重启间隙 9h 伪影一直 live）。`get_app_breakdown` 同步。
- 返回加 `capped_per_record_seconds` + `categories_legend`（6 id→中文）；修过期 docstring + tool schema 描述（删"未分类归 uncategorized"误导文案）。

### 4. P0.2 重写 `server/report/generator.py` 的 `_REPORT_SYSTEM`（防臆造）

教 flat-6 分类；强约束 `_unclassified`（系统内部积压）≠ `uncategorized`、前者不当用户行为讲；先看 `vlm_desc` 再判断 app 是否"未知"；时长用"约/大概"、可疑 >1h 单条存疑；看到大桶时引导用户配 per-app 规则而非编理由；**删"摸鱼时刻/反差亮点"**（臆造诱因）；保留网易云口吻 + "无数据出处不写"铁律。（AgentRunner 是 append 不是 `.format()`，prompt 里字面 `{}` 不崩；`test_reports.py` stub 了 runner 不断言 prompt。）

### 5. P0.3 per-app 覆盖后端（空默认 = 零行为变化）

- 新 `server/settings/overrides.py`：`load_overrides`/`validate_overrides`/`save_overrides`/`build_ruleset`/`find_note`，单一来源；settings key=`app_overrides`，形状 `{version,apps:{<token>:{category,note}}}`；category 限 6 个 id、note ≤300 字；**note 查找按子串扫描不是 dict 索引**（key 是用户 token、`app_name` 是全名）。
- `server/worker/loop.py`：每任务 `load_overrides` → `build_ruleset(...)` 取代空 `RuleSet()`（用户钉的分类权重 2.0 确定性压过 VLM 1.5）+ `find_note(...)` 注入 VLM describe。
- `server/vlm/client.py`：`describe(..., app_note=None)` 末尾追加用户提供的应用背景。
- `server/api/routes/settings.py`：`GET/PUT /v1/settings/app-overrides`，gate 在 `business_deps`，非法 category/超量 → 400。

### 6. 前端 KV 编辑器（P1.1）

`frontend/src/components/admin/AppOverridesSection.tsx`：PaaS 环境变量风格，每行 `[应用名][固定分类下拉][说明][删除]` + 添加/保存。react-query useQuery/useMutation 模式照 `TokenManager`，样式照现有 inline-style + CSS 变量。**分类下拉硬编码 flat-6**（不读 `/v1/categories`——box 上还有旧 20 类两级 taxonomy 会冒废弃项）。`api.ts`/`queryKeys.ts`/`types/api.ts` 各加对应项。挂在设置页 EmbeddingDiagnostics 下面。`npm run build`（tsc+vite）通过。

### 7. 测试 + 提交

- 新增 12 测试：`test_agent_tools` 拆桶/封顶 + `test_app_overrides`（模块/路由/规则压过 VLM）；改 `test_worker_pipeline` 的 fake describe 加 `app_note`。**467 passed**，ruff check/format 全绿。
- commit `1e0d134`（后端 Phase 0）、`9cb754f`（前端编辑器），push origin `feature/refactor-split`（clash 偶发 exit 128，重试即过）。

### 8. 授权回填 box（用户选"仅规则命中"，Vanish/Claude→work）

只读 dry-run 预览（231 行：Vanish 190 + Claude 41，子串只命中 `Vanish`/`Claude` 无误伤）→ 备份 231 行到 `~/TimeTraceData/category_backfill_backup_20260602.json` → `UPDATE analysis_results SET category_final='work' WHERE category_final IS NULL AND record_id IN (Vanish/Claude)` → 种下 `app_overrides` 设置（vanish/claude→work + note）→ 复核：Vanish/Claude NULL 归零、work 上升、设置就位。用读写连接 `timeout=10` + `PRAGMA busy_timeout=8000` 避与运行中 server 抢写锁。

### 9. 部署 + 验证

用户自己 `gh workflow run deploy.yml --ref feature/refactor-split` 触发（run 26809213804，✓ 18s）。只读验证 box 跑新代码：`/v1/settings/app-overrides` 返 **401**（路由存在+gate，旧代码会 404）、`/healthz` 200。

---

## 遇到的问题与解决

### 问题1：报告把"系统内部积压"当成"用户行为洞察"还臆造具体故事

**现象：** "未分类 3h15m，Vanish/Claude 是隐藏 BOSS"。
**根因：** `COALESCE(category_final,'uncategorized')` 把 NULL 遗留和真 uncategorized 抹平 + prompt 让模型找"反差亮点"。
**解决：** 拆桶（数据层）+ 重写 prompt 教模型区分 + 引导（prompt 层）。VLM 从未困惑，困惑的是工具和 prompt。

### 问题2：时长全被 9h 睡眠伪影撑爆

**现象：** 单应用动辄一天 9 小时。
**根因：** 聚合 clamp 只有 `MAX(0,...)` 防负、无上限；仓库已有的 `_cap_implausible_record_durations`（>5min 封顶）只在 `init()` 跑，常驻机重启间隙不生效。
**解决：** 读侧 `_clamped_dur_sql` 复用同一 5min 阈值（`_MAX_ORPHAN_BRIDGE_MS`），单条 >5min 记 0。按 capture 设计（≤30s 心跳 + 180s idle 关闭）正常无单条 >5min，故 cap 安全。

### 问题3：ruff E501 对中文按双宽计

**现象：** triple-quoted prompt 多行报 line-too-long。
**解决：** 改回括号隐式字符串拼接（原 prompt 风格），每段中文 ≤~40 字。

### 问题4：用户事后担心"是否破坏旧数据/来不及"

**解决：** 澄清——代码改动纯读侧/prompt/加功能零数据写；唯一数据写是回填 231 条**本来就空**的分类（用户确认 Vanish/Claude→work）+ 已备份可逆。Phase 0 已部署上线，明天报告只会更准。pyramid（多天工程）明确**先不做**，排到周三演示之后。

---

## 知识清单

- **NULL ≠ uncategorized**：`category_final IS NULL`（描述过但没分类的遗留/排队）和真 `uncategorized`（分类器判归不进任何类）语义完全不同，工具层必须分桶，否则报告把系统内部状态当用户行为。线上真 uncategorized 是 0 条，"未分类"全是 NULL 遗留。
- **读侧时长封顶**：DB 侧 cap 只在 init 跑、常驻机重启间隙失效 → 在聚合 SQL 里复用同阈值（5min）做读侧封顶，单条 >5min 记 0。按 capture 设计正常无单条 >5min。
- **死规则的代价**：`worker/loop.py` 长期 new 空 `RuleSet()` → 确定性规则层（权重 2.0 > vlm 1.5）形同虚设，同一 IM 被逐帧 VLM 判断撕成 social/work。一条 `app=Vanish→work` 规则即确定性修好。
- **per-app 覆盖单一来源**：`overrides.py` 同时喂 worker（RuleSet）、VLM describe（note）、HTTP 路由，wire 形状不漂移；note 查找必须子串扫描不能 dict 索引。
- **前端分类下拉别信 `getCategories`**：box 上旧 20 类两级 taxonomy 仍在，硬编码 flat-6 更安全。
- **box DB 写授权边界**：只读诊断随便做；写要用户明确授权 + dry-run 预览 + 备份可逆。回填"仅规则命中"= 只填本来为空的格子、不覆盖任何已有分类。
- **报告返回形状变化 additive-safe**：runner JSON 序列化整体、MCP 原样返回、前端两处 `get_category_stats` 引用只是工具名→标签映射，新增字段不破 parser，仅一个测试断言要改。

---

## 待办 / 遗留

- [ ] 【周三演示之后】pyramid 本体 + 给 4 篇架构文档补评审修正（见 [infra/PLAN-REPORT-ACCURACY-FIXES.md](../../infra/PLAN-REPORT-ACCURACY-FIXES.md) §2，演示前冻结）。
- [ ] 【需授权】其余 ~2800 条旧 NULL 仍未回填（报告会诚实显示"尚未分类/待回填"）；若要更满的报告可扩到高频应用规则回填。
- [ ] 【可选·未授权】清理 box 旧 20 类两级 taxonomy（`apply_label` by-id 校验会认旧 id）。
- [ ] 【infra agent / 用户】前端 scp 到 VPS，公网才看得到新编辑器 + 新看板。
- [ ] outbox 备份 / `category_backfill_backup_20260602.json` 确认无误后可删。
