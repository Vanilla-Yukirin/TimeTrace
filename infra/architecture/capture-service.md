# Capture Service

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

- 监听活跃窗口切换（窗口标题 / 进程 / 应用 / URL）
- 按策略触发关键帧截图（切窗触发 + 定时补帧 + 最小间隔节流）
- 采集键鼠计数、idle 段
- 原始数据落库（`records` / `screenshots`）并写图片文件
- 标记 `pending_vlm`（供 Analysis Worker 拉取）

---

## 依赖库

| 库 | 用途 |
|----|------|
| `pywin32` | Windows API 访问（窗口焦点、标题、进程名、URL） |
| `mss` | 截图（ultra-fast、ctypes、thread-safe） |
| `pynput` | 键鼠事件监听（计数 + idle 检测） |
| `pystray` | Windows 托盘图标（暂停、隐私模式切换） |

---

## 输入 / 输出接口

**输入**
- Windows 窗口焦点变化事件
- 定时器 tick（1 秒粒度）
- 键鼠事件流（pynput）

**输出**
- SQLite：插入 `records`、`screenshots`
- 文件系统：写入 `screenshots/YYYY/MM/DD/<record_id>.png`、`thumbs/YYYY/MM/DD/<record_id>.jpg`
- 日志：结构化 jsonl（structlog）

---

## 截图触发策略

```
切窗事件触发 → 插入 record → 取消前一个截图 Task → 创建新的延迟 Task（switch_capture_delay_s）
    └─ 延迟结束后：验证 HWND 是否与预期一致？
         ├─ 不一致（用户已切走） → 放弃，不截图
         └─ 一致 → elapsed < min_capture_interval_s? → 跳过（速率保护）
               └─ 通过 → 截图、落库、标记 pending

心跳 tick（每秒轮询） → elapsed >= max_capture_interval_s? → 截图补帧
```

**关键设计：可取消 Task**
- 每次切窗先 `cancel()` 前一个待截图任务，再新建延迟任务
- 快速连续切换时，只有最终停留的窗口会触发截图
- 延迟截图不阻塞检测循环（Task 后台运行），HWND 校验防止截到错误窗口

参数建议见 [采集参数](../engineering/capture-params.md)。

---

## 轮询 vs 事件驱动

| 方式 | 响应延迟 | 实现复杂度 | 阶段 |
|------|---------|-----------|------|
| **轮询（默认）** | ≤ 1s | 低：`asyncio.sleep(1.0)` 循环调用 `get_active_window()` | Phase 1 |
| **WinEvent Hook** | 毫秒级 | 高：`SetWinEventHook(EVENT_SYSTEM_FOREGROUND)` + hook 回调线程 + asyncio 桥接 | Phase 2 可选 |

Phase 1 采用 1s 轮询：实现简单，对时间追踪/回放场景够用。WinEvent Hook 的优势（毫秒级响应）留到 Phase 2 评估，届时需处理 out-of-context hook 不注入目标进程地址空间等细节。

---

## 当前实现

`src/timetrace/capture/service.py`：

```python
class CaptureService:
    def __init__(self, ...):
        self._pending_screenshot_task: asyncio.Task | None = None

    async def run(self) -> None:
        while True:
            await self._tick()
            await asyncio.sleep(1.0)

    async def _tick(self) -> None:
        win = get_active_window()
        if window_switched:
            record_id = await self._db.insert_record(ctx, reason="switch")
            await self._cancel_pending_screenshot()   # 取消上一个
            self._pending_screenshot_task = asyncio.create_task(
                self._delayed_screenshot(record_id, win.hwnd)
            )

    async def _delayed_screenshot(self, record_id: str, expected_hwnd: int) -> None:
        await asyncio.sleep(self._cfg.switch_capture_delay_s)
        current_win = get_active_window()
        if current_win is None or current_win.hwnd != expected_hwnd:
            return  # 窗口已变，放弃
        if time.monotonic() - self._last_capture_ts < self._cfg.min_capture_interval_s:
            return  # 速率保护
        await self._save_screenshot(record_id, expected_hwnd, time.monotonic())
```

---

## 错误与重试策略

| 场景 | 处理方式 |
|------|---------|
| 截图失败（权限/瞬态） | 记录 `error_code`，跳过该帧，不阻塞采集主循环 |
| SQLite busy | 短退避重试（10–50ms 抖动，最多 3 次）；WAL 模式减少写冲突 |
| 采集主循环异常 | TaskGroup 捕获，日志记录，服务重启 |

---

## 性能预算

| 指标 | 目标 |
|------|------|
| CPU | 均值 < 1–2%（多数时间等待事件） |
| 内存 | < 80 MB（不缓存大图，写盘后立即释放） |
| I/O | 截图写盘为主要开销；通过 `min_capture_interval_s` 限制峰值 |

---

## 相关文档

- [采集参数推荐值](../engineering/capture-params.md)
- [隐私策略](../privacy/strategy.md)
- [Analysis Worker（消费 pending 任务）](analysis-worker.md)
- [存储 Schema（records 表）](../storage/schema.md)
