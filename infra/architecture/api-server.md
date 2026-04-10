# Local API Server

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

- 为 Web UI 与 MCP 提供统一业务接口（查询、筛选、统计、summary 触发、反馈写入）
- 负责分页 / 采样，保证外部请求不会拉爆数据
- 统一鉴权策略（本地 token / 一次性授权）
- 通过 FastAPI 自动生成 OpenAPI 文档（访问 `http://127.0.0.1:8765/docs`）

---

## 技术栈

- **FastAPI** — ASGI Web 框架，原生异步，自动 OpenAPI / JSON Schema 生成
- **Uvicorn** — ASGI server，与 FastAPI 配合运行
- **监听地址** — 默认 `127.0.0.1:8765`（仅本机访问）

---

## 接口约定

```
GET  /v1/records
     ?date=2026-04-09
     &start=<epoch_ms>
     &end=<epoch_ms>
     &apps=<comma_separated>
     &cats=<comma_separated>
     &q=<keyword>
     &limit=200
     &cursor=<record_id>

POST /v1/summary
     {start, end, mode, max_frames, include_stats}

POST /v1/feedback
     {record_id, action: "confirm"|"edit", category, tags}

GET  /v1/categories

POST /v1/search
     {query_text, start?, end?, top_k, filters}

GET  /healthz
     → {"status": "ok"}
```

---

## 分页 / 采样策略

| 场景 | 策略 |
|------|------|
| UI 时间轴 | 基于游标（cursor）的分页 + 时间桶聚合（1min / 5min buckets） |
| MCP 工具 | 强制 `max_items`；超限时等间距采样或按应用/分类分层采样，避免塞给模型原始帧 |

---

## 路由组织

```
src/timetrace/api/
├── app.py           ← FastAPI 工厂，注册路由
└── routes/
    ├── records.py   ← GET /v1/records
    ├── search.py    ← POST /v1/search
    └── feedback.py  ← POST /v1/feedback, GET /v1/categories
```

---

## 鉴权策略（本地安全）

- 默认仅监听 `127.0.0.1`，不对外暴露
- **本地 token**：首次启动自动生成，托盘图标可复制
- **可选一次性授权码**：短 TTL（适合 MCP 接入授权）

---

## 当前实现片段

```python
# src/timetrace/api/app.py
def create_app(db: Database) -> FastAPI:
    app = FastAPI(
        title="TimeTrace Local API",
        version="0.1.0",
        description="Local-first desktop activity memory layer – Local API",
    )
    app.state.db = db
    app.include_router(records.router,  prefix="/v1")
    app.include_router(search.router,   prefix="/v1")
    app.include_router(feedback.router, prefix="/v1")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}
    return app
```

---

## 相关文档

- [MCP Layer（复用此 API）](mcp-layer.md)
- [Web UI（调用此 API）](web-ui.md)
- [存储 Schema](../storage/schema.md)
