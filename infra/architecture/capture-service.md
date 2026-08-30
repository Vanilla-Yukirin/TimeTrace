# Capture Service

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

采集层是 TimeTrace 最轻的一环：监听活跃窗口、按策略截关键帧、检测 idle，然后把数据**经 `BackendClient` 抽象提交出去**——它不知道也不关心数据落本地 DB 还是推给远程 server。代码在 `src/timetrace/client/capture/`。

---

## 职责

- 监听活跃窗口切换（标题 / 进程 / 应用 / URL）
- 按策略触发关键帧截图（切窗触发 + 心跳补帧 + 最小间隔节流 + 延迟校验）
- 检测 idle 段（pynput 键鼠监听）
- 经 `BackendClient` 提交 record / screenshot，并标记 pending（供 Analysis Worker 拉取）

**采集不直接碰 DB / PHashIndex**。它只调 `BackendClient` 的四个方法（`submit_record` / `close_record` / `submit_screenshot` / `mark_pending`），底层是直连本地还是 HTTP 上传由注入的 backend 决定。三种 backend 实现与 outbox 可靠性见 [Client/Server 三层拆分](client-server-split.md)。

---

## 依赖库

| 库 | 用途 |
|----|------|
| `pywin32` + ctypes | Windows API（窗口焦点、标题、进程名、URL；`window.py` 用 ctypes 直调 `QueryFullProcessImageNameW` 取真实进程名） |
| `mss` | 截图（ctypes、thread-safe） |
| `pynput` | 键鼠监听（idle 检测）；listener 线程 `daemon=True`，进程退出不被阻塞 |
| `pystray` | Windows 托盘（暂停、隐私切换、退出） |

---

## BackendClient：采集的唯一出口

`CaptureService.__init__` 收一个 `backend: BackendClient`，全程只通过它出数据：

| 调用 | 单进程（InProcessBackend） | 双进程（OutboxBackend → Http） |
|------|---------------------------|------------------------------|
| `submit_record` | 直接 `db.insert_record`，返回服务端 record UUID | 生成 `client_record_id`，append 进 outbox，返回该 id |
| `submit_screenshot` | `db.insert_screenshot` + 维护 pHash BK-tree | 组装 ingest payload + 图片字节 append 进 outbox，返回 None |
| `close_record` | `db.close_record` | append 带最后可信观测时间的 close 条目（或直发 `/close`） |
| `mark_pending` | `db.mark_pending` | no-op（ingest 路由已自动 mark） |

`_last_record_id` 的类型恒为 `str`，但**语义随 backend 不同**：InProcess 下是 DB 里真实存在的 record UUID；Outbox/Http 下是 `client_record_id`，此刻服务端还没有对应行（行要等 sender drain 后才物化）。`/v1/ingest/record/{id}/close` 路由接受两种形式（by-client-id 兜底），所以采集层调 `close_record` 时**不需要分支判断 backend 类型**。

三种 backend 实现均已完成且行为正确，无桩。

---

## 主循环与 idle

`CaptureService.run()`：先在 daemon 线程启动 pynput idle 监听，然后 1s 轮询循环 `_tick()`；`finally` 里用最后一次可信观测时间关闭未结束 record（`_safe_close_record(where="shutdown", ts_end_ms=...)`）、用 daemon 线程 `stop` 监听器（防 pynput `stop()` 卡死阻塞退出）、取消挂起的截图任务。

`_tick()` 流程：

```
检查 monotonic 轮询间隔
  gap > 5s ?
    └─ 用上一 tick 的墙钟关闭旧 record（where="observation_gap"）
       清除窗口状态，当前 tick 重新开启 record

读 idle_seconds
  idle_s >= idle_threshold_s ?
    └─ 进入 idle：首次进入时 close 最后一条 record（where="idle_start"），置 _last_record_id=None，return（idle 期间不采集）
  否则若刚从 idle 恢复 → log idle_end

get_active_window() 为 None → return
组装 CaptureContext(app/process/title/url)
should_capture(ctx, privacy) == False → return        # 隐私过滤

窗口切换？(hwnd != prev_hwnd 且 hwnd != 0)
  └─ close 旧 record（where="window_switch"）
     submit_record(reason="switch", event_type="window_switch")
     取消挂起截图任务 → 新建延迟截图 Task(switch_capture_delay_s)
     更新 _last_record_id / _prev_hwnd / _prev_app, return

心跳：elapsed >= max_capture_interval_s ?
  └─ close 旧 record（where="heartbeat"）
     submit_record(reason="heartbeat", event_type="heartbeat")
     _save_screenshot(...) 补帧
```

**有界时间块设计**：每条 record 持续上限 = `max_capture_interval_s`（默认 30s）。采集循环用 monotonic 间隔识别休眠/进程暂停等不可观测 gap，并以 gap 前最后一次 tick 的墙钟收口；服务端再拒绝负数或超过 5 分钟的边界，常驻维护循环每分钟回收超过 5 分钟的 open lease。分析层要展示连续 session 按相邻同应用记录合并即可，不依赖 `ts_end = null` 开区间。

