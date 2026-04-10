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
type TimelineItem = {
  ts: number;        // 起始时间戳（epoch ms）
  dur: number;       // 持续时长（ms）
  lane: string;      // 轨道名：Activity / App / Category / Frames
  label: string;     // 显示文字
  id: string;
};

function TimelineCanvas(props: {
  items: TimelineItem[];
  onSelectRange: (startTs: number, endTs: number) => void;
  onClickItem: (id: string) => void;
}) { /* ... */ }
```

**Phase 1 范围**：单日视图、少轨道（Activity + Frames）、基础缩放。
**Phase 1.5+**：多轨道（App / Category）、批量选区、统计面板。

### RecordDetailPanel

展示单帧信息与反馈按钮：缩略图、窗口标题、应用名、VLM 描述、分类、confidence、decision_trace 折叠展示。

### SearchPage

- 文本相似检索结果列表（Phase 1.5+）
- 点击结果跳转时间轴定位

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
Web UI ──GET /v1/records──► Local API
       ◄── JSON records ────

Web UI ──POST /v1/feedback──► Local API ──► SQLite feedback 表
       ◄── {ok} ─────────────

Web UI ──POST /v1/search───► Local API ──► Vector Layer
       ◄── {items} ──────────
```

---

## 渐进式开发策略

| Phase | UI 复杂度 |
|-------|----------|
| Phase 1 | 单日时间轴、少轨道、详情表、基础过滤 |
| Phase 1.5 | 搜索页、VLM 描述展示、摘要触发 |
| Phase 2 | 批量反馈、统计面板、多轨道、高级隐私设置 |

---

## 相关文档

- [Local API Server](api-server.md)
- [MCP Layer](mcp-layer.md)
- [工程化技术选型对比](../engineering/tech-stack.md)
