# 设计：多路径客户端连接（endpoint 失效转移 + 原生 SSH 隧道 + 并发上传）

**状态：** P0 文档 + P1/P2/P3 均已实现并测试通过（commits c32a7a4 / b241609 /
66ba77c / 8247fcc，全量 451 passed）。P3+ 多活仍不做。
**动机来源：** 2026-06-02 夜，用户（技术型、公网域名带宽小、网络环境多变）提出客户端应支持多连接路径 + 自动 failover。

---

## 1. 动机

`timetrace-client` 目前只认 `client.toml` 里**单个** `server.url`。但真实使用中，同一台采集机在不同时刻有**截然不同的最优路径**：

| 场景 | 最优路径 | 特性 |
|------|----------|------|
| 在家（与 box 同局域网） | `http://192.168.2.105:8765` 直连 | 极快、低延迟、零绕路 |
| 在外，但有高带宽中转 | SSH 隧道 → 日本 VPS 的非公开 FRP 端口 | 大带宽、巨延迟、需 SSH 包裹加密 |
| 在外，无中转 | `https://timetrace.yukirin.me` 公网域名 | 哪都能用、带宽小（香港小机）、HTTPS |

**实测依据（2026-06-02）**：公网香港路径上行仅 ~300KB/s（传 2MB 要 6.8s），5000 条积压要数小时；切到 LAN 直连后 **~21 条/秒**，几分钟排空。带宽差异 ~100×。所以"按当前网络选最快路径"价值巨大。

核心诉求拆成三件事：

- **P1 — 多 endpoint + 健康探测 failover**：配置里放一个**有序** endpoint 列表，客户端探测后用第一个"健康 + 启用"的；任一个能连通即视为通道可用。不做负载均衡（单活）。
- **P2 — 原生 SSH 隧道连接类型**：客户端自己托管一条 `ssh -L` 隧道（而非依赖外部 autossh），把"非公开 HTTP 端口"安全地暴露成一个本地 endpoint。
- **P3 — 并发上传**：多路并发抢 outbox 发送，隐藏（尤其高延迟中转的）每请求 RTT，吃满带宽。

---

## 2. 安全模型：为什么 SSH 套裸 HTTP ≈ HTTPS（用户明确问的）

**裸 HTTP 直连公网端口的问题（真实风险）：**
- **token 明文**：`Authorization: Bearer tt_live_...` 明文过线，链路任一跳（ISP / 中转机运营方 / 公网节点）可窃取 → 拿到即可往库里写。
- **截图 + 窗口标题明文**：用户在做什么全裸奔。
- **无服务器认证**：可被 MITM 冒充。

**SSH 隧道把这些全解决：** SSH 传输层是强加密（与 TLS 同量级），并用 host key 认证对端防 MITM。所以 **SSH 套裸 HTTP = 拿到机密性 + 完整性 + 服务器认证，且后端无需配 TLS 证书**——业界成熟做法（内网服务经 SSH 暴露）。

> **边界**：SSH 只加密 `client → 跳板机` 这段。跳板机 → 最终后端如还有一跳（FRP），那段靠 FRP 自身加密。日本场景 `client →(SSH,加密)→ 日本:非公开端口 →(FRP)→ box`，SSH 罩住最长公网段，FRP 罩住日本→box，端到端 OK。

**结论**：除"LAN 内裸 HTTP"（可信网段，可接受）外，**任何公网段都必须 TLS（公网域名）或 SSH 隧道包裹，永不裸 HTTP 出公网**。

---

## 3. 关键不变量（所有路径共享一个后端）

**所有 endpoint 必须指向同一个后端（同一台 box、同一个 DB）。** 这是 failover 安全的前提：
- LAN IP、公网域名、SSH 中转，最终都到那台 box → 同一个 DB。
- ingest 幂等（`client_record_id` UNIQUE + `screenshots(record_id, hash_sha256)` UNIQUE）→ **中途换 endpoint 重发不会重、不会丢**。
- close 路由按 server id 或 client_record_id 双重兜底（[ingest.py](../src/timetrace/server/api/routes/ingest.py) `close_record`）→ 换 endpoint 后仍能关闭。

如果某 endpoint 指向**不同**后端，数据就分裂了。配置语义因此是"**同一后端的多条网络路径**"，不是"多个服务器"。

---

## 4. P1 — 多 endpoint + 健康探测 failover

### 4.1 配置 schema（`client.toml`）

向后兼容：旧的单 `url` 仍可用（视作只有一个 endpoint）。新增 `[[server.endpoints]]` 数组（有序 = 优先级，越靠前越优先）：

