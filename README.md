# TimeTrace

**Windows-first、local-first 的个人工作记忆层。** TimeTrace 在后台按窗口事件与画面变化采集活跃窗口和关键帧，把原始记录存进本地 SQLite + 文件系统，再通过时间轴、混合搜索、分层摘要和 MCP，把「我刚才做过什么」变成可回放、可检索、可供 AI 使用的上下文；CPU、内存与每日磁盘增长的长期基准仍待补。

> **架构说明**：项目从单进程拆为 **客户端 / 服务端可分离**。**两种模式都可用**：
>
> - **单进程**（默认 / 推荐 单机使用）：`uv run timetrace` 一个进程跑全部
> - **双进程 / 分布式**（多设备 / 远程）：`uv run timetrace-server` 跑服务端 + `uv run timetrace-client` 跑采集端，可分别部署在不同机器
>
> 完整重构方案见 [devlogs/infra/](devlogs/infra/)。

## 当前能力与边界

| 层 | 已上线 | 当前边界 |
|---|---|---|
| 采集 | 活跃窗口、关键帧、idle、隐私黑名单、单/双进程 outbox | OCR 区域识别与落盘前模糊尚未实现 |
| 检索 | records FTS5、以图搜图 pHash、`/v1/search/text` 的 FTS5 + 文本向量 RRF | `/v1/search/by-image` 的文本通道仍是 LIKE；向量索引仍是 numpy 全量 cosine |
| 分析 | 本地 VLM 描述、flat-6 分类、审计日志、文本 embedding | Classifier V2 只有校准实验和预备稿，未进生产 |
| 记忆 | `5min → 1h → 6h → day → week` 指标级联、LLM 叙述、粗到细下钻 | `source_hash` 不包含子叙述文本，手动重叙述父层仍需 `--force` |
| AI 接口 | Web Agent、报告、MCP、`search_summaries` | 唯一写工具是 `apply_label`，MCP 不返回原始截图；实时工具清单看 `mcp_layer/server.py` |
| 运维 | 登录鉴权、Docker/Compose 后端、Cloudflare Tunnel + nginx、GitHub Actions 前后端同 SHA 发布 | 生产 LLM 单卡串行；客户端公网故障转移仍需专项验证 |

这里的「已上线」表示仓库代码已落地并在个人生产环境跑通过，不等于每项都已经完成通用产品化或长期性能验收。当前真实待办统一维护在 [devlogs/PLAN.md](devlogs/PLAN.md)。

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

- API 在 `http://127.0.0.1:8765`，`/healthz` 可直接探活；`/docs` 需要先登录并完成首次改密
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

- 在多台 Windows 桌面采集，并汇总到 Windows/Linux/macOS server；当前 capture client 只支持 Windows
- 把 server 部署在家里小主机或云服务器，外出时仍能采集（断网时 outbox 缓冲）
- VLM key 集中放服务端，客户端只采集

### Server 端（Linux / macOS / Windows）

```bash
uv sync
uv run timetrace-server                  # 起服务，默认监听 127.0.0.1:8765
```

首次启动会自动生成一个 bearer token 写到 `~/.config/timetrace-server/tokens.json`（POSIX 上 chmod 600）并打印到日志，**复制这个 token**。

服务端管理命令：

```bash
uv run timetrace-server info                     # 看 data_dir / token_file / 监听地址
uv run timetrace-server tokens list              # 列所有 token（值已 mask，只显示 label + 后 8 位）
uv run timetrace-server tokens add Yuki-Laptop   # 给新设备发 token，完整值打印一次
uv run timetrace-server tokens revoke Yuki-Laptop  # 吊销
uv run timetrace-server backfill <start> <end>      # 历史指标级联回填
uv run timetrace-server narrate <start> <end>       # 生成待处理分层叙述；重跑需加 --force
```

`tokens add/revoke` 后**重启服务端**，新 token 才生效。

### Client 端（采集机器）

```bash
uv sync
uv run timetrace-client init             # 交互式填 server URL + token + 设备名等
uv run timetrace-client                  # 起采集
```

`init` 把配置写到 `%USERPROFILE%/TimeTraceData/client.toml`。每个字段都显示当前值作为默认，按 Enter 接受。

客户端启动后会在 `http://127.0.0.1:8764` 提供本机控制面板，也可以从托盘选择“打开控制面板”。页面只展示采集、连接和 outbox 状态，可安全暂停/恢复采集以及启停已配置的 HTTP 连接；它不会返回 bearer token、截图或本地文件路径。控制服务固定绑定 IPv4 loopback，不开放 CORS，写操作同时校验 Host、同源 Origin、会话 cookie 和 CSRF token。

端口可在 `client.toml` 调整；端口被占用时仅控制面板不可用，采集和上传继续运行：

```toml
[control]
enabled = true
port = 8764
```

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

