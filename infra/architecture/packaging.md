# 打包与依赖分发

> 本文说明包结构、CLI 入口、可选依赖、Windows Release 与 server 容器分发。
> 当前部署操作见 [私人部署与运维](web-deployment.md)。历史 systemd 模板不代表当前生产启动方式。

## 构建后端

- **build-backend**：`hatchling`（`[build-system]`）
- **wheel 打包**：`[tool.hatch.build.targets.wheel].packages = ["src/timetrace"]`（src layout）
- **依赖管理**：uv。运行依赖在 `[project].dependencies`，dev 在 `[dependency-groups].dev`（pytest / pytest-asyncio / pytest-cov / ruff）

## 三层目录

`src/timetrace/` 下四个包：`common / client / server / embserver`。前三个是主体（采集 / API / DB / VLM / worker 等），`embserver` 是可独立部署的本地 embedding 推理服务，带自己的重依赖 extra。详见 [tech-stack.md](../engineering/tech-stack.md) 的「三层重组」。

## 入口点（`[project.scripts]`）

| 命令 | 指向 | 说明 |
|------|------|------|
| `timetrace` | `timetrace.main:main` | 单进程默认；pystray 主线程 + asyncio 管理 capture、server 主任务、report scheduler 及受开关控制的 rollup/narrate，不依赖固定任务数 |
| `timetrace-client` | `timetrace.client.cli:main` | 客户端守护；子命令 `init`（交互 / `--non-interactive`）、`print-config` |
| `timetrace-server` | `timetrace.server.cli:main` | 服务端守护；子命令 `info`、`tokens list/add/revoke`、`backfill`、`narrate`、`repair-data-quality`。与 `main.py` 共享 `server/bootstrap.py` |
| `timetrace-embserver` | `timetrace.embserver.cli:main` | embedding 服务；子命令 `serve / info / status / load / unload / ttl / selftest`（控制类走 daemon `/admin/` HTTP，仿 `lms`） |

运行模式（单进程 vs 双进程）的语义见 CLAUDE.md「运行模式」表。

## 可选依赖（`[project.optional-dependencies]`）

注意：当前**运行依赖的并集已全在 `[project].dependencies`** 里（让 plain `uv sync` 后单进程 `timetrace` 直接能跑）。optional-deps 桶是 forward-looking 的适配器换装位，不是"今天的并集"。

| extra | 内容 | 状态 |
|-------|------|------|
| `embserver` | `transformers>=4.57.3` / `qwen-vl-utils>=0.0.14` / `accelerate>=1.0` | ✅ **真桶**（已落地，跑本地 Qwen3-VL embedding；见下方 torch 注意事项） |
| `client-priv` | OCR / 分类器 / 模糊（P4） | 🚧 TBD（条目注释占位） |
| `server-pg` | `asyncpg`（P5 Postgres 适配） | 🚧 TBD（条目注释占位） |
| `server-redis` | `redis`（P5 队列适配） | 🚧 TBD（条目注释占位） |
| `server-s3` | `boto3`（P5 blob 适配） | 🚧 TBD（条目注释占位） |
| `headless` | `psutil`（P6 无 GUI TUI client） | 🚧 TBD（条目注释占位） |
| `all-extras` | 一次装全部适配器 | 空桶，待上面有真条目再聚合 |

**为什么 embserver 单独成桶**：torch 链体积巨大（GB 级），而部署主体只需要 HTTP 客户端。当前生产文本 embedding 走配置的 OpenAI-compatible 端点（LM Studio/nomic）；Qwen3-VL embserver 是未接入主 worker/检索的可选能力储备。把重依赖隔离到 extra，让 plain `uv sync` 与主部署保持轻量。

**torch 不在 extra 里（重要）**：`embserver` extra 只含 `transformers / qwen-vl-utils / accelerate`。`torch` / `torchvision` 须**先**从正确的 PyTorch index 单独装，否则一次走错的 `--extra-index` 会静默拉到 `+cpu` build：

```bash
# 1. 先装 torch（CUDA 机器，按显卡选 index）
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
# 2. 再补 embserver 的纯 Python 依赖
uv sync --extra embserver
# 3.（可选，int8/int4 自量化）pip install bitsandbytes
```

## 宿主机旁路服务与退役 systemd 单元

仓库仍保留两个 user 级 service 模板，但主 server 已切换到 Docker；systemd server unit 只用于应急回滚。可选 embserver 仍可独立使用 systemd：

### `deploy/timetrace-server.service`

