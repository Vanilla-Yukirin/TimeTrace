# MCP 公网接入打通：DNS-rebinding host 修复 + 部署链路排查（frpc 红鲱鱼 / 真因=中继安全组没放 10089）

**日期：** 2026-06-03
**目标：** 让 TimeTrace 的 MCP 接口在公网 `https://timetrace.yukirin.me/mcp/` 上真正可用，并在 Claude Code 里连上它；过程中连带排查并恢复被 firewall 掐断的工作流部署链路。

---

## 背景

用户在前端「API Tokens」设置页看到一句"给 MCP 客户端（Claude Code）与采集端用的 Bearer token"，于是问：**MCP 搭好了吗？怎么用？想测一下。** 最初以为是本地测，澄清后是要**直接测云端** `timetrace.yukirin.me`。

关键约束（本会话内反复生效）：
- 平时**禁手动 ssh 改部署机**，部署一律走 `deploy.yml` 工作流——但本会话用户**明确授权**直接 SSH 上 box 跑命令（工作流路径已坏、演示在即）。
- Bash = Git Bash（D 盘 `/d/...`）；PowerShell 是用户终端实际环境；python 不用 python3。
- 提交不加 Co-Authored-By。
- 演示时间：周三 2026-06-03 上午 10 点。

CLAUDE.md 当时还把 `mcp_layer/tools.py` 标成"Phase 1.5+ stub"——**这条已过期**，真实现早就换地方了（见下）。

---

## 操作步骤

### 1. 核对 MCP 到底搭没搭好（对着真代码，不凭 CLAUDE.md）

读 `server/mcp_layer/` + `api/app.py`，结论：**MCP 是真实现，不是 stub。**

- 真 server 在 [server/mcp_layer/server.py](../../src/timetrace/server/mcp_layer/server.py)：`build_mcp_server(db, vlm_cfg)` 返回 FastMCP 实例，`stateless_http=True / json_response=True / streamable_http_path="/"`，工具实现来自 [server/agent/tools.py](../../src/timetrace/server/agent/tools.py)。
- `api/app.py::create_app` 用 FastAPI lifespan 驱动 session manager，`app.mount("/mcp", mcp_app)`，并在 `auth` 存在时套 `BearerOnlyMiddleware`（ASGI 层 bearer 门，非 FastAPI Depends——因为 mount 的子 app 不走父 app 的依赖链）。
- **6 个工具**：`search_activity` / `get_recent_activity` / `get_app_breakdown` / `get_category_stats` / `apply_label`（唯一写操作，只能打标签）/ `ask_agent`（服务端本地 LLM 单轮问答）。
- `mcp_layer/tools.py` 里那几个 `get_category_stats/search_activity` 旧 stub 是**死代码**，已被 `server.py` 取代。
- 单进程 `uv run timetrace` 也会 `ServerAuth.load_or_generate()` 并把 `auth` 传进 `create_app`，所以 **`/mcp` 即便本地也要 bearer token**。

`skills/timetrace/SKILL.md` 已是现成的 MCP 使用说明（6 工具 + 只打标签约束），且能通过开放路由 `GET /skill` 在线下载。

### 2. 公网探测 + token 性质

```bash
for p in /healthz /mcp/ /skill; do curl -s -o /dev/null -w "$p -> %{http_code}\n" "https://timetrace.yukirin.me$p"; done
# /healthz -> 200   /mcp/ -> 401   /skill -> 200
```

链路活、`/mcp/` 401（bearer 门在）。读 nginx conf 确认公网拓扑：VPS nginx 托管整个 SPA + 反代 `/v1 /thumbs /blob /healthz /docs /openapi.json /skill /mcp/` 经 frp 到 box `127.0.0.1:8765`；`/admin/tokens` 挂在 `/v1` 前缀下（`app.include_router(admin_routes.router, prefix="/v1")`），所以公网站点能铸 token。

**关键事实：token 按服务器隔离** —— `/mcp/` 校验走 box 端 `~/.config/timetrace-server/tokens.json`，本地铸的对云端无效。Web UI 铸的 token **立即生效、不用重启**（admin.py 热更 auth，与 CLI `tokens add` 需重启的区别）。

用户在 box Web UI 铸了 token：`tt_live_h74xnibVRUBXC9Jjh18O0aNMOd3W8l4QdDDcsWZQ-SM`（明文保留，用户私有库、可随时吊销）。