```toml
[server]
auth_token = "tt_live_..."
# 兼容旧字段：若没写 endpoints 数组，url 视作唯一 endpoint
# url = "https://timetrace.yukirin.me"

[[server.endpoints]]
name    = "lan"                       # 人类可读标识，托盘菜单显示
url     = "http://192.168.2.105:8765"
enabled = true

[[server.endpoints]]
name    = "japan-relay"
url     = "http://127.0.0.1:19765"    # 由 P2 的原生 SSH 隧道或外部 autossh 提供
enabled = true

[[server.endpoints]]
name    = "public"
url     = "https://timetrace.yukirin.me"
enabled = true
```

**顺序只在配置文件里手动调**（托盘只开关，不排序——见 4.4）。

### 4.2 选路算法（单活 + 健康探测）

- 维护一个 `EndpointSelector`：按配置顺序遍历 `enabled` 的 endpoint，对每个打 `GET /healthz`（短超时，如 3s）。
- **第一个健康的即选中**，作为当前活跃 endpoint。
- 重新探测时机：
  - 启动时探一遍。
  - 当前活跃 endpoint **连续失败 N 次** → 立刻从头重探（可能降级到下一个）。
  - **定期**重探（如每 60s）——让"回到家"后能自动从公网升级回 LAN（即使当前公网是通的，只要更高优先级的 LAN 变健康就切回）。
- "任一健康即通道可用"：全部探完都不健康 → 没有活跃 endpoint，sender 进入退避等待 + 周期重探，不报错退出。

### 4.3 接入点

- `HttpBackend` 当前在构造时绑定单 `base_url`。改为：transport 从 `EndpointSelector.current()` 取活跃 URL；活跃 endpoint 变化时换底层 `httpx.AsyncClient` 的 `base_url`（或每请求传完整 URL）。
- `OutboxSender` 发送失败时，除退避外，触发 `EndpointSelector` 重探/切换。
- auth_token / device_id header 与 endpoint 无关，照常注入。

### 4.4 托盘 UI

托盘菜单（[tray.py](../src/timetrace/client/tray.py)）新增子菜单 **"连接"**：
- 列出每个 endpoint（显示 `name` + 健康状态点：● 活跃 / ○ 健康待命 / ✕ 不可达）。
- 每项是一个 **checkable** 菜单项，点击切换该 endpoint 的 `enabled` 开/关。
- 开关后：写回 `client.toml` + 通知 `EndpointSelector` 重探。
- **不提供排序**（顺序只在配置文件手动调）。
- 托盘需要拿到 `ClientConfig`（目前只传了 `privacy_cfg`）+ 一个"重探"回调 + endpoint 健康快照。

---

## 5. P2 — 原生 SSH 隧道 endpoint 类型

让某个 endpoint 由客户端**自己托管**一条 SSH 本地转发，而非依赖外部 autossh。

### 5.1 配置

```toml
[[server.endpoints]]
name    = "japan-relay"
enabled = true
# type=ssh 时客户端自起隧道；url 指向隧道本地端
type        = "ssh"
url         = "http://127.0.0.1:19765"   # 本地监听端（客户端连这里）
ssh_host    = "japan-vps"                 # ssh 别名或 user@host
ssh_port    = 22
remote_host = "127.0.0.1"                 # 跳板机上 FRP 暴露的非公开端
remote_port = 18765
# identity_file 可选；默认走 ssh-agent / ~/.ssh/config
```

等价于 `ssh -N -L 19765:127.0.0.1:18765 japan-vps`。

### 5.2 隧道管理器

- `SshTunnel`：用 `subprocess`（系统 `ssh`，跨平台最稳，Win10+ 自带 OpenSSH）起 `ssh -N -L local:remote_host:remote_port host`。
  - 选 `subprocess` 而非 asyncssh：复用用户已配好的 `~/.ssh/config` / agent / known_hosts，零密钥管理代码，最少惊喜。
- 生命周期：endpoint 启用且被选中 → 起隧道；进程退出 → 指数退避重启；客户端退出 → 杀隧道。
- 健康探测自然覆盖：隧道没起来 → `http://127.0.0.1:19765/healthz` 探测失败 → 该 endpoint 视为不可达 → failover 到下一个。
- `-o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3` 保证转发失败立刻退、断线快速感知。

### 5.3 与 P1 的关系（实现修正）

设计初稿设想"被选中才起隧道（懒启动）"——**实现时发现不可行**：`EndpointSelector`
只能选中 `/healthz` 探测通过的 endpoint，而探测又需隧道先通，鸡生蛋。故隧道
**在启动时就为所有 enabled 的 ssh endpoint 拉起**（`SshTunnelManager`），探测通了
才被选中。P2 仍是 P1 的一个 endpoint 子类型，不改 P1 选路主干。

> 落地局限：隧道集合在启动时从 enabled+ssh endpoint 固定；托盘 toggle 改的是
> *选路*（enabled），不动隧道进程生死——固定一条空闲隧道无害，全动态生命周期留待后续。

---

## 6. P3 — 并发上传（含乱序 + ack 危险分析）

