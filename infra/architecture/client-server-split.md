# Client / Server 三层拆分

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

本页讲清楚一件事：**为什么 TimeTrace 把代码拆成 `common / client / server` 三层、运行时却仍保留单进程与双进程两种形态，以及这两种形态如何通过同一套抽象（`BackendClient` Protocol + `bootstrap.py`）共用绝大部分代码而不分叉。**

读这页前，你只需要知道 TimeTrace 做的事：本地采集活跃窗口 + 关键帧截图 → 落库 → VLM 分析 → 检索 / 时间轴回放。

---

## 为什么要拆

最初 TimeTrace 是单进程：采集循环直接 `import` 并调用 `Database` / `PHashIndex`。这对单机日常使用完全够用，但三个真实需求迫使分层：

| 需求 | 单进程的问题 |
|------|-------------|
| **多设备** | 想在台式机 + 笔记本同时采集、汇总到一处。单进程模型下每台机器各有一份孤立 DB，无法合并 |
| **远程访问** | 想在外面用手机 / 另一台机器看时间轴。采集进程和 API/DB 绑死在同一台机器上，无法分离部署 |
| **headless 服务端** | 想把重活（VLM 推理、DB、检索）放到家里的 Linux 小主机常驻，桌面端只留一个极轻的采集器。但采集依赖 `pywin32` / `mss` 等 Windows-only 库，DB/检索却完全平台无关——它们不该被绑在一起 |

结论是按 **依赖边界 + 部署边界** 切三层：

| 层 | 目录 | 职责 | 平台约束 |
|----|------|------|---------|
| **common** | `src/timetrace/common/` | 两端共享的配置、数据模型、wire 协议、pHash 编解码 | 纯 Python，跨平台 |
| **client** | `src/timetrace/client/` | 采集（窗口/截图/idle）、托盘、outbox、三种 backend、`init` 命令 | Windows-only（采集部分） |
| **server** | `src/timetrace/server/` | API、DB、queue、blob、worker、VLM、embedding、pHash 索引、MCP、登录/鉴权、`bootstrap` | 跨平台（可跑在 Linux 小主机） |

一条硬规则：**client 不许 import server，server 不许 import client。** 两端只能通过 `common/` 交换类型。这条规则的执行靠 `common/config.py`（如 `VLMConfig` 放在 common 而非 server，避免 `common → server` 反向 import）、`common/protocol.py`（wire schema）、`common/models.py`（`CaptureContext`）。

> 注意：`client/core/backend.py` 里 `InProcessBackend` 确实 import 了 `server.db.Database` / `server.phash_index`——但全部包在 `if TYPE_CHECKING:` 里只做类型标注，运行时不触发跨层 import。真正的运行时实例由调用方（`main.py`）注入。

---

## 两种运行形态

拆成三层后，运行时入口有 **四个**（`pyproject.toml` 的 `[project.scripts]`），但跑起来只有两种拓扑：

| 入口 | 进程数 | 装了什么 | 适用场景 | 状态 |
|------|--------|---------|---------|------|
| `timetrace`（`main.py`） | **1** | capture + worker + api + 托盘 + reclaim 全在一个进程 | 单机日常 | 默认、稳定 |
| `timetrace-server`（`server/cli.py`）+ `timetrace-client`（`client/cli.py`） | **2** | server：api+db+worker+vlm；client：只采集 | 多设备 / 远程 / headless | 已接线可用 |
| `timetrace-embserver`（`embserver/cli.py`） | 独立 | 本地多模态 embedding 服务（8766），server 通过 HTTP 调它 | 自托管 embedding | 见 overview |

### 选哪种？

- **就一台 Windows 机器、自己用** → `uv run timetrace`，零配置，capture 直连本地 DB。
- **想分离采集与存储 / 想远程看 / 想让小主机扛 VLM** → server 跑在小主机，client 跑在每台桌面，client 通过 HTTP 把数据推给 server。

### 关键事实：单进程模式不走 HTTP

这是最容易误判的一点。单进程下 capture 经 `InProcessBackend` **直接函数调用** `SqliteDatabase` + `PHashIndex`，**完全不经过 HTTP，也不碰 outbox**。`HttpBackend` / `OutboxBackend` / `OutboxSender` / `/v1/ingest/*` 路由全部实装，但单进程模式一行都不用——它们只在双进程的 client 端被激活。

**改采集行为时，两条路径都要想到**：单进程的 InProcess 直调路径，和双进程的 Outbox→Http→ingest 路径。

---

## `BackendClient` Protocol：统一三种实现

拆分能成立，核心是一个抽象：采集层（client tier）只认识一个最小接口——`BackendClient`（`client/core/backend.py:BackendClient`），底层传输可以随意替换。

