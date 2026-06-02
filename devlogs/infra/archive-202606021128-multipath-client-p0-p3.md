# 多路径客户端连接 P0–P3：endpoint 失效转移 + 原生 SSH 隧道 + 并发上传 + 慢链路抗抖

**日期：** 2026-06-02
**目标：** 让 `timetrace-client` 从"单 `server.url`"升级为**多路径**：一个有序 endpoint 列表 + 健康探测 failover（LAN 直连 / SSH 中转 / 公网域名），原生托管 SSH 隧道，并发上传隐藏高延迟 RTT；随后按真实香港链路实测做了 failover 抗抖加固。

---

## 背景

同一台采集机在不同时刻最优路径差 ~100×（实测：LAN 直连 ~21 条/s vs 香港公网 ~300KB/s，见同期"上传诊断"归档）。用户要求客户端支持多路径自动 failover，按优先级单活（不做负载均衡），顺序在配置里手动调、托盘只开关。分三件事：P1 endpoint failover、P2 原生 SSH 隧道、P3 并发上传。用户明确"千万别停，连夜推完 P0→P3"。

---

## 操作步骤

### P0：设计文档（commit `c32a7a4`）

`infra/PLAN-MULTIPATH-CLIENT.md`：动机、**安全分析（SSH 套裸 HTTP ≈ HTTPS**：拿机密性+完整性+服务器认证，后端免证书；公网段永不裸 HTTP）、**不变量（所有 endpoint 指向同一后端，ingest 幂等保证换路重发不重不丢）**、P1/P2/P3 设计、**P3 乱序+单游标 ack 危险分析**、配置 schema、分期。

### P1：endpoint 列表 + 健康探测 failover + 托盘开关（commit `b241609`）

- **config**（`client/core/config.py`）：新增 `[[server.endpoints]]` 有序数组（`EndpointSection`：name/url/enabled + P2 预留 ssh 字段）；缺省时旧单 `url` 合成单 endpoint（**向后兼容**）；render/print/load 全通。`ServerSection.all_endpoints()` / `enabled_endpoints()`。
- **EndpointSelector**（新 `client/core/endpoints.py`）：按优先级探测 `/healthz`，选第一个健康+启用的；`run()` 周期重探可升级回更高优先级（回家自动切回 LAN）；`health` 快照给托盘。
- **HttpBackend**：新增 `base_url_provider`——每请求按当前活跃 endpoint 构造绝对 URL；无健康 endpoint 抛 `BackendError` 让 outbox 保留条目。
- **OutboxSender**：`on_send_failure` 钩子，发送失败立即触发重选（快速 failover）。
- **cli 接线**：materialize endpoint 列表（托盘与 selector 共享同一批 `EndpointSection` 对象）→ selector 后台任务 + 失败钩子。
- **托盘**：新增"连接"子菜单，每 endpoint 可勾选开/关（持久化 client.toml）+ 显示 ●活跃/○健康/✕不可达；顺序只在配置改。
- 测试 `test_endpoints.py`：config 解析/round-trip(含 ssh) + selector 选路/降级/升级/跳过禁用 + backend provider。439 passed。

### P2：type=ssh 原生托管 SSH 隧道（commit `66ba77c`）

- `SshTunnel` / `SshTunnelManager`（新 `client/core/ssh_tunnel.py`）：为每个 enabled 的 ssh endpoint 起 `ssh -N -L <local>:<remote_host>:<remote_port> <host>`，复用系统 ssh（`~/.ssh/config` + agent + known_hosts，零密钥代码）。`BatchMode=yes` 不卡密码、`ExitOnForwardFailure=yes` 转发失败快退、ServerAlive 保活。
- **关键修正**：设计初稿想"被选中才起隧道（懒启动）"——实现时发现**不可行**（selector 要靠 `/healthz` 探测才选得上，探测又需隧道先通，鸡生蛋）→ 改为**启动时为所有 enabled+ssh endpoint up-front 拉起**；退出即重启+指数退避；客户端关闭 terminate（超时升级 kill）。
- 测试：命令构造（端口/identity/必须本地端口）+ fake 进程的重启/停止/manager 过滤。446 passed。

### P3：滑动窗口并发上传（commit `8247fcc`）

朴素"多 worker 各自抢 outbox"会坏数据，两个硬约束：① 打破 record→screenshot→close 顺序（screenshot 先到→服务端用无元数据的 payload 建记录→record 到了 was_new=False 不回填→**元数据丢**）；② outbox 是**单游标 ack、严格 FIFO**，乱序完成无法乱序 ack。

选定**滑动窗口 sender**（gated by `[upload] concurrency`，默认 1 = 原串行不变）：
- 每轮取至多 `concurrency` 条 FIFO 窗口、至多 N 条在途；
- **按 record_id 屏障**：同 record 后续条目等前一条完成再发（保序，消除危险①）；
- **按序 ack**：只在"它及之前全部完成"时推进单游标（连续前缀推进，化解危险②）；stop 感知，未成功留 pending（至少一次）；
- 服务端**零改动**。
- 测试：全排空+按序ack / 同record保序 / 跨record并发重叠 / 并发丢超大图 / stop留pending。451 passed。

### 后续：慢香港链路实测后的 failover 抗抖

部署多路径后，用户离家切回公网（localhost + public 双 endpoint），实测香港路 healthz 探测频繁抖动：

