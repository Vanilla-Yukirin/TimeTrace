# 打包与依赖分发

> 本文档说明 TimeTrace 的打包方式、入口点、可选依赖分组、systemd 部署单元与公网拓扑。
> 面向想读懂当前架构的工程师。部署操作细节见 [devlogs/infra/archive-202605161000-deployment-architecture.md](../../devlogs/infra/archive-202605161000-deployment-architecture.md)。

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
| `timetrace-server` | `timetrace.server.cli:main` | 服务端守护；子命令 `info`、`tokens list/add/revoke`、`backfill`、`narrate`。与 `main.py` 共享 `server/bootstrap.py` |
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

## systemd 部署单元（Linux 小主机）

两个 user 级 service 模板（`systemctl --user` + `loginctl enable-linger`，不 root）：

### `deploy/timetrace-server.service`

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

- **触发**：push `deploy` 自动发布；手动兜底使用 `gh workflow run deploy.yml --ref main -f ref=<branch|tag|sha>`，input 默认 `main`
- **Fork 安全**：`if: github.repository == 'Vanilla-Yukirin/TimeTrace'` + secret 不被 fork 继承（双保险）
- **路径**：后端 job 经 FRP SSH 进 box，让 box 自取 event SHA 对应的 `deploy.sh` 并镜像该 SHA；前端 job 在 runner checkout 同一 SHA 后发布到 xcy
- **`deploy/deploy.sh` 做的事**：
  1. `git fetch origin --prune` + `git reset --hard origin/$BRANCH`（镜像状态，**不是污染**——见 CLAUDE.md「动态部署模型」）
  2. `uv sync`（**plain，不含 embserver torch**）
  3. schema 无独立 migration（`SqliteDatabase` 启动自建）
  4. `systemctl --user daemon-reload` + `restart timetrace-server`（embserver 不在此列）
  5. `curl /healthz`（8765）健康探针，失败则 `exit 1`
- **健康检查**：`deploy.sh` 在 box 内完成 `/healthz` 探针；workflow 当前没有第二次 runner-side healthz

**手工运维边界**：主 server 的代码部署与重启只走 workflow；LM Studio 模型可用 `lms load/unload/ps` 手工管理；可选 `timetrace-embserver` 当前不在主部署流程内，若启用则用它自己的 CLI/systemd 管理。禁止手工 SSH 修改主仓库 git 或重启 `timetrace-server`。

## 公网拓扑

```
浏览器 / MCP 客户端
   │ HTTPS, timetrace.yukirin.me
   ▼
Cloudflare ── 云 VPS nginx 反代 (deploy/nginx-timetrace.yukirin.me.conf)
                  │ proxy_pass 127.0.0.1:18765 (VPS 侧 frp 隧道入口)
                  ▼
              frp 隧道 ──► 家里小主机 :8765 (timetrace-server)
                                         │ 本地调
                                         ▼
                                   embserver :8766（可选；当前生产未接线）
```

- MCP（streamable HTTP）的 nginx location 需长连接友好配置（`proxy_http_version 1.1` + 关 buffering + 长 timeout）
- **Web UI / OpenAPI 鉴权后才开公网**；dev server 按需起 `ssh -L` 隧道访问
- embserver（8766）仅本机回环，不经 nginx 暴露

> 另有 `Dockerfile`（多阶段非 root）+ `docker-compose.yml`（loopback-only bind）随仓提供，作为容器化备选路径；当前生产部署走 systemd + deploy.sh，不走容器。

## 数据 / 配置目录

- 数据：`%USERPROFILE%/TimeTraceData/`（Windows）/ `$HOME/TimeTraceData`（Linux 部署机），**不在仓库内**
- server token：`~/.config/timetrace-server/tokens.json`

## 当前分支与 Linux 部署

`pywin32` 已带 `sys_platform == 'win32'` marker，Linux 可以 plain `uv sync`。长期分支模型为 `main`（唯一开发主干）+ `deploy`（生产指针）；发布使用 `git push origin main:deploy`，历史 `feature/refactor-split` 不再作为默认部署源。部署脚本仍只安装默认依赖，不会拉 `embserver` 的 torch/transformers optional extra。

---

*最后更新：见 git log*
