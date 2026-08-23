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

API 启动后用 `/healthz` 公开探活；`/docs` 与 `/openapi.json` 需要先登录并完成首次改密。

## 运行模式（重要：常被混淆）

代码已按 client / server / common 三层重组，**但运行时仍有两种入口**：

| 入口 | 状态 | 进程数 | 适用场景 |
|------|------|--------|----------|
| `uv run timetrace` (`main.py`) | ✅ 默认、稳定 | **1 个**（capture + API + worker + 托盘 全装一起） | 单机日常使用 |
| `uv run timetrace-server` + `uv run timetrace-client` | ✅ 已生产使用 | 2 个（client 只采集，server 跑 API+DB+VLM） | 多设备、远程访问、headless 小主机 |

**关键事实**：单进程模式下 `capture` 通过 `InProcessBackend` 直接调 `Database` + `PHashIndex`，不走 HTTP。HttpBackend / Outbox / OutboxSender / `/v1/ingest/*` 路由都已实装但**单进程模式不使用**，只在双进程模式下被 client 端激活。改 capture 行为时记得两条路径都要想到。

## 架构关键点

> 端到端数据流、模块职责、Schema 等深度文档统一在 [infra/readme.md](infra/readme.md)。这里只列"读多个文件才能拼出"的关键事实。

**进程拓扑**（[main.py](src/timetrace/main.py)）：

- 主线程跑 pystray 托盘 + asyncio event loop
- `asyncio.TaskGroup` 并行管理 `capture`、`worker`、`api`、`quit_watcher`、`reclaim`、`report_scheduler`，以及受配置开关控制的 `rollup` / `narrate`；不要依赖固定任务数判断拓扑
- SIGINT / 托盘 Quit / uvicorn 退出**统一路由到** `quit_event`（`loop.call_soon_threadsafe(quit_event.set)`），由 `_watch_quit` 设置 `server.should_exit=True` 并取消同伴任务，避免 uvicorn 二次抛 SIGINT 中断 event loop
- `_reclaim_loop` 每 60s 清理 worker 异常退出残留的 `analysis_task` 行

**VLM 接入**（已实装，非桩）：

- 通过 `.env` 配置（`TIMETRACE_VLM_BASE_URL` / `_API_KEY` / `_MODEL` / `_DISABLE_THINKING`），见 [.env.example](.env.example)
- `main.py` 在 `AppConfig()` 之前调用 `load_dotenv()`，让 `VLMConfig.from_env()` 看到环境变量
- 没设 API Key → `VLMConfig.from_env()` 返回 `None` → worker 跳过 `_describe`，搜索接口的 semantic 通道返回 `unavailable`，**不报错**
- 退出前必须 `await vlm_client.aclose()`，否则 httpx 连接池会在 atexit 报警

**「金字塔」记忆层**（指标级联 + 叙述层，[server/summary/](src/timetrace/server/summary/)）：

- **指标级联**（`rollup.py`）：L1 帧按固定时间窗 `5min→1h→6h→day→week` 自底向上滚进单张自相似 `summaries` 表。`cat_seconds`/`app_seconds` 是可加的 Σ-own-span（能跨层 SUM），`active_seconds` 是墙钟并集（不可加、不入级联、读时现算）。时长口径是「本机捕获活跃下限」非总时间——深度文档 [infra/storage/pyramid-schema.md](infra/storage/pyramid-schema.md)
- **叙述层**（`narrative.py`）：每窗用 LLM 生成 `description`(流水账)/`key_points`/`evaluation`。叶子读帧描述、父层读子叙述（summary-of-summaries），**严格自底向上**——父窗必须等子窗叙述完才生成（`NarrativeCascade._has_pending_children` 门控），否则只会复述指标且**永不自纠**。零描述帧的窗（纯 window_switch 无截图）短路跳 LLM、写指标版极简叙述
- **source_hash 重发**：父窗 source_hash 是子窗 Merkle，**只指纹指标/分类、不含叙述文本**；记录被重分类→叶子哈希变→冒泡→`upsert_summary` 重置 pending→重新叙述。注意：子窗叙述本身变了**不**触发父窗重做（已知缺口，目前只在手动 `--force` 下碰到）
- **两个后台 loop**（`bootstrap.py`，均默认关）：`_rollup_loop`（`TIMETRACE_ROLLUP_ENABLED=1`，纯 SQL、不依赖 VLM）+ `_narrate_loop`（`TIMETRACE_NARRATE_ENABLED=1`，依赖 VLM）。轮询式：每 tick 每 grain 限 20 窗、间隔 `loop_interval_s` 配速单卡——为稳态设计，大积压排空时偏慢（可临时连续灌或调大 batch）。env 开关别加行内注释（`=1  # x` 会被当成值，`_env_truthy` 不认）
- **暴露给 agent/MCP**：`search_summaries`（`agent/tools.py` + `mcp_layer/server.py`）——粗粒度总览→按返回的 `drill_down_grain` + 该窗 iso 区间换细 grain 下钻，**时间区间即父子链路**
- **admin CLI**：`timetrace-server backfill <start> <end>`（建指标）、`narrate <start> <end> [--force] [--grains 1h,6h,day,week] [--max-tokens N]`（生成/重叙述；`--grains` 只重跑指定层）
- **LM Studio 配置硬约束**（box 的 35B 端点，叙述/VLM/报告共用）：必须 **context ≥16384 + `max concurrent predictions`(=parallel)=1**。parallel 默认 4 会把 KV cache 等分成 1/4（4096→实际每请求 1024），叙述 prompt 立刻溢出。该 35B 是**永远思考**模型（`enable_thinking:false` 与 `/no_think` 实测都关不掉），思考算进输出 token → `max_tokens` 要给够（per-grain：5min 4000、聚合窗 6000-7000）。单卡 parallel=1 → 所有消费者（worker 图片分析 / 叙述 / 报告 / ask_agent）**FIFO 排队串行**，嵌入(nomic)是另一个模型、抢卡时会触发换模型 thrash

