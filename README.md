# TimeTrace

**Windows-first、local-first 的个人工作记忆层。** TimeTrace 在后台按窗口事件与画面变化采集活跃窗口和关键帧，把原始记录存进本地 SQLite + 文件系统，再通过时间轴、混合搜索、分层摘要和 MCP，把「我刚才做过什么」变成可回放、可检索、可供 AI 使用的上下文；CPU、内存与每日磁盘增长的长期基准仍待补。

> **架构说明**：项目从单进程拆为 **客户端 / 服务端可分离**。**两种模式都可用**：
>
> - **单进程**（默认 / 推荐 单机使用）：`uv run timetrace` 一个进程跑全部
> - **双进程 / 分布式**（多设备 / 远程）：`uv run timetrace-server` 跑服务端 + `uv run timetrace-client` 跑采集端，可分别部署在不同机器
>
> 当前架构看 [项目 Wiki](infra/readme.md)，下一步与验证基线看 [滚动 PLAN](devlogs/PLAN.md)。

## 当前能力与边界

| 层 | 已上线 | 当前边界 |
|---|---|---|
| 采集 | 活跃窗口、关键帧、idle、隐私黑名单、单/双进程 outbox | OCR 区域识别与落盘前模糊尚未实现 |
| 检索 | records FTS5、以图搜图 pHash、`/v1/search/text` 的 FTS5 + 文本向量 RRF | 图搜语义通道已接描述 embedding，关键词退路仍是 LIKE；向量索引为 numpy cosine 扫描 |
| 分析 | 本地 VLM 描述、flat-6 分类、审计日志、文本 embedding | Classifier V2 只有校准实验和预备稿，未进生产 |
| 记忆 | `5min → 1h → 6h → day → week` 指标级联、LLM 叙述、粗到细下钻 | `source_hash` 不包含子叙述文本，手动重叙述父层仍需 `--force` |
| AI 接口 | Web Agent、报告、MCP、`search_summaries` | 唯一写工具是 `apply_label`，MCP 不返回原始截图；实时工具清单看 `mcp_layer/server.py` |
| 多设备 | 设备身份与 Token 绑定、设置页设备管理、设备筛选与时间轴泳道、设备级摘要 | 全历史摘要覆盖与另一台客户端迁移仍需验收 |
| 运维 | 私人 Tailscale Serve + nginx、Docker 后端、同 SHA SPA、Windows Release 工作流 | 客户端签名/自动更新、传输与恢复长测仍待补齐 |

这里的「已上线」表示仓库代码已落地并在个人生产环境跑通过，不等于每项都已经完成通用产品化或长期性能验收。当前真实待办统一维护在 [devlogs/PLAN.md](devlogs/PLAN.md)。

---

## Windows 客户端安装包