```python
class BackendClient(Protocol):
    async def submit_record(self, ctx, reason, event_type="heartbeat") -> str: ...
    async def close_record(self, record_id: str) -> None: ...
    async def submit_screenshot(self, payload: ScreenshotSubmission) -> str | None: ...
    async def mark_pending(self, record_id: str) -> None: ...
```

采集循环（`client/capture/service.py:CaptureService`）拿到的就是这四个方法，**不知道也不关心**背后是本地 DB 还是远程 HTTP。三种实现：

### 1. `InProcessBackend`（单进程）

`client/core/backend.py:InProcessBackend`。直接适配 `Database` + `PHashIndex`：

- `submit_record` → `db.insert_record`，返回服务端真实 record UUID
- `submit_screenshot` → `db.insert_screenshot` + 顺手维护 pHash BK-tree（用 record 的 `ts_start` 而非当前时刻做桶键，保证与 `PHashIndex.from_db` 重建一致）
- `mark_pending` → `db.mark_pending`

`main.py` 用它把采集半边"螺栓"到 server bootstrap 上：

```python
backend = InProcessBackend(components.db, phash_index=components.phash_index)
capture_svc = CaptureService(config.capture, config.privacy, backend, storage_cfg=config.storage)
```

### 2. `HttpBackend`（双进程的底层传输）

`client/core/backend.py:HttpBackend`。把"`submit_record` 然后 `submit_screenshot`"这套两步调用，映射到 `/v1/ingest/*`：

- 本地生成 `client_record_id`（UUID），`submit_record` 发一个 record-only multipart POST，返回 `client_record_id` 给采集层（采集层后续都用它）；服务端真实 id 由 backend 内部 `_record_id_for` 字典记账
- `submit_screenshot` 用同一个 `client_record_id` 发第二个 POST，带图片字节；服务端凭 `client_record_id` 把截图挂到已存在的 record 上
- `close_record` → `POST /v1/ingest/record/{id}/close`
- `mark_pending` 是 **no-op**——ingest 路由对每个成功 POST 已经自动 `mark_pending`，采集层那次显式调用在此传输上是冗余的
- 路径解析：截图写盘时是相对 data_dir 的相对路径，`HttpBackend` 用 `resolve_path_under_data_dir` 解析（拒绝 `data_dir` 外的逃逸路径，防恶意 ScreenshotSubmission 把宿主机密文件喂进上传管线）
- 鉴权：`auth_token`（Bearer）+ `device_id`（`X-Device-Id`）双 header，自有 client 在构造时烤进 headers、借用 client（测试用 ASGITransport）则每请求 `_extra_headers()` 注入
- 多端点：构造可传 `base_url_provider`（取代固定 `base_url`），多端点 failover 模式下每请求从选择器解析 active base URL；`timeout_s` 可配（默认 30，client 侧实际传 120，应对住宅上行慢链路）

`HttpBackend` 还额外暴露 `post_ingest` / `post_close` 两个 wire 级方法，供 `OutboxSender` 重放队列条目用（见下）。

### 3. `OutboxBackend`（双进程 client 的默认 backend）

`client/core/outbox_backend.py:OutboxBackend`。这是双进程下采集层真正拿到的 backend。它不直接发网络，而是把每次调用 **先 append 进本地 Outbox**，由后台 `OutboxSender` 串行 drain 给 `HttpBackend`。

- 同样本地生成 `client_record_id`，组装出与 `HttpBackend` 一致的 ingest payload，连同图片字节 append 到 outbox
- `submit_screenshot` 返回 `None`（截图的服务端真实 id 要等 drain 时才知道，采集层本就不持久化这个 id，所以返回 None 无碍）
- `make_http_sender(http)` 把一个 `HttpBackend` 包成 `OutboxSender` 需要的 `SendCallable`：拿一条 `OutboxEntry`，按其 payload 调 `http.post_ingest` / `http.post_close` 重放上墙

这样采集与上传 **解耦**：网络断了、server 重启了，采集照常落 outbox，恢复后 sender 自动补发。

`client/cli.py` 的接线全貌（已演进为多端点 + 隧道 + 并发上传，详见 [`infra/PLAN-MULTIPATH-CLIENT.md`](../PLAN-MULTIPATH-CLIENT.md)）：

