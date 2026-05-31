# 打包与依赖分发

> 本文档说明 TimeTrace 的打包方式、入口点、可选依赖分组、systemd 部署单元与公网拓扑。
> 面向想读懂当前架构的工程师。部署操作细节见 [deploy/README.md](../../deploy/README.md)。

## 构建后端

- **build-backend**：`hatchling`（`[build-system]`）
- **wheel 打包**：`[tool.hatch.build.targets.wheel].packages = ["src/timetrace"]`（src layout）
- **依赖管理**：uv。运行依赖在 `[project].dependencies`，dev 在 `[dependency-groups].dev`（pytest / pytest-asyncio / pytest-cov / ruff）

## 三层目录

`src/timetrace/` 下四个包：`common / client / server / embserver`。前三个是主体（采集 / API / DB / VLM / worker 等），`embserver` 是可独立部署的本地 embedding 推理服务，带自己的重依赖 extra。详见 [tech-stack.md](../engineering/tech-stack.md) 的「三层重组」。

## 入口点（`[project.scripts]`）

| 命令 | 指向 | 说明 |
|------|------|------|
| `timetrace` | `timetrace.main:main` | 单进程默认（pystray 主线程 + asyncio `TaskGroup` 跑 capture / worker / api / quit_watcher / reclaim 5 任务） |
| `timetrace-client` | `timetrace.client.cli:main` | 客户端守护；子命令 `init`（交互 / `--non-interactive`）、`print-config` |
| `timetrace-server` | `timetrace.server.cli:main` | 服务端守护；子命令 `info`、`tokens list/add/revoke`。与 `main.py` 共享 `server/bootstrap.py`（`build_server_components` + `serve(extra_tasks=...)`），不会再次漂移 |
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

**为什么 embserver 单独成桶**：torch 链体积巨大（GB 级），而部署主体（采集 + API + DB + VLM 转发）完全不需要本地推理——VLM 默认转发给 LM Studio，embedding 经 `/v1/embeddings` 转发给 embserver 进程。把重依赖隔离到 extra，让 plain `uv sync` 轻量、deploy.sh 默认不拉。

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

- **触发**：仅 `workflow_dispatch`（`.github/workflows/deploy.yml`）。GH Web 按钮或 `gh workflow run deploy.yml --ref <ref>`，默认 ref `feature/refactor-split`
- **Fork 安全**：`if: github.repository == 'Vanilla-Yukirin/TimeTrace'` + secret 不被 fork 继承（双保险）
- **路径**：CI runner 经 FRP 隧道 SSH 进家里小主机 → `git fetch && git reset --hard origin/<ref>` → `bash deploy/deploy.sh <ref>`
- **`deploy/deploy.sh` 做的事**：
  1. `git fetch origin --prune` + `git reset --hard origin/$BRANCH`（镜像状态，**不是污染**——见 CLAUDE.md「动态部署模型」）
  2. `uv sync`（**plain，不含 embserver torch**）
  3. schema 无独立 migration（`SqliteDatabase` 启动自建）
  4. `systemctl --user daemon-reload` + `restart timetrace-server`（embserver 不在此列）
  5. `curl /healthz`（8765）健康探针，失败则 `exit 1`
- **CI 收尾**：runner 再经隧道打一次 `/healthz` smoke test

**deploy.sh 不管的唯一例外**：embserver 的模型加载（`lms load/unload/ps`）在部署流程之外，可手动。**禁止手动 ssh 改部署机 git / 重启 systemd**——无 CI 留痕、跳过探针。

## 公网拓扑

```
浏览器 / MCP 客户端
   │ HTTPS, timetrace.yukirin.me
   ▼
Cloudflare ── 云 VPS nginx 反代 (deploy/nginx-timetrace.yukirin.me.conf)
                  │
                  ▼
              frp 隧道 ──► 家里小主机 :8765 (timetrace-server)
                                         │ 本地调
                                         ▼
                                   embserver :8766 (本机回环)
```

- MCP（streamable HTTP）的 nginx location 需长连接友好配置（`proxy_http_version 1.1` + `Connection ""` + 关 buffering）
- **Web UI / OpenAPI 鉴权后才开公网**；dev server 按需起 `ssh -L` 隧道访问
- embserver（8766）仅本机回环，不经 nginx 暴露

> 另有 `Dockerfile`（多阶段非 root）+ `docker-compose.yml`（loopback-only bind）随仓提供，作为容器化备选路径；当前生产部署走 systemd + deploy.sh，不走容器。

## 数据 / 配置目录

- 数据：`%USERPROFILE%/TimeTraceData/`（Windows）/ `$HOME/TimeTraceData`（Linux 部署机），**不在仓库内**
- server token：`~/.config/timetrace-server/tokens.json`

## ⚠️ 扩展 v1 不可直接 Linux 部署

`origin/main` 在重构全部完工前保持 **v1 legacy 不动**：pywin32 无平台 marker，Linux `uv sync` 会失败。当前部署一律走 `feature/refactor-split` 分支（其 pyproject 已给 pywin32 加 `sys_platform == 'win32'` marker）。feature 完工合并后，deploy 默认 ref 才翻回 main。

---

*最后更新：见 git log*