### 3. 第一发请求 → 421，定位 DNS-rebinding 防护

```
POST https://timetrace.yukirin.me/mcp/  (initialize)
HTTP/1.1 421 Misdirected Request
Invalid Host header
```

逐层拆解命运：nginx 通 → frp 通 → `BearerOnlyMiddleware` **通过**（token 有效，否则是 401 不是 421）→ MCP 传输层 `TransportSecurityMiddleware` 拒。

读 mcp 包源码定位根因：[fastmcp/server.py:175-181] —— 当未显式传 `transport_security` 且默认绑定 host 是 `127.0.0.1` 时，FastMCP **静默开启** DNS-rebinding 防护，只放行 `127.0.0.1:* / localhost:* / [::1]:*`。`build_mcp_server` 正中此默认 → 公网域名 `timetrace.yukirin.me` 不在白名单 → 421。本地/隧道（Host=localhost）一直能用、公网这条从没通过。

### 4. 修复 + 本地验证（实事求是先验逻辑）

改 [mcp_layer/server.py](../../src/timetrace/server/mcp_layer/server.py)：显式传 `TransportSecuritySettings`，**保留防护开启**、`allowed_hosts` 加 `timetrace.yukirin.me`（+`:*`）并保留 localhost 三件套；`allowed_origins` 同步（仅浏览器型客户端需要，Claude Code 不发 Origin）。用模块常量 `_MCP_ALLOWED_HOSTS / _MCP_ALLOWED_ORIGINS` 承载。

不依赖运行中的旧进程，直接用真实中间件验证白名单逻辑：

```
timetrace.yukirin.me        -> True   timetrace.yukirin.me:443 -> True
127.0.0.1:8765 / localhost:5173 / [::1]:8765 -> True
evil.com / sub.timetrace.yukirin.me -> False   （无通配泄漏）
```

ruff check/format 全过。提交 `c2d94d8`，push（`3d2c0f6..c2d94d8`）。

### 5. 触发工作流部署 → 连续失败

```
gh workflow run deploy.yml --ref feature/refactor-split
```

两次都 exit 255，6s 内挂在 "Deploy via SSH" 步：

```
Connection closed by 121.43.33.13 port ***
##[error]Process completed with exit code 255.
```

判断：HTTP 服务链路活（healthz 200），但**专供部署的 SSH 链路**（经 121.43.33.13 = 2v4G 中继，FRP 把公网端口反代到 box sshd:22）断了，`deploy.sh` 根本没机会跑。

### 6. 用户授权直连内网 box，手跑 deploy.sh

用户在家，让我直连内网 box（`GTi13-Ultra` = `192.168.2.105`，22 端口 OPEN、密钥已配对），一口气跑通 MCP。

内网直打 `192.168.2.105:8765` 给 `000`（连不上）——SSH 探测发现 **API 绑 `127.0.0.1:8765`**（只听 localhost、靠 frpc 转发，内网直打不通）。其余探测：box 当前部署旧代码 `09649d8`、三条 frpc 隧道（2v4G/JPVPS/xcy）都 active、`uv` 不在非登录 PATH（deploy.sh 自己会修 PATH）。

读 [deploy/deploy.sh](../../deploy/deploy.sh)：幂等，`git fetch` + `git reset --hard origin/<ref>` + `uv sync` + 预建沙箱目录 + systemd 重启 + healthz 探活；脚本头注释**明确认可手动 `ssh host 'bash deploy/deploy.sh'`**。

上 box 前安全检查（避免 reset 回退掉别人的看板 UI 提交）：

```bash
git merge-base --is-ancestor 09649d8 HEAD   # ✅ 09649d8 是 c2d94d8 祖先
git ls-remote origin feature/refactor-split # tip=d9baf54，含 c2d94d8
```

经内网 SSH 跑工作流那条等价命令（FRP 通道换成内网直连）：

```bash
ssh GTi13-Ultra 'set -o pipefail; cd "$HOME/Github/TimeTrace" && git fetch origin feature/refactor-split && git checkout FETCH_HEAD -- deploy/deploy.sh && export TIMETRACE_BRANCH=feature/refactor-split && bash deploy/deploy.sh'
# [deploy] now at d9baf54 ... [deploy] healthz OK ... [deploy] OK. Deployed d9baf54.
```

### 7. 真实 MCP 客户端端到端验证（公网）

用 mcp SDK 的 streamable-HTTP client（和 Claude Code 内部同一套）打公网：

