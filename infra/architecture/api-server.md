# Local API Server

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)
> 相关：[登录鉴权系统](auth-system.md) · [MCP Layer](mcp-layer.md) · [Web UI](web-ui.md) · [公网部署](web-deployment.md)

---

## 职责

- 为 Web UI、MCP、capture client 提供统一 HTTP 接口（查询 / 筛选 / 多模态搜索 / 反馈写入 / ingest 上行）
- 分页 / 采样，保证外部请求不拉爆数据
- 统一鉴权：cookie session（浏览器）+ bearer token（机器）双通道，见 [登录鉴权系统](auth-system.md)
- FastAPI 自动生成 OpenAPI（登录后访问 `/docs`）

监听默认 `127.0.0.1:8765`（loopback only）。公网访问由前置 nginx 反代 + FRP 隧道承担（见 [公网部署](web-deployment.md)），8765 本身始终不直接暴露。

---

## 技术栈与装配

- **FastAPI** + **Uvicorn**（ASGI）
- app 工厂：[`server/api/app.py::create_app`](../../src/timetrace/server/api/app.py)
- 组件装配：[`server/bootstrap.py`](../../src/timetrace/server/bootstrap.py) 的 `build_server_components` 先构建所有单例（DB / PHashIndex / VLM / Auth / UserStore 等），再调 `create_app` 工厂建出应用——`create_app` 被单进程（`main.py`）和双进程（`timetrace-server`）共享，避免两入口漂移
- `create_app` 把 db / phash_index / vlm_client / blob_storage / auth / users / auth_cfg / thumbs_dir 挂到 `app.state`，路由从 `request.app.state` 取

`create_app` 的参数大多 `Optional`：传 `None` 则对应功能关闭或路由开放。`users=None`（旧测试 fixture）时业务路由不挂鉴权依赖——这是测试兼容的有意设计，不是漏 gate。

---

## 路由表

`✓` = 实装并启用。鉴权列含义见 [登录鉴权系统](auth-system.md)。

### 业务路由（`require_principal`：cookie 或 bearer）

| 方法 | 路径 | 说明 | 模块 |
|------|------|------|------|
| GET | `/v1/records` | 时间范围查询（见下参数） | [records.py](../../src/timetrace/server/api/routes/records.py) |
| GET | `/v1/records/{record_id}` | 单条详情（含 screenshots 列表） | records.py |
| GET | `/v1/apps` | distinct app_name + count（降序，筛选 UI 用） | records.py |
| GET | `/v1/categories` | 可见分类列表 | feedback.py |
| GET | `/v1/runtime-info` | version / data_dir / api_host / api_port（Settings 页只读） | records.py |
| POST | `/v1/feedback` | 用户确认 / 修正分类 | feedback.py |
| POST | `/v1/search/by-image` | 多模态搜索（pHash + BM25 + RRF） | search.py |
| POST | `/v1/agent/chat` | Web 端 agent 对话（工具调用循环，SSE 流式） | [agent.py](../../src/timetrace/server/api/routes/agent.py) |
| GET | `/v1/reports/latest` | 取最新已存看板（无则 404） | [reports.py](../../src/timetrace/server/api/routes/reports.py) |
| POST | `/v1/reports/generate` | 立即生成看板（同步，返回结果） | reports.py |
| POST | `/v1/reports/generate/stream` | 立即生成看板（SSE 流式 agent 步骤 + token） | reports.py |
| GET | `/v1/settings/app-overrides` | 取 per-app 分类覆盖 KV | [settings.py](../../src/timetrace/server/api/routes/settings.py) |
| PUT | `/v1/settings/app-overrides` | 校验并持久化覆盖 KV（非法 → 400） | settings.py |
| GET | `/thumbs/{path:path}` | 缩略图（path-traversal 防护 + `Cache-Control: private`） | thumbs.py |

### 认证路由

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/v1/auth/login` | 无（还没 cookie；限速保护） | 验证 → set HttpOnly cookie |
| POST | `/v1/auth/logout` | `require_session` | 删会话 + 清 cookie，204 |
| GET | `/v1/auth/me` | `require_session` | 前端 `RequireAuth` mount 时轮询此门 |
| POST | `/v1/auth/change-password` | `require_session` | must-change 状态下仍可达（否则死锁） |

### Admin 路由（`require_session_password_set`：cookie-only，且已改默认密码）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/admin/tokens` | 列 bearer token（只 label + created_at，不回吐 value） |
| POST | `/v1/admin/tokens` | 铸新 token（**只此一次**返回全值），改运行中 ServerAuth 内存 + 持久化，即时生效 |
| DELETE | `/v1/admin/tokens/{label}` | 按 label 吊销，即时生效 |