**数据流挂钩**：

- `Capture → Database`：每条 record 触发 `INSERT INTO analysis_tasks` 入队
- `Worker → VLM`：从 `analysis_tasks` 拉任务、调 VLM、回写 `analysis_results.vlm_desc` + `category_final`
- `Capture → PHashIndex`：每张 thumb 计算 pHash，加入内存 BK-tree（`PHashIndex.from_db` 启动时从 DB 重建）
- `API /v1/search/by-image`：pHash 视觉通道（BK-tree 距离）+ 图→VLM 描述→`vlm_desc LIKE` 文本通道 + RRF；这里的 `_bm25_search` 名字是历史残留，尚未迁 FTS5

**前后端契约**：

- 前端**不被** Python 后端托管。`/thumbs/{path}` 与 `/blob/{path}` 是受 `require_principal` 保护的 `FileResponse` 路由，不是裸 `StaticFiles`；前端开发时由 Vite 代理 API，生产静态文件由 `yukirin-server` 的 loopback-only nginx 托管
- 缩略图 URL 模式：`/thumbs/{path.replace(/\\/g, '/')}`（windows 反斜杠转正斜杠）
- 列表项已包含 `vlm_desc / category_final / app_name / window_title / url`，详情切换无需再请求

## 项目约定

- **入口**：三个 `[project.scripts]`：
  - `timetrace` → `main.py:main` 单进程默认（pystray 主线程 + asyncio loop）
  - `timetrace-client` → `client/cli.py:main` 客户端守护；子命令 `init` (交互/--non-interactive)、`print-config`
  - `timetrace-server` → `server/cli.py:main` 服务端守护；子命令 `info`、`tokens list/add/revoke`、`backfill <start> <end>`（建指标级联）、`narrate <start> <end> [--force] [--grains] [--max-tokens]`（生成/重叙述）—— 后两个见 admin_cmd.py
  - main.py 与 server/cli.py 共享 `server/bootstrap.py`（build_server_components + serve(extra_tasks=...)），不会再次漂移
- **三层目录**：`src/timetrace/{common,client,server}/`。配置在 `common/config.py` + `client/core/config.py`（ClientConfig 现在吃 storage/capture/privacy 三段，是双进程 client 的单一 source of truth；含 `apply_env_overrides()` 接 9 个 TIMETRACE_* env vars）；wire schema 在 `common/protocol.py`；capture / 托盘 / outbox / backend / init_cmd 在 `client/`；api / db / queue / blob / worker / vlm / phash / mcp / admin_cmd / bootstrap 在 `server/`。
- **DB 路径**：`server/db/sqlite.py::SqliteDatabase`，`server/db/__init__.py` 导出 `Database = SqliteDatabase` 别名 —— 现在所有调用方都还是用 `from timetrace.server.db import Database`，PostgresDatabase 在 P5 进来时这条别名升级为 typing.Protocol。
- **BackendClient Protocol** in `client/core/backend.py`：capture 不再直接用 db，全走 `BackendClient`。三种实现：
  - `InProcessBackend`：直调 `SqliteDatabase` + `PHashIndex`，单进程模式用
  - `HttpBackend`：POST `/v1/ingest/record` + `.../close`，双进程下底层 transport（支持 `data_dir` 相对路径解析、`auth_token` + `device_id` 双 header 注入）
  - `OutboxBackend`：把 capture 调用先 append 进 Outbox，由 `OutboxSender` 后台 drain 给 HttpBackend；已是 timetrace-client 默认 backend