### 6.1 目标 vs 朴素方案的危险

**目标**：并发隐藏每请求 RTT（尤其高延迟中转），提升吞吐。

**用户的朴素设想**："多 worker 各自抢 outbox 发送、互相独立"。**直接这么做会坏数据**，两个硬约束：

**危险 A — 打破 record→screenshot→close 顺序。**
capture 往 outbox 按 `record` → `screenshot` → `close` 顺序 append。当前单 worker 严格 FIFO 天然保证服务端先见到 record。并发抢则可能：
- `screenshot` 先于 `record` 到 → 服务端 `ingest_or_get_record` 会**用 screenshot 那条 payload（不含 app_name/window_title）创建记录** → 该记录元数据为 NULL，之后 record 那条到了是 `was_new=False` **不回填** → **元数据丢失**。
- `close` 先于 `record` 到 → 404（[ingest.py](../src/timetrace/server/api/routes/ingest.py) close 未知 id 返 404）→ 客户端重试，最终 record 到了才成功（这个能自愈，但浪费重试）。

**危险 B — outbox 的 ack 模型是单游标、严格 FIFO。**
[outbox.py](../src/timetrace/client/core/outbox.py) 用单个 `acked` 整数游标，`ack_next` 只能 ack 队首。**并发乱序完成无法乱序 ack**——这是单消费者设计，与"多 worker 各自完成各自 ack"根本冲突。

### 6.2 选定方案：滑动窗口 sender（FIFO 序、并发在途、按 record 屏障）

不做"N 个独立 worker 乱序抢"（会触发危险 A/B），改做**单协调者 + 有界并发在途**，既拿到并发收益又不坏数据：

- **最多 N 条在途**（`concurrency` 可配，默认如 4）。按 outbox FIFO 顺序往外发，允许至多 N 条未完成。
- **按 record_id 屏障**：不为某 `record_id` 发后续条目，直到它前面同 `record_id` 的条目**已完成**——保住 record→screenshot→close 顺序（危险 A 消除）。跨不同 record_id 自由并发。
- **按序 ack**：一条只有在它**及它之前所有条目都完成**后才推进 `acked` 游标（推进到"连续已完成前缀"的末尾）。完成但前面没完成的，先挂在"已完成集合"里等（危险 B 用"连续前缀推进"化解，不改单游标语义）。
- **失败**：该条退避重试，不推进游标越过它（严格 FIFO 持久化语义不变）。

效果：高延迟链路上 N 条 RTT 重叠 → 吞吐 ~N×；顺序与 ack 持久化语义都不破；服务端**零改动**。

### 6.3 每路独立健康探测（用户要求）

并发各路"各自探各自的"——在滑动窗口模型里落为：`EndpointSelector` 的探测对每个 endpoint 独立计时 + **指数退避**（1/2/4/8…s，即用户说的"两倍两倍"），失败的路退避加长、健康的路快速复用。窗口里的并发在途都走当前选中的活跃 endpoint（单活），失败触发重探/切路。

> 若未来真要"多 endpoint 同时各发一部分"（多活负载均衡），需要 outbox 升级为**每条目独立 ack**（位图 / 完成集合持久化）而非单游标——记为 P3+ 的独立大改，本期不做。

---

## 7. 分期与落地顺序

| 阶段 | 交付 | 状态 | 服务端改动 |
|------|------|------|-----------|
| **P0** | 本文档 | ✅ c32a7a4 | 无 |
| **P1** | endpoint 列表 + 健康探测 failover + 托盘开关 | ✅ b241609 | 无 |
| **P2** | `type=ssh` endpoint + 原生隧道托管 | ✅ 66ba77c | 无 |
| **P3** | 滑动窗口并发 sender + 每路独立退避探测 | ✅ 8247fcc | 无 |
| P3+（不做） | 多活 + outbox 每条目 ack | 暂不做 | 可能 |

**向后兼容**：每阶段都保持旧 `client.toml`（单 `url`、单 worker）可用——不写 `endpoints` 就是单 endpoint，`concurrency` 默认 1 就是旧串行行为。

---

## 8. 测试要点

- **P1**：配置解析（单 url 兼容 / endpoints 数组 / enabled 过滤）；EndpointSelector 选第一个健康的 / 全不健康进等待 / 高优先级恢复后切回；托盘开关写回配置。
- **P2**：隧道进程起停 / 退出重启退避 / 客户端退出杀隧道（mock subprocess，不真起 ssh）。
- **P3**：滑动窗口按 record_id 屏障（同 record 串行、跨 record 并发）；按序 ack（乱序完成 → 连续前缀推进）；失败不越界；`concurrency=1` 退化为旧串行（回归保护）。
- 不 mock outbox 文件层，用 `tmp_path` 真实 spool（沿用现有 outbox 测试风格）。