连接已有服务端可从 [GitHub Releases](https://github.com/Vanilla-Yukirin/TimeTrace/releases) 下载 Windows x64 ZIP 和 SHA-256 校验文件，按 [安装与更新说明](packaging/windows/README.md) 校验、解压并运行安装器。安装包只提供采集客户端，仍需服务端地址、独立 Token 与设备配置。

Windows 客户端从开始菜单或托盘打开桌面窗口。状态页显示待上传截图和实时活动，截图配置支持定时、智能、去重及 JPG、PNG、WebP，软件设置支持主题、自启动和检查更新。检查更新提供正式版下载入口。最近版本和能力边界见 [PLAN](devlogs/PLAN.md)。

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

前端是独立 Vite 服务（不被 Python 后端托管），需要另开终端。本地 HTTP 开发登录需在后端环境设置 `TIMETRACE_INSECURE_COOKIE=1`；正式 HTTPS 保持默认安全配置，详见 [部署手册](infra/architecture/web-deployment.md#cookie_secure-与-insecure_cookie)。

```bash
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173
```

---

## 双进程模式（多设备 / 远程访问）

适用场景：

- 在多台 Windows 桌面采集，并汇总到 Windows/Linux/macOS server；当前 capture client 只支持 Windows
- 把 server 部署在常开主机，客户端通过私人网络访问（断网时 Outbox 缓冲）
- VLM key 集中放服务端，客户端只采集

### Server 端（Linux / macOS / Windows）

```bash
uv sync
uv run timetrace-server                  # 起服务，默认监听 127.0.0.1:8765
```

首次启动会自动生成 bearer token，写到 `~/.config/timetrace-server/tokens.json`（POSIX 上 chmod 600）。从该文件读取 token，用于客户端初始化；日志只显示凭据文件位置。

服务端管理命令：

```bash
uv run timetrace-server info                     # 看 data_dir / token_file / 监听地址
uv run timetrace-server tokens list              # 列所有 token（值已 mask，只显示 label + 后 8 位）
uv run timetrace-server tokens add Yuki-Laptop   # 给新设备发 token，完整值打印一次
uv run timetrace-server tokens revoke Yuki-Laptop  # 吊销
uv run timetrace-server backfill <start> <end>      # 历史指标级联回填
uv run timetrace-server narrate <start> <end>       # 生成待处理分层叙述；重跑需加 --force
uv run timetrace-server repair-data-quality          # 只读审计确定性数据质量问题
uv run timetrace-server repair-data-quality --apply  # 先在线备份 SQLite，再单事务修复
```

`tokens add/revoke` 后**重启服务端**，新 token 才生效。

### Client 端（采集机器）

```bash
uv sync
uv run timetrace-client init             # 交互式填 server URL + token + 设备名等
uv run timetrace-client                  # 起采集
```

`init` 把配置写到 `%USERPROFILE%/TimeTraceData/client.toml`。每个字段都显示当前值作为默认，按 Enter 接受。每台客户端使用独立 Token、device ID 和 Outbox；不要复制另一台机器的完整数据目录作为初始化方式。服务端设置页可查看设备、编辑名称/描述与停用，停用设备不会同时撤销其 Token。

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

当前私人入口为 **Tailscale Serve → loopback nginx → 同 SHA SPA / Docker API**。GitHub Actions 发布包含后端、前端与部署资产的 GHCR 不可变镜像，再由主机运行 `timetrace-update` 拉取并激活。生产发布需先通过目标 SHA 的 CI，并取得对应授权。

首次安装、网关配置、更新器与兼容门禁、备份和回滚步骤统一在 [部署手册](infra/architecture/web-deployment.md)。最近一次有记录的生产验收见 [2026-10-23 归档](devlogs/infra/archive-202610230447-private-ts-pr24-deployment.md)，当前待验收事项见 [PLAN](devlogs/PLAN.md)。

---

## 开发

```bash
uv sync --locked
python scripts/check.py all                # lint + 单元测试 + 前端 + 双容器端到端
uv run pytest tests/test_storage.py -k name # 单测
python scripts/check.py e2e                # 单独运行双容器验收
```

完整检查需要 Python 3.12+、uv、Node.js 22 和 Docker Linux engine。E2E 启动真实客户端与服务端，注入固定画面和模型响应，覆盖离线恢复、回执丢失、分析、摘要、MCP 与浏览器；证据保存在 `artifacts/`。操作与日志/指标规范见 [工程验收文档](infra/architecture/engineering-workflow.md)。

GitHub Actions 对 `main` 的 push 和 PR 执行同一检查入口，另在 Windows/Linux 验证单元测试并构建 Windows 安装包。服务端和客户端制品发布复用 CI 门禁。最近一次实际执行结果只记录在 [滚动 PLAN](devlogs/PLAN.md)。

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
- [infra/readme.md](infra/readme.md) — 当前架构地图与深度文档索引（仓库内 Markdown Wiki）
- [工程验收文档](infra/architecture/engineering-workflow.md) — 本地/CI 同一入口、双容器 E2E、日志和指标
- [部署手册](infra/architecture/web-deployment.md) — 私网入口、首次安装、发布、健康检查、备份与回滚
- [devlogs/README.md](devlogs/README.md) — 按时间和主题索引开发/排障事实；具体 archive 是历史快照
- [`.env.example`](.env.example) — VLM 启用模板
- [CLAUDE.md](CLAUDE.md) — AI agent 的项目约束、运行命令和不可破坏的不变量

事实冲突时按「真实代码与测试 → `infra/` 活文档 → `devlogs/PLAN.md` → 最新 devlog → 旧 devlog」判断。这样可以保留排障历史，同时避免旧结论重新污染当前实现。

---

## 许可证

MIT
