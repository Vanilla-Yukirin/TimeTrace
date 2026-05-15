# CI/CD 工作流设计 v1 — workflow_dispatch + FRP SSH

**日期：** 2026-05-16
**状态：** 设计定稿，`.github/workflows/deploy.yml` 实装与本日志同 PR 落地
**关联：** [部署架构决策 v1](archive-202605161000-deployment-architecture.md) 的运行时层面

---

## 目标

让"我在 GitHub 仓库点一下按钮 → 30 秒后小主机跑上新版本"成为常规操作；同时**保证 fork 出去的仓库不会误部署到我的机器**。

CI 已有 ruff + pytest workflow（在 push/PR 上跑），本文档只覆盖**新增的 deploy workflow**。

---

## 工作流总图

```
触发 (workflow_dispatch only)
    │
    ▼
runner: ubuntu-latest
    │
    ├── repo guard: github.repository == 'Vanilla-Yukirin/TimeTrace'?
    │   └── no → skip 整个 job
    │
    ├── 装 SSH key (从 secret 落到 ~/.ssh/id_ed25519, chmod 600)
    │
    ├── 写 known_hosts (从 secret，避免首连 prompt)
    │
    ├── ssh -J <jump> tt-rb4g (经 FRP) →
    │       cd ~/TimeTrace && bash deploy/deploy.sh
    │
    └── 打印 deploy.sh 输出（成功/失败状态码决定 workflow 结果）
```

---

## Secrets 清单

| Secret 名 | 内容 | 谁用 |
|---|---|---|
| `DEPLOY_SSH_KEY` | CI 专用 ed25519 私钥（无口令） | runner 用来 SSH |
| `DEPLOY_SSH_KEY_PUB_FINGERPRINT` | （可选）记一下方便 audit | 文档用 |
| `DEPLOY_KNOWN_HOSTS` | 跳板机 host key 行（防中间人） | runner |
| `DEPLOY_JUMP_HOST` | 跳板机别名/IP（如 `121.x.x.x:22`） | runner |
| `DEPLOY_TARGET_HOST` | 跳板上 FRP 映射出来的小主机端口（如 `127.0.0.1:22XXX`） | runner |
| `DEPLOY_USER` | 小主机用户名（默认 Yuki，secret 化避免硬编码） | deploy.sh |

**关键**：所有 secret 都不会被 GitHub fork 继承，这是仓库级 secret 的天然行为。fork 出去的仓库即使把 deploy.yml 整个抄过去、把 repo guard 也改了，也没有 secret 可用，CI 跑到 SSH step 必失败。**双层保险**。

---

## Repo Guard：fork 安全的核心

deploy job 的第一行：

```yaml
jobs:
  deploy:
    if: github.repository == 'Vanilla-Yukirin/TimeTrace' && github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps: ...
```

两条 AND：

- `github.repository == 'Vanilla-Yukirin/TimeTrace'`：fork 出去的仓库 `github.repository` 是 `OtherUser/TimeTrace`，整个 job skip
- `github.event_name == 'workflow_dispatch'`：哪怕未来加了别的触发器（PR / schedule），也只有手动按钮才生效

skip 的 job 在 GH UI 上显示为绿色（pass，因为 condition false 不算失败），fork 用户的 CI 不会因此变红。

---

## 触发方式

### 方式 1：GitHub 网页

`Actions` tab → 选 `deploy` workflow → `Run workflow` 按钮（默认 main 分支）

### 方式 2：本地 gh CLI

```powershell
# Windows PowerShell（装完 winget install GitHub.cli + gh auth login 后）
gh workflow run deploy.yml
gh run list --workflow=deploy.yml --limit 5    # 看历史
gh run watch <run-id>                          # 实时跟踪
```

### 方式 3：长期再说

`push: tags: ['v*']` —— 等手动跑稳之后再开。这一条目前**不在 deploy.yml 里**，为的是初期完全可控。

---

## deploy.sh 职责（在小主机上跑）

```bash
#!/usr/bin/env bash
set -euo pipefail

# === 用户可覆盖的环境变量 ===
: ${TIMETRACE_USER:=Yuki}
: ${TIMETRACE_HOME:=/home/${TIMETRACE_USER}}
: ${TIMETRACE_REPO_URL:=https://github.com/Vanilla-Yukirin/TimeTrace.git}
: ${TIMETRACE_BRANCH:=main}
: ${TIMETRACE_DATA_DIR:=${TIMETRACE_HOME}/TimeTraceData}

# === 步骤 ===
# 1. cd 到代码目录（首次会 git clone）
# 2. git fetch + reset --hard origin/$BRANCH
# 3. uv sync --extra server
# 4. systemctl --user daemon-reload
# 5. systemctl --user restart timetrace-server.service
# 6. 等 5 秒，curl http://127.0.0.1:8765/healthz 验活
# 7. 打印 git log -1 让人知道部署到哪个 commit
```