- **Outbox**：`client/core/outbox.py`，append-only `log.jsonl` + `blobs/` + `state.json`（atomic rename + fsync）。`_read_log` 对末尾 partial JSON 行容错。`OutboxSender` 严格 FIFO + 指数 backoff + 可选 token bucket 限速 + `compact_every_n_acks=200` inline compaction（crash-safe 顺序：state.acked=0 先于 log 重写，最坏 at-least-once replay）。
- **Auth**：`server/auth.py::ServerAuth`，`load_or_generate()` 读 `~/.config/timetrace-server/tokens.json`（POSIX 上 chmod 600）或首启自动生成 `tt_live_<32urlbytes>`；`/v1/ingest/*` 与 `/mcp` 是 bearer-only，records/search/feedback 等浏览器业务路由要求 cookie 或 bearer principal，只有 `/healthz` 是公开探活。token CRUD 通过 `timetrace-server tokens` 子命令或 Web admin；CLI 改完重启 server 才生效。
- **Ingest 路由幂等性**：`/v1/ingest/record` 用 `client_record_id` UNIQUE 索引做 record 级幂等；`screenshots(record_id, hash_sha256)` UNIQUE 索引做 screenshot 级幂等防 outbox at-least-once replay 双插。`/close` 路由按 server id 或 client_record_id 兜底，未知 id 返 404；blob 路径用 record.ts_start 算日期目录（不是上传时刻）。
- **数据目录**：`%USERPROFILE%/TimeTraceData/`（不在仓库内）
- **日志**：`structlog.get_logger(__name__)`，禁用 `print`
- **前端代码风格**：以 inline style + Tailwind 工具类混用为主，已装 `@radix-ui/react-dialog/select/separator/slot/tooltip`、`@tanstack/react-query`、`lucide-react`，新增 UI 优先复用
- **文档过期警告格式**：所有"本页/本段内容已过期"的标注统一用 `## **⚠️ 一句话标题**`（H2 + 加粗紧贴 emoji，无空格）。这样 `grep '^## \*\*⚠️' infra/ devlogs/` 能稳定枚举所有 deprecation 标注，不会因为下个人写成 H3 / blockquote / `> ⚠️` 而漏命中
- **devlog 写完即不改**：`devlogs/**/*.md` 是历史快照（只追加新归档，不改老归档）。子目录索引 `devlogs/README.md` + `infra/readme.md` 可以更新，但具体 archive 文件本身视为只读 —— 想纠正 / 补充就再写一份新 archive

## 部署 / 运行环境

