# 2026-10 Owner Review：性能 / 图标 / 交互 / 多设备 / 检索与 Agent / 时区

**创建：** 2026-10-22
**基线：** `main` @ `fbffc13`（Linux 本地 617 passed / 7 skipped；Windows-only 采集测试由 CI 覆盖）
**状态：** W1–W6 已合入 `main`（PR #14–#21），未部署。图标替换等 owner 方案。归档见 [`archive-202610220210-owner-review-w1-w6.md`](../devlogs/infra/archive-202610220210-owner-review-w1-w6.md)。

> Owner 在日常使用中提出 5 个不舒服的方向 + 1 个时区决策。本文记录代码层面定位到的根因与修改方向，
> 作为下列 PR 的执行依据。无法访问生产数据，所有改动以架构与测试为依据。

---

## 执行顺序

| 序 | 工作包 | 覆盖问题 | PR |
|---|---|---|---|
| W1 | 前端性能 | §1 | #16 |
| W2 | 多设备叙述过渡修复（不动 schema） | §4 | #15 |
| W3 | 时区契约：UTC 存储 + 请求携带 IANA 时区 | §6 | #17 |
| W4 | 统一检索层 + Agent 工具收敛 + 报告读金字塔 | §5 | #18 |
| W5 | 设备查询闭环 + 多设备泳道时间轴 + 前端交互修复 | §3 §4（= PLAN 原 PR 2） | #19 #20 |
| W6 | 设备级摘要金字塔 v2 | §4（= PLAN 原 PR 3） | #21 |
| — | 图标替换 | §2，只列 TODO，等 owner 方案 | — |

部署不在本轮范围：所有 PR 只合入 `main`，不推进 `deploy`。

---

## §1 GPU / CPU 占用高、打开卡顿

主因在 Web SPA，按影响排序：

1. **今天视图每秒整页重渲染**：`TimelinePage` 的 `useNow(1s)` 在存在 open record 时（今天几乎总是）每秒 setState，`TimelineCanvas` 未 memo，sr-only 列表为全天数千条记录各渲染一个 `<button>`。
   → 时钟下沉到只显示时长的叶子组件；`TimelineCanvas` memo；sr-only 列表只镜像视口内记录。
2. **mousemove 每次 setState + O(N) hitTest**。→ rAF 合并；记录按 `ts_start` 排序后二分裁剪/命中；tooltip 位置不进 React state 驱动整树。
3. **每 30s 全量重拉一天记录**（最多 20 页串行，历史日期也轮询），新数组引用触发整页重绘。→ 历史日不轮询；今天的轮询放缓并保持结构共享。
4. **全屏常驻樱花动画 + `background-attachment: fixed` + `backdrop-filter: blur`**：底下有东西在动时 blur 每帧重算，是 GPU 占用主因。→ 樱花默认关闭（可在设置里开）；去掉 fixed 背景与 blur，改为实色半透明。
5. **全局 `* { transition: ... }`**：所有元素常驻过渡。→ 仅在主题切换瞬间通过 `<html>` class 打开。
6. **wheel 缩放不节流、只看 deltaY 符号、忽略 deltaX**：触控板一划缩放几十次、无法横移。→ 按幅度缩放、deltaX 平移、rAF 合并。

服务端次要因素：单 aiosqlite 连接 + 全局 `asyncio.Lock`，读请求与 worker/rollup/narrate 写串行。WAL 已开，后续可加只读连接（本轮不强求，记为技术债）。

客户端：每帧 Lanczos ×2 + JPEG optimize + 原始像素 sha256，CPU 尖峰仅在截图时，暂不处理。

## §2 图标替换（TODO，等 owner 方案）

当前吉祥物 `cat-mascot.svg` 注释为 "traced white cat"（描摹），存在版权风险。

- [ ] `frontend/src/assets/cat-mascot.svg` → `components/brand/CatMascot.tsx`（侧栏底部 / 登录页 / 空状态）
- [ ] `frontend/src/assets/cat-mascot.png` → `components/brand/Logo.tsx`
- [ ] `frontend/public/favicon.ico`、`favicon-16/32/48.png`、`apple-touch-icon.png` → `frontend/index.html`
- [ ] `frontend/public/icons.svg`、`src/assets/hero.png`、`react.svg`、`vite.svg`：确认是否为 Vite 模板残留，未引用则删除
- [ ] `tools/cat-curled-headphones.svg`（基于 AI 参考图重绘）一并评估/删除
- [ ] `demo-slides/` 内旧猫素材
- [ ] Windows 客户端 `client/windows_runtime.py::create_app_icon`（托盘 + exe，经 `packaging/windows/build_assets.py` 产 `.ico`）与新品牌统一
- [ ] 客户端本地控制台 `client/local_api.py::_render_html` 补 favicon
- [ ] 新图标保留一份源 SVG，由单个脚本导出全部 ico/png 尺寸，前端与客户端共用
- [ ] 写新 devlog archive 说明替换原因与素材授权；旧 mascot devlog 按约定不改

