# TimeTrace 分支接管、部署链路摸排与 Docker 生产切换完整会话归档

**日期：** 2026-08-23
**目标：** 从分支治理与 TODO 梳理出发，摸清 TimeTrace 真实公网/部署链路，最终完成可重复的 Docker/Compose 生产部署并在 yukirin-server 验收。

---

## 背景

这次会话开始时，TimeTrace 已经长期缺少稳定维护，前后端部署位置与公网入口的记忆发生漂移：前端曾发布到 xcy，后端实际运行在家里的 yukirin-server，Puck、FRP、Cloudflare Tunnel、Nginx 和 GitHub Actions 的职责被混在一起。用户希望先接管 agent/control 分支、整理 TODO，随后把优先级收敛到“先发布上线、先在本地/家里服务器跑起来”。

会话中最终确认的目标架构是：

```text
浏览器
  -> Cloudflare Edge / Tunnel
  -> yukirin-server 127.0.0.1:8080 nginx
       ├─ /                 -> /srv/timetrace/web/current（SPA）
       └─ API/MCP/媒体路径   -> 127.0.0.1:8765（TimeTrace server）

GitHub Actions
  -> GHCR 不可变 SHA 镜像
  -> 云端 FRP 暴露的 SSH 端口
  -> yukirin-server Docker Compose

宿主机保留：GPU、LM Studio :1234、nginx、cloudflared、原数据目录
容器内运行：timetrace-server（API + DB worker + MCP）
```

容器化边界是用户明确确认的：只容器化 TimeTrace server；GPU 不做 passthrough，LM Studio 继续在宿主机运行，只要容器网络能访问 `127.0.0.1:1234` 即可。旧仓库和现有数据先保持不动。

---

## 操作步骤

### 1. 接管 agent/control 分支并解释提交历史

会话首先审查了 agent/control 分支相对 main 的 6/7 个提交，把它们按提交边界解释为同一组“修复 + 进展同步”工作：部署 SHA 固定、部署排队竞态修复、React hooks/响应式修补、文档与进度同步等。

随后复用已有 PR，通过 `gh` 提交并请求 `@codex review`。用户确认 merge 后，本地只删除已经确认合并或退役的分支，保留含义不明的 backup 分支与未跟踪文档。

### 2. 新分支整理 TODO 与两份文档

创建新的工作分支检查 TODO。结论是功能待办体量较大，涉及 classifier、隐私、故障转移、数据生命周期等，需要人为排序和讨论；相比继续功能扩张，更应先把部署与真实运行状态理顺。

两份未跟踪文档经确认后在新分支提交并 push。随后用户把优先级明确改为“发布上线 + 本地部署，先跑起来”。

### 3. 摸清 Puck、xcy、FRP、Cloudflare 与 Nginx 的真实链路

通过仓库、GitHub Actions 与服务器只读检查，逐步纠正了几项旧认知：

- Puck 不在 TimeTrace 当前正式运行链路中。
- xcy 曾是 SPA 发布目标，但公网 Cloudflare Tunnel 已绕过 xcy，直接指向家里 FastAPI `127.0.0.1:8765`。
- 因此公网 `/healthz` 是 200，而公网 `/` 是 FastAPI JSON 404；“前端已发布”与“公网真正访问到前端”是两件事。
- yukirin-server 已经配置 loopback Nginx `127.0.0.1:8080`，能够从 `/srv/timetrace/web/current` 托管 SPA，并把 API/MCP/媒体路径代理到 `8765`。
- GitHub runner 通过云服务器的 FRP 公网端口直接落到 yukirin-server 的 sshd；云服务器只是隧道端点，不运行 TimeTrace。

前端自动发布随后改为同 SHA 不可变 release：上传到 `/srv/timetrace/web/releases/<sha>`，校验后原子切换 `current`，Nginx smoke 失败则恢复旧 symlink。

### 4. 排查旧 GitHub Actions 后端部署失败

修通 GitHub runner 到 FRP SSH 后，旧 workflow 进入服务器但失败于：

```text
fatal: could not read Username for 'https://github.com'
```

原因不是 Puck，也不是 Docker，而是旧模型要求生产机自己的私有仓库 clone 执行 `git fetch`，但该 clone 的 HTTPS origin 没有可用凭据。会话曾在服务器生成一对独立 deploy key 作为候选方案，但未加入 GitHub，也未加入 `authorized_keys`。

### 5. 从“给旧 clone 配 Git 凭据”转向容器镜像部署

用户意识到 yukirin-server 是多功能服务器，希望 TimeTrace 更正规地隔离，决定改用 Docker。最终方案没有把整个服务器或 GPU 栈塞进容器，而是：

- GitHub Actions 构建 `ghcr.io/vanilla-yukirin/timetrace-server:<git-sha>`。
- 运行用户固定为 UID/GID 1000，与宿主机现有数据目录 owner 对齐。
- Linux 使用 `network_mode: host`，所以 Python 默认监听的 `127.0.0.1:8765` 直接成为宿主机 loopback 服务，同时能访问宿主机 LM Studio `127.0.0.1:1234`。
- `/home/vanilla/TimeTraceData` 挂到容器 `/home/timetrace/TimeTraceData`。
- `/home/vanilla/.config/timetrace-server` 挂到容器同语义 token 路径。
- 旧 `.env` 仅首次复制到 `/srv/timetrace/config/timetrace.env`，之后容器部署不依赖旧仓库。

