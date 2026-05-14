# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

TimeTrace 是一个 **Windows Only、本地优先**的桌面活动记忆层：低打扰采集活跃窗口与关键帧截图，落地到 SQLite + 本地文件，提供时间轴回放、多模态搜索（关键词 + 以图搜图 + VLM 语义）、MCP 上下文导出。Python 3.12+、asyncio、FastAPI、aiosqlite。

## 常用命令

```bash
# 后端
uv sync                                    # 安装/同步依赖
uv run timetrace                           # 启动应用（捕获 + 分析 + API + 托盘）
uv run pytest                              # 全部测试
uv run pytest tests/test_storage.py -k name  # 单测
uv run ruff check src/                     # Lint
uv run ruff format src/                    # 格式化

# 前端（独立 dev server，需另开一个终端）
cd frontend && npm run dev                 # http://127.0.0.1:5173/，vite 代理 /v1/* /thumbs/* 到 8765
cd frontend && npm run build               # 产物到仓库根的 frontend-dist/
```

API 启动后访问 `http://127.0.0.1:8765/docs` 看 OpenAPI、`/healthz` 探活。

## 架构关键点

> 端到端数据流、模块职责、Schema 等深度文档统一在 [infra/readme.md](infra/readme.md)。这里只列"读多个文件才能拼出"的关键事实。

**进程拓扑**（[main.py](src/timetrace/main.py)）：

- 主线程跑 pystray 托盘 + asyncio event loop
- `asyncio.TaskGroup` 同时运行 5 个任务：`capture`、`worker`、`api`、`quit_watcher`、`reclaim`
- SIGINT / 托盘 Quit / uvicorn 退出**统一路由到** `quit_event`（`loop.call_soon_threadsafe(quit_event.set)`），由 `_watch_quit` 设置 `server.should_exit=True` 并取消同伴任务，避免 uvicorn 二次抛 SIGINT 中断 event loop
- `_reclaim_loop` 每 60s 清理 worker 异常退出残留的 `analysis_task` 行

**VLM 接入**（已实装，非桩）：

- 通过 `.env` 配置（`TIMETRACE_VLM_BASE_URL` / `_API_KEY` / `_MODEL` / `_DISABLE_THINKING`），见 [.env.example](.env.example)
- `main.py` 在 `AppConfig()` 之前调用 `load_dotenv()`，让 `VLMConfig.from_env()` 看到环境变量
- 没设 API Key → `VLMConfig.from_env()` 返回 `None` → worker 跳过 `_describe`，搜索接口的 semantic 通道返回 `unavailable`，**不报错**
- 退出前必须 `await vlm_client.aclose()`，否则 httpx 连接池会在 atexit 报警

**数据流挂钩**：

- `Capture → Database`：每条 record 触发 `INSERT INTO analysis_tasks` 入队
- `Worker → VLM`：从 `analysis_tasks` 拉任务、调 VLM、回写 `analysis_results.vlm_desc` + `category_final`
- `Capture → PHashIndex`：每张 thumb 计算 pHash，加入内存 BK-tree（`PHashIndex.from_db` 启动时从 DB 重建）
- `API /v1/search/by-image`：pHash 视觉通道（BK-tree 距离）+ FTS5 BM25 语义通道 + RRF 融合

**前后端契约**：

- 前端**不被** Python 后端托管。`api/app.py` 只 mount 了 `/thumbs/`（StaticFiles 指向 `~/TimeTraceData/thumbs/`），前端独立 vite 服务通过代理调 API
- 缩略图 URL 模式：`/thumbs/{path.replace(/\\/g, '/')}`（windows 反斜杠转正斜杠）
- 列表项已包含 `vlm_desc / category_final / app_name / window_title / url`，详情切换无需再请求

## 项目约定

- **入口**：`src/timetrace/main.py:main` → pystray 主线程 + asyncio loop。重构期还是单进程（含两侧），P3a-5 接 Outbox 后才会拆 `timetrace-client` / `timetrace-server` 双入口。
- **三层目录**：`src/timetrace/{common,client,server}/`。配置在 `common/config.py`；wire schema 在 `common/protocol.py`；capture / 托盘 / outbox / backend 在 `client/`；api / db / queue / blob / worker / vlm / phash / mcp 在 `server/`。
- **DB 路径**：`server/db/sqlite.py::SqliteDatabase`，`server/db/__init__.py` 导出 `Database = SqliteDatabase` 别名 —— 现在所有调用方都还是用 `from timetrace.server.db import Database`，PostgresDatabase 在 P5 进来时这条别名升级为 typing.Protocol。
- **BackendClient Protocol** in `client/core/backend.py`：capture 不再直接用 db，全走 `BackendClient`；本地用 `InProcessBackend`（直调 SqliteDatabase + PHashIndex），P3a 起多了个 `HttpBackend`（POST `/v1/ingest/record` + `.../close`，bearer 可选）。
- **Outbox**：`client/core/outbox.py`，append-only `log.jsonl` + `blobs/` + `state.json`（atomic rename）。**当前还没接进 capture / HttpBackend 的运行路径** —— 是 P3a-5 的事。
- **Auth**：`server/auth.py::ServerAuth`，`load_or_generate()` 读 `~/.config/timetrace-server/tokens.json` 或首启自动生成 `tt_live_<32urlbytes>` 并 logger.info；`create_app(..., auth=...)` 给 `/v1/ingest/*` 套 `Depends(bearer)`，`/healthz` 与 frontend-facing 的 records / search / feedback 不要 token。
- **数据目录**：`%USERPROFILE%/TimeTraceData/`（不在仓库内）
- **日志**：`structlog.get_logger(__name__)`，禁用 `print`
- **前端代码风格**：以 inline style + Tailwind 工具类混用为主，已装 `@radix-ui/react-dialog/select/separator/slot/tooltip`、`@tanstack/react-query`、`lucide-react`，新增 UI 优先复用

## 仍然存在的桩代码

| 文件 | 方法 | 状态 |
|------|------|------|
| `server/mcp_layer/tools.py` | `get_category_stats()`、`search_activity()` | Phase 1.5+ stub（其余 MCP 工具已实装） |

## 测试

- `tests/` 当前 176 passed：`test_api / test_auth / test_backend_inprocess / test_blob_local / test_http_backend / test_ingest / test_outbox / test_phash_index / test_privacy / test_queue_inmemory / test_rules / test_search / test_storage / test_vlm / test_vlm_smoke / test_worker_pipeline`
- `pytest-asyncio` `asyncio_mode = "auto"`（pyproject.toml）
- 不 mock DB，全部用 `tmp_path` 下的真实 SQLite 文件；HttpBackend E2E 用 `httpx.ASGITransport(app=...)` 直接打 in-process FastAPI，零 socket 零线程
- VLM 测试分两层：`test_vlm.py` 单元（mock httpx），`test_vlm_smoke.py` 真实端点（需 `.env`，无 key 自动 skip）

## 开发日志与文档入口

- [infra/readme.md](infra/readme.md) — 架构 / 存储 / 隐私 / 路线图深度文档
- [devlogs/README.md](devlogs/README.md) — 开发过程归档（按 backend / frontend / infra / research 分类，新会话排查问题前先翻一下相邻分类）
- [README.md](README.md) — 用户向快速启动
- `.env.example` — VLM 启用模板
