# Web UI

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

- **时间轴回放**：日历选择、缩放/滚动、多轨道渲染
- **检索与过滤**：关键词 / 应用 / 分类 / 时间范围
- **详情面板**：帧缩略图、窗口信息、描述、分类、置信度、decision_trace
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
  onGoToday: () => void;
}) { /* Canvas 2D 渲染，双轨道：Activity + Frames */ }
```

> Phase 1.5+：多轨道（App / Category）时再引入 `TimelineItem` 抽象层做统一转换。

**Phase 1 范围**：单日视图、少轨道（Activity + Frames）、基础缩放。
**Phase 1.5+**：多轨道（App / Category）、批量选区、统计面板。

### RecordDetailPanel

展示单帧信息与反馈按钮：缩略图、窗口标题、应用名、VLM 描述、分类、confidence、decision_trace 折叠展示。

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
- 匹配徽标说明理由："pHash 距离 4" / "语义 rank 3" / "关键词 rank 7"
- 每行 **「在时间轴中查看」** 按钮 → `navigate('/?date=YYYY-MM-DD&highlight=recordId')`

**请求分派逻辑**（`hooks/useSearchQuery.ts`）：
- 有图 → `POST /v1/search/by-image`（multipart）
- 无图、有关键词或筛选 → `GET /v1/records?q=...&apps=...&categories=...`
- 完全空查询 → 搜索按钮 disabled

**TanStack Query key 稳定性**：`File[]` 在默认 hash 器下会坍塌为 `{}`，需要把文件用 `name:size:lastModified` 串成指纹作为 key，避免不同参考图撞缓存。

### SettingsPage

- 采集参数（min/max 间隔、idle 阈值）
- 隐私黑名单（应用/标题关键词列表）
- 存储配额（图片保留天数、最大占用）
- 模型配置（API Key、模型选择）

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
       ◄── {items, channels} ──          └───► FTS5 / LIKE（语义 / 关键词）
                                         └───► RRF 融合

Web UI ──GET /v1/apps───────► Local API ──► records 聚合
       ◄── [{name, count}] ──
```

---

## 渐进式开发策略

| Phase | UI 复杂度 |
|-------|----------|
| Phase 1 | 单日时间轴、少轨道、详情面板、搜索页（关键词 + pHash 视觉通道 + 筛选） |
| Phase 1.5 | VLM 描述展示、语义通道启用、摘要触发 |
| Phase 2 | 批量反馈、统计面板、多轨道、高级隐私设置 |

---

## 相关文档

- [Local API Server](api-server.md)
- [MCP Layer](mcp-layer.md)
- [工程化技术选型对比](../engineering/tech-stack.md)
