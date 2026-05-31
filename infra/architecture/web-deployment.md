# 公网部署与安全

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)
> 相关：[登录鉴权系统](auth-system.md) · [API Server](api-server.md)

---

TimeTrace 原本是纯本地工具（`127.0.0.1:8765`，靠 SSH `-L` 隧道按需访问）。引入[登录系统](auth-system.md)后，Web UI 可以在鉴权门后开放公网，让你从任何浏览器看自己的活动时间轴。本文讲这套公网拓扑、它的信任边界、以及出事时怎么止血。

> 数据全程仍在自己的家用小主机上，不上任何云。公网链路只是把"你"接到"你的盒子"，云 VPS 只做 TLS 终结 + 反代，不存任何活动数据。

---

## 拓扑

```
浏览器
  │ HTTPS (timetrace.yukirin.me)
  ▼
Cloudflare (DNS / 可选 CDN，子域 timetrace.yukirin.me)
  │
  ▼
云 VPS：nginx 反代 (TLS 终结 + 登录限速 + 安全 header)
  │ proxy_pass http://127.0.0.1:18765
  ▼
frps (VPS 上的 FRP 服务端)
  ╎ FRP 隧道（家里小主机在 NAT 后主动外连建立）
  ▼
家用小主机 (Ubuntu，NAT 后)：
  ├── frpc → 把 127.0.0.1:8765 暴露到 VPS 的 127.0.0.1:18765
  ├── timetrace-server.service   (API + worker + VLM，loopback 8765)
  └── timetrace-embserver.service (Qwen3-VL embedder，loopback 8766)
```

关键事实：

- **家用主机在 NAT 后，不开任何入站端口**。是它主动外连 VPS 建 FRP 隧道，所以家里路由器不用做端口映射，也没有暴露面
- **VPS 只是 SSH 跳板 + TLS 终结 + 反代**，不存活动数据。它能看到经过的流量（TLS 已在它这里终结），所以它本身要可信——这是这套架构的信任假设
- nginx `proxy_pass` 到 `127.0.0.1:18765`（FRP 隧道在 VPS 侧的落地端口），FRP 再把它接到家里的 `127.0.0.1:8765`
- 配置文件：[`deploy/nginx-timetrace.yukirin.me.conf`](../../deploy/nginx-timetrace.yukirin.me.conf)、[`deploy/timetrace-server.service`](../../deploy/timetrace-server.service)、[`deploy/timetrace-embserver.service`](../../deploy/timetrace-embserver.service)
- 完整部署架构归档：[`devlogs/infra/archive-202605161000-deployment-architecture.md`](../../devlogs/infra/archive-202605161000-deployment-architecture.md)

---

## Web UI 的登录门槛

公网开放的前提是登录系统已实装（见 [auth-system.md](auth-system.md)）：

- 所有业务路由（records / search / feedback / thumbs）都被 `require_principal` 守，公网访客没 cookie 也没 bearer → 401
- 默认 admin/admin **首登强制改密**，且改密闸是后端 403（curl 也绕不过）
- 登录端点双层限速：nginx `limit_req` + server 内存 per-IP 锁定
- `/docs` `/openapi.json` 藏在 `require_session_password_set` 后，不向公网扫描器泄露 API 地图

**部署默认假设公网可达，所以登录系统不是可选项**——`AuthConfig` 永远被构造，`UserStore` 永远 seed admin。

---

## SSL / TLS

nginx vhost（[`deploy/nginx-timetrace.yukirin.me.conf`](../../deploy/nginx-timetrace.yukirin.me.conf)）在 VPS 上终结 TLS：

- 证书：Let's Encrypt（certbot）或 Cloudflare origin cert，`/etc/letsencrypt/live/timetrace.yukirin.me/`
- 协议：TLSv1.2 + TLSv1.3，`ssl_ciphers HIGH:!aNULL:!MD5`
- HTTP(:80) → HTTPS(:443) 永久跳转
- 安全 header：`X-Frame-Options SAMEORIGIN`（防点击劫持）、`X-Content-Type-Options nosniff`、`Referrer-Policy strict-origin-when-cross-origin`
- `client_max_body_size 64m`（multipart ingest 可能较大）
- MCP 的 SSE / streamable-HTTP：`proxy_buffering off` + `proxy_read_timeout 3600s`，否则分块不 flush