### Ingest 路由（专用 bearer，**不接 cookie**）

| 方法 | 路径 | 说明 | 模块 |
|------|------|------|------|
| POST | `/v1/ingest/record` | 双进程 client → server 上行；create/upsert record + screenshots | [ingest.py](../../src/timetrace/server/api/routes/ingest.py) |
| POST | `/v1/ingest/record/{id}/close` | 标记 record 关闭（ts_end / duration） | ingest.py |

幂等：record 级 `client_record_id` UNIQUE（re-POST 返回既有 id）；screenshot 级 `screenshots(record_id, hash_sha256)` UNIQUE（重传同 blob = ON CONFLICT DO NOTHING）。blob 落盘日期目录用 `record.ts_start`（采集时刻）而非上传时刻，所以延迟 drain 的 outbox 仍归到采集当天。

### 其它

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/healthz` | **无** | 探活，systemd / nginx / CI smoke 打 |
| GET | `/blob/{path:path}` | `require_principal` | 原图灯箱（全分辨率截图原件，path-traversal 防护） |
| GET | `/skill` | **无** | 返回 `skills/timetrace/SKILL.md`（文档非访问，MCP 仍 bearer-gated） |
| ANY | `/mcp/*` | `BearerOnlyMiddleware`（专用 bearer） | MCP streamable-HTTP，见 [MCP Layer](mcp-layer.md) |
| GET | `/docs` `/openapi.json` | `require_session_password_set` | 不向公网泄露 API 地图；默认 `docs_url=None`，登录后重新挂出 |

> **`/v1/summary` 未实装**：Phase 1.5 的活动摘要端点（`{start, end, mode, max_frames, include_stats}`）目前没有路由。摘要能力当前只以 MCP 工具形态部分存在（见 [MCP Layer](mcp-layer.md)），REST 侧待补。

---

## 双通道鉴权决策

完整设计在 [登录鉴权系统](auth-system.md)，这里只给 API 层的速查表：

| 调用者 | 凭证 | 守业务路由的依赖 |
|--------|------|-------------------|
| 浏览器里的人 | `tt_session` HttpOnly cookie（密码登录换） | `require_principal`（cookie 分支） |
| capture client / 脚本 | `Authorization: Bearer tt_live_...` | `require_principal`（bearer 分支） |
| MCP 客户端（Claude Code/Desktop） | 同上 bearer | `BearerOnlyMiddleware` |
| capture client 的 ingest 上行 | 同上 bearer | `make_bearer_dependency`（专用） |

三个依赖（`require_session` / `require_session_password_set` / `require_principal`）定义在 [`server/api/deps.py`](../../src/timetrace/server/api/deps.py)。`require_principal` 返回 `CookiePrincipal | BearerPrincipal`，路由可据此审计区分"人"还是"机器"。

为什么 ingest 和 mcp 不复用 `require_principal` 而各有专用 bearer 守卫：

- **ingest 是写入面，永远不该从人类会话进来**，所以连 cookie 通道都不给
- **mcp 是 mount 进来的独立 ASGI 子 app**，FastAPI 的 `Depends` 链包不到它，必须在 ASGI 层用中间件 gate（且不能用 `BaseHTTPMiddleware`，否则破坏 SSE 流）

`/thumbs` 同样曾是 `StaticFiles` mount（绕过 Depends），现已改写为带 `require_principal` 的自定义 `FileResponse` 路由——细节见 [auth-system.md 的"两个 ASGI 陷阱"](auth-system.md#两个-asgi-陷阱mount-不走-depends)。

---

## `/v1/records` 参数

```
GET /v1/records
    ?start=<epoch_ms>          # inclusive，默认 0
    &end=<epoch_ms>            # inclusive，默认 9_999_999_999_999
    &app=<name>               # 单 app_name（保留，向后兼容）
    &apps=<a,b,c>             # 多 app_name（逗号分隔，多选）
    &categories=<c1,c2>       # 多 category_final（逗号分隔）
    &q=<keyword>             # ≥3 字符走 FTS5 trigram + BM25 相关性排序，<3 字符多字段 LIKE 兜底；
                             #   检索字段：window_title / app_name / process_name / url / vlm_desc
    &limit=200               # ≤ 500
    &cursor=<record_id>      # keyset 分页：上页最后一条 id
→ { items: [...], next_cursor: <id|null> }
```

`next_cursor` 仅在 `len(rows) == limit`（可能还有下一页）时给出。列表项已含 `vlm_desc / category_final / app_name / window_title / url`，前端详情切换无需再请求。

**thumb_path 归一化**：DB 存 `thumbs/2026/04/15/uuid.jpg`（相对 data_dir），`/thumbs` 路由从 thumbs_dir 提供，所以 records 路由会 `_strip_thumbs_prefix` 去掉前导 `thumbs/` 段并把反斜杠转正斜杠，前端拼成 `/thumbs/2026/04/15/uuid.jpg`。

---

## `/v1/search/by-image` 形态

```
POST /v1/search/by-image  (multipart)
    images: List[UploadFile]    # 1–5 张参考图
    visual: bool = true         # pHash 通道（用第一张图）
    semantic: bool = true       # VLM→BM25 通道（用所有图）
    radius: int = 10            # pHash 汉明半径上限
    q: str | None               # 可选关键词（与图搜一起）
    start, end: int | None      # epoch ms，半开区间
    apps, categories: str | None (csv)
    limit: int = 50 (≤ 200)
→ { items, total, visual_channel, semantic_channel }
```

```json
{
  "items": [
    {
      "screenshot_id": "...", "record_id": "...",
      "ts_start": 1712345678000, "ts_end": 1712345699000,
      "app_name": "Chrome", "window_title": "...", "url": null,
      "thumb_path": "2026/04/22/xxx.jpg",
      "vlm_desc": null, "category_final": null,
      "match": {
        "visual_distance": 4, "semantic_rank": null, "text_rank": null,
        "rrf_score": 0.0164, "reasons": ["pHash 距离 4"]
      }
    }
  ],
  "total": 37,
  "visual_channel": "ok",          // ok / disabled / unavailable
  "semantic_channel": "unavailable"
}
```

融合策略（RRF k=60）见 [相似检索层](../storage/vector-search.md)。`unavailable` = 通道被选中但产不出结果（pHash 索引为空、VLM 未接入等）。

> **向量检索通道休眠中**：`db.vector_search`（numpy 余弦全表扫，配合 [embedding](../../src/timetrace/server/embedding/client.py) 写入的 `analysis_results.text_embedding`）已实装，但 search 路由当前还未调用它——语义通道目前走 VLM 描述 → LIKE 计分 fallback（search.py 的 `_bm25_search`，尚未切到 FTS5 MATCH）。详见 [vector-search.md](../storage/vector-search.md)。

---

## 分页 / 采样策略

| 场景 | 策略 |
|------|------|
| UI 时间轴 | cursor（keyset）分页 |
| MCP 工具 | 强制 `max_items`；超限等间距 / 分层采样，不塞原始帧给模型 |

---

## 路由组织

```
src/timetrace/server/api/
├── app.py            ← FastAPI 工厂：注册路由、装 app.state、装鉴权依赖、CSRF 中间件、mount /mcp
├── deps.py           ← require_session / require_session_password_set / require_principal
├── mcp_auth.py       ← BearerOnlyMiddleware（纯 ASGI，gate mount 进来的 /mcp）
└── routes/
    ├── auth.py       ← /v1/auth/{login,logout,me,change-password}（cookie 侧）
    ├── admin.py      ← /v1/admin/tokens CRUD（cookie-only，Web UI 管 bearer token）
    ├── records.py    ← /v1/records(/{id}) /apps /runtime-info
    ├── search.py     ← /v1/search/by-image（pHash + BM25 + RRF）
    ├── feedback.py   ← /v1/feedback /categories
    ├── agent.py      ← /v1/agent/chat（Web 端 agent 对话，SSE）
    ├── reports.py    ← /v1/reports/{latest,generate,generate/stream}（看板）
    ├── settings.py   ← /v1/settings/app-overrides（GET/PUT 分类覆盖 KV）
    ├── ingest.py     ← /v1/ingest/record(/{id}/close)（双进程上行，bearer-only）
    ├── blob.py       ← /blob/{path}（原图灯箱，带 auth + path-traversal 防护）
    ├── skill.py      ← /skill（返回 SKILL.md，无 auth）
    └── thumbs.py     ← /thumbs/{path}（带 auth + path-traversal 防护）
```

**共享状态**（`app.state`）：`db` / `phash_index` / `vlm_client` / `blob_storage` / `auth`（ServerAuth）/ `users`（UserStore）/ `auth_cfg` / `vlm_cfg`（`VLMConfig | None`，agent/reports 路由据此判断 LLM 是否可用）/ `data_dir` / `thumbs_dir` / `api_host` / `api_port`。

---

## 相关文档

- [登录鉴权系统](auth-system.md) — 双通道 auth、限速、CSRF、token 生命周期
- [公网部署与安全](web-deployment.md) — nginx 反代、TLS、信任边界
- [MCP Layer](mcp-layer.md) — `/mcp` 工具层
- [Web UI](web-ui.md) — 前端如何消费此 API
- [存储 Schema](../storage/schema.md) · [相似检索层](../storage/vector-search.md)