```python
async with streamablehttp_client(URL, headers={"Authorization": f"Bearer {TOKEN}"}) as (r,w,_):
    async with ClientSession(r,w) as s:
        await s.initialize(); await s.list_tools(); await s.call_tool("get_recent_activity", {...})
```

结果（强制 `PYTHONUTF8=1` 避免 GBK 控制台报 UnicodeEncodeError）：

```
[1] initialize OK -> server=timetrace v1.27.1     （之前 421，现在握手成功）
[2] tools/list -> 6 个工具全在
[3] get_recent_activity(24h) -> 真返回 box 数据：QQ/social、VSCode/work，中文 VLM 描述正常
[4] get_app_breakdown(24h,top5) -> VSCode 293min / Edge 180min / Vanish 98min / Terminal 75min / QQ 73min
```

**核心任务达成：公网 MCP 端到端通。**

### 8. 顺手修工作流部署路径——走了弯路

诊断 frpc@2v4G：从 5-23 一直 active，`ss` 显示到 `121.43.33.13:4070` 的控制连接 ESTAB、ssh proxy 本地转发 `127.0.0.1:22`；frpc 日志 `~/.config/frp/logs/2v4G.log` 显示 `login to server success` + `[ssh] start proxy success`——**box 这头 frpc 始终健康**。

我误判成"frpc 跑僵尸"，`systemctl --user restart frpc@2v4G`，重触发 deploy 仍 255，第三次才 success，于是又错误归因成"frpc 重启 + frps proxy 重注册竞态"。

**用户纠正了真因**（见问题3）。

---

## 遇到的问题与解决

### 问题1：公网 MCP 返回 421 Invalid Host header

**现象：** 带有效 token POST `/mcp/` 仍 `421 Misdirected Request / Invalid Host header`。
**原因：** FastMCP 未显式传 `transport_security` 且默认绑 `127.0.0.1` 时静默开启 DNS-rebinding 防护，只放行 localhost 类 Host；经 nginx 反代后 box 收到的 Host 是公网域名 → 被拒。token 其实有效（已穿过 bearer 层）。
**解决：** `build_mcp_server` 显式传 `TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=[timetrace.yukirin.me, ...localhost])`，commit `c2d94d8`，部署后 421→200。

### 问题2：工作流 deploy 连续 exit 255

**现象：** `gh workflow run deploy.yml` 6s 失败，`Connection closed by 121.43.33.13 port ***`。
**原因（最终）：** 见问题3。
**临时解决：** 用户授权后经内网 SSH（`GTi13-Ultra`=192.168.2.105）手跑 `deploy.sh`，把 `c2d94d8`（随 d9baf54）落到 box。注意 box API 绑 localhost，内网直打 `:8765` 不通，验证只能走公网域名或 box 上 localhost。

### 问题3：「frpc 重启修好」是误判——真因是中继安全组没放 10089

**现象：** 重启 frpc@2v4G 后第三次 deploy 成功，我归因成 frpc 重启 + 竞态。
**真因（用户撞防火墙发现）：** **2v4G 中继（121.43.33.13，阿里云）的安全组/防火墙里没放行入站 10089**（frpc ssh proxy 的 remotePort，`~/.config/frp/2v4G.toml`：ssh local22→remote10089）。用户补上这条规则才通的——我那次 deploy "成功"、本机 SSH "两条路都通"，时间上都发生在**用户加防火墙规则之后**，与 frpc 重启无关。frpc 全程健康（日志 `start proxy success` 为证）。
**解决：** 用户在阿里云安全组放行 10089 入站。工作流 deploy 恢复（`conclusion=success`，deploy.sh 正常跑完）。
**教训：** 下次 deploy 卡 `Connection closed by <relay>:<port>`，**先查中继安全组放没放行那个 remotePort**，别先动 box frpc。撞真机数据优先于凭代码/日志自信推演（用户推回来默认他可能对）。

### 问题4：为什么前几天能连、今天突然断（"定时规则"）

**现象：** 用户说前几天一直这样连得好好的，今天突然不行。
**推断（根据当前会话推断，未在阿里云控制台核实）：** 阿里云安全组有**临时安全组规则**功能，加规则可设自动过期时间，到期自动删。10089 当初若按临时规则加（设了几天有效期），到期被删 → 正好对应"定时规则"体感。
**建议：** 重新加务必设**长期/永久**（否则可能演示前再次到期）；可在安全组入方向规则看 10089 有无过期时间，或 ActionTrail 操作审计查它何时被删。

