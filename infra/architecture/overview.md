# 架构总览

> 返回 [Wiki 首页](../readme.md)

---

## 分层原则

| 层 | 原则 |
|----|------|
| **采集层（Capture）** | 极轻：不做模型推理、不做重计算；只做"采集 → 落盘 → 落库 → 标记 pending" |
| **分析层（Worker）** | 异步：可控并发、可重试、可降级；通过 SQLite 状态字段驱动，无需引入 Redis |
| **API 层** | 统一业务逻辑：Web UI 与 MCP 复用同一服务层，避免两套逻辑分叉 |
| **存储层** | 本地优先：SQLite 单文件 + 文件系统，serverless / zero-config，可分发 |

---

## 端到端数据流

```mermaid
flowchart TD
  A[Capture Service\n窗口事件 / 截图 / 键鼠计数] -->|insert raw| B[(SQLite: records / screenshots)]
  A -->|write files| C[(FS: screenshots/ thumbs/)]
  B --> D[Analysis Worker\npoll pending tasks]
  D -->|VLM describe| E[Cloud / Local Model Provider]
  D -->|Embedding| F[Embedding Provider]
  D -->|update analyzed| B
  D -->|update vectors| G[(Vector Layer: faiss / sqlite-vec / files)]
  B --> H[Local API Server\nFastAPI / ASGI]
  G --> H
  H --> I[Web UI\nTimeline / Search / Settings]
  H --> J[MCP Layer\nTools / Context]
  I -->|feedback| H -->|write feedback| B
  J -->|no raw image by default| H
```

---

## 进程 / 线程建议

| 进程 | 职责 |
|------|------|
| `timetrace_capture` | 采集、托盘图标、轻量日志 |
| `timetrace_worker` | 分析工作器（可与 capture 同进程不同线程；稳定后再拆） |
| `timetrace_api` | 本地 API（供 UI 与 MCP 复用） |
| `timetrace_ui` | 静态前端文件（由 API server 提供或单独静态服务） |

> **当前实现**：三个任务由 `asyncio.TaskGroup` 在同一进程内并发运行（见 `src/timetrace/main.py`）。

```python
async with asyncio.TaskGroup() as tg:
    tg.create_task(capture_svc.run(),  name="capture")
    tg.create_task(worker.run(),        name="worker")
    tg.create_task(server.serve(),      name="api")
```

---

## 模块职责速览

| 模块 | 文件 | 核心职责 |
|------|------|---------|
| Capture Service | `src/timetrace/capture/service.py` | 监听窗口切换、触发截图、写 records |
| Privacy Guard | `src/timetrace/capture/privacy.py` | 黑名单过滤、暂停判断 |
| Analysis Worker | `src/timetrace/worker/loop.py` | 状态机驱动 VLM/Embedding 流程 |
| Rule Engine | `src/timetrace/rules/engine.py` | 规则匹配 + KNN 投票分类 |
| Database | `src/timetrace/storage/database.py` | aiosqlite 封装、Schema 初始化 |
| API Factory | `src/timetrace/api/app.py` | FastAPI 应用工厂 |
| MCP Tools | `src/timetrace/mcp_layer/tools.py` | MCP 工具函数 |
| Config | `src/timetrace/config.py` | Dataclass 配置，带合理默认值 |

---

## 相关文档

- [Capture Service](capture-service.md)
- [Analysis Worker](analysis-worker.md)
- [Rule/Feedback Engine](rule-engine.md)
- [Local API Server](api-server.md)
- [MCP Layer](mcp-layer.md)
- [存储策略](../storage/overview.md)
