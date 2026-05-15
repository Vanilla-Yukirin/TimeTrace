# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

TimeTrace 是一个 **Windows Only、本地优先**的桌面活动记忆层：低打扰采集活跃窗口与关键帧截图，落地到 SQLite + 本地文件，提供时间轴回放、多模态搜索（关键词 + 以图搜图 + VLM 语义）、MCP 上下文导出。Python 3.12+、asyncio、FastAPI、aiosqlite。

## 常用命令

```bash
# 后端
uv sync                                    # 安装/同步依赖
uv run timetrace                           # 单进程模式：捕获 + 分析 + API + 托盘 全在一个进程
uv run pytest                              # 全部测试
uv run pytest tests/test_storage.py -k name  # 单测
uv run ruff check src/                     # Lint
uv run ruff format src/                    # 格式化

# 前端（独立 dev server，需另开一个终端）
cd frontend && npm run dev                 # http://127.0.0.1:5173/，vite 代理 /v1/* /thumbs/* 到 8765
cd frontend && npm run build               # 产物到仓库根的 frontend-dist/
```

API 启动后访问 `http://127.0.0.1:8765/docs` 看 OpenAPI、`/healthz` 探活。

## 运行模式（重要：常被混淆）

代码已按 client / server / common 三层重组，**但运行时仍有两种入口**：

| 入口 | 状态 | 进程数 | 适用场景 |
|------|------|--------|----------|
| `uv run timetrace` (`main.py`) | ✅ 默认、稳定 | **1 个**（capture + API + worker + 托盘 全装一起） | 单机日常使用 |
| `uv run timetrace-server` + `uv run timetrace-client` | 🚧 P3a-5b 在做 | 2 个（client 只采集，server 跑 API+DB+VLM） | 多设备、远程访问、headless 小主机 |

**关键事实**：单进程模式下 `capture` 通过 `InProcessBackend` 直接调 `Database` + `PHashIndex`，不走 HTTP。HttpBackend / Outbox / OutboxSender / `/v1/ingest/*` 路由都已实装但**单进程模式不使用**，只在双进程模式下被 client 端激活。改 capture 行为时记得两条路径都要想到。

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

- **入口**：当前默认 `src/timetrace/main.py:main` → pystray 主线程 + asyncio loop（单进程）。P3a-5b 加 `timetrace-client` / `timetrace-server` 双入口（client/cli.py + server/cli.py），main.py 保留做单进程兼容入口。
- **三层目录**：`src/timetrace/{common,client,server}/`。配置在 `common/config.py` + `client/core/config.py`；wire schema 在 `common/protocol.py`；capture / 托盘 / outbox / backend 在 `client/`；api / db / queue / blob / worker / vlm / phash / mcp 在 `server/`。
- **DB 路径**：`server/db/sqlite.py::SqliteDatabase`，`server/db/__init__.py` 导出 `Database = SqliteDatabase` 别名 —— 现在所有调用方都还是用 `from timetrace.server.db import Database`，PostgresDatabase 在 P5 进来时这条别名升级为 typing.Protocol。
- **BackendClient Protocol** in `client/core/backend.py`：capture 不再直接用 db，全走 `BackendClient`。三种实现：
  - `InProcessBackend`：直调 `SqliteDatabase` + `PHashIndex`，单进程模式用
  - `HttpBackend`：POST `/v1/ingest/record` + `.../close`，双进程下底层 transport（支持 `data_dir` 相对路径解析、`auth_token` + `device_id` 双 header 注入）
  - `OutboxBackend`：把 capture 调用先 append 进 Outbox，由 `OutboxSender` 后台串行 drain 给 HttpBackend（P3a-5b 接线后是 timetrace-client 默认 backend）