当前生产部署：server 以非 root Docker 容器跑在家里 Ubuntu 小主机（NAT 后）；公网由 Cloudflare Tunnel 出站接入本机 `127.0.0.1:8080` nginx，nginx 统一托管 SPA 并反代受鉴权保护的 API。推送 `deploy` 分支后，GitHub Actions 只构建并发布包含 server、SPA 与部署资产的不可变 GHCR 镜像；部署机再通过出站 HTTPS 主动拉取该 SHA，完成 Compose 与前端的原子切换，不依赖 GitHub Runner 经 FRP 反向 SSH。

容器的运行进程仅有 TimeTrace server，但镜像也携带同 SHA 的 SPA 和部署资产供更新器提取。宿主机上的 GPU / LM Studio、nginx 与 Cloudflare Tunnel 不进容器；Compose 使用 host network 访问 LM Studio 的 `127.0.0.1:1234`。部署用户现有的 `$HOME/TimeTraceData` 和 `$HOME/.config/timetrace-server` 原位挂载，旧源码仓库只作为首次切换时的自动回滚目标。完整部署脚手架见：

- [devlogs/infra/archive-202605161000-deployment-architecture.md](devlogs/infra/archive-202605161000-deployment-architecture.md)
- [devlogs/infra/archive-202605161015-cicd-workflow.md](devlogs/infra/archive-202605161015-cicd-workflow.md)
- [`deploy/Dockerfile`](deploy/Dockerfile) — 多阶段、非 root、包含同 SHA SPA 的生产镜像
- [`deploy/docker-compose.yml`](deploy/docker-compose.yml) — 生产 host-network Compose 与原位数据挂载
- [`timetrace-update.sh`](timetrace-update.sh) — 部署机主动解析 `deploy` SHA、拉镜像并执行发布的一键入口
- [`deploy/deploy-container.sh`](deploy/deploy-container.sh) — SQLite 快照、后端/SPA 切换、健康检查和自动回滚
- [`deploy/nginx-timetrace.yukirin.me.conf`](deploy/nginx-timetrace.yukirin.me.conf) — loopback nginx 的 SPA/API 同源入口模板
- [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) — 只构建并发布 GHCR 不可变镜像
- [`deploy/deploy.sh`](deploy/deploy.sh) / [`deploy/timetrace-server.service`](deploy/timetrace-server.service) — 已退役的源码部署路径，仅保留作应急参考

首次安装必须先由有 sudo 权限的操作者创建宿主机目录并安装 nginx 配置；以下命令在仓库检出目录中以未来执行更新的部署用户运行：

```bash
sudo install -d -m 755 /srv/timetrace
sudo install -d -o "$USER" -g "$(id -gn)" -m 700 \
  /srv/timetrace/runtime /srv/timetrace/config
sudo install -d -o "$USER" -g "$(id -gn)" -m 755 /srv/timetrace/web
install -d -m 700 "$HOME/TimeTraceData" "$HOME/.config/timetrace-server"

sudo install -m 644 deploy/nginx-timetrace.yukirin.me.conf \
  /etc/nginx/sites-available/timetrace
sudo ln -sfn /etc/nginx/sites-available/timetrace \
  /etc/nginx/sites-enabled/timetrace
sudo nginx -t
sudo systemctl reload nginx

install -d "$HOME/.local/bin"
install -m 755 timetrace-update.sh "$HOME/.local/bin/timetrace-update"
```

确认该用户可执行 `docker version`，并按实际部署补齐环境文件、token 与数据目录；不要把凭据提交进仓库。完成一次性 bootstrap 后，日常手动更新只有一句：

```bash
timetrace-update
```

若 GHCR 包不是公开可读，需先用仅含 `read:packages` 权限的 token 执行一次 `docker login ghcr.io`。指定完整 Git SHA 运行 `timetrace-update <sha>` 可部署旧版本；脚本仍会进行健康检查并在失败时恢复上一套后端与 SPA。

---

## 开发

```bash
uv sync
uv run pytest                               # 全套测试
uv run pytest tests/test_storage.py -k name # 单测
uv run ruff check src/
uv run ruff format src/
```

GitHub Actions 对 `main` 的 push 和 PR 运行 lint、format check 与 pytest。最近一次带日期和 commit 的全量结果只记录在 [滚动 PLAN](devlogs/PLAN.md)，避免多个 README 复制后漂移。

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
│   ├── summary/     # 五层指标级联 + LLM 叙述
│   ├── agent/       # Web Agent 与共享工具实现
│   ├── llm_log.py   # 跨调用方 LLM 请求账本
│   └── mcp_layer/   # FastMCP 对外入口
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

- [devlogs/PLAN.md](devlogs/PLAN.md) — **当前未完成项与下一步，先看这里**
- [infra/readme.md](infra/readme.md) — 当前架构地图与深度文档索引
- [devlogs/README.md](devlogs/README.md) — 按时间和主题索引开发/排障事实；具体 archive 是历史快照
- [`.env.example`](.env.example) — VLM 启用模板
- [CLAUDE.md](CLAUDE.md) — AI agent 的项目约束、运行命令和不可破坏的不变量

事实冲突时按「真实代码与测试 → `infra/` 活文档 → `devlogs/PLAN.md` → 最新 devlog → 旧 devlog」判断。这样可以保留排障历史，同时避免旧结论重新污染当前实现。

---

## 许可证

MIT
