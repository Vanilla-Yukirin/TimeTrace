# 部署设施演进 + 首次真部署发现的 4 个 bug + infra plan B

**日期：** 2026-05-18
**目标：** 把 2026-05-17 整晚的部署 review、deploy.yml 简化、infra 加 deprecation 标注、真实部署后抓出的 bug 修复，一次性归档。

---

## 背景

P3c 部署设施第一稿（ff707c1 之前）落地后，连着经过两轮 review + 一次真实 dress rehearsal，前后修了 7 个 commit。这里按"演进路径 → 真部署 → bug 收尾"组织，方便后人理解为什么 deploy.yml 长成现在这个样子。

---

## 演进路径（commit 时序）

| Commit | 主题 | 性质 |
|---|---|---|
| `ff707c1` | runner 不再 checkout，target 自 curl deploy.sh | 架构精简 |
| `4b3dbbf` | 路径迁移到 `~/Github/TimeTrace` | 与本地约定对齐 |
| `90e7036` | infra plan B：4 个架构页加 H2 deprecation 警告 + readme 阶段速览 | 文档一致性 |
| `f666619` | review 跟进：privacy 状态澄清 / systemd override 列全 / v1 迁移钩子 / 残留路径 | review-driven |
| `d41de7c` | 首次真实部署发现的 3 个 bug | production-driven |
| `99e8ed1` | TIMETRACE_BRANCH 默认改 `feature/refactor-split` + chmod 700 收敛 | dress rehearsal-driven |

---

## 关键设计决定

### 1. Runner 不 checkout，target 自 curl deploy.sh

第一版 deploy.yml 用 `actions/checkout` + `ssh ... 'bash -s' < deploy/deploy.sh`。改为 ssh 进 target → 远端 `curl raw.githubusercontent.com/.../deploy.sh | bash`。

**好处**：
- runner 真正"零源文件"，更符合最小职责
- 本机 `ssh GTi13-Ultra-2v4G 'curl ... | bash'` 一字不差复现 CI 行为
- 大 payload（git clone / uv sync）始终走 target 自己的带宽，不过云中转

**风险**：deploy.sh 必须存在于给定 ref 的 GitHub raw。pin 到 SHA 等于 pin 部署版本。

### 2. infra Plan B：deprecation 标注而非整页重写

P3a-P3c 完成后 `infra/architecture/*.md` 描述与现状偏离严重。两种选项：

- A. 整页重写 → 1-2 天工作量，且 P4/P5 主体落地还要再改
- B. 顶部加 H2 deprecation 警告 + 链到当前事实 → 30 分钟

选 B。约定：

```markdown
## **⚠️ 本页描述 v1 单进程架构，与 P3a-5b 之后的现状不一致**

主要变更：
- ...

**当前事实**：
- 代码：...
- 设计：...

整页重写计划在 P4 OCR 管线落地后进行。
```

规范要点：
- H2 + 加粗 + emoji 紧贴（`## **⚠️ ...**`）—— `grep '^## \*\*⚠️' infra/ devlogs/` 可枚举所有 deprecation 锚点
- 已写入 `CLAUDE.md` 项目约定段强制后续遵守
- 同时立约定："devlog 写完即不改"（archive 是历史快照，纠正/补充另写新 archive）

### 3. systemd unit 三处路径硬编码 + fork override 文档

`WorkingDirectory` / `EnvironmentFile` / `ReadWritePaths` 三处都硬编码 `%h/Github/TimeTrace`，systemd **不读** deploy.sh 的 `TIMETRACE_REPO_DIR` env。fork 用户迁路径必须 d/override.conf 同时覆盖三处，漏任一都失败。

unit 顶部新加独立段说明，避免读者误以为 override 一处就够。

### 4. v1 → v2 路径迁移钩子（幂等）

老版本部署用 `~/TimeTrace`，新版本 `~/Github/TimeTrace`。deploy.sh Step 0：

```bash
if [[ -d "${LEGACY_REPO_DIR}/.git" && ! -d "${TIMETRACE_REPO_DIR}/.git" ]]; then
    mv "${LEGACY_REPO_DIR}" "${TIMETRACE_REPO_DIR}"
fi
```

旧 `.git` / `.env` / `.venv` 全部跟着搬。`TimeTraceData/` 与 repo 解耦不动。没旧路径或已迁过 → 静默 no-op。

---

## 真实部署发现的 4 个 bug

小主机端（vanilla@gti13-ultra, Ubuntu 26.04）的 AI 帮跑 dress rehearsal，抓出 4 个真问题。

### Bug 1：`TIMETRACE_USER` 默认写死 "Yuki"

```bash
# 错
: "${TIMETRACE_USER:=Yuki}"
: "${TIMETRACE_HOME:=/home/${TIMETRACE_USER}}"

# 修
: "${TIMETRACE_USER:=$(id -un)}"
: "${TIMETRACE_HOME:=${HOME}}"
```

非 Yuki 用户首次部署在 `mkdir /home/Yuki/...` 必挂 EACCES。`id -un` 比 `$USER` 更可靠（cron/sudo 等环境 `$USER` 可能未设）。

