# Web UI

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## **⚠️ 2026-05-28 起加入登录态（公网部署引出）**

本页未覆盖登录系统改造。设计与决策见 [`devlogs/infra/archive-202605280400-login-system-design.md`](../../devlogs/infra/archive-202605280400-login-system-design.md)。

要点：
- **新增页面**：`/login`（admin/admin 默认）、`/login/change-password`（首次强制改密）
- **新增 React 设施**：`contexts/AuthContext.tsx`、`components/RequireAuth.tsx`（路由守卫）
- **`lib/api.ts` 改造**：`credentials: 'include'` 发送 cookie + 401 拦截自动跳登录 + cross-tab 退出同步（一个 tab logout 其他 tab 也踢）
- **Settings 页面新增两区**：Account（改密 + 退出）+ API Tokens（CRUD；新建 token 弹窗内置可复制的 `.mcp.json` 模板供 Claude Code 接入）
- **单用户模型**：无注册、无用户管理、无 role —— 唯一用户 = admin

---

## 职责

- **时间轴回放**：日历选择、缩放/滚动、多轨道渲染
- **检索与过滤**：关键词 / 应用 / 分类 / 时间范围
- **详情面板**：帧缩略图（可点击放大）、窗口信息、描述、分类、置信度（`decision_trace` 已存库但尚未在 UI 暴露，属未来工作）
- **反馈交互**：确认/修改分类、标签编辑、黑名单与隐私模式设置

---

## 技术选型

| 库 / 工具 | 用途 | 选择理由 |
|-----------|------|---------|
| **React** | UI 组件框架 | 组件化 UI，适合交互式本地面板 |
| **Vite** | 构建工具 | 快速 HMR，现代 ESM 构建，适合本地开发 |
| **TanStack Query** | 异步数据获取与缓存 | 内置 loading/error 状态、自动重试、缓存失效 |

> 为何不选 Next.js：SSR 价值对本地单机面板不高，且偏重。
> 为何不选 Vue：与既定 React 技能栈不一致。

---

## 核心组件

### TimelineCanvas

多轨道时间轴渲染，支持缩放、hover、选区。

```ts
// Phase 1 实现：直接传 ApiRecord[]，无须转换
function TimelineCanvas(props: {
  records: ApiRecord[];        // GET /v1/records 返回的原始记录
  date: Date;                  // 当前查看日期（用于初始化视口）
  selectedRecordId: string | null;
  onSelectRecord: (id: string | null) => void;
}) { /* Canvas 2D 渲染，双轨道：Activity + Frames；goToday 由组件内部 useTimelineState 提供 */ }
```

> Phase 1.5+：多轨道（App / Category）时再引入 `TimelineItem` 抽象层做统一转换。

**Phase 1 范围**：单日视图、少轨道（Activity + Frames）、基础缩放。
**Phase 1.5+**：多轨道（App / Category）、批量选区、统计面板。

### RecordDetailPanel

展示单帧信息与反馈按钮：缩略图、窗口标题、应用名、VLM 描述、分类、confidence。缩略图点击后触发 `ImageLightbox` 进入放大查看模式。（`decision_trace` 已存库但尚未在 UI 暴露。）

### ImageLightbox

跨页面共享的弹层放大查看组件，触发点位于详情面板缩略图与搜索结果行缩略图。

**核心交互**：
- 居中弹层（非全屏，最大 `min(1200px, 92vw) × 90vh`），半透明遮罩 + 背景模糊
- 关闭：点击遮罩 / 右上角 ✕ / Esc 三种方式（基于 Radix Dialog 默认行为）
- 上下条切换：图片左右覆盖式圆形按钮 + 全局键盘 ← / →；首尾边界自动 disabled
- 底部黑色渐变阴影区呈现分类徽章、时间区间、应用名 + 窗口标题、URL、VLM 描述

**切换范围语义**：
- 时间线页：`useRecords(date)` 返回的当日全部记录（保留时间相邻关系）
- 搜索页：当前搜索结果列表（保留检索相关性顺序）
- **不过滤无截图项**：切到没有 `thumb_path` 的活动时主图区显示「该活动无截图」占位，底部信息条照常显示该活动元数据
- 单条活动只展示首张截图（`thumb_path`），不切换 `screenshots[]` 多帧

**实现要点**：
- 基于 `@radix-ui/react-dialog`，自带 portal / focus trap / Esc 关闭
- 父组件维护 `lightboxIndex` 状态（`-1` 为关闭），把列表整体映射成 `LightboxItem[]` 传入；切换时由父组件回调 `onIndexChange`
- 索引钳位 `Math.min(idx, len - 1)`，`open` 严格要求 `items.length > 0`，防止后台 refetch 缩短列表导致越界
- 搜索页提交新 `submitted` 时通过 `useEffect` 关闭弹层（旧索引指向的活动可能已不在新结果集里）
- 动画：CSS @keyframes 配合 Radix `data-state` 属性钩子（`lightbox-overlay-in` / `lightbox-content-in`），约 180–200ms 淡入 + 缩放，无需引入 framer-motion

### SearchPage

路由 `/search`，独立入口。实现位置：`frontend/src/pages/SearchPage.tsx` + `frontend/src/components/search/`。

