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
云 VPS xcy：nginx（TLS + SPA 静态文件 + API 反代）
  ├── /var/www/timetrace.yukirin.me 直接提供前端
  │
  │ API 路径 proxy_pass http://127.0.0.1:18765
  ▼
frps (VPS 上的 FRP 服务端)
  ╎ FRP 隧道（家里小主机在 NAT 后主动外连建立）
  ▼
家用小主机 (Ubuntu，NAT 后)：
  ├── frpc → 把 127.0.0.1:8765 暴露到 VPS 的 127.0.0.1:18765
  ├── timetrace-server.service   (API + worker + VLM，loopback 8765)
  └── timetrace-embserver.service（可选，当前生产未启用；loopback 8766）
```

关键事实：

- **家用主机在 NAT 后，不开任何入站端口**。是它主动外连 VPS 建 FRP 隧道，所以家里路由器不用做端口映射，也没有暴露面
- **VPS 保存构建后的 SPA 静态文件，但不保存活动数据库或截图**。它同时承担 TLS 终结与反代，能看到经过的流量，因此 VPS 本身要可信——这是这套架构的信任假设
- nginx `proxy_pass` 到 `127.0.0.1:18765`（FRP 隧道在 VPS 侧的落地端口），FRP 再把它接到家里的 `127.0.0.1:8765`
- 配置文件：[`deploy/nginx-timetrace.yukirin.me.conf`](../../deploy/nginx-timetrace.yukirin.me.conf)、[`deploy/timetrace-server.service`](../../deploy/timetrace-server.service)、[`deploy/timetrace-embserver.service`](../../deploy/timetrace-embserver.service)
- 完整部署架构归档：[`devlogs/infra/archive-202605161000-deployment-architecture.md`](../../devlogs/infra/archive-202605161000-deployment-architecture.md)

---

## Web UI 的登录门槛

公网开放的前提是登录系统已实装（见 [auth-system.md](auth-system.md)）：

- 所有业务路由（records / search / feedback / thumbs）都被 `require_principal` 守，公网访客没 cookie 也没 bearer → 401
- 默认 admin/admin **首登强制改密**，且改密闸是后端 403（curl 也绕不过）
- 登录端点限速由 server 内存 per-IP 锁定承担（失败超阈值 → 429）；nginx 侧目前**未**加 `limit_req`（可作为可补的 TODO）
- `/docs` `/openapi.json` 藏在 `require_session_password_set` 后，不向公网扫描器泄露 API 地图

**部署默认假设公网可达，所以登录系统不是可选项**——`AuthConfig` 永远被构造，`UserStore` 永远 seed admin。

---

## SSL / TLS

nginx vhost（[`deploy/nginx-timetrace.yukirin.me.conf`](../../deploy/nginx-timetrace.yukirin.me.conf)）在 VPS 上终结 TLS：

- 证书：Let's Encrypt（certbot）或 Cloudflare origin cert，`/etc/letsencrypt/live/timetrace.yukirin.me/`
- 协议：TLSv1.2 + TLSv1.3，`ssl_ciphers HIGH:!aNULL:!MD5`
- HTTP(:80) → HTTPS(:443) 永久跳转
- 安全 header：**当前实配的 nginx 只对前端静态资源加了 `Cache-Control`**（`public, max-age=31536000, immutable`）；`X-Frame-Options` / `X-Content-Type-Options` / `Referrer-Policy` 等加固 header **尚未配置**，是可补的 TODO（实际访问控制落在应用层登录鉴权，见 [auth-system](auth-system.md)）
- `client_max_body_size 50M`（multipart ingest 可能较大；见 `deploy/nginx-timetrace.yukirin.me.conf`）
- MCP 的 SSE / streamable-HTTP：`proxy_buffering off` + `proxy_read_timeout 3600s`，否则分块不 flush

前端 SPA 静态资源由 nginx 从 `/var/www/timetrace.yukirin.me` 提供，`try_files ... /index.html` 兜底 SPA 路由；hash 资产长期缓存，`index.html` 明确 `no-cache`。后端拥有的路径（`/v1` `/v1/agent`（SSE）`/v1/reports`（长阻塞）`/healthz` `/mcp` `/thumbs` `/blob` `/skill` `/docs` `/openapi.json`）才 `proxy_pass` 给隧道。

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

- **触发方式**：push `deploy` 分支自动发布；`workflow_dispatch` 通过 Actions 的 ref 选择器或 `gh workflow run deploy.yml --ref <branch|tag>` 手动发布。GitHub 在触发时记录不可变 `github.sha`，不会因生产队列等待而漂移
- **fork 安全双保险**：
  1. `if: github.repository == 'Vanilla-Yukirin/TimeTrace'` —— fork 跑不起来这个 job
  2. GH secret 不被 fork 继承 —— 即便 fork 改了 guard 也拿不到 SSH key
- 后端 job **不 checkout、不 pipe 远端脚本**：它经 FRP SSH 到 box，由 box 自己 `git fetch origin <SHA>` → 取该 SHA 的 `deploy.sh` → `git reset --hard <SHA>` → `uv sync` → restart → healthz。
- `publish-frontend` job 与后端并行：runner checkout 同一 ref、`npm ci && npm run build`，再用 xcy 专用低权 `ghdeploy` 用户先 rsync hash 资产、最后替换 `index.html`。两个 job 故障域独立。
- CI（[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)）：`main` push/PR 触发，plain `uv sync` → `ruff check` → `ruff format --check` → `pytest`。`uv sync` 不带 `--extra embserver`，所以 CI 不拉 torch/transformers。

---

## 分支与 deploy.sh 镜像语义

[`deploy/deploy.sh`](../../deploy/deploy.sh) 在家用主机上跑 `git reset --hard origin/<ref>`：

- **镜像语义**：本地 git 状态是一次性的、可丢弃的，`origin` 才是真相
- 长期分支模型只有 `main`（开发主干）和 `deploy`（生产指针）。发布时把已通过 CI 的 main fast-forward 到 deploy：`git push origin main:deploy`。历史 `feature/refactor-split` 不再作为默认部署源
- **部署机本地分支可能指向 deploy ref 的提交链**——这是镜像语义，不是开发分支污染，不要在部署机上“整理分支”
- 判断真实代码状态看 `origin/*`，不看部署机本地分支名
- 部署一律走工作流，**禁止手动 ssh 改部署机 git / 重启 systemd**——那样没 CI 留痕、跳过 healthz 探针 / unit 同步 / 沙箱目录预建。唯一例外是 deploy.sh 不管的 LM Studio 模型加载（`lms load/unload`），本就在流程外
- `uv sync`（plain）：deploy.sh 第 138 行实跑 plain `uv sync`（读 pyproject + uv.lock），不带 `--extra embserver`，所以部署机不拉 torch/transformers
- schema 无显式迁移步骤：app 首次 touch DB 时幂等建表
- `deploy.sh` 只管 `timetrace-server.service`（安装/更新 unit + restart）；embserver 完全在 deploy 流程之外——unit 文件存在但需手动 enable / 手动重启。torch/transformers 走 optional extra，`deploy.sh` 的 plain `uv sync` 不拉

### systemd 用户单元

- `systemctl --user`（非 root）+ `loginctl enable-linger`（survive logout / headless）
- 模板：[`deploy/timetrace-server.service`](../../deploy/timetrace-server.service)、[`deploy/timetrace-embserver.service`](../../deploy/timetrace-embserver.service)
- 秘密（VLM creds / `TIMETRACE_*`）走仓库根的 `.env`（chmod 600，**不进 git**），unit 文件用 `EnvironmentFile=-%h/Github/TimeTrace/.env`（`-` 前缀 = 文件缺失不阻止启动）
- 加固：`NoNewPrivileges` / `PrivateTmp` / `ProtectSystem=strict` / `ProtectHome=read-only` + `ReadWritePaths=%h/Github/TimeTrace %h/TimeTraceData %h/.cache %h/.local %h/.config/timetrace-server`

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