**显式不做**：

- 不删数据库（`~/TimeTraceData/db/timetrace.db` 按代码 schema 升级）
- 不重启 OS、不动 systemd unit 文件本身（只 reload + restart 单元）
- 不发通知（脚本失败 GitHub Actions 自然变红，邮件已经在路上）

---

## systemd unit（用户级，非 root）

文件位置：`~/.config/systemd/user/timetrace-server.service`（不进 repo，仓库里只放模板 `deploy/timetrace-server.service`，部署时 `cp` 过去）

```ini
[Unit]
Description=TimeTrace Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/TimeTrace
ExecStart=%h/.local/bin/uv run timetrace-server
Restart=on-failure
RestartSec=5
Environment="TIMETRACE_DATA_DIR=%h/TimeTraceData"
# .env 单独读：
EnvironmentFile=-%h/TimeTrace/.env

[Install]
WantedBy=default.target
```

**为什么 user 级 (`systemctl --user`)** 而不是 system 级：

- 不需要 root，部署脚本不需要 sudo
- 数据目录在用户家目录，权限简单
- 用户登出不退出（`loginctl enable-linger Yuki` 一次性配）
- 升级风险小：换用户、换路径不影响系统服务

---

## 失败 / Rollback 策略（初期手动）

deploy.sh 失败的可能：

| 失败位置 | 处理 |
|---|---|
| `git fetch` 失败 | 网络问题，重试 |
| `uv sync` 失败 | 依赖冲突，看日志，可能要回滚 commit |
| `systemctl restart` 失败 | unit 文件配错，看 `journalctl --user -u timetrace-server -n 50` |
| `curl /healthz` 失败 | server 起不来，看 server 日志 |

**无自动 rollback**：初期手动 rollback 即可：

```powershell
gh workflow run deploy.yml -f ref=<上一个 commit>    # 等 deploy.yml 支持 ref input 时
# 或临时方案：本机 ssh 到小主机，手动 git reset --hard <旧 commit> + systemctl restart
```

未来加自动 rollback 的前提：

- deploy.sh 先打 git stash 或 git tag `pre-deploy-${SHA}`
- healthz 失败自动 `git reset --hard pre-deploy-${SHA}` + restart
- 这是 P7 的事，初期不做

---

## 与现有 ci.yml 的关系

| Workflow | 触发 | 作用 | Fork 友好 |
|---|---|---|---|
| `ci.yml`（已有） | push, PR | ruff check + format check + pytest | ✅ 任何 fork 都能跑 |
| `deploy.yml`（新） | workflow_dispatch | SSH 部署到小主机 | ✅ fork 跑会 skip job |

两者完全独立，互不依赖。fork 用户拿到这个仓库后：

- `ci.yml` 自动跑（验证他们的 fork 没破坏功能）
- `deploy.yml` 即使手动按按钮也只 skip（repo guard 阻止），不会有副作用

---

## 演进路线

| 阶段 | 触发 | 检查 | rollback |
|---|---|---|---|
| **现在 (v1)** | workflow_dispatch | healthz curl | 手动 |
| 半年内 | + tag push (`v*`) | + smoke test 套件 | + git tag pre-deploy |
| 一年内 | + main push (带 mergeability check) | + canary（先部分流量） | + 自动健康监控 |

每升级一阶段写一份新 devlog，记录触发条件 + 失败模式。

---

## 待办（实装阶段）

- [ ] 写 `deploy/deploy.sh`（带详尽注释）
- [ ] 写 `deploy/timetrace-server.service`（systemd 模板）
- [ ] 写 `.github/workflows/deploy.yml`（workflow_dispatch + repo guard）
- [ ] 本机 `ssh-copy-id tt-rb4g` + 生成 CI 专用 ed25519 keypair
- [ ] 在 GH 仓库 settings 配 7 个 secrets
- [ ] 第一次手动跑 deploy.yml + 看 logs + 调
- [ ] 跑稳 5 次后才考虑加 tag 触发
