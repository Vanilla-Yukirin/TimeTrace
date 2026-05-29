# Local API Server

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## **⚠️ 本页描述 v1 单进程架构，与 P3a/P3b 之后的现状不一致**

主要变更：
- 新增 `/v1/ingest/record` + `/v1/ingest/record/{id}/close` 两条路由（multipart 上行 / close 端点），受 `Depends(bearer)` 保护
- `ServerAuth` token 体系：首启自动 mint `tt_live_<32urlbytes>` 落 `~/.config/timetrace-server/tokens.json`（POSIX chmod 600）
- 路由幂等：`client_record_id` UNIQUE + `screenshots(record_id, hash_sha256)` UNIQUE
- `timetrace-server` 独立入口；`bootstrap.py` 共享单 / 双进程装配；CLI 子命令 `info` / `tokens list/add/revoke`
- 监听仍是 `127.0.0.1:8765`（loopback only）—— 公网访问由 SSH `-L` 隧道或前置 Caddy/nginx 反代承担

## **⚠️ 2026-05-28 起加入登录系统 + 双通道 auth（公网部署引出）**

本页"鉴权策略"段已陈旧。设计与决策见 [`devlogs/infra/archive-202605280400-login-system-design.md`](../../devlogs/infra/archive-202605280400-login-system-design.md)。

要点：
- **双通道 auth 并存**：Cookie session（浏览器、密码登录换）+ Bearer token（MCP / capture client / 脚本）
- 浏览器路由（`/v1/records` / `/search` / `/feedback` / `/categories` / `/runtime-info`）从公开 → **cookie 或 bearer 任一**
- `/v1/ingest/*` **仍只 bearer**（机器对机器）
- `/mcp/*` **仅 bearer**，通过 starlette `BearerOnlyMiddleware` 包裹后再 mount（解决 `app.mount` 不传 FastAPI `Depends` 的陷阱）
- `/thumbs/*` 从 `StaticFiles.mount` → 自定义 `FileResponse` 路由 + path-traversal 防护 + auth（同上陷阱）
- 新增 7 条路由：`/v1/auth/{login,logout,me,change-password}` + `/v1/admin/tokens` CRUD（cookie only）
- 新增 2 张表：`auth_users` / `auth_sessions`（不动现有 8 张业务表，单用户无 `user_id` 分区）
- 默认 admin/admin + 首次登录强制改密；session 30 天 HttpOnly cookie + DB-backed 可即时 revoke

**当前事实**：
- 代码：[`src/timetrace/server/api/`](../../src/timetrace/server/api/)、[`server/auth.py`](../../src/timetrace/server/auth.py)、[`server/admin_cmd.py`](../../src/timetrace/server/admin_cmd.py)、[`server/bootstrap.py`](../../src/timetrace/server/bootstrap.py)
- 设计：[`devlogs/infra/archive-202605151200-client-server-split-kickoff.md`](../../devlogs/infra/archive-202605151200-client-server-split-kickoff.md) P3a/P3b 段
- CLI：[`archive-202605171500-p3b3-cli-design.md`](../../devlogs/infra/archive-202605171500-p3b3-cli-design.md)

整页重写计划在 P5 Postgres/Redis/S3 适配器接入后进行（届时 routes 仍稳定，只是底层 Database / Queue / BlobStorage 多种选项）。

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
     ?start=<epoch_ms>
     &end=<epoch_ms>
     &app=<name>                 # 单个应用（保留，向后兼容）
     &apps=<a,b,c>               # 多个应用（逗号分隔）
     &categories=<cat1,cat2>     # 多个分类（作用于 analysis_results.category_final）
     &q=<keyword>                # 匹配 window_title OR vlm_desc，LIKE 元字符自动转义
     &limit=200
     &cursor=<record_id>

GET  /v1/records/{record_id}     # 单条详情（含 screenshots 列表）

GET  /v1/apps                    # 聚合返回 [{name, count}]，count 降序，用于筛选 UI

GET  /v1/categories

POST /v1/feedback
     {record_id, action: "confirm"|"edit", category, tags}

POST /v1/search/by-image (multipart)
     images: List[UploadFile]    # 1–5 张参考图
     visual: bool = true         # 启用 pHash 通道（用第一张图）
     semantic: bool = true       # 启用 VLM→BM25 通道（用所有图）
     radius: int = 10            # pHash 汉明半径上限
     q: str | None               # 可选关键词（与图搜一起走）
     start, end: int | None      # epoch ms，支持半开区间
     apps, categories: str | None (csv)
     limit: int = 50 (≤ 200)
     → {items, total, visual_channel, semantic_channel}

POST /v1/summary                 # Phase 1.5 目标，未实现
     {start, end, mode, max_frames, include_stats}

GET  /healthz
     → {"status": "ok"}
```

### /v1/search/by-image 响应形态

```json
{
  "items": [
    {
      "screenshot_id": "...",
      "record_id": "...",
      "ts_start": 1712345678000,
      "app_name": "Chrome",
      "window_title": "...",
      "thumb_path": "2026/04/22/xxx.jpg",
      "vlm_desc": null,
      "category_final": null,
      "match": {
        "visual_distance": 4,
        "semantic_rank": null,
        "text_rank": null,
        "rrf_score": 0.0164,
        "reasons": ["pHash 距离 4"]
      }
    }
  ],
  "total": 37,
  "visual_channel": "ok",          // ok / disabled / unavailable
  "semantic_channel": "unavailable"
}
```

融合策略见 [相似检索层](../storage/vector-search.md)（RRF k=60）。`unavailable` 表示该通道选中但无法产出结果（如 pHash 索引为空、VLM 未接入）。

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
├── app.py           ← FastAPI 工厂，注册路由与共享 state
└── routes/
    ├── records.py   ← GET /v1/records, GET /v1/records/{id}, GET /v1/apps, GET /v1/runtime-info
    ├── search.py    ← POST /v1/search/by-image（pHash + BM25 + RRF 融合）
    └── feedback.py  ← POST /v1/feedback, GET /v1/categories
```

**共享状态**（`app.state`）：
- `db` — `Database` 实例
- `phash_index` — 进程启动时从 SQLite 重建的 `PHashIndex` 单例，供 `/search/by-image` 查询
- `data_dir` / `thumbs_dir` — 静态 `/thumbs` 挂载

**静态路由**：`/thumbs/*` 由 FastAPI `StaticFiles` 挂载至 `thumbs_dir`，前端直接访问。

---

## 鉴权策略（本地安全）

- 默认仅监听 `127.0.0.1`，不对外暴露
- **本地 token**：首次启动自动生成，托盘图标可复制
- **可选一次性授权码**：短 TTL（适合 MCP 接入授权）

---

## 当前实现片段

```python
# src/timetrace/api/app.py
def create_app(
    db: Database,
    storage_cfg: StorageConfig | None = None,
    phash_index: PHashIndex | None = None,
) -> FastAPI:
    app = FastAPI(
        title="TimeTrace Local API",
        version="0.1.0",
        description="Local-first desktop activity memory layer – Local API",
    )
    app.state.db = db
    app.state.phash_index = phash_index
    if storage_cfg is not None:
        storage_cfg.thumbs_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/thumbs", StaticFiles(directory=storage_cfg.thumbs_dir))

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