## §3 前端交互

- 时间轴只有一条活动 lane，重叠记录互相覆盖、hitTest 取第一个命中（多设备时尤甚）→ W5 多设备 lane。
- 无键盘导航（←/→ 上一条/下一条，Esc 取消选择）。
- 选中记录 / `?highlight=` 进入时不自动把记录滚到视口。
- 分类筛选、搜索条件不进 URL，刷新/后退丢失；切换日期清空分类筛选。
- 头部「共记录」是 Σ 各记录时长，多设备并行时会超过墙钟 → 改为墙钟并集，Σ 另标。

## §4 多设备

设备身份已入库（`devices` 表、`records.device_id`），读侧零覆盖：`summary/`、`agent/`、`retrieval.py`、`search.py`、`report/`、`mcp_layer/` 无设备过滤。当天多设备并行时摘要变差的直接原因：

1. 叙述 system prompt 写死「这台电脑」。
2. 5min 叶子上下文每行无设备名，两台机器的帧按时间交错 → 模型误判为高频切换/分心。
3. 3000 字符上限按时间顺序截断，帧数翻倍时窗口后半段被丢。
4. `active_ms` 是 Σ own-span，并行时 5 分钟窗显示 9 分钟活跃，误导模型。
5. 父窗只读子窗 `description` 且截 400 字，`key_points`/`evaluation` 不上传。

**W2（过渡）**：叶子上下文按设备分组并带设备名；prompt 改为「一台或多台设备，可能并行」；截断按设备公平配额；metrics 补 `device_ms`（逐设备）并在 prompt 中给出墙钟并集；子窗 key_points 一并上传。不改 schema，可用 `narrate --force` 重跑。

**W5 / W6**：沿用 PLAN 原 PR 2 / PR 3 设计（设备目录、候选阶段过滤、多 lane 时间轴、`device_summaries` v2）。

## §5 搜索 / 检索 / Agent / 摘要

| 问题 | 位置 |
|---|---|
| Agent/MCP `search_activity` 只有关键词通道；向量通道只在 REST `/v1/search/text` | `agent/tools.py`、`api/routes/search.py` |
| `search_summaries` 的 query 是 Python 子串过滤，未用已建好的 `summaries_fts` | `agent/tools.py`、`db/sqlite.py` |
| Agent system prompt 未引导使用 `search_summaries` / `query_stats`，「今天干了啥」不先读摘要 | `agent/runner.py` |
| `get_app_breakdown` / `get_category_stats` 只有 `hours_back`，「今天」= 滚动 24h，与逻辑日不一致 | `agent/tools.py` |
| 2 字中文词（论文/周报）低于 trigram 下限 → 全表 LIKE、无相关度排序 | `db/sqlite.py` |
| 图搜文本通道仍是 LIKE（`_bm25_search` 名不副实），未接 VLM 健康门 | `api/routes/search.py` |
| 报告生成器走 Agent 自由工具循环，不读已有 day/6h 叙述 | `report/generator.py` |

**W4**：
- 单一 `retrieve()` 检索层（records 关键词 + 向量 + 摘要 FTS，RRF），REST / MCP / Agent 共用。
- Agent 工具统一时间参数（`period` / ISO 区间，`hours_back` 仅作兼容），统计工具支持逻辑日；system prompt 改为「先摘要、后下钻」。
- `search_summaries` 走 `summaries_fts`（≥3 字）+ LIKE 兜底。
- 报告生成先注入 day/6h 叙述与统计作为上下文。
- 图搜文本通道改走 records FTS。

## §6 时区契约（owner 决策 2026-10-22）

- **存储**：一律 UTC epoch ms（现状已如此：`ts_start/ts_end` 为 epoch ms）。
- **查询**：涉及「日 / 周 / 今天 / 本地时刻格式化」的接口接受 IANA 时区（`tz` 查询参数或 `X-Timezone` 请求头），服务端用 `zoneinfo` 计算边界与格式化，不依赖进程/容器本地时区，不写死偏移。
- **前端**：用 `Intl.DateTimeFormat().resolvedOptions().timeZone` 取浏览器时区，随所有 API 请求携带；显示一律按浏览器时区。
- **默认值**：请求未带时区时回退服务端配置 `TIMETRACE_TZ` / 运行环境时区（保持旧客户端与 MCP 行为不变）。
- **金字塔**：`5min`/`1h` 窗与整点偏移时区无关；`6h`/`day`/`week` 是按服务端配置时区预计算的派生层。请求时区与之不同时，按请求时区计算区间边界并在响应中标注 `pyramid_tz`，不重建金字塔。