### 问题5：PowerShell 里 `claude mcp add` 粘贴出错

**现象：** 用户把带 `\` 续行的多行命令贴进 PowerShell，结果：第一行在**没有 `--header`** 的情况下就把 timetrace 加进去了（无 token），`--header` 行单独报 `ParserError: Missing expression after unary operator '--'`，再加又提示 `already exists`。
**原因：** `\` 是 bash 续行符，**PowerShell 不认**（PowerShell 续行是反引号 `` ` ``）。
**解决：**
```powershell
claude mcp remove timetrace
claude mcp add --transport http timetrace https://timetrace.yukirin.me/mcp/ --header "Authorization: Bearer tt_live_h74xnibVRUBXC9Jjh18O0aNMOd3W8l4QdDDcsWZQ-SM"
```
第二条**全部写一行、不要任何 `\` 或换行**。验证 `claude mcp list` 应见 `timetrace ... ✓ Connected`。

---

## 知识清单

- **MCP 已实装**：`mcp_layer/server.py`（FastMCP）6 工具挂 `/mcp/`，与 REST API 同进程同端口 8765，streamable-HTTP，bearer-gated；`mcp_layer/tools.py` 旧 stub 是死代码（CLAUDE.md 那条 stub 说明已过期）。
- **FastMCP DNS-rebinding 坑**：默认绑 127.0.0.1 时静默开 host 白名单，只放 localhost → nginx 反代后公网域名 Host 必 421。修法 = 显式 `TransportSecuritySettings` allowlist 公网域名。bearer 才是真边界，这层是 defense-in-depth。
- **token 按服务器隔离**：`/mcp/` 校验 box 端 tokens.json，本地铸的对云端无效；Web UI 铸的立即生效（admin.py 热更，prefix `/v1/admin/tokens`），CLI `tokens add` 需重启。
- **部署链路真因排查顺序**：`Connection closed by <relay>:<port>` → 先查中继安全组/防火墙放没放行该 remotePort，再看 frps，最后才 box frpc（frpc 健康标志 = 日志 `login to server success` + `[ssh] start proxy success`）。阿里云临时安全组规则会到期自动删。
- **LAN 兜底部署**：`ssh GTi13-Ultra(192.168.2.105) 'cd ~/Github/TimeTrace && git fetch ... && bash deploy/deploy.sh'`，deploy.sh 脚本头认可手动跑；box API 绑 127.0.0.1:8765，**内网直打 :8765 不通**（靠 frpc 转发）。
- **deploy.sh 行为**：`git reset --hard origin/<TIMETRACE_BRANCH>`（默认 feature/refactor-split），自修非登录 shell 的 uv PATH，幂等，healthz 探活后才算 OK。reset 前先 `git merge-base --is-ancestor` 确认不回退他人提交。
- **真实 MCP 验证**：mcp SDK `streamablehttp_client` + `ClientSession`（同 Claude Code 内部），`PYTHONUTF8=1` 避开 GBK 控制台 UnicodeEncodeError。
- **PowerShell 续行**：用反引号 `` ` ``，不是 `\`；长命令直接写一行最省事。
- **Claude Code 接 MCP**：`claude mcp add --transport http timetrace <url> --header "Authorization: Bearer <token>"`（默认 local scope，不进 git；`-s user` 全局）；或项目根 `.mcp.json`（会被 git 跟踪，别提交带 token 的它）。URL 末尾 `/` 不能省。

---

## 待办 / 遗留

- [ ] **演示前必做**：确认阿里云 10089 安全组规则设成**长期/永久**，别让它再到期（否则周三演示前工作流部署又会断）。
- [ ] 用户在 Claude Code 里 `remove` + 单行重加 timetrace，`claude mcp list` 确认 `✓ Connected`。
- [ ]（可选）CLAUDE.md 把 `mcp_layer/tools.py = stub` 那条更新成"真实现在 mcp_layer/server.py，6 工具实装"。
- [ ]（可选）把这次的 MCP host 修复补进相关架构文档；nginx `/mcp/` 已有 buffering off + 长超时，无需改。
- [x] MCP host 修复 `c2d94d8` 已部署到 box（d9baf54）+ 公网端到端验证通过。
- [x] 工作流部署路径恢复（用户放行 10089）。
