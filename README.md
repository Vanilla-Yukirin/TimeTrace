# TimeTrace

**Windows-first, local-first 桌面活动记忆层。** 低打扰采集活跃窗口与关键帧截图，落地到 SQLite + 本地文件系统；时间轴回放、关键词 + 以图搜图 + VLM 语义搜索；MCP 上下文导出。

> **架构说明**：项目从单进程拆为 **客户端 / 服务端可分离**。**两种模式都可用**：
>
> - **单进程**（默认 / 推荐 单机使用）：`uv run timetrace` 一个进程跑全部
> - **双进程 / 分布式**（多设备 / 远程）：`uv run timetrace-server` 跑服务端 + `uv run timetrace-client` 跑采集端，可分别部署在不同机器
>
> 完整重构方案见 [devlogs/infra/](devlogs/infra/)。

---

## 快速启动（单机单进程）

要求：Python 3.12+，[uv](https://github.com/astral-sh/uv)

```bash
git clone https://github.com/Vanilla-Yukirin/TimeTrace.git
cd TimeTrace
uv sync
uv run timetrace
```

跑起来之后：

- API 在 `http://127.0.0.1:8765/docs`（OpenAPI 文档），`/healthz` 探活
- 系统托盘出现 TimeTrace 图标，右键 Quit 退出
- 数据写在 `%USERPROFILE%/TimeTraceData/`（不在仓库里）

可选：在仓库根放 `.env` 文件配 VLM 凭证（参考 [`.env.example`](.env.example)），不配也能跑，只是 VLM 描述与语义搜索不可用。

### 前端

前端是独立 vite 服务（不被 Python 后端托管），需要另开终端：

```bash
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173
```

---

## 双进程模式（多设备 / 远程访问）

适用场景：

- 在 Windows 桌面 + Mac + Linux 小主机同时采集，归到同一时间线
- 把 server 部署在家里小主机或云服务器，外出时仍能采集（断网时 outbox 缓冲）
- VLM key 集中放服务端，客户端只采集

### Server 端（任意机器：Linux / macOS / Windows 都行）

```bash
uv sync
uv run timetrace-server                  # 起服务，监听 0.0.0.0:8765
```

首次启动会自动生成一个 bearer token 写到 `~/.config/timetrace-server/tokens.json`（POSIX 上 chmod 600）并打印到日志，**复制这个 token**。

服务端管理命令：

```bash
uv run timetrace-server info                     # 看 data_dir / token_file / 监听地址
uv run timetrace-server tokens list              # 列所有 token（值已 mask，只显示 label + 后 8 位）
uv run timetrace-server tokens add Yuki-Laptop   # 给新设备发 token，完整值打印一次
uv run timetrace-server tokens revoke Yuki-Laptop  # 吊销
```

`tokens add/revoke` 后**重启服务端**，新 token 才生效。

### Client 端（采集机器）

```bash
uv sync
uv run timetrace-client init             # 交互式填 server URL + token + 设备名等
uv run timetrace-client                  # 起采集
```

`init` 把配置写到 `%USERPROFILE%/TimeTraceData/client.toml`。每个字段都显示当前值作为默认，按 Enter 接受。

**非交互（CI / Ansible / Docker / systemd 首启）**：

```bash
TIMETRACE_SERVER_URL=https://my-server.example:8765 \
TIMETRACE_AUTH_TOKEN=tt_live_xxxxxxxxxxxx \
uv run timetrace-client init --non-interactive
```

支持的环境变量：`TIMETRACE_SERVER_URL`、`TIMETRACE_AUTH_TOKEN`、`TIMETRACE_DEVICE_ID`、`TIMETRACE_DEVICE_NAME`、`TIMETRACE_DEVICE_DESC`、`TIMETRACE_OUTBOX_DIR`、`TIMETRACE_UPLOAD_MAX_KBPS`、`TIMETRACE_DATA_DIR`、`TIMETRACE_PRIVACY_MODE`。env 优先级 > client.toml。

**调试**：

```bash
uv run timetrace-client init --probe                # init 后跑一次 GET /healthz
uv run timetrace-client print-config                # 看实际生效的配置（token mask 显示）
```

### 部署到家里小主机

我自己的部署方式：server 跑在家里 Ubuntu 小主机（NAT 后），通过 FRP 反向隧道映射 SSH 端口到云服务器，CI/CD 通过云服务器 ProxyJump SSH 进小主机部署。Web UI 永不公网，看页面走 `ssh -L`。完整部署架构与脚手架见：

- [devlogs/infra/archive-202605161000-deployment-architecture.md](devlogs/infra/archive-202605161000-deployment-architecture.md)
- [devlogs/infra/archive-202605161015-cicd-workflow.md](devlogs/infra/archive-202605161015-cicd-workflow.md)
- [`deploy/deploy.sh`](deploy/deploy.sh) — 在小主机上跑的部署脚本（带详尽注释）
- [`deploy/timetrace-server.service`](deploy/timetrace-server.service) — systemd `--user` 单元模板
- [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) — workflow_dispatch 手动触发，带 fork-safe repo guard

---

## 开发

```bash
uv sync
uv run pytest                               # 全套测试
uv run pytest tests/test_storage.py -k name # 单测
uv run ruff check src/
uv run ruff format src/
```

测试当前 252 passed，对所有 PR 在 GitHub Actions 上跑（`ci.yml`）。

---

## 当前架构

```
src/timetrace/
├── common/          # 跨层共享：config、CaptureContext、phash 算法、wire schema
├── client/
│   ├── capture/     # 切窗监听、截图、隐私门控、空闲检测
│   ├── core/        # BackendClient Protocol + InProcessBackend / HttpBackend / OutboxBackend
│   ├── tray.py      # pystray 托盘
│   ├── cli.py       # timetrace-client 入口（含 init / print-config 子命令）
│   └── init_cmd.py  # 交互式 / env 驱动的初始化流程
├── server/
│   ├── api/         # FastAPI app + routes (records / search / feedback / ingest)
│   ├── auth.py      # bearer token 自生成 + 校验
│   ├── admin_cmd.py # timetrace-server tokens / info 子命令
│   ├── bootstrap.py # main.py 与 cli.py 共享的服务端装配
│   ├── db/          # SqliteDatabase（PostgresDatabase 留 P5）
│   ├── queue/       # InMemoryQueue（RedisQueue 留 P5）
│   ├── storage/     # LocalBlobStorage（S3 留 P5）
│   ├── phash_index/ # BK-tree 内存索引
│   ├── vlm/         # OpenAI 兼容 VLM 客户端
│   ├── worker/      # 分析 Worker
│   └── mcp_layer/   # MCP 工具
└── main.py          # 单进程入口（capture + api + worker + tray）
```

### Backend 架构（capture → 哪里）

| 模式 | Backend 实现 | 路径 |
|---|---|---|
| 单进程 | `InProcessBackend` | capture → 直调 `SqliteDatabase` + `PHashIndex`（不走 HTTP） |
| 双进程 | `OutboxBackend` → `OutboxSender` → `HttpBackend` | capture → outbox.append → 后台 sender → POST /v1/ingest |

`HttpBackend` 也可独立直连（不经 outbox），目前只在测试里这样用。

---

## 项目文档

- [infra/readme.md](infra/readme.md) — 架构 / 存储 / 隐私 / 路线图深度文档
- [devlogs/](devlogs/) — 开发过程归档（按 backend / frontend / infra / research 分类）
- [`.env.example`](.env.example) — VLM 启用模板
- [CLAUDE.md](CLAUDE.md) — Claude Code 协作约定（也是给人读的快速地图）

---

## 许可证

MIT
