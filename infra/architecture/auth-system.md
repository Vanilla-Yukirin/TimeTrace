# 登录鉴权系统

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)
> 相关：[API Server](api-server.md) · [公网部署与安全](web-deployment.md)

---

TimeTrace 有**两条并存的鉴权通道**，分别服务于两类调用者：

| 通道 | 调用者 | 凭证 | 持久化位置 | 实现 |
|------|--------|------|------------|------|
| **Cookie session** | 浏览器里的人 | 密码登录换来的 `tt_session` HttpOnly cookie | `auth_users` / `auth_sessions` 两张表（DB） | [`server/users.py::UserStore`](../../src/timetrace/server/users.py) |
| **Bearer token** | 机器（capture client / MCP / 脚本） | `tt_live_<32urlbytes>` API key | `~/.config/timetrace-server/tokens.json` | [`server/auth.py::ServerAuth`](../../src/timetrace/server/auth.py) |

两者互为孪生：`UserStore` 产出 `CookiePrincipal`，`ServerAuth` 产出 `BearerPrincipal`，业务路由的统一依赖 `require_principal` 接受任一种，并把对应的 principal 透传给路由，使审计日志能区分"浏览器里的人"还是"用 API key 的机器"——而无需重新解析 header。

为什么分两套而不是统一成一套？Bearer token 是长期机器凭证，没有"必须改密""会话过期"的概念，丢进文件里 server 启动时一次性读入即可；Cookie session 要支持登录限速、改密后踢掉其它设备、即时 revoke，必须落 DB 且要密码哈希。两套关注点不同，强行合并只会互相迁就。

---

## Cookie session 通道（浏览器）

### Schema（两张新表，不动现有业务表）

登录系统加了 `auth_users` 和 `auth_sessions` 两张表，**不碰**现有业务表。系统是单用户的，所以业务表里没有 `user_id` 分区——一台 server 一个 admin。

```
auth_users
  username           TEXT PRIMARY KEY
  password_hash      TEXT      -- bcrypt cost 12
  password_must_change INTEGER  -- 1 = 首登强制改密

auth_sessions
  id           TEXT PRIMARY KEY   -- session token 本体 = secrets.token_urlsafe(32)，~256 bit 熵
  username     TEXT NOT NULL REFERENCES auth_users(username)
  created_at   INTEGER NOT NULL    -- epoch ms，建会话时刻
  last_seen_at INTEGER NOT NULL    -- 每次 resolve 顺手 touch（非安全门，仅 telemetry）
  expires_at   INTEGER NOT NULL    -- epoch ms
  user_agent   TEXT                -- 审计用
  ip           TEXT                -- 审计用
```

### 密码哈希

直接用 `bcrypt`（不走 passlib，一个依赖不值得套抽象），cost=12。

- `hash_password()` / `verify_password()` 在 [`users.py`](../../src/timetrace/server/users.py)
- 坏哈希（DB 行损坏）在 `verify_password` 里吞掉异常返回 `False`，让路由干净地 401，而不是 500 崩请求
- **bcrypt 72 字节硬上限**：bcrypt 5.x 对超过 72 字节的密码直接 `ValueError`，所以 `validate_new_password` 显式拦在前面，报"too long"而不是把 bcrypt 的内部错误冒泡出去（注意是字节不是字符，约 25 个 emoji 就超）
- 密码复杂度：≥8 字符、含至少一个字母 + 一个数字。**不强制特殊字符**——真正的防线是登录限速 + bcrypt + 30 天 cookie，强逼 `!@#` 只会逼出便利贴密码而买不到多少熵

### admin/admin 默认 + 首启强制改密

首次启动（DB 里没有任何 user）时，`UserStore.ensure_admin_seeded()` 写入一行 `admin` / `hash("admin")` 且 `password_must_change=1`。该方法幂等：只要已有任意 user 就不覆盖。

- 默认账密由 `AuthConfig`（[`common/config.py`](../../src/timetrace/common/config.py)）的 `admin_username` / `admin_initial_password` 决定，可经 `TIMETRACE_ADMIN_USERNAME` / `TIMETRACE_ADMIN_PASSWORD` env 覆盖
- 日志只在**随机生成**初始密码（operator 没设 env、却又是公网部署）时打出明文，因为那是 operator 唯一能学到密码的途径；**字面量 "admin" 默认不打**（无意义），**operator 自己设的密码也不打**（他已经知道）

**强制改密不是前端把戏**：`password_must_change` 一旦置位，带这个会话的请求在 `require_principal` / `require_session_password_set` 里被 **403** 拒（注意是 403 不是 401——它已认证，只是被改密这道闸挡住）。这一点很关键：

