# 部署架构决策 v1 — 小主机 + FRP + 仅手动触发

**日期：** 2026-05-16
**状态：** 决策定稿；脚本实装与本日志同 PR 落地
**关联：** [client-server-split-kickoff](archive-202605151200-client-server-split-kickoff.md) 的 P3a-5b 完成后立即进入

---

## 起因

P3a-5b 双进程入口已就绪（`timetrace-server` + `timetrace-client`），意味着 server 端有了"可以独立部署"的物理形态。在写 deploy 脚本之前必须先把"部署到哪台机器、走什么网络路径、谁能触发部署"三件事固化下来，避免脚本边写边改。

---

## 设备清单

| 设备 | 角色 | 公网 IP | 备注 |
|---|---|---|---|
| 小主机 | **部署目标** | 无（NAT） | Ubuntu 22.04，开机自动 FRP 到下面两台云 |
| 二类云 (RB4G, 121.x.x.x) | FRP 跳板 | 有 | 2V4G，**带宽仅 2 Mbps（256 KB/s）**；流量单价高、磁盘小 |
| JPVPS (151.x.x.x) | FRP 跳板（备） | 有 | 不限带宽，但**借的不归我**，不能放生产数据 |
| 本机 (Windows) | 开发 + capture client | — | 在家时 LAN 直连小主机 192.168.2.105；不在家时走云端 FRP |

---

## 决策：部署在小主机，不在云

| 维度 | 二类云 (RB4G) | JPVPS | 小主机 |
|---|---|---|---|
| 带宽 | 2 Mbps ❌ | 不限 ✅ | 家宽（上行 ~30-50 Mbps）✅ |
| 磁盘 | 贵 + 小 ❌ | 不归我 ❌ | 充足 ✅ |
| 流量计费 | 出网很贵 ❌ | — | 家宽包月 ✅ |
| 公网 IP | 有 ✅ | 有 ✅ | 无（FRP 解决）⚠️ |
| 所有权 | 我的 ✅ | 借的 ❌ | 我的 ✅ |
| 长时在线 | 是 ✅ | 是 ✅ | 是 ✅ |
| 数据留在物理上属于我的设备 | ✅ | ❌ | ✅ |

**最终：部署到小主机**。云服务器**只做 SSH 跳板**（不存数据、不跑应用）。这不是首选方案而是带宽 + 所有权约束下的唯一可行解。

### 已知 trade-off

- **客户端上传带宽 = 我家上行带宽**：在家时 LAN 直连无影响；外出时本机要把截图通过云端 FRP → 小主机，受云端 2 Mbps 出网带宽限制。OutboxSender 的 `max_kbps` 配置就是为这个场景兜底。
- **server 端 web UI 无法直接公网访问**：见下"安全模型"。

---

## 网络拓扑

```
家里:
  小主机 (192.168.2.105) ──┬── frpc 守护，开机自启
                           │
                           ├── 反向隧道 → RB4G:22XXX
                           └── 反向隧道 → JPVPS:22XXX

公网:
  RB4G  (121.x.x.x) ──── frps ────┐
                                   │── 我从任意网络 ssh tt-rb4g
  JPVPS (151.x.x.x) ──── frps ────┘                   ssh tt-jpvps
                                                      → 落到小主机的 22 端口

家里时:
  本机 ──── LAN ──── 小主机 (192.168.2.105:22)  直连
```

**`~/.ssh/config`（本机）**：

- `tt-rb4g` / `tt-jpvps`：经云端 FRP 落到小主机
- `tt-lan`：在家时直连 192.168.2.105

CI/CD 用 `tt-rb4g`（国内更稳）；JPVPS 是冷备。

### SSH 鉴权状态（待迁移）

当前是**密码登录**。CI/CD 上线前必须切换到公钥：

1. 本机 `ssh-copy-id tt-rb4g` 装本机开发用公钥（一次性）
2. 单独生成一对 CI 专用 ed25519 keypair，公钥放小主机 `authorized_keys`，私钥进 GitHub Secret
3. 两把 key 不复用：开发 key 在本机有口令保护；CI key 无口令但权限受限

---

## 安全模型

### Web UI 永不公网

前端 / OpenAPI / 搜索接口涉及高隐私截图与文本，**不开任何反代到公网**。本机要看时走临时 SSH 本地隧道：

```powershell
ssh -L 5173:localhost:5173 tt-rb4g    # 看前端
ssh -L 8765:localhost:8765 tt-rb4g    # 看 OpenAPI/healthz
```

按需起，用完关，零外暴面。

### Auth 双层

| 层 | 谁需要 | 凭证 |
|---|---|---|
| 浏览器端 | 看前端、管设备、看/重发 token | 用户名 + 密码（高复杂度） |
| Ingest API | client 上传 record/screenshot | `Authorization: Bearer tt_live_<32urlbytes>` |

**device pairing 流程**（与 Tailscale/Syncthing 加设备类似）：