```python
outbox   = Outbox(client_cfg.outbox.root_dir)
selector = EndpointSelector(client_cfg.server.all_endpoints())          # 多端点失效转移
http     = HttpBackend(base_url_provider=selector.current_url,          # 每请求解析 active URL
                       auth_token=..., device_id=..., data_dir=..., timeout_s=120.0)
backend  = OutboxBackend(outbox, data_dir=client_cfg.storage.data_dir)  # 喂给 CaptureService
sender   = OutboxSender(outbox, make_http_sender(http), max_kbps=...,
                        max_image_bytes=..., on_send_failure=...,        # 失败回调驱动 failover
                        concurrency=client_cfg.upload.concurrency)      # 后台 drain
tunnels  = SshTunnelManager(client_cfg.server.enabled_endpoints())      # 原生 SSH 隧道
# TaskGroup: capture(backend) ‖ sender ‖ endpoints(selector) ‖ ssh_tunnels(条件) ‖ quit_watcher
```

---

## `bootstrap.py`：单 / 双进程共享 server 组装

`main.py`（单进程）和 `server/cli.py`（双进程 server）需要的 server 侧组装是**同一套**：DB、PHashIndex、Blob、Auth、UserStore、VLM、Embedding、Worker、FastAPI app。如果各写一份，必然漂移（注释里记着：landing 三天内就已经各写各的、开始分叉）。

`server/bootstrap.py` 收口成两个函数 + 一个 dataclass：

- **`ServerComponents`**（frozen dataclass）——握住每个 server 单例的句柄
- **`build_server_components(config)`**——实例化整张图：`db.init()` → `PHashIndex.from_db` → VLM/Embedding（缺 key/model 则 None 跳过，不报错）→ Blob → `ServerAuth.load_or_generate`（首启自动生成 `tt_live_` token 并 log）→ `UserStore.ensure_admin_seeded`（首启 seed admin，幂等）→ Worker → `create_app(...)`
- **`serve(components, config, quit_event, *, extra_tasks=...)`**——在一个 `asyncio.TaskGroup` 里跑 `worker / api(uvicorn) / quit_watcher / reclaim / report_scheduler`（`report_scheduler` 是后加的 AI 看板定时调度任务，定时让 agent 生成 HTML 洞察报告；无 VLM 时直接退出，`server/bootstrap.py:199-218`、`:226`），`quit_event` fire 即优雅退出，finally 里 `aclose` VLM/Embedding httpx 池 + `db.close`

关键设计是 **`extra_tasks`**：单进程的 `main.py` 不 fork `serve`，而是把采集协程作为命名任务注入：

```python
await serve(components, config, quit_event, extra_tasks={"capture": capture_svc.run()})
```

`_watch_quit` 退出时会连 `extra_tasks` 的名字一起 cancel，TaskGroup 不论谁加了什么都干净收尾。于是：

- **单进程** = bootstrap 全套 server 任务 + 注入的 `capture` 任务，共 6 个任务
- **双进程 server** = bootstrap 全套，无 `extra_tasks`，5 个任务
- **双进程 client** = `client/cli.py` 自己的小 TaskGroup（capture + sender + endpoints + ssh_tunnels(条件) + quit_watcher），不碰 bootstrap

两端的 server 组装代码因此 100% 共享，永不再漂移。

---

## Protocol 升级路径（Database / Queue / BlobStorage）

当前 server 侧的 DB / queue / blob 是具体类，但已为多后端预留：

- **Database**：`server/db/sqlite.py:SqliteDatabase` 是实现，`server/db/__init__.py` 导出 `Database = SqliteDatabase` 别名。所有调用方都写 `from timetrace.server.db import Database`。等 PostgresDatabase 进来（多设备/远程的持久后端），这个别名升级为 `typing.Protocol`，调用方一行不动。
- **BlobStorage**：`server/storage/blob.py:LocalBlobStorage` 写本地文件系统。同理可升级为 Protocol 接 S3/对象存储。
- **Queue**：worker 当前直接吃 `analysis_results.status` 状态机（`claim_next_task` 用 `UPDATE…RETURNING` 原子抢占 `pending_vlm` 行，无独立任务表、无需 Redis），未来若要分布式可抽象成 Protocol。

`BackendClient` 已经是 Protocol（client 侧先行示范了这个模式）；server 侧三件套是同一招的待落地版本。

---

## Outbox 可靠性

双进程下采集与上传之间隔着一个崩溃安全的本地队列。设计目标：**网络/服务端任意时刻挂掉，采集数据不丢、不乱序、不无限堆盘。**

### 落盘结构（`client/core/outbox.py`）

```
<outbox_root>/
  log.jsonl               # 一行一条 JSON，append-only
  blobs/<entry_id>-image  # 截图字节（可选，每条）
  blobs/<entry_id>-thumb  # 缩略图字节（可选，每条）
  state.json              # {"acked": N} —— log 的前 N 条已发送
```

### 写入顺序与崩溃安全

