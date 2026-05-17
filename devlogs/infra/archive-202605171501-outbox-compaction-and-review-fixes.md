# Outbox compaction + review-driven 修复（capture helper / ctypes buffer / 类型收窄）

**日期：** 2026-05-17
**目标：** 把 P4 周边的几个稳定性短板补完：Outbox 不再无限增长、capture 失败半路不再炸整个循环、ctypes 长路径不再过早失败、bootstrap 类型签名收窄。

---

## 背景

- 双进程模式接线后 OutboxSender 一直 append-only 到 `log.jsonl`，永远不回收。kickoff devlog 里早就有 "N 天后过期 entry 应删除" 的承诺，但当时 P3a 没实装
- review 又抓出 4 个 nit：capture 的 idle_start 裸 await close_record 不一致 / outbox_backend `_read_path` 硬编码 kind / bootstrap extra_tasks 类型过宽 / ctypes buffer 一刀切 1024

一次 PR 收完，与隐私管线主体（P4 的 OCR 部分）解耦推进。

---

## 实现细节

### 1. Outbox compaction —— 关键 crash-safety 顺序

文件：`src/timetrace/client/core/outbox.py`

```python
async def compact(self, *, min_acked: int = 1) -> int:
    """Reclaim acked entries: rewrite log, drop blobs."""
    async with self._lock:
        entries = await asyncio.to_thread(self._read_log)
        acked = await asyncio.to_thread(self._read_acked)
        if acked < min_acked:
            return 0

        kept = entries[acked:]
        acked_blob_ids = [e["entry_id"] for e in entries[:acked]]

        # === 顺序载荷：state 写在前，log 写在后 ===
        await asyncio.to_thread(self._write_acked, 0)        # Step 1
        await asyncio.to_thread(self._rewrite_log, kept)     # Step 2

        # Step 3: 删旧 blob（best-effort）
        for entry_id in acked_blob_ids:
            for kind in ("image", "thumb"):
                (self._blobs_dir / f"{entry_id}-{kind}").unlink(missing_ok=True)

        return acked
```

**为什么是 state → log 而不是反过来**：

| 顺序 | crash 中间发生时 | 后果 |
|---|---|---|
| **state 先（当前选）** | log 仍有 `[acked + unacked]`，state 说 acked=0 → 整批 replay | 已 acked 的 entries 重发一次。server 端 `client_record_id` UNIQUE 索引兜底 → 200 OK with was_new=False → **零数据损失** |
| log 先（反向） | log 已变短只含 unacked，state 仍说 acked=N → 把 `[0..N]` 当已确认跳过 | 实际只 `len(new_log) - N` 个 unacked 能投递，**永久丢数据** |

at-least-once 是工程允许的（server 幂等），**silent loss 不可接受**。这条顺序是**载荷的**，注释里写了"do not flip"。

测试增量：`test_compact_survives_crash_between_state_and_log` 用 monkeypatch 替换 `_rewrite_log` 抛错，验证重启后 `pending_count == 2`（含已 acked 的 entry_a 重新待发）。

### 2. OutboxSender 集成 inline compaction

文件：`src/timetrace/client/core/outbox_sender.py`

```python
def __init__(self, ..., compact_every_n_acks: int = 200):
    self._compact_every_n_acks = compact_every_n_acks
    self._acks_since_compact = 0

async def _drain_pending(self, stop_event):
    async for entry in self._outbox.iter_pending():
        ...
        await self._outbox.ack_next(entry.entry_id)
        sent += 1
        self._acks_since_compact += 1
        if (self._compact_every_n_acks > 0
                and self._acks_since_compact >= self._compact_every_n_acks):
            reclaimed = await self._outbox.compact()
            ...
            self._acks_since_compact = 0
```

**N=200 选型**：典型 capture 速率 ≈ 1 entry/窗口切换。200 acks ≈ 几小时正常使用，远低于任何磁盘压力阈值，但能 amortise rewrite 成本。**N=0 显式关 compaction**（unit test 用）。

