# 公网部署与安全

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)
> 相关：[登录鉴权系统](auth-system.md) · [API Server](api-server.md)

---

TimeTrace 原本是纯本地工具（`127.0.0.1:8765`，靠 SSH `-L` 隧道按需访问）。引入[登录系统](auth-system.md)后，Web UI 可以在鉴权门后开放公网，让你从任何浏览器看自己的活动时间轴。本文讲这套公网拓扑、它的信任边界、以及出事时怎么止血。

> 数据全程仍在自己的家用小主机上，不上任何云。公网链路只是通过 Cloudflare Tunnel 把浏览器接到本机 loopback nginx，不保存活动数据库或截图。

---

## 拓扑

```
浏览器
  │ HTTPS (timetrace.yukirin.me)
  ▼
Cloudflare Edge（DNS + TLS）
  ╎ Cloudflare Tunnel（家里小主机主动外连建立）
  ▼
家用小主机 (Ubuntu，NAT 后)：
  ├── nginx 127.0.0.1:8080
  │   ├── / → /srv/timetrace/web/current
  │   └── API/MCP/文件路径 → 127.0.0.1:8765
  ├── timetrace-server Docker container（API + worker + VLM）
  └── GPU / LM Studio / cloudflared（宿主机服务）
```

关键事实：

- **家用主机在 NAT 后，不开 Web 入站端口**。cloudflared 主动建立出站 Tunnel，路由器无需端口映射
- **SPA、数据库和截图都留在家用主机**；Cloudflare Edge 负责公网 TLS，本机 nginx 统一分流静态页面与后端路径
- nginx 只监听 `127.0.0.1:8080`，后端由 Docker host network 提供 `127.0.0.1:8765`
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

公网 TLS 由 Cloudflare Edge 终结。本机 nginx vhost（[`deploy/nginx-timetrace.yukirin.me.conf`](../../deploy/nginx-timetrace.yukirin.me.conf)）只监听 `127.0.0.1:8080`，不绑定宿主机 80/443，也不维护第二套证书：

- Cloudflare Tunnel 把公网 HTTPS 请求送到本机 loopback HTTP origin
- nginx 设置 `client_max_body_size 50M`；MCP/SSE/长报告路径关闭 buffering 并使用长 timeout
- 前端从 `/srv/timetrace/web/current` 提供，hash 资产长期缓存，`index.html` 明确 `no-cache`
- `/v1`、`/healthz`、`/mcp`、`/thumbs`、`/blob`、`/skill`、`/docs` 与 `/openapi.json` 等后端路径反代到 `127.0.0.1:8765`

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

> 信任边界一句话：**应用只信任本机 nginx 覆盖写入的 `X-Real-IP`**；nginx 再只信 loopback cloudflared 提供的 `CF-Connecting-IP`。如果 8765 被直接暴露而绕过 nginx，这个假设就会破坏，因此后端不能映射到公网接口。

---

## CI/CD 与 fork 安全

- **触发方式**：push `deploy` 分支自动构建正式镜像；`workflow_dispatch` 可为指定 ref 构建。GitHub 在触发时记录不可变 `github.sha`
- **fork 安全**：正式 job 有 `github.repository` guard；唯一写权限是当前仓库的 `packages: write`，workflow 不持有部署机 SSH 凭据
- **制品**：Docker 多阶段构建 server + SPA，推送 `ghcr.io/.../timetrace-server:<sha>`；Compose 与更新脚本也打入该镜像
- **激活**：owner SSH 登录部署机后执行 `timetrace-update`。部署机主动访问 GitHub/GHCR，不要求 GitHub Runner 能连入家庭网络
- **CI**：main push/PR 都跑后端与前端检查；Docker 临时镜像只在 PR 构建和冒烟，合并到 main 后不重复构建，正式 deploy workflow 再构建可发布镜像

---

## 分支与不可变镜像语义

- 长期分支只有 `main`（开发主干）和 `deploy`（生产制品指针）；已通过 CI 的 main 只以 fast-forward 推进到 deploy
- 部署机不再镜像 Git 仓库。`timetrace-update` 只查询远端 `deploy` SHA，再拉取对应不可变 GHCR tag
- `/srv/timetrace/runtime/current` 与 `/srv/timetrace/web/current` 分别指向同 SHA 的后端发布资产和 SPA；二者通过同一次健康检查后切换
- 直接 `git pull/reset`、`docker compose up` 或重启旧 systemd unit都会绕过回滚，因此禁止；指定 `timetrace-update <sha>` 才是正式回滚入口
- schema 无独立迁移步骤，应用首次访问数据库时幂等建表；数据与 token 不进入镜像

### 退役 server unit 与可选 embserver unit

- `systemctl --user`（非 root）+ `loginctl enable-linger`（survive logout / headless）
- 主 server unit 已退役并保留作应急回滚；生产 server 由 Compose 容器运行
- 模板：[`deploy/timetrace-server.service`](../../deploy/timetrace-server.service)、[`deploy/timetrace-embserver.service`](../../deploy/timetrace-embserver.service)
- 秘密（VLM creds / `TIMETRACE_*`）走仓库根的 `.env`（chmod 600，**不进 git**），unit 文件用 `EnvironmentFile=-%h/Github/TimeTrace/.env`（`-` 前缀 = 文件缺失不阻止启动）
- 加固：`NoNewPrivileges` / `PrivateTmp` / `ProtectSystem=strict` / `ProtectHome=read-only` + `ReadWritePaths=%h/Github/TimeTrace %h/TimeTraceData %h/.cache %h/.local %h/.config/timetrace-server`

---

## 数据泄露应急止血

公网链路的"断路器"是 **TimeTrace 对应的 Cloudflare Tunnel published route / cloudflared 服务**：停用该 route 后，公网无法到达本机 nginx，但家用主机仍能通过 loopback 访问。

止血优先级（从快到彻底）：

1. **最快——停用 TimeTrace published route**：在 Cloudflare Tunnel 中禁用对应 hostname 路由；不要误停宿主机上其它 tunnel/process
2. **轮换凭证**：怀疑 cookie/token 泄露 → Web UI 改密（顺手踢掉所有其它会话）+ `timetrace-server tokens revoke <label>` 吊销可疑 bearer token（CLI 改完**重启 server** 才生效；Web UI 的 `/v1/admin/tokens` DELETE 即时生效）
3. **宿主机兜底**：停用本机 TimeTrace nginx site 或对应 cloudflared user unit；操作前确认不会影响其它服务
4. **恢复**：威胁排除后重新启用 published route，并验证公网 `/healthz`、首页与鉴权边界

> 优先在 Cloudflare 侧只停 TimeTrace hostname route，避免误伤同一主机上的其它 tunnel 或服务。

记住数据从未上云，所以"泄露"的最坏情况是公网链路上的访问被滥用，而不是数据落到第三方存储——掐掉链路就回到纯本地状态。

---

## 相关文档

- [登录鉴权系统](auth-system.md) — cookie/bearer 双通道、限速、CSRF 细节
- [API Server](api-server.md) — 路由表与鉴权决策
- [隐私策略](../privacy/strategy.md) — 本地优先的隐私边界
- 部署架构归档：[`devlogs/infra/archive-202605161000-deployment-architecture.md`](../../devlogs/infra/archive-202605161000-deployment-architecture.md)