前端 SPA 静态资源由 nginx 直接从磁盘 `/var/www/timetrace` 提供，`try_files ... /index.html` 兜底 SPA 路由。后端拥有的路径（`/v1` `/healthz` `/mcp` `/thumbs` `/docs` `/openapi.json`）才 `proxy_pass` 给隧道。

---

## cookie_secure 与 INSECURE_COOKIE

session cookie 默认 `Secure`（只在 HTTPS 下发送）——公网部署正确。但本地 HTTP dev（`http://127.0.0.1:5173`）下 `Secure` 会让浏览器**直接不存这个 cookie**，登录后立刻"未登录"。

开关：`TIMETRACE_INSECURE_COOKIE`（[`common/config.py::AuthConfig.from_env`](../../src/timetrace/common/config.py)）

| 环境 | `TIMETRACE_INSECURE_COOKIE` | `cookie_secure` | CSRF Origin guard |
|------|------------------------------|------------------|-------------------|
| 公网 / HTTPS | 不设（默认） | `True` | **启用** |
| 本地 HTTP dev | `1` / `true` / `yes` | `False` | 跳过（避免 Vite 代理 Host/Origin 不一致误杀） |

注意 `cookie_secure` 还**联动 CSRF Origin guard**：guard 只在 `cookie_secure=True` 时启用（见 [auth-system.md 的 CSRF 段](auth-system.md#csrf-考量)）。所以这一个 env 同时控制 cookie 安全属性和 CSRF 防护两件事——公网默认全开，本地 dev 一起关。

**不要在公网部署设 `TIMETRACE_INSECURE_COOKIE`**，否则 cookie 明文可被中间人偷、且 CSRF 防护一并失效。

---

## X-Real-IP 信任边界

登录限速按源 IP keying，**IP 取得正确与否直接决定限速能不能被绕过**。

- nginx 用 `proxy_set_header X-Real-IP $remote_addr` ——`$remote_addr` 是 nginx 看到的**实际 TCP peer**，覆盖写入，客户端无法伪造
- server 的 `_client_ip()`（[`routes/auth.py`](../../src/timetrace/server/api/routes/auth.py)）信 `X-Real-IP`，回退到直连 ASGI peer（loopback / dev 没 nginx 时）
- **绝不信 `X-Forwarded-For`**：nginx 用 `$proxy_add_x_forwarded_for` 把真实 peer **追加**在客户端传来的 XFF 之后，其最左值完全受攻击者控制。若按 XFF 限速，攻击者每请求换一个 XFF 就拿到全新限速桶，锁定永不触发（暴破绕过）

nginx conf 里 `X-Forwarded-For` 仍被 set（`$proxy_add_x_forwarded_for`，给上游做日志/通用语义），但 **server 端的限速逻辑刻意不读它**——这是有意为之，不是疏漏。

> 信任边界一句话：**家用主机信任 nginx 设的 `X-Real-IP`**，因为流量只可能从 FRP 隧道（= nginx）进来。如果哪天家用主机的 8765 直接暴露（不经 nginx），这个假设就破了——所以 8765 必须保持 loopback-only。

---

## CI/CD 与 fork 安全

- **触发方式**：仅 `workflow_dispatch`（GH Web 按钮 / `gh workflow run deploy.yml --ref <ref>`），不在 push/PR 上自动部署
- **fork 安全双保险**：
  1. `if: github.repository == 'Vanilla-Yukirin/TimeTrace'` —— fork 跑不起来这个 job
  2. GH secret 不被 fork 继承 —— 即便 fork 改了 guard 也拿不到 SSH key
- 部署流程（[`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml)）：checkout → 用 secret 里的 SSH key 经 FRP 隧道连家用主机 → `ssh ... 'bash -s' < deploy/deploy.sh <ref>` → smoke-check `/healthz`
- CI（[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)）：push 到 `main` / `feature/**` 或 PR 触发，`uv sync --extra server` → ruff → pytest。**只 server extra**，不拉 embserver 的 torch/transformers

---

## deploy.sh 镜像语义（重要：别误判分支"乱了"）

[`deploy/deploy.sh`](../../deploy/deploy.sh) 在家用主机上跑 `git reset --hard origin/<ref>`：

- **镜像语义**：本地 git 状态是一次性的、可丢弃的，`origin` 才是真相
- 因为重构期默认部署 ref 是 `feature/refactor-split`，**部署机本地那个叫 `main` 的分支会指向 feature 分支的提交链**——这是设计的镜像状态，不是污染、不是 bug、不需要"修复"
- 判断真实代码状态看 `origin/*`，不看部署机本地分支名
- 部署一律走工作流，**禁止手动 ssh 改部署机 git / 重启 systemd**——那样没 CI 留痕、跳过 healthz 探针 / unit 同步 / 沙箱目录预建。唯一例外是 deploy.sh 不管的 LM Studio 模型加载（`lms load/unload`），本就在流程外
- `uv sync --frozen --no-dev`：尊重锁文件，prod 绝不静默重解析
- schema 无显式迁移步骤：app 首次 touch DB 时幂等建表
- embserver unit 只在已安装时才重启（`systemctl --user cat` 探测），torch/transformers 走 optional extra，`deploy.sh` 的 plain `uv sync` 不拉

### systemd 用户单元

- `systemctl --user`（非 root）+ `loginctl enable-linger`（survive logout / headless）
- 模板：[`deploy/timetrace-server.service`](../../deploy/timetrace-server.service)、[`deploy/timetrace-embserver.service`](../../deploy/timetrace-embserver.service)
- 秘密（VLM creds / `TIMETRACE_*`）走仓库根的 `.env`（chmod 600，**不进 git**），unit 文件用 `EnvironmentFile=-%h/TimeTrace/.env`（`-` 前缀 = 文件缺失不阻止启动）
- 加固：`NoNewPrivileges` / `PrivateTmp` / `ProtectSystem=strict` / `ProtectHome=read-only` + `ReadWritePaths=%h/TimeTraceData %h/TimeTrace`

---

## 数据泄露应急止血

公网链路的"断路器"是 **frpc**：它一停，VPS 那侧的隧道落地端口就接不到家里，nginx `proxy_pass` 502，公网瞬间断到数据。家用主机仍能本地（loopback / SSH 隧道）访问。

止血优先级（从快到彻底）：

1. **最快——掐 frpc**：停掉家用主机的 frpc（或在其配置里注释掉 timetrace 那段 proxy 再 reload）。隧道断 = 公网立即够不到 8765。数据仍在盒子里、本地仍可用
2. **轮换凭证**：怀疑 cookie/token 泄露 → Web UI 改密（顺手踢掉所有其它会话）+ `timetrace-server tokens revoke <label>` 吊销可疑 bearer token（CLI 改完**重启 server** 才生效；Web UI 的 `/v1/admin/tokens` DELETE 即时生效）
3. **VPS 侧兜底**：停 nginx vhost / 停 frps，从云端切断
4. **恢复**：威胁排除后，把 frpc 配置里注释掉的 proxy 段恢复 + reload，公网重新可达

> 设计上 frpc 的那段 proxy 配置就是"应急开关"——平时启用，出事注释掉 reload，恢复时取消注释 reload。比改 nginx / 改 DNS 都快且可逆。

记住数据从未上云，所以"泄露"的最坏情况是公网链路上的访问被滥用，而不是数据落到第三方存储——掐掉链路就回到纯本地状态。

---

## 相关文档

- [登录鉴权系统](auth-system.md) — cookie/bearer 双通道、限速、CSRF 细节
- [API Server](api-server.md) — 路由表与鉴权决策
- [隐私策略](../privacy/strategy.md) — 本地优先的隐私边界
- 部署架构归档：[`devlogs/infra/archive-202605161000-deployment-architecture.md`](../../devlogs/infra/archive-202605161000-deployment-architecture.md)