---

## `_safe_close_record`：容错收口

五处需要关 record 的调用（shutdown / observation_gap / idle_start / window_switch / heartbeat）统一走 `_safe_close_record(record_id, *, where=..., ts_end_ms=...)`。其中 `ts_end_ms` 是客户端最后一次可信观测的墙钟时间，Outbox 延迟重放时也不会改写。方法用 `try/except` 包住 `backend.close_record`，失败时打一条带 `where` 结构化字段的 warning 然后**吞掉**——采集主循环必须能扛住单次 close 失败（双进程下 server 短暂不可达、网络抖动都可能让 close 失败），不能因此把整条采集链拖垮。`where` 让事后翻日志能定位是哪个调用点失败。

---

## 延迟截图：可取消 Task + HWND 校验

切窗不立即截图，而是建一个延迟 `switch_capture_delay_s`（默认 1.5s）的 `asyncio.Task`（`_delayed_screenshot`）：

- **每次切窗先 `_cancel_pending_screenshot()`** 取消上一个挂起任务，再建新的 → 快速连续切窗时只有最终停留的窗口会触发截图
- 延迟结束后**校验 HWND**：当前活跃窗口 hwnd 与预期不符（用户已切走）→ 放弃，不截图
- 再查 `min_capture_interval_s`：距上次截图太近 → 跳过（速率保护）
- 通过则 `_save_screenshot`

`_cancel_pending_screenshot()` 先把引用 detach 再 cancel，最多等 0.2s 让任务确认取消；若仍在跑（executor 线程没结束）就打 warning 继续，绝不让取消阻塞采集循环或退出路径——因为 `capture_active_window` 跑在 `run_in_executor` 里可能卡住。

---

## 截图落盘与 pHash

`_save_screenshot(record_id, hwnd, now)`：

1. 更新 `_last_capture_ts = now`
2. 若 `storage_cfg is None` 或 `privacy.store_images == False` → 只 `mark_pending`，不截图（隐私"只记元数据"模式）
3. 否则 `run_in_executor` 调 `capture_active_window(record_id, storage_cfg, hwnd)`，返回 `(rel_img, rel_thumb, sha256, width, height, phash)`
4. 有结果则构造 `ScreenshotSubmission` 经 `backend.submit_screenshot` 提交（路径是相对 data_dir 的**相对路径**，HttpBackend 端会用 `resolve_path_under_data_dir` 安全解析）
5. 最后 `mark_pending`

**pHash**：`capture_active_window` 在保存图像后立即算 64-bit 感知哈希（灰度 → DCT-II → 8×8 低频 → 中位数阈值）。失败则 `phash=None` 打 warning，不中断落库；失败帧不进视觉索引。pHash 进 BK-tree 这一步发生在 **backend 内部**——`InProcessBackend.submit_screenshot` 用 record 的 `ts_start`（不是 `time.time()`）作桶键调 `PHashIndex.insert`，保证运行时 insert 与 `PHashIndex.from_db()` 重建用的桶键一致。

---

## 隐私集成

`should_capture(ctx, privacy)`（`client/capture/privacy.py`）在每次 `_tick` 取窗后、提交前调用，按 `PrivacyConfig` 过滤：暂停（`paused`）、应用黑名单（`app_blacklist`）、标题关键词（`title_keywords`）命中即不采集。`store_images=False` 则在 `_save_screenshot` 层退化为只记元数据。`PrivacyConfig.mode`（off / text_only / full）是 P4 OCR+分类+模糊管线的前向选择器，当前运行时隐私由前述四个字段承载。

---

## 错误与重试

| 场景 | 处理 |
|------|------|
| 截图失败（权限/瞬态） | `capture_active_window` 返回 None / phash None，跳过该帧，不阻塞主循环 |
| close_record 失败 | `_safe_close_record` 吞掉 + 结构化 warning（带 `where`） |
| 双进程上传失败 | 不在采集层处理——数据已落 outbox，由 `OutboxSender` 指数 backoff 重试（见 [三层拆分](client-server-split.md)） |
| 采集主循环异常 | TaskGroup 捕获并触发统一退出 |

---

## 性能预算

| 指标 | 目标 |
|------|------|
| CPU | 均值 < 1–2%（多数时间等事件） |
| 内存 | < 80 MB（写盘后立即释放，不缓存大图） |
| I/O | 截图写盘为主，`min_capture_interval_s` 限峰 |

---

## 相关文档

- [Client/Server 三层拆分](client-server-split.md) — BackendClient 三实现 + Outbox 可靠性
- [Analysis Worker](analysis-worker.md) — 消费 pending 任务
- [隐私策略](../privacy/strategy.md)
- [存储 Schema（records 表）](../storage/schema.md)