### Bug 2：systemd 226/NAMESPACE — ReadWritePaths 引用未创建目录

systemd 在 mount namespace 准备阶段（**早于** ExecStart）就要求所有 `ReadWritePaths` 路径存在。首次部署：
- `~/TimeTraceData/` —— server 启动时才 lazy 创建
- `~/.config/timetrace-server/` —— 首启 token 自动 mint 时才创建

→ namespace 准备失败 → unit start fail。

修复（deploy.sh Step 2.5）：

```bash
install -d -m 700 \
    "${TIMETRACE_DATA_DIR}" \
    "${TIMETRACE_HOME}/.config/timetrace-server"
chmod 700 \
    "${TIMETRACE_DATA_DIR}" \
    "${TIMETRACE_HOME}/.config/timetrace-server"
```

`install -d -m 700` 创建时给 700 perm，但对**已存在**目录不改 mode。所以补一行显式 `chmod 700` 收敛（review-L1）。

### Bug 3：`StartLimitIntervalSec` / `StartLimitBurst` 在错误的 section

systemd 230+ 把这俩 key 从 `[Service]` 移到 `[Unit]`。Ubuntu 26.04 上的新 systemd 直接报 `Unknown key, ignoring` —— **重启限流没生效**。

修复：移到 `[Unit]` 段。journal 里 "Unknown key" warning 消失。

### Bug 4：`TIMETRACE_BRANCH=main` 默认在 Linux 上必挂

零环境变量 dress rehearsal 命中：

```
error: Distribution `pywin32==311` can't be installed
       because it doesn't have a source distribution or wheel for the current platform
```

`feature/refactor-split` 在 `44d61b5` 给 pywin32/mss/pynput/pystray 加了 `; sys_platform == 'win32'` 标记，但 **main 仍是重构前的 v1 状态**，pywin32 是无 marker 硬依赖。deploy.sh 默认 `TIMETRACE_BRANCH=main` → 任何零 env 的 Linux 首次部署必挂。

修复方案选择：

| 选项 | 评价 |
|---|---|
| Cherry-pick 平台 marker 到 main | 违反"main 在合并前不动"原则 |
| 改 deploy.sh / deploy.yml 默认到 `feature/refactor-split` | ✅ 1 行修复，符合 v1 → v2 过渡期事实 |
| 等 feature merge 进 main | 长期方案 |

选方案 2 + 注释清楚"feature 合 main 后改回 main"。

---

## Production verification

修完 4 个 bug 后第二次 dress rehearsal（**零环境变量**）跑通：

```
[deploy] resolved knobs: TIMETRACE_USER = vanilla
[deploy] cloning fresh...
[deploy] uv sync...  (cache hit 后秒过)
[deploy] systemd unit changed; copying...
[deploy] restarting timetrace-server.service...
[deploy] healthz OK at http://127.0.0.1:8765/healthz
[deploy] OK. Deployed d41de7c.
```

`systemctl --user status timetrace-server`: `Active: active (running)`，journal 无 "Unknown key" warning。

Idempotency 也顺带证了：第二次跑 deploy.sh，前次 uv sync 走 cache 几秒过；v1 migration / install -d / unit 更新都正确 no-op。

---

## 知识清单

- **deploy.sh 用 `$(id -un)` 而非 `${USER}` / `${USER:-...}`**：cron/sudo/non-login shell 下 `$USER` 可能未设；`id -un` 是 syscall，永远准
- **systemd ReadWritePaths 是 mount namespace 级要求**：必须在 ExecStart 之前的 sandbox 准备阶段存在；server 端 lazy create 不顶用
- **`install -d -m 700` ≠ `chmod 700`**：前者只在创建时设 mode，后者强制收敛已存在目录
- **systemd 230+ StartLimit* 必须在 [Unit]**：放错段位是 silent ignore，不是 hard error
- **`curl raw.githubusercontent.com | bash` 部署模式的副作用**：deploy.sh 自动跟 ref 走，runner 真正零状态。本机 ssh 命令完全可复现
- **deprecation 警告统一格式 `## **⚠️ ...**`**：H2 + 加粗紧贴 emoji，`grep '^## \*\*⚠️'` 可枚举锚点；CLAUDE.md 写成项目约定

---

## 相关 commit

| Hash | 作用 |
|---|---|
| `ff707c1` | runner 不 checkout + target 自 curl deploy.sh + 兼容 tag/SHA |
| `4b3dbbf` | 路径迁移 ~/TimeTrace → ~/Github/TimeTrace |
| `90e7036` | infra plan B (deprecation 警告 + 阶段速览) |
| `f666619` | review 跟进 (privacy / systemd override / v1 迁移钩子 / 残留路径) |
| `d41de7c` | 3 个 prod bug 修复 (TIMETRACE_USER + install -d + StartLimit 段位) |
| `99e8ed1` | TIMETRACE_BRANCH 默认改 feature/refactor-split + chmod 700 收敛 |