**inline 选择 + 复用既有 asyncio.Lock**：不另起 task，不引新竞态。同一 lock 域内顺序 ack → 计数 → compact。

### 3. CaptureService `_safe_close_record` helper

文件：`src/timetrace/client/capture/service.py`

原来 4 处调用点（shutdown / idle_start / window_switch / heartbeat）各自重复 try/except + logger.warning。其中 **idle_start 那处之前裸 await close_record，没 try/except，失败会杀整个 capture 循环** —— 与其它 3 处行为不一致。

```python
async def _safe_close_record(self, record_id: str, *, where: str) -> None:
    try:
        await self._backend.close_record(record_id)
    except Exception:
        logger.warning("capture.close_record_failed", where=where, record_id=record_id, exc_info=True)
```

4 处全替换为 `await self._safe_close_record(rid, where="...")`，统一吞错 + structured log key 定位调用点。

### 4. ctypes QueryFullProcessImageNameW 两段式 buffer

文件：`src/timetrace/client/capture/window.py`

原版固定 1024 wchars。Windows extended-path / WSL bridge / 深嵌套 mount 理论上能超过。改成：

```python
_ERROR_INSUFFICIENT_BUFFER = 122

def _query_full_process_image_name(handle: int) -> str:
    for buf_size_value in (1024, 32 * 1024):
        buf_size = wintypes.DWORD(buf_size_value)
        buf = ctypes.create_unicode_buffer(buf_size.value)
        if _QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_size)):
            return buf.value
        err = ctypes.get_last_error()
        if err != _ERROR_INSUFFICIENT_BUFFER:
            raise OSError(err, "QueryFullProcessImageNameW failed")
    raise OSError(_ERROR_INSUFFICIENT_BUFFER,
                  "QueryFullProcessImageNameW: path exceeds 32k wchars")
```

32K 是 Windows long-path 上限，cover 全场景。

### 5. OutboxBackend `_read_path` kind 参数 + bootstrap extra_tasks 类型收窄

- `_read_path(raw, kind="screenshot")` → `_read_path(raw, *, kind)`。`submit_screenshot` thumb 路径传 `kind="thumb"`，traversal guard 报错信息与 HttpBackend wire-side 对称
- `bootstrap.serve` 的 `extra_tasks: dict[str, Awaitable[None]]` → `dict[str, Coroutine[Any, Any, None]]`。`tg.create_task` 实际只接 coroutine，更宽的 `Awaitable` 让 future / 自定义 `__await__` 对象到 runtime 才 raise；类型层早 fail

---

## 测试增量

| 文件 | 用例数变化 |
|---|---|
| `test_outbox.py` | 14 → 21 (+7 compact noop / min_acked 跳过 / 删 acked blob 保留 unacked / 后续可继续 ack / FIFO ordering 保持 / 关键 mid-compact crash recovery) |
| `test_outbox_sender.py` | 8 → 10 (+2 threshold 触发 / threshold=0 关闭) |

测试 252 → 261 passed (+9)，ruff 全绿。

---

## 知识清单

- **at-least-once + idempotent server > silent loss**：客户端 outbox 类型系统选 at-least-once（接受重发），server 端用 UNIQUE 索引兜底幂等。两侧合在一起 = 一致性 + 可恢复性都保住
- **关键写顺序写在 docstring 里 + "do not flip"**：crash-safety 论据看似显然，半年后回头看就不是。把 reasoning 沉进代码注释，不只在 commit message
- **Capture loop 错误统一吞**：每个 try/except 自身没问题，但 4 处分散写就会有"这处忘记 try" 的概率。helper 化是单一致性源
- **ctypes Windows API 全场景 buffer**：先 MAX_PATH 级（1024 wchars），失败时 fallback 到 long-path（32k），既不浪费内存又不在罕见路径上挂

---

## 相关 commit

| Hash | 作用 |
|---|---|
| `4e5c589` | _safe_close_record + 3 review nit |
| `548b0de` | Outbox compaction |
