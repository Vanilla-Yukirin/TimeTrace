# 公网部署搭建：HK VPS + nginx + Let's Encrypt + frp 隧道 + 公网暴露止血

**日期：** 2026-05-30（跨 05-28 ~ 05-30 多次会话）
**目标：** 把 TimeTrace 从"SSH 隧道才能访问"升级成"公网域名 + HTTPS"。家里小主机经 frp 反代到 HK VPS，nginx 反代 + 自动续签证书。本篇记录基础设施搭建 + 一个真实的数据泄露止血。

---

## 背景

原架构：家里小主机 GTi13-Ultra 跑 timetrace-server（127.0.0.1:8765），本机要访问得 `ssh -L 8765` 隧道。目标改成公网 `timetrace.yukirin.me` + HTTPS，后端流量经 frp 回家。约束：VLM 必须留家里（本地 GPU），公网只代理 HTTP，模型推理不出本地网。

诉求：带宽优先（前端看高清截图，允许转圈但不能一张张慢慢出）。

---

## 操作步骤

### 1. VPS 选型（绕了个弯）

- 用户先去注册 **BandwagonHost（搬瓦工）**，卡在密码重置邮件收不到（国内邮箱无声丢弃 + 可能注册邮箱拼错），放弃。
- 转用**已有的香草云 HK VPS**（`103.117.123.204:22000`，1C2G 5Mbps，¥190/年）。
- `ping` 实测 **37-38ms 稳定、0 丢包**；`tracert` 第 9-12 跳全是 `59.43.x.x` → **确认 CN2 GIA 精品线路**。结论：这台够好，别折腾。
- 5Mbps 带宽分析：thumb 列表 12 张 ~360KB → 0.6s 流畅；12 张全清并发 10MB → 16s 卡。结论：日常够用，全清批量看靠 BlurHash 占位 + 异步加载磨平。**不升级带宽**（升 10Mbps 要 ¥540/9 月，续费贵，不值）。

### 2. 勘查 VPS 现状（已是个反代节点）

`ssh xcy` 巡检发现这台**已经在跑东西**：
- nginx 占 80/443，反代 `yukirin.me` / `yukirin.moe` → `localhost:3000`（用户的 Next.js 个人主页，PM2 守护，**别动**）
- frps 在端口 1997（token `998244353`，TLS 不强制），systemd 启用
- certbot 已装、已给 yukirin.me/.moe 签过证
- Redis + Docker（装了没跑容器）

→ 决策：**和平共存**，给 timetrace 加独立 server block + 子域名，不碰现有。

### 3. frpc 连不上诊断

家里 frpc 配置 `~/.config/frp/xcy.toml` 看着正常，但 frps 日志显示 04-23 后再无连接。根因：**香草云防火墙入方向没放行 1997**（之前能连，某次改防火墙删了）。用户在面板加 `允许 tcp 0.0.0.0/0 1997` 后，家里 frpc 自动重连成功（指数退避无限重试）。

### 4. DNS（Cloudflare，DNS-only）

`yukirin.me` 托管在 Cloudflare。加 A 记录 `timetrace → 103.117.123.204`，**Proxy status 必须灰色云（DNS only）**，不要橙色云。原因（向用户详解）：
- 橙云 = CF 当中间人解密所有流量 → 与 TimeTrace"本地隐私优先"理念冲突（CF 能看到每张截图）
- 橙云免费版对 SSE/长连接有 100s 超时 → /mcp streamable HTTP 会断
- 单人自用没 DDoS 风险，CDN 缓存对私有数据无意义
- 证书签发更简单（直连 VPS，少一层）

验证：`nslookup timetrace.yukirin.me` 解析到 103.117.123.204，ping 直达 38ms（不经 CF）。

### 5. nginx + certbot（分两阶段）