- **已退役**：生产运行时由 `timetrace-server` 容器接管；该 unit 保留而不删除
- `ExecStart=… uv run timetrace-server`（plain，不含 torch）
- `Environment=TIMETRACE_DATA_DIR=/home/%u/TimeTraceData`
- `.env` 在仓库根，dotenv 自动加载（VLM / embedding 端点配置）
- `Restart=on-failure`

### `deploy/timetrace-embserver.service`

- `ExecStart=%h/.local/bin/timetrace-embserver`（直接调入口；torch + `--extra embserver` 是**手动预装的前置条件**，不在 unit 里现装——见 service 文件头注释）
- `Environment=` 三行：`TIMETRACE_EMBSERVER_MODEL=%h/TimeTraceData/models/Qwen3-VL-Embedding-2B` / `TIMETRACE_EMBSERVER_DTYPE=bfloat16` / `TIMETRACE_EMBSERVER_TTL=900`
- API key 建议 pin（`TIMETRACE_EMBSERVER_API_KEY=tt_emb_…`，否则每次重启重新生成、控制子命令无法鉴权）
- 需要 CUDA GPU；端口 8766（与主 API 8765 分开）
- `Restart=on-failure` / `RestartSec=3`，但**不随 deploy.sh 自动重启**（重依赖、opt-in，手动 `systemctl --user enable --now timetrace-embserver` 管理）

## 部署流程（CI/CD）

- **构建触发**：push `deploy` 自动构建并发布；`workflow_dispatch` 可为指定 ref 生成镜像。workflow 使用触发事件记录的不可变 `github.sha`
- **单一制品**：多阶段 Dockerfile 同时构建 Python server 与 React/Vite SPA，最终镜像携带 server runtime、`frontend-dist`、Compose 和部署脚本；Node 工具链不进入最终镜像
- **无入站部署依赖**：workflow 只向 GHCR 写入 `<sha>` 镜像，不持有部署机 SSH key，也不经 FRP 连接部署机
- **主机主动激活**：部署机的 Docker 组用户运行 `timetrace-update [<sha>]`，通过出站 HTTPS 解析 `deploy`、拉镜像、提取 release，随后进行后端/SPA 健康检查与原子切换；失败恢复上一套
- **数据边界**：SQLite、截图和 token 目录继续原位 bind mount；GPU、LM Studio、nginx 和 Tailscale 留在宿主机；本项目无 Cloudflare 公网回退

**手工运维边界**：不要在部署机运行源码 `git pull/reset`、直接 `docker compose up` 或手动重启旧 systemd unit。主 server 的唯一激活入口是 `timetrace-update`；LM Studio 与可选 embserver 继续按各自宿主机入口管理。

## Windows 客户端发行

[windows-client-release.yml](../../.github/workflows/windows-client-release.yml) 由 `vX.Y.Z` 标签触发，在 Windows 构建并测试客户端 Setup.exe，发布到 GitHub Release，同时附 SHA-256。标签必须属于 `main`，并与 Python 包版本一致。

安装向导支持当前用户安装、覆盖升级、登录自启动选项和 Windows 设置卸载入口。安装包包含采集客户端和桌面控制台。签名和自动更新器仍待实现。安装、卸载和校验见 [Windows 包说明](../../packaging/windows/README.md)，最近发布版本见 [PLAN](../../devlogs/PLAN.md)。

## 私人网络拓扑

Tailscale Serve → `127.0.0.1:19446` nginx → SPA / `127.0.0.1:8765` API；鉴权和 MCP Host/Origin 检查继续启用。Compose 使用 host network 访问 LM Studio 的 loopback 端点。embserver 的 8766 不经 nginx 暴露，旧公网/8080 链路已退役。首次安装与配置统一看 [部署手册](web-deployment.md)。

## 数据 / 配置目录

- 数据：`%USERPROFILE%/TimeTraceData/`（Windows）/ `$HOME/TimeTraceData`（Linux 部署机），**不在仓库内**
- server token：`~/.config/timetrace-server/tokens.json`

## 当前分支与 Linux 部署

`pywin32` 已带 `sys_platform == 'win32'` marker。长期分支模型为 `main`（唯一开发主干）+ `deploy`（生产指针）；发布制品使用 `git push origin main:deploy`，镜像就绪后在 Linux 部署机执行 `timetrace-update` 激活。生产镜像只安装默认依赖，不会拉 `embserver` 的 torch/transformers optional extra。

---

*最后更新：见 git log*
