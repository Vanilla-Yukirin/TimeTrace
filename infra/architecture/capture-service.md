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
切窗事件触发 → 立即尝试截图
    └─ 距上次截图 < min_capture_interval_s? → 跳过（防抖）
    └─ 通过 → 截图、落库、标记 pending

心跳 tick（每秒） → 检查是否需要补帧
    └─ 距上次截图 < max_capture_interval_s? → 跳过
    └─ 通过 → 截图、落库、标记 pending
```

参数建议见 [采集参数](../engineering/capture-params.md)。

---

## 当前实现

`src/timetrace/capture/service.py`：

```python
class CaptureService:
    async def run(self) -> None:
        while True:
            await self._tick()
            await asyncio.sleep(1.0)

    async def _tick(self) -> None:
        ctx = self._get_active_context()
        if not should_capture(ctx, self._privacy):
            return
        elapsed = now - self._last_capture_ts
        if elapsed < self._cfg.min_capture_interval_s:
            return
        await self._maybe_capture(ctx, now, reason="heartbeat")
```

> **注意**：`_get_active_context()` 目前是桩代码（返回空字符串），真实实现需要 pywin32（Phase 1 开发任务）。

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