> 如果强制改密只是前端的一个 redirect，任何非浏览器客户端（curl / 脚本）都能跳过它，用默认 admin/admin 读全部数据、甚至铸 bearer token——在密码被改之前。所以闸门必须在后端依赖里。

改密走 `POST /v1/auth/change-password`：校验旧密码 → 校验新密码复杂度 → 校验 new≠old → 写新哈希、清 `must_change` → **踢掉除当前会话外的所有会话**（初始 admin/admin 可能被路人瞥见，改密应顺手把其它设备登出）。

### Session 生命周期

- 登录成功 → `secrets.token_urlsafe(32)` 铸一个 session token（即 `auth_sessions.id`）→ 落 `auth_sessions`，`expires_at = now + 30 天` → set HttpOnly cookie
- 每个 user 的并发会话有上限（`max_sessions_per_user=10`）：超出时 `prune_sessions_over_cap` 驱逐最老的，防 30 天会话集合无界增长、让陈旧的被抓会话自然老化
- `resolve_session`：过期则惰性删除返回 `None`；user 行消失（孤儿会话）也删；否则 touch `last_seen_at`
- logout → 删该会话行 + 清 cookie
- **即时 revoke**：因为会话是 DB-backed，删行即失效，不像纯 JWT 那样要等过期

### Cookie 属性

由 [`routes/auth.py::_set_session_cookie`](../../src/timetrace/server/api/routes/auth.py) 设置：

```
key      = tt_session   (AuthConfig.cookie_name)
max_age  = 30 天        (session_ttl_s)
httponly = True         (JS 读不到，防 XSS 偷 cookie)
secure   = cookie_secure (公网 True；本地 HTTP dev 需 TIMETRACE_INSECURE_COOKIE=1 关掉)
samesite = lax          (拦经典跨站 POST)
path     = /
```