实现文件：

- `deploy/Dockerfile`
- `deploy/docker-compose.yml`
- `deploy/deploy-container.sh`
- `.github/workflows/deploy.yml`
- `.github/workflows/ci.yml`
- `.dockerignore`

镜像采用 uv 多阶段构建与 `--no-editable` 安装，运行阶段非 root、read-only rootfs、`cap_drop: ALL`、`no-new-privileges`，并用 Python stdlib 探测 `/healthz`。

### 6. 实现首次切换与回滚协议

`deploy-container.sh` 的关键顺序是：

1. 校验 Compose 和宿主机数据/token 目录。
2. 首次把旧 `.env` 复制到独立配置目录，权限设为 0600。
3. 在改变运行服务之前先 pull 完整镜像。
4. 首次切换时停止旧 systemd 服务。
5. 服务停止后复制 SQLite 主库、WAL、SHM 到 `pre-docker-<sha>` 快照目录。
6. 启动新容器，等待 HTTP health 与 Docker HEALTHCHECK 同时通过。
7. 成功后原子切换 runtime `current`，disable 旧 unit。
8. 任一步骤失败：删除新容器并重启旧 systemd；后续容器版本失败则恢复上一个 Compose release。

GHCR 登录没有写入服务器全局 `~/.docker/config.json`。Workflow 为每次 run 建立 `/tmp/timetrace-ghcr-<run>-<attempt>` 的独立 `DOCKER_CONFIG`，部署后删除。

### 7. 本地与 CI 验证

本地验证：

```text
uv run pytest --tb=short             -> 542 passed
uv run ruff check src/               -> passed
uv run ruff format --check src/      -> 94 files formatted
npm run lint --prefix frontend       -> passed
npm run build --prefix frontend      -> passed
YAML parse                            -> passed
22 个 workflow shell block bash -n   -> passed
deploy scripts bash -n               -> passed
git diff --check / secret scan       -> passed
```

由于本机 Docker Desktop daemon 未运行，没有把启动 Docker Desktop 当作前置依赖；新增的 Ubuntu CI job 真实 build 镜像、以 hardened 参数启动容器并探测 `/healthz`。

分支 `codex/docker-deployment` 推送后建立 PR #4，并请求 `@codex review`。最终 PR HEAD `a93ad4d`，三项 CI 全绿，merge state 为 `CLEAN`；记录本文时 PR 仍 open、尚未合并。

### 8. 生产部署与独立验收

最终生产 workflow run：

```text
run 32591445065
resolve-ref       success
build-image       success
deploy            success
publish-frontend  success
```

生产运行 SHA 为 `cf80394086def009109ab3f76cf255a713c3d5bc`。Actions 之外又通过 SSH 独立检查：

```text
container status     running
container health     healthy
container user       1000:1000
root filesystem      read-only
network              host
127.0.0.1:8765       {"status":"ok"}
nginx :8080 /        contains id="root"
container -> LMStudio /v1/models  200
legacy systemd       inactive + disabled
runtime current      releases/cf80394...
web current          releases/cf80394...
temporary GHCR dirs  0
```

首次 SQLite 快照保存在：

```text
/home/vanilla/TimeTraceData/db/pre-docker-d7b36a84c3eb662772de57bd62a4fbc8efcbaacf/
```

旧仓库没有改名、reset、pull 或删除。之前生成但未投入使用的 deploy key 经确认不在 `authorized_keys` 后删除；它未承担任何访问能力，不影响现有 SSH。

---

## 遇到的问题与解决

### 问题 1：把前端发布位置误当成公网入口

**现象：** xcy 有前端文件，但 `https://timetrace.yukirin.me/` 返回 FastAPI 404。

**原因：** Cloudflare Tunnel 直连家里的 `8765`，根本不经过 xcy。

**解决：** 分开核验“前端发布目标、后端部署目标、公网实际 origin”，在家里建立 Nginx 统一入口并发布真实 SPA。

### 问题 2：旧 Actions SSH 成功后仍无法部署

**现象：** `fatal: could not read Username for 'https://github.com'`。

**原因：** 旧部署要求服务器 clone 自行 fetch 私有 GitHub 仓库。

**解决：** 不再给多功能服务器长期 Git 拉取凭据；改为 Actions 构建 GHCR 镜像，服务器只拿短期 `GITHUB_TOKEN` pull 镜像。

### 问题 3：工作流首次 dispatch 在解析阶段失败

**原始错误：**

```text
HTTP 422: Invalid Argument - failed to parse workflow:
Unrecognized named-value: 'runner'.
Located at position 1 within expression: runner.temp
```

**原因：** job 级 `env` 不能使用 `runner.temp` context。