1. server 端 `/v1/admin/devices` 页面（浏览器 + 密码登录）点 "Add device"
2. server 生成新 token（带 device_name label），返回给浏览器一次显示
3. 用户**手动复制** token → 粘到客户端 `client.toml`（或交互 init 流程粘进去）
4. 客户端从此每次请求带这个 token
5. 浏览器 admin 页面可吊销/重发任意设备 token

### Token 安全性 reasoning

- `tt_live_<32urlbytes>` 总熵 ≥ 192 bits，单独泄露**无法暴破**
- 权限模型已分层：上传 token 不能拉历史、不能删数据、不能列其他设备 → 即使泄露损失有限
- 传输层走 FRP TLS 模式（`tls_enable = true`）；后续可在 FRP 后加 Caddy 自动 Let's Encrypt
- token 落在 client 机器的 `client.toml`（明文，但设备本地，等同 SSH key 待遇）

### 服务端 token 文件权限

`~/.config/timetrace-server/tokens.json` 是单点失效的根凭证，必须 `chmod 600`。Linux 上 `ServerAuth.load_or_generate()` 落盘后立即 fchmod；Windows 不强制（NTFS ACL 默认家目录非共享）。

---

## CI/CD 触发策略（分阶段）

| 阶段 | 触发方式 | 说明 |
|---|---|---|
| **初期** | 仅 `workflow_dispatch`（手动） | GH Web 按钮，或本机 `gh workflow run deploy.yml`。完全不自动 |
| 中期 | `workflow_dispatch` + tag push (`v*`) | 仅打 tag 时自动；commit 不触发 |
| 远期 | 上面 + main push（带可合并性检测） | merge 进 main 自动部署，但要先有 health check + auto-rollback |

**初期手动是因为**：

- 反复调试 deploy 脚本期间不希望每次 push 都触发
- 手动按钮能让 review/test 节奏完全由人决定
- GH Actions 单跑 workflow_dispatch 仍可拿真实 secret，调试体验和自动触发等价

升级到 tag/push 触发的时机：deploy.sh 在小主机上稳定跑过 ≥ 5 次手动后再说。

---

## Fork 安全（开源友好）

仓库 fork 出去之后必须**默认不能部署到我的小主机**。三层守卫：

1. **GitHub 行为兜底**：仓库 secrets 不会 fork 给下游 fork；fork 出去的仓库跑 CI 拿不到 SSH 私钥/host
2. **Repo 字符串守卫**：deploy.yml 的 deploy job 加 `if: github.repository == 'Vanilla-Yukirin/TimeTrace' && github.event_name == 'workflow_dispatch'`。fork 仓库 `github.repository` 不匹配，整个 job skip
3. **手动触发限制**：初期不接 push 自动，fork 用户即使想部署也得**主动**点按钮

fork 出去的人想部署到自己机器，需要：

- 改 `if:` 字符串到自己的仓库名
- 改 `deploy/deploy.sh` 顶部的 `: ${TIMETRACE_USER:=Yuki}` 默认值
- 在自己仓库设置 SSH 相关 secrets

deploy.sh 顶部 / deploy.yml header 注释要把这套清晰写出来。

---

## 用户名 / 路径变量化

`deploy/deploy.sh` 顶部用 `: ${VAR:=default}` 模式给所有"环境特定但非 secret"的值留 override 钩子：

```bash
: ${TIMETRACE_USER:=Yuki}              # systemd --user 服务跑在哪个 user
: ${TIMETRACE_HOME:=/home/${TIMETRACE_USER}}
: ${TIMETRACE_REPO_URL:=https://github.com/Vanilla-Yukirin/TimeTrace.git}
: ${TIMETRACE_BRANCH:=main}
: ${TIMETRACE_DATA_DIR:=${TIMETRACE_HOME}/TimeTraceData}
```

理由：username / 路径都是 OS-level metadata（`whoami` 谁都能看），不是 secret，写默认值方便我跑、留 env override 方便 fork 用户跑。**真 secret 走 GitHub Secret + scp 注入**，永远不进 repo。

---

## 已知遗留 / 阻塞项

| 项 | 阻塞什么 | 计划 |
|---|---|---|
| 小主机仍密码 SSH | CI/CD 整个跑不了 | 部署脚本上线前 `ssh-copy-id` + 生成 CI 专用 keypair |
| 没装 Caddy / 公网域名 | FRP 之外无 TLS | 暂用 FRP 自带 `tls_enable`；Caddy 等真上公网域名再说 |
| Web UI 无密码 admin | 浏览器目前裸开 | P3b-3 后随 init flow 落地浏览器密码 |
| FRP 配置文件未进仓库 | 文档不全 | 单独写 `infra/deployment/frp-setup.md` 时归档（不进 git，只放路径示例） |

---

## 下次决策触发条件

本文档定的决策**重构期间不可动摇**，除非以下任一发生：

- 家宽上行突发降到 < 5 Mbps（影响外出回家场景）
- 我换了一台真正归自己的、带宽充足的云服务器（这时可能重新评估）
- 隐私分类管线（P4）做完后，截图体积和频率改变带宽假设

任一条触发后开新 devlog 重新评估。