**查询形态（可任意组合）**：
- 关键词（作用于 `window_title` + `vlm_desc`）
- 参考图（多图，上限 5 张；支持点击、拖拽、Ctrl+V 粘贴）
- 时间范围（预设 "今天 / 近 7 天 / 近 30 天"，或自定义日期）
- 应用多选（chip，数据来自 `GET /v1/apps`）
- 分类多选（chip，数据来自 `GET /v1/categories`）

**搜索模式切换（仅在上传参考图后显示）**：
- **视觉相似**：pHash 通道，画面像素层面一致
- **语义相似**：VLM + BM25 通道，内容层面一致
- 两者可二选一或都选；都选时走 RRF 融合
- 语义通道不可用时（VLM 未配置）显示行内提示，不报错

**结果展示**：
- 行内展开（点击整行折叠/展开详情）
- 行内缩略图（折叠态 80×50、展开态最大 320 高）点击触发 `ImageLightbox` 放大查看，并支持在结果列表内左右切换
- 匹配徽标说明理由："pHash 距离 4" / "语义 rank 3" / "关键词 rank 7"
- 每行 **「在时间轴中查看」** 按钮 → `navigate('/?date=YYYY-MM-DD&highlight=recordId')`

**请求分派逻辑**（`hooks/useSearchQuery.ts`）：
- 有图 → `POST /v1/search/by-image`（multipart）
- 无图、有关键词或筛选 → `GET /v1/records?q=...&apps=...&categories=...`
- 完全空查询 → 搜索按钮 disabled

**TanStack Query key 稳定性**：`File[]` 在默认 hash 器下会坍塌为 `{}`，需要把文件用 `name:size:lastModified` 串成指纹作为 key，避免不同参考图撞缓存。

### SettingsPage

## **⚠️ 下列四类编辑器属未实装的未来设想，当前 SettingsPage 不是这套**

- 采集参数（min/max 间隔、idle 阈值）
- 隐私黑名单（应用/标题关键词列表）
- 存储配额（图片保留天数、最大占用）
- 模型配置（API Key、模型选择）

**当前实装**（`frontend/src/pages/SettingsPage.tsx`）：
- **Account**（`components/admin/AccountSection.tsx`）：改密 + 退出
- **API Tokens**（`components/admin/TokenManager.tsx`）：Token CRUD
- **Embedding 诊断**（`components/admin/EmbeddingDiagnostics.tsx`）
- **App 覆盖**（`components/admin/AppOverridesSection.tsx`）：per-app 覆盖
- **后端状态**：只读块，展示版本 / 数据目录 / API 地址（来自 `GET /v1/runtime-info`）

### DashboardPage / ReportView

> 2026-05 后落地，路由 `/dashboard`（`App.tsx`）。

AI 自动分析时段内真实活动并生成报告，含应用 / 分类时长统计。

- **DashboardPage**（`frontend/src/pages/DashboardPage.tsx`）：时段切换（最近 3h / 24h / 7 天）、「重新生成」触发本地大模型流式分析（SSE-over-fetch，附非流式与轮询兜底）、展示工具步骤进度。
- **ReportView**（`frontend/src/components/dashboard/ReportView.tsx`）：渲染 JSON 格式报告（新报告）；旧 `format:'html'` 报告回退为自带样式的 HTML 直出。

### AgentPage

> 2026-05 后落地，路由 `/agent`（`App.tsx`）。

`ask_agent` 对话页（`frontend/src/pages/AgentPage.tsx`），流式对话 + 工具调用展示（`components/agent/{AgentSidebar,TurnView}.tsx`），本地多会话持久化。

### FeedbackControls

- 确认分类 / 修改分类（单条）
- 批量操作（Phase 2）
- 撤销最近反馈

---

## 前端与 API 交互

```
Web UI ──GET /v1/records──► Local API ──► SQLite
       ◄── JSON records ────

Web UI ──POST /v1/feedback──► Local API ──► feedback 表
       ◄── {ok} ─────────────

Web UI ──POST /v1/search/by-image──► Local API ──► pHash Index（视觉）
       ◄── {items, channels} ──          └───► LIKE（语义 / 关键词，FTS5 为规划升级）
                                         └───► RRF 融合

Web UI ──GET /v1/apps───────► Local API ──► records 聚合
       ◄── [{name, count}] ──
```

---

## 渐进式开发策略

| Phase | UI 复杂度 |
|-------|----------|
| Phase 1 | 单日时间轴、少轨道、详情面板、搜索页（关键词 + pHash 视觉通道 + 筛选） |
| Phase 1.5 | VLM 描述展示 ✅已实装、语义通道启用 ✅已实装、摘要触发 |
| Phase 2 | 批量反馈、统计面板（✅已实装为 Dashboard 页 + ReportView）、多轨道、高级隐私设置 |

> ✅ 标记项已落地：VLM 描述展示见 `RecordDetailPanel.tsx`，语义通道（VLM describe → `_bm25_search` → RRF）见 `server/api/routes/search.py`，统计面板已实装为 `/dashboard`（`DashboardPage.tsx` + `components/dashboard/ReportView.tsx`）。

---

## 相关文档

- [Local API Server](api-server.md)
- [MCP Layer](mcp-layer.md)
- [工程化技术选型对比](../engineering/tech-stack.md)