`cookie_secure` 的开关逻辑见 [公网部署文档](web-deployment.md#cookie_secure-与-insecure_cookie)。

### 登录限速（防暴破）

`_LoginRateLimiter`（[`users.py`](../../src/timetrace/server/users.py)）：内存里 per-IP 的失败时间戳列表。

- 窗口 15 分钟内失败 ≥5 次 → 锁定 30 分钟（`login_rate_window_s` / `login_rate_threshold` / `login_lockout_s`）
- 锁定窗口从**窗口内最老的那次失败**起算；老的失败一旦滑出窗口，计数掉到阈值下，下次尝试又放行
- 触发锁定时登录返回 **429** + `Retry-After`
- **重启清零**：计数器在内存里，重启即丢——对单用户系统可接受，攻击者熬过重启也没占到便宜
- per-IP keying：单用户够用；要多租户则把 key 换成 `(ip, username)`，免得共享 NAT 下一个租户把另一个锁出去

**这里的 IP 不能信 `X-Forwarded-For`**。nginx 用 `$proxy_add_x_forwarded_for` 把真实 peer **追加**到客户端传来的 XFF 之后，其最左值完全受攻击者控制——攻击者每次请求换一个 XFF 就拿到全新的限速桶，锁定永不触发。所以 `_client_ip()` 信的是 nginx 用 `$remote_addr` **覆盖**（非追加）写入的 `X-Real-IP`，回退到直连 ASGI peer（loopback / dev 没有 nginx 时）。信任边界细节见 [公网部署文档](web-deployment.md#x-real-ip-信任边界)。

### 登录限速的两层防御

注意 server 端的内存限速**不是唯一一层**：nginx vhost 在 `/v1/auth/login` 上还挂了 `limit_req zone=tt_login`（5 r/s + burst 10）。nginx 那层在请求到 Python 之前就削掉洪峰；server 那层即便绕过 nginx（直连 loopback）也拦得住。

---

## Bearer token 通道（机器）

### tokens.json schema 与生命周期

token 存在 `~/.config/timetrace-server/tokens.json`（POSIX 上 chmod 600，Windows 上靠 NTFS 用户目录 ACL，chmod 在 Win 上是 no-op）：

```json
{
  "tokens": [
    {"value": "tt_live_...", "label": "default", "created_at": 1747300000000}
  ]
}
```

- 选 JSON 不选 TOML：stdlib `json` 能读能写，`tomllib` 只读，为一个文件拉 `tomli-w` 不值
- **schema 单一 owner**：所有读写都走 `ServerAuth.read_tokens` / `write_tokens`（public classmethod）。`admin_cmd.py`（CLI）和 admin 路由（Web UI）都复用，不各自实现 JSON 形状，schema 改动只在 `auth.py` 一处涟漪
- **首启自动 mint**：`ServerAuth.load_or_generate()` 在文件不存在时铸一个 `tt_live_<token_urlsafe(32)>`，返回 `was_generated=True`，bootstrap 把它 `logger.info` 出来供用户拷进 `timetrace-client init`
- token 校验是内存集合查找；`find_label()` 反查 label（不是 value）给 `BearerPrincipal`，使审计日志能说"是哪个集成在打 /v1/records"而绝不打印密钥本身

### token CRUD：两条路径

| 路径 | 入口 | 是否热生效 | 适用 |
|------|------|-----------|------|
| **CLI** | `timetrace-server tokens list/add/revoke`（[`admin_cmd.py`](../../src/timetrace/server/admin_cmd.py)） | ❌ 写文件，**需重启 server** | ssh 进盒子的机器管理员；引导期（第一个 token 还没 server 在跑） |
| **Web UI** | `/v1/admin/tokens` GET/POST/DELETE（[`routes/admin.py`](../../src/timetrace/server/api/routes/admin.py)） | ✅ 改运行中的 `ServerAuth` 内存集 + 持久化，**立即生效** | 登录后的 admin 在浏览器里管 token |

两条都通过同一个 `read_tokens`/`write_tokens` 落同一个文件。区别只在内存同步：CLI 写完文件就完事，靠下次重启 `load_or_generate` 把文件读进内存；Web UI 走 `ServerAuth.add_token`/`revoke_token`，**同时**改内存集和文件，所以新 token 立刻能用。

- `add` 拒重复 label（label 是 revoke 的 handle，也是 `.mcp.json` 服务名提示，必须唯一）
- `revoke` 支持按 label / 全值 / 末 8 位匹配（CLI）；Web UI 按 label
- token 全值**只在创建时返回一次**（CLI 打印一次、API 的 `TokenCreated` 返回一次），列举时只给 label + created_at，绝不回吐 value
- CLI 为什么不做成 `/v1/admin/tokens` 而单独留 CLI：引导鸡生蛋（第一个 token 得在还没 token 时创建）；机器管理员就是需要它的人，不值得为它单独设计浏览器 auth

---

## 统一依赖：谁守哪条路由

依赖定义在 [`server/api/deps.py`](../../src/timetrace/server/api/deps.py)，三个：

| 依赖 | 接受 | 用于 | 拒绝时 |
|------|------|------|--------|
| `require_session` | **仅 cookie**（允许 must-change 会话） | 作用于"人"自身的路由：`/v1/auth/me` `/logout` `/change-password`——这些必须在 must-change 状态下仍可达，否则用户没法改密 | 401 |
| `require_session_password_set` | **仅 cookie**，且已改过默认密码 | admin 操作 + `/docs`：still-default 账户不该做的事。must-change 会话给 403（已认证、只是被闸） | 401 / 403 |
| `require_principal` | **cookie 或 bearer** | 产出/消费**数据**的路由：records / search / feedback / thumbs / mcp（实际 mcp 用专门中间件，见下） | 401（must-change cookie 给 403） |

为什么改密路由用 `require_session` 而不是 `require_session_password_set`：用户处于 must-change 时，得能访问改密接口才改得了密码——否则就是死锁。而 admin token 管理、`/docs` 这类用 `require_session_password_set`，把 still-default 账户挡在外面。

`require_principal` 的检查顺序：先 cookie（浏览器常路径，便宜）→ 再 bearer（机器路径）。**关键安全点**：cookie 会话若 user 仍 `password_must_change`，在这里就 **403**，原因同上——堵死非浏览器客户端用默认凭证抢跑的窗口。bearer principal 没有 must-change 概念，直接放行。

### 各路由的鉴权一览

实际装配在 [`server/api/app.py::create_app`](../../src/timetrace/server/api/app.py)：

| 路由 | 守卫 | 备注 |
|------|------|------|
| `GET /healthz` | **无** | 探活，systemd / nginx / CI smoke 要打 |
| `POST /v1/auth/login` | 无（还没 cookie）| 限速保护 |
| `/v1/auth/{me,logout,change-password}` | `require_session` | |
| `/v1/admin/tokens` (GET/POST/DELETE) | `require_session_password_set`（cookie-only）| 管 token 是交互式 admin 操作，持 bearer 的脚本不该对自己做 |
| `/v1/records` `/v1/search/*` `/v1/feedback` 等业务 | `require_principal`（cookie 或 bearer）| 浏览器走 cookie，脚本走 bearer |
| `/thumbs/{path}` | `require_principal` | 见下"thumbs 改造" |
| `/v1/ingest/*` | **专用 bearer**（`make_bearer_dependency`）| 写入面，**绝不**该被人类会话调用，所以不接 cookie |
| `/mcp/*` | **专用 bearer**（`BearerOnlyMiddleware`）| 机器对机器，无 cookie 流 |
| `/docs` `/openapi.json` | `require_session_password_set` | 不向公网扫描器泄露 API 地图；本地登录后仍可看 |

注意 `ingest` 用的是 `make_bearer_dependency` 这个**独立的** bearer 依赖（在 `auth.py`），不是 `require_principal`——ingest 是写入面，永远不该从人类会话进来，所以连 cookie 通道都不给。

### 测试 / legacy 兼容：users=None 即开放

`create_app` 里大量 `if users is not None`：旧测试 fixture 用 `create_app(db)` 不传 auth 装配，此时业务路由不挂任何依赖（开放），匹配 `auth=None → ingest 开放` 的老模式。这让 261 个测试里不关心 auth 的那些不必每个都搭一套登录。

---

## 两个 ASGI 陷阱：mount 不走 Depends

FastAPI 的 `dependencies=[...]` 只包**注册在父 app 上的路由**，不包 `app.mount()` 进来的子 ASGI app。登录系统踩到两处：

### `/thumbs` 改造

原来是 `app.mount("/thumbs", StaticFiles(...))`，`StaticFiles` 是独立 ASGI app，绕过 `Depends` 链，没法 gate。改成自定义 `FileResponse` 路由（[`routes/thumbs.py`](../../src/timetrace/server/api/routes/thumbs.py)）：

- 重新实现 StaticFiles 真正用到的那一小片（GET 单文件 + 正确 Content-Type + `Cache-Control: private`）
- 套 `require_principal`（cookie 或 bearer）
- **path-traversal 防护**：`resolve()` 两端再 `relative_to(base)` 验包含，`../` 逃逸 / 非文件目标 / symlink-to-dir 一律 404
- 性能：每张 thumb 现在过一次 Python + session/token 查找。session 查找是一条带索引的 SQLite SELECT（亚毫秒），bearer 是内存 O(N) N≈1-5。30 张缩略图的时间轴页 <30ms auth 开销，可接受。若成热点可改 nginx `auth_request` 打 `/v1/auth/me`

### `/mcp` 改造

`app.mount("/mcp", mcp_server.streamable_http_app())` 同样绕过 Depends。用纯 ASGI 中间件 `BearerOnlyMiddleware`（[`api/mcp_auth.py`](../../src/timetrace/server/api/mcp_auth.py)）：

- **不用 `BaseHTTPMiddleware`**：它会把响应体物化以桥接 starlette 的 request/response API，**会破坏 SSE / 分块流**——而 MCP 的 `streamable_http_app` 正是基于流式（名字就说了）。纯 ASGI 中间件只看 `scope` 决定 401-还是-放行，不碰内层流
- **必须在 `mount` 之前包裹**：starlette 一旦 mount 就冻结子 app 的中间件栈，`add_middleware` 变 no-op
- 只 gate `http` scope；lifespan / websocket 原样放行（MCP 的 `session_manager.run()` 依赖 lifespan 完整抵达内层 app）
- **bearer-only 不接 cookie**：MCP 客户端是 Claude Code / Desktop，不是浏览器，没有 cookie 流。admin 在 Web UI 铸的 token 是唯一入口

---

## CSRF 考量

`SameSite=Lax` 已拦经典跨**站** POST，但同**站**兄弟子域（如 `evil.yukirin.me`）被浏览器视为 same-site，仍会带 cookie，跨子域伪造 POST 能打穿。

防御在 [`app.py`](../../src/timetrace/server/api/app.py) 的 `csrf_origin_guard` 中间件：**仅在 secure/公网模式**（`cookie_secure=True`）下启用——拒绝任何"带 cookie 的 mutating 请求（POST/PUT/PATCH/DELETE）其 Origin host 与服务 Host 不符"的请求（403）。

- **dev 下整段跳过**：Vite 代理改写 Host 而浏览器 Origin 仍是 `:5173`，启用会误杀（false-403）
- bearer 请求（无 cookie）和登录（还没 cookie）不受影响

---

## 相关文档

- [API Server](api-server.md) — 完整路由表、请求/响应形态
- [公网部署与安全](web-deployment.md) — nginx 反代、cookie_secure、X-Real-IP 信任边界、应急止血
- [隐私策略](../privacy/strategy.md) — 登录后隐私边界的转移
- 设计归档：[`devlogs/infra/archive-202605280400-login-system-design.md`](../../devlogs/infra/archive-202605280400-login-system-design.md)