VPS 上 `ssh xcy` 操作：
1. **HTTP-only 阶段**：`/etc/nginx/sites-available/timetrace.yukirin.me` 先只配 80 + ACME challenge webroot，`nginx -t` + reload，外部 `curl http://...` 返 404（证明 server block 生效）
2. **签证书**：`certbot certonly --webroot -w /var/www/timetrace.yukirin.me -d timetrace.yukirin.me` → 成功，有效到 2026-08-25，certbot 自动装续签 timer
3. **HTTPS 阶段**：重写 config 加 443 ssl + `proxy_pass http://127.0.0.1:18765`（frps 本地监听口）+ HTTP 301 跳 HTTPS + `client_max_body_size 50M` + `proxy_buffering off` / `proxy_read_timeout 3600s`（给 /mcp SSE 长连）

### 6. frpc 加 timetrace-api 隧道

家里 `~/.config/frp/xcy.toml` 末尾追加（不动现有 rdp-GTi-tcp 段）：
```toml
[[proxies]]
name = "timetrace-api"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8765
remotePort = 18765
```
`systemctl --user restart frpc@xcy.service`。frps 日志确认注册 `[timetrace-api] tcp proxy listen port [18765]`。

### 7. 端到端验证全通

```
curl http://127.0.0.1:18765/healthz   → 200 (46ms,  本地→frp→家)
curl https://timetrace.yukirin.me/    → 200 (152ms, 公网→nginx→tls→frp→家)
                                       {"status":"ok"}
```
整条链路打通，152ms 一个完整往返。

---

## 遇到的问题与解决

### 问题：公网暴露 = 真实数据泄露（严重）

链路打通后立刻发现：**5 个面向浏览器/AI 的路由（records / search / feedback / mcp / thumbs）全是公开的**（原假设 loopback only）。`curl https://timetrace.yukirin.me/v1/records` 直接拉到真实数据：

- records 含详细 VLM 描述（"Visual Studio Code... uv run timetrace-client init... 设备 Yuki-Win, ThinkBook14+2026"）
- thumb 路径暴露 → `https://timetrace.yukirin.me/thumbs/2026/05/17/...jpg` 可直接下载截图
- 任何扫到域名的爬虫都能拿走

**这不是潜在风险，是正在发生的泄露。**

**止血**（用户在外，授权 ssh 直接做）：注释家里 frpc 的 `[[proxies]] timetrace-api` 段（加 `# Disabled 2026-05-28: public exposure leak` 注释），重启 `frpc@xcy.service`。30 秒生效，公网回 502。**nginx / DNS / 证书 / frps 全保留**，等登录系统做完取消注释即复活。备份 `~/.config/frp/xcy.toml.bak.20260528-034614`。

---

## 知识清单

- **判断 VPS 线路质量**：`tracert <IP>` 看 `59.43.x.x` 段出现次数。3+ 次 = CN2 GIA（顶级），1-2 次 = CN2 GT，全程 `202.97` = 163 普通骨干。国外段（`129.250.x` NTT）回包慢是测量假象，不算真实延迟。
- **Cloudflare 灰云 vs 橙云**：灰云 = 仅 DNS 解析，流量直达源站、端到端加密、支持任意协议、源 IP 暴露；橙云 = CF 当中间人、隐藏源 IP + DDoS 防护 + CDN，但解密明文 + 100s 长连超时 + 100MB 体积限。隐私优先选灰云。
- **certbot 两阶段签发**：先 HTTP-only 配 ACME webroot 跑通 → certbot certonly → 再升级 HTTPS config。比一上来全 HTTPS 排查简单。
- **frp v2 toml**：proxy 声明在 **frpc 端**（客户端），frps 端只管 bindPort/auth。改透哪个口在家里 frpc 改。
- **公网暴露前必须想清 auth**："loopback only"的假设一旦反代到公网就是裸奔。止血最快是切断 frpc 隧道（保留 nginx/证书）。

---

## 待办 / 遗留

- [x] 公网链路打通（nginx + LE + frp + DNS）
- [x] 数据泄露止血（frpc 注释）
- [ ] 登录系统（见 [archive-...-login-system-impl-phase1-6](archive-202605300221-login-system-impl-phase1-6.md)）做完后取消 frpc 注释恢复公网
- [ ] BlurHash 占位优化（缓解 5Mbps 看全清）—— 未来
- [ ] thumb 压缩参数调松（q70→q85，用户觉得现在压太狠）—— 未来