- **commit `c4b5fce`**：① 探测超时 3s→8s（HK 正常 ~1s，慢窗口不被误杀）；② **EndpointSelector 粘滞**——一轮全探测失败时不把 current 置 None（探测只是提示，发送自身的重试才是存活判据），消除 current→None→"no healthy endpoint" 的误失败；③ **托盘菜单定时 update_menu()**（Windows pystray 文字标签构建时定死不刷新，连接子菜单一直显示 `?`）。
- **commit `2508b55`**：周期探测 `select(upgrade_only=True)` **跳过当前 endpoint**——只探"比当前更高优先级"的（判断是否升级回去），当前 endpoint 的存活由"发送成功与否"证明，不再浪费稀缺上行去 healthz 它，也消除 `probe_miss_keeping_current` 每 30s 刷屏；发送失败 `on_send_failure` 仍走全量探测找任一可用路。455 passed。

实测重启后日志：`endpoint.selected name=public` 一次 → 安静；偶发真实网络抖（`Server disconnected`）在 public 上重试 → `recovered`，数据不丢；托盘正确显示 `● public` / `✕ localhost`。

---

## 遇到的问题与解决

### 问题1：P3 并发 worker 死锁（`await Event` 漏 `.wait()`）

**现象：** 写完并发 drain 跑 `test_outbox_sender` **卡住**（后台 pytest 不返回）。
**根因：** worker 屏障写成 `await done[pre]`——`done[pre]` 是 `asyncio.Event`，不可直接 await；同 record 的后续 worker 崩，done 永不 set，ack 循环 `await done[e]` 死等。
**解决：** 改 `await done[pre].wait()`。`pkill` 掉卡住的后台 pytest，修后 16 passed。**教训：asyncio.Event 必须 `.wait()`；并发代码先写"同record保序/stop留pending"等测试才敢上。**

### 问题2：P2 SSH 隧道"懒启动"不可行（鸡生蛋）

**现象：** 设计想"被选中才起隧道"，但 selector 选中前提是 healthz 探测通过，探测又需隧道先通。
**解决：** 改 up-front 起（所有 enabled+ssh endpoint 启动即拉起），探通了才被选中。P0 文档 §5.3 已校正。

### 问题3：托盘连接子菜单一直显示 `?`

**现象：** glyph 应显示 ●/○/✕，实际全 `?`。
**根因：** Windows pystray 菜单**文字标签**在构建时定死、之后不刷新（✓ 勾选状态会刷，文字不刷）；菜单构建时 selector 还没进 runtime holder。
**解决：** 托盘加 5s 定时 `icon.update_menu()` 强制重算 label 回调。重启后正确显示 ●/✕。

### 问题4：慢香港链路上 healthz 探测频繁误判

**现象：** public 被选中后每 30s `endpoint.none_healthy` + `no healthy endpoint available` 误失败，随即 recover，反复抖。
**根因：** 短超时 healthz 在慢/丢包链路不稳（哪怕空闲也偶发 >3s），但真正的 ingest（120s 超时）能成。
**解决：** 8s 超时 + 粘滞 + **不探当前 endpoint**（c4b5fce / 2508b55）。把"探测当真理"改成"探测只为发现升级机会，发送才是存活判据"。

### 问题5：push 偶发被 clash 拦

**现象：** `git push` 偶发 exit 128（github 走到 clash 坏节点）。
**解决：** 重试即过；clash 节点恢复期间正常。全部 commit 最终都 push 到 origin。

---

## 知识清单

- **多路径安全前提**：所有 endpoint 必须指向**同一后端**（同 DB）；靠 ingest 幂等（client_record_id UNIQUE + screenshot hash UNIQUE）+ close 的 by-client-id 兜底，换路重发不重不丢。语义是"同一后端的多条网络路径"，不是"多个服务器"。
- **SSH 套裸 HTTP ≈ HTTPS**：SSH 传输层强加密 + host key 认证防 MITM，后端免配 TLS 证书；公网段永不裸 HTTP。
- **单游标 outbox 与并发的冲突**：outbox 是单消费者严格 FIFO（单 `acked` 整数游标），并发乱序完成无法乱序 ack → 用**滑动窗口 + 按 record_id 屏障 + 连续前缀按序 ack**，既拿并发又不破 ack 语义、服务端零改。
- **探测是 hint 不是 gate**：慢链路上短超时 healthz 不可靠，发送成功与否才是真存活；故①粘滞（探测失败不弃当前）②不探当前 endpoint（省稀缺上行 + 去噪），只探更高优先级判断升级。
- **懒启动隧道的鸡生蛋**：依赖探测选中才起隧道是死锁；隧道必须 up-front 起，探测随后通过。
- **asyncio.Event 必须 `.wait()`**：`await event` 是 TypeError，并发屏障极易踩，配套测试是护栏。
- **Windows pystray 菜单文字不动态刷新**：dynamic 文字 label 在 win32 后端构建时定死，需 `icon.update_menu()` 周期刷新；`checked` 状态则会刷。
- **向后兼容**：不写 `endpoints` 数组 = 单 endpoint；`concurrency` 默认 1 = 原串行；旧 client.toml 重启即用。

---

## 待办 / 遗留

- [ ] 用户切回香港后建议重启吃 `2508b55`（消周期探测噪音 + 省上行）——非正确性，方便时即可。
- [ ] P2 局限：隧道集合启动时固定；托盘 toggle 改的是选路（enabled）不动隧道进程生死。全动态生命周期留后续。
- [ ] P3+（不做）：多活负载均衡需 outbox 升级为每条目 ack（位图/完成集合持久化）而非单游标。
- [ ] 用户后续会再加"日本 SSH 中转"endpoint（type=ssh），届时填 ssh_host + FRP 暴露端口即可。