- **GitHub identity**：仓库是 `Vanilla-Yukirin/TimeTrace`。本地 git config 的 `Yuki` 只是临时本地标签，不要混
- **部署目标**：家里 Ubuntu 小主机（NAT 后）；GitHub Actions 只发布 GHCR 制品，主机通过出站 HTTPS 主动拉取，不再依赖 CI 经 FRP SSH 入站；历史拓扑见 [devlogs/infra/archive-202605161000-deployment-architecture.md](devlogs/infra/archive-202605161000-deployment-architecture.md)
- **公网入口**：Cloudflare Edge → outbound Cloudflare Tunnel → `yukirin-server` 的 `127.0.0.1:8080` nginx；nginx 从 `/srv/timetrace/web/current` 托管 SPA，并把 `/v1`、`/mcp`、`/healthz`、`/thumbs`、`/blob` 等路径反代到 `127.0.0.1:8765`。Puck/xcy 均不在 TimeTrace 正式运行链路中；敏感接口仍必须经过现有鉴权，MCP 不返回原始截图
- **CI/CD 触发**：`push` 到 `deploy` 分支或 `workflow_dispatch` 只构建并发布不可变 GHCR 镜像，不连接部署机。GitHub 在触发时把 ref 固定为 `github.sha`，排队期间不会漂移。PR 会构建临时镜像做容器冒烟；main push 不重复构建 Docker
- **部署模型（唯一长期模型）**：`main` 是开发主干，`deploy` 是只接受 main fast-forward 的生产指针；发布制品命令是 `git push origin main:deploy`。`deploy.yml` 构建并推送同时包含 server、SPA 和部署资产的 `ghcr.io/vanilla-yukirin/timetrace-server:<sha>`；镜像就绪后，在部署机以 Docker 组用户执行 `timetrace-update`，由部署机通过出站 HTTPS 解析 `deploy` SHA、拉镜像并原子切换 `/srv/timetrace/runtime/releases/<sha>` 与 `/srv/timetrace/web/releases/<sha>`，失败自动恢复上一套
- **容器边界**：只容器化 `timetrace-server`。生产 Compose 使用 Linux host network，使容器仍可访问宿主机 LM Studio `127.0.0.1:1234`；GPU、LM Studio、nginx、Cloudflare Tunnel 都留在宿主机。现有 `/home/vanilla/TimeTraceData` 与 `/home/vanilla/.config/timetrace-server` 原位 bind mount，不搬库、不复制截图；首次切换会无插值解析旧仓库 `.env`，再以 Compose 安全的单引号 dotenv 规范化到 `/srv/timetrace/config/timetrace.env`，保留引号、转义与字面 `$` 的原值
- **旧源码部署已退役但保留回滚**：`~/Github/TimeTrace` 与 `timetrace-server.service` 不改名、不删除。首次容器切换会停旧 unit、做 stopped-service SQLite 快照并启动容器；失败则自动恢复旧 unit，成功才 disable 旧 unit。`deploy/deploy.sh` 与 unit 模板仅作历史/应急参考，不再是自动部署主路径
- **发布制品走工作流，部署激活走主机更新器**：不要在部署机 `git reset/pull/checkout`、直接 `docker compose up` 或手动 `systemctl restart`。标准入口是 `timetrace-update [<sha>]`，它保留不可变 SHA、健康检查和自动回滚；容器不管理的 LM Studio 模型仍可用 `lms load/unload/ps` 操作
- **数据库降级门禁只保证标准入口**：成功的 device-aware release 会安装 guard-aware `timetrace-update` 并提交最低数据库兼容标记；标准入口会拒绝让旧镜像读取已有设备归属的数据。具备 trusted-shell/root 权限的操作者仍能手工执行历史 helper 绕过门禁，这属于显式 break-glass，不是受支持的部署路径

## 仍然存在的桩代码 / 已知 bug

| 文件 | 问题 | 状态 |
|------|------|------|
| `server/mcp_layer/tools.py` | 整个文件是早期 Phase 1.5 桩（`get_category_stats`/`search_activity` 返回空） | ⚠️ **死代码**：真正的 MCP 走 `mcp_layer/server.py` → `agent/tools.py`（已全实装），没人 import 这个文件，可删 |
| ~~`client/capture/window.py::_get_process_info`~~ | ~~app_name 全 "Unknown"~~ | ✅ 已修：`0ec105e` ctypes 直调 `QueryFullProcessImageNameW`；416 PID 实测 242 (58%) 拿到真名，剩余 174 是系统进程权限不允许 |

## 测试

- 最近一次全量基线与日期只维护在 `devlogs/PLAN.md`；不要在本文件复制动态测试数。金字塔与 agent 相关入口包括：`test_rollup_cascade / test_summary_windows / test_source_hash / test_narrative / test_agent_tools / test_agent_runner / test_query_stats / test_mcp_tools / test_mcp_auth / test_admin_backfill / test_admin_cmd / test_llm_log / test_pyramid_routes`
- `pytest-asyncio` `asyncio_mode = "auto"`（pyproject.toml）
- 不 mock DB，全部用 `tmp_path` 下的真实 SQLite 文件；HttpBackend E2E 用 `httpx.ASGITransport(app=...)` 直接打 in-process FastAPI，零 socket 零线程
- VLM 测试分两层：`test_vlm.py` 单元（mock httpx），`test_vlm_smoke.py` 真实端点（需 `.env`，无 key 自动 skip）

## 开发日志与文档入口

- [infra/readme.md](infra/readme.md) — 架构 / 存储 / 隐私 / 路线图深度文档
- [devlogs/README.md](devlogs/README.md) — 开发过程归档（按 backend / frontend / infra / research 分类，新会话排查问题前先翻一下相邻分类）
- [README.md](README.md) — 用户向快速启动
- `.env.example` — VLM 启用模板