- **Outbox**：`client/core/outbox.py`，append-only `log.jsonl` + `blobs/` + `state.json`（atomic rename + fsync）。`_read_log` 对末尾 partial JSON 行容错。`OutboxSender` 严格 FIFO + 指数 backoff + 可选 token bucket 限速。
- **Auth**：`server/auth.py::ServerAuth`，`load_or_generate()` 读 `~/.config/timetrace-server/tokens.json` 或首启自动生成 `tt_live_<32urlbytes>` 并 logger.info；`create_app(..., auth=...)` 给 `/v1/ingest/*` 套 `Depends(bearer)`，`/healthz` 与 frontend-facing 的 records / search / feedback 不要 token。
- **Ingest 路由幂等性**：`/v1/ingest/record` 用 `client_record_id` UNIQUE 索引做 record 级幂等；`screenshots(record_id, hash_sha256)` UNIQUE 索引做 screenshot 级幂等防 outbox at-least-once replay 双插。`/close` 路由按 server id 或 client_record_id 兜底，未知 id 返 404；blob 路径用 record.ts_start 算日期目录（不是上传时刻）。
- **数据目录**：`%USERPROFILE%/TimeTraceData/`（不在仓库内）
- **日志**：`structlog.get_logger(__name__)`，禁用 `print`
- **前端代码风格**：以 inline style + Tailwind 工具类混用为主，已装 `@radix-ui/react-dialog/select/separator/slot/tooltip`、`@tanstack/react-query`、`lucide-react`，新增 UI 优先复用

## 部署 / 运行环境

- **GitHub identity**：仓库是 `Vanilla-Yukirin/TimeTrace`。本地 git config 的 `Yuki` 只是临时本地标签，不要混
- **部署目标**：家里 Ubuntu 小主机（NAT 后），CI 经云服务器 FRP 隧道 SSH 进；详见 [devlogs/infra/archive-202605161000-deployment-architecture.md](devlogs/infra/archive-202605161000-deployment-architecture.md)
- **Web UI 永不公网**：前端 / OpenAPI 走临时 `ssh -L 5173:localhost:5173 tt-rb4g` 隧道，按需起
- **CI/CD 触发**：仅 `workflow_dispatch`（GH Web 按钮 / `gh workflow run deploy.yml`）；Fork 安全 = `if: github.repository == 'Vanilla-Yukirin/TimeTrace'` + GH secret 不被 fork 继承双保险
- **systemd 用户**：`systemctl --user`（不 root）+ `loginctl enable-linger`，service 模板在 `deploy/timetrace-server.service`

## 仍然存在的桩代码 / 已知 bug

| 文件 | 问题 | 状态 |
|------|------|------|
| `server/mcp_layer/tools.py` | `get_category_stats()` / `search_activity()` | Phase 1.5+ stub |
| ~~`client/capture/window.py::_get_process_info`~~ | ~~app_name 全 "Unknown"~~ | ✅ 已修：`0ec105e` ctypes 直调 `QueryFullProcessImageNameW`；416 PID 实测 242 (58%) 拿到真名，剩余 174 是系统进程权限不允许 |

## 测试

- `tests/` 当前 227 passed：`test_api / test_auth / test_backend_inprocess / test_blob_local / test_client_config / test_http_backend / test_ingest / test_outbox / test_outbox_sender / test_phash_index / test_privacy / test_queue_inmemory / test_rules / test_search / test_storage / test_vlm / test_vlm_smoke / test_worker_pipeline`
- `pytest-asyncio` `asyncio_mode = "auto"`（pyproject.toml）
- 不 mock DB，全部用 `tmp_path` 下的真实 SQLite 文件；HttpBackend E2E 用 `httpx.ASGITransport(app=...)` 直接打 in-process FastAPI，零 socket 零线程
- VLM 测试分两层：`test_vlm.py` 单元（mock httpx），`test_vlm_smoke.py` 真实端点（需 `.env`，无 key 自动 skip）

## 开发日志与文档入口

- [infra/readme.md](infra/readme.md) — 架构 / 存储 / 隐私 / 路线图深度文档
- [devlogs/README.md](devlogs/README.md) — 开发过程归档（按 backend / frontend / infra / research 分类，新会话排查问题前先翻一下相邻分类）
- [README.md](README.md) — 用户向快速启动
- `.env.example` — VLM 启用模板