**解决：** SSH ControlPath 改成 `/tmp/timetrace-ssh-${run_id}-${run_attempt}-%C`。该失败发生在 SSH 前，生产服务没有被触碰。

### 问题 4：生成 workflow 时 Bash 续行符被 JS template literal 吃掉

**现象：** YAML 中出现 `ssh -MNf + -p ...`、`docker run -d + --name ...`。

**原因：** JavaScript template literal 把行尾反斜杠当作自身续行，patch 下一行的 `+` 进入文件。

**解决：** 用显式转义的普通字符串重打 patch，并以 `rg` + 对每个 `run` block 执行 `bash -n` 作为门禁。

### 问题 5：Windows 无法直接验证 Linux 绝对 env_file

**现象：** 本地 `docker compose config` 把 `/srv/timetrace/config/timetrace.env` 解释成 Windows 相对路径并报文件不存在。

**原因：** Compose CLI 在 Windows host 上按 Windows 文件语义解析，而目标文件只存在于 Linux 生产机。

**解决：** 本地做 YAML/Bash 检查，Linux runner 做真实容器 smoke，生产脚本在目标机做 `compose config --quiet`。

### 问题 6：Windows CI 限速测试偶发失败

**原始断言：**

```text
assert sum(sleeps) >= 2.0
E assert 1.8569999999999822 >= 2.0
```

**原因：** 既有 wall-clock/限速测试抖动，与 Docker 代码无关；相同代码本地及前一轮 CI 均全绿。

**解决：** 只重跑失败 job，随后 542 tests 通过；没有把无关业务测试修改混进部署 PR。

### 问题 7：旧 systemd 正常停止却显示 failed

**现象：** 日志已有 `api.lifespan.exit`，但 unit 显示 `status=143`、`failed`。

**原因：** 旧 uv launcher 收到 SIGTERM 后以 143 退出，systemd 把它记成非成功退出。

**解决：** 新容器健康且旧 unit disabled 后执行 `systemctl --user reset-failed`。最终旧 unit 是 `inactive + disabled`。

### 问题 8：GitHub Actions Node 20 弃用告警

**现象：** runner 提示 checkout、docker actions、ssh-agent action 的 Node 20 runtime 被强制切到 Node 24。

**处理：** 本次所有 job 正常完成，不在首次容器切换中顺手升级整组 action；留作独立维护项。

---

## 知识清单

- `network_mode: host` 在此不是为了对公网暴露端口，而是同时满足“应用继续绑定 loopback”和“容器访问宿主机 LM Studio loopback”。
- Docker 化不等于数据迁移。保持应用的 `Path.home()/TimeTraceData` 语义，在容器 HOME 下挂载宿主机原目录，可以避免改业务代码与批量搬 13GB 数据。
- SQLite 第一次切换应先停唯一 writer，再复制主库/WAL/SHM；只复制主库可能遗漏未 checkpoint 数据。
- 部署凭据应区分构建期、pull 期与长期机器身份。Actions 的短期 `GITHUB_TOKEN` + 临时 `DOCKER_CONFIG` 比在多功能服务器保存 PAT 或私库 deploy key 更小权限、更易清理。
- 健康检查至少分三层：Docker HEALTHCHECK、直接 API health、Nginx 入口 smoke。Actions job 绿不代替生产机独立检查。
- 发布目标、运行目标、公网 origin 必须分别核验；“某台机器上有前端文件”不能证明公网流量经过它。
- 旧源码仓库可以作为首次 cutover 的回滚锚点而无需参与日常部署；确认容器稳定后再决定是否改名或迁档。
- 对多功能服务器，清理动作也要最小化：临时 registry 凭据使用独立目录，未使用 key 先验证不在 authorized_keys 再删除。

---

## 最终状态

- Docker/Compose 生产后端：已完成并在 yukirin-server 运行。
- 数据/token：原位挂载，旧数据未迁移。
- GPU/LM Studio：继续宿主机运行，容器网络实测 200。
- Nginx 与真实 SPA：宿主机 `8080` 已就绪并验证。
- GitHub Actions：GHCR 镜像、容器部署、同 SHA SPA 发布全部可重复执行。
- 旧 systemd：保留回滚能力，当前 inactive/disabled。
- PR #4：CI 全绿、CLEAN，但尚未合并。
- GOAL：完成；记录的执行用量为 488,307 tokens，约 44 分钟。

---

## 待办 / 遗留

- [ ] 合并 PR #4；合并前不要从旧 main 推进 `deploy`，否则旧 workflow 可能重新走源码部署。
- [ ] 将 Cloudflare Tunnel 的 TimeTrace origin 从 `127.0.0.1:8765` 改为 `127.0.0.1:8080`，再验证公网 `/` 返回真实 SPA。目前公网 `/healthz` 200、`/` 仍是 FastAPI 404。
- [ ] Tunnel 稳定后再决定是否删除旧 XCY Secrets；本次未删除。
- [ ] 单独升级产生 Node 20 deprecated warning 的 GitHub Actions 依赖。
- [ ] 以后若要搬数据，应先设计停写、快照、校验与回滚，不要在当前 Docker 首发中顺手移动 13GB 数据树。