`append` 先写 blob 文件（含 `fsync`），再 append JSONL 行（含 `fsync`）：

- 崩在 blob 与行之间 → blob 泄漏（无害，下次 compaction 回收）
- 崩在行与 ack 之间 → 下次 sender 重读重发 = **at-least-once**

at-least-once 之所以安全，靠服务端幂等：`/v1/ingest/record` 用 `client_record_id` UNIQUE 索引做 record 级幂等，`screenshots(record_id, hash_sha256)` UNIQUE 做 screenshot 级幂等。重放只会得到 `200 was_new=False`，不会双插。

`_read_log` 对**末尾**一行 partial JSON 容错（崩在 append 半途），但更早的位置 JSON 损坏会直接抛——那是真损坏，宁可让运维介入也不静默丢数据。

### 严格 FIFO + 单 sender

`ack_next(entry_id)` 只允许 ack 当前队头，id 对不上就抛。一个 Outbox 配一个 sender。为什么必须严格按序：采集产生的是 `submit_record → submit_screenshot → close_record` 这个有序序列，服务端也期望这个顺序（`/close` 只有在对应 record 已存在时才成功）。FIFO 免费给了这个保证。`concurrency > 1` 时启用 `_drain_window_concurrent` 滑动窗口并发 drain（高延迟链路下隐藏延迟），正是用文档早先预言的方案保住这个顺序：per-record_id barrier（`prereq` / `last_by_record`，同一 record 的 record→screenshot→close 串行）+ 严格按序 ack（只推进 contiguous-completed 前缀的游标），单游标 outbox 仍安全，见 `outbox_sender._drain_window_concurrent` 与 [`infra/PLAN-MULTIPATH-CLIENT.md`](../PLAN-MULTIPATH-CLIENT.md) §6.2。

### OutboxSender 的发送循环（`client/core/outbox_sender.py`）

- **指数 backoff**：每次连续失败翻倍，封顶 `backoff_max_s`，成功即重置。失败的条目**永不跳过**——一直重试到成功或 stop（at-least-once + 幂等服务端 = 持久性故事）
- **token bucket 限速**：`max_kbps > 0` 时，每条发送前先 `consume(entry_size)` 阻塞到预算够（预抢占式，发之前就把这次的字节量算进配额）；`max_kbps=0` 时限速器零开销空转
- **inline compaction**：每 `compact_every_n_acks`（默认 200）次成功 ack 后，在同一循环里调 `outbox.compact()`，保持围绕 outbox 锁单线程、ack 与重写之间无竞态
- **超大图丢弃**：`_is_oversize_image` 把超过 `max_image_bytes`（默认 2MB，经 `client.toml [upload] max_image_mb` 配置）的图片条目直接 ack 跳过并记日志，防老的 5MB PNG 永久堵死 FIFO（同 capture 的 record-only 条目仍正常上传）
- **失败回调驱动 failover**：每次发送失败（backoff 之前）触发 `on_send_failure`，让端点选择器快速重选，下次重试可命中另一条路径
- **idle 轮询**：队列空时 sleep `idle_poll_interval_s`（当前无文件 watcher，未来可在同进程 `append` 时同步唤醒）

### Compaction 的崩溃安全顺序（load-bearing，别翻）

`compact` 回收已 ack 的条目，顺序是 **先写 state 再重写 log**：

```
state.acked = 0      ← step 1（atomic rename）
log = [仅未ack条目]   ← step 2（atomic rename）
acked 条目的 blob     ← step 3（best-effort unlink）
```

崩在 step1/step2 之间：log 还是完整的 `[已ack + 未ack]`，state 说 `acked=0` → sender 重放已 ack 的条目 → 服务端幂等吸收 → 数据不丢。反过来（先 log 后 state）会导致 state 说 `acked=N` 但新 log 比 N 短 → **跳过未发条目，不可恢复**。所以这个顺序是承重的。

`state.json` 与 `log.jsonl` 的每次写都走 tmp 文件 + `fsync` + atomic rename，绝不留半写文件。

---

## 相关文档

- [架构总览](overview.md) — 全景与模块职责
- [Capture Service](capture-service.md) — 采集循环细节
- [Local API Server](api-server.md) — `/v1/ingest/*` 与业务路由
- [Analysis Worker](analysis-worker.md) — 消费 `analysis_results`（status 状态机）
- 双进程接线设计：[`devlogs/infra/archive-202605151200-client-server-split-kickoff.md`](../../devlogs/infra/archive-202605151200-client-server-split-kickoff.md)
- compaction：[`devlogs/infra/archive-202605171501-outbox-compaction-and-review-fixes.md`](../../devlogs/infra/archive-202605171501-outbox-compaction-and-review-fixes.md)
