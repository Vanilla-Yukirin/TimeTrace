# 进程退出挂死排查与修复（Ctrl+C & 托盘退出）

**日期：** 2026-04-18  
**目标：** 彻底修复 TimeTrace 在 Ctrl+C 与托盘右键退出两种场景下的进程挂死问题，使两条路径均能干净退出。

---

## 背景

TimeTrace 是一个以 `asyncio.TaskGroup` 并发运行 capture / worker / api / quit_watcher / reclaim 五个任务的 Python 应用。进程退出有两条入口：

1. **托盘退出**：pystray 线程 `on_quit()` → `loop.call_soon_threadsafe(quit_event.set)` → `_watch_quit()` 取消任务 → TaskGroup 正常退出
2. **Ctrl+C**：原本期望 `KeyboardInterrupt` 被捕获后走 `finally` 清理，但一直挂死

上一个会话已修复过一次（`_cancel_pending_screenshot` 用 `asyncio.wait` 替代无限 await），托盘退出验证通过，但 Ctrl+C 始终无法干净退出。

---

## 操作步骤

### 1. 第一轮修复：`finally` 块 cancel + gather（失败）

在 `main()` 的 `except (KeyboardInterrupt, SystemExit)` 后的 `finally` 中，取消所有 pending tasks 并 `loop.run_until_complete(asyncio.gather(...))` 等待清理。

**结果**：进程仍然挂死。日志显示 `tray.quit_requested` 出现后 `loop_closed=True`，`call_soon_threadsafe` 报 `RuntimeError: Event loop is closed`。

### 2. 第二轮修复：`shutdown_default_executor()`（失败）

在 `finally` 的 `loop.close()` 前加 `loop.run_until_complete(loop.shutdown_default_executor())`，期望等待截图 executor 线程结束。

**结果**：立即挂死无法启动。原因：`idle.start()` 当时是用 `run_in_executor(None, ...)` 运行的，pynput listener 线程在 default executor 里阻塞，`shutdown_default_executor()` 永远等不到它结束。

> 此时发现 `idle.start()` 放 executor 是错的，改为 `threading.Thread(daemon=True)` 启动，同时去掉 `shutdown_default_executor()`。

### 3. 第三轮修复：pynput listener daemon=True（部分有效）

**分析**：pynput `keyboard.Listener` / `mouse.Listener` 继承 `threading.Thread`，默认 daemon=False。进程退出时 Python 会等所有非 daemon 线程结束。listener 线程永远不会自己结束（等待键鼠事件），导致进程挂死。

**修改**：`idle.py` 在 `start()` 里 `self._kb_listener.daemon = True` 和 `self._ms_listener.daemon = True`（在 `.start()` 之前设置）。

**结果**：不见改善。根本原因还没找到。

### 4. 加诊断日志定位根因（关键发现）

在 `_on_quit()`、`_watch_quit()` 中增加日志，记录 `loop.is_running()` 和 `loop.is_closed()`，以及托盘退出请求是否成功调度。

**关键日志对比**：

| 场景 | loop_running | loop_closed | 结果 |
|------|-------------|-------------|------|
| 托盘退出 | True | False | `_on_quit_scheduled` → `main.stop_requested` → `main.shutdown_complete` ✓ |
| Ctrl+C 后托盘退出 | False | True | `main._on_quit_failed: Event loop is closed` × |

**结论**：Ctrl+C 触发后，event loop 已经走到 `loop.close()` 然后挂在非 daemon 线程上。此时 loop 关闭，托盘退出无法注入 `quit_event.set()`。

### 5. 根因分析

```
Ctrl+C
  → Python 抛 KeyboardInterrupt 到 loop._run_once()
  → except (KeyboardInterrupt, SystemExit): pass  ← 捕获
  → finally:
      gather(pending, ...) 运行清理
        → uvicorn capture_signals().__exit__ 调用 signal.raise_signal(SIGINT)
        → 第二次 KeyboardInterrupt，gather 被打断
      → loop.close()  ← loop 关闭，但非 daemon 线程仍活着
  → 非 daemon 线程（uvicorn 内部 / screenshot executor）撑住进程
  → 托盘退出注入失败（loop 已关闭）
  → 永久挂死
```

另一个根因：原代码使用 `except* (KeyboardInterrupt, SystemExit)` — 这是 Python 3.11+ 的异常组语法，**不捕获裸 `KeyboardInterrupt`**，只匹配 `ExceptionGroup`。

### 6. 最终修复：SIGINT 走托盘退出同一路径

**核心思路**：不让 Ctrl+C 抛异常，而是用 `signal.signal(SIGINT, ...)` 拦截，路由到和托盘退出完全相同的 `_request_quit()` 路径。

```python
# main.py
import signal

def _request_quit() -> None:
    try:
        loop.call_soon_threadsafe(quit_event.set)
    except RuntimeError:
        pass

# 拦截 SIGINT，走和托盘退出完全相同的优雅关闭路径
signal.signal(signal.SIGINT, lambda sig, frame: _request_quit())

start_tray_thread(config.privacy, _request_quit)  # 托盘退出也用同一函数
```

关闭路径统一为：

```
Ctrl+C 或 托盘退出
  → _request_quit()
  → loop.call_soon_threadsafe(quit_event.set)
  → _watch_quit() 唤醒
  → server.should_exit = True
  → cancel capture / worker / reclaim
  → TaskGroup 正常退出（uvicorn 通过 should_exit 干净退出，无 signal.raise_signal）
  → db.close()
  → main.shutdown_complete
  → loop.close()
  → main() 正常返回
  → pynput listener (daemon) 自动终止
  → 进程退出 ✓
```

**验证**：两次测试均输出 `main.stop_requested` → `main.shutdown_complete`，进程干净退出。

---

## 修改文件汇总

| 文件 | 修改内容 |
|------|---------|
| `src/timetrace/main.py` | 新增 `import signal`；`_on_quit` → `_request_quit`；`signal.signal(SIGINT, ...)` 拦截 Ctrl+C；`except*` → `except` |
| `src/timetrace/capture/service.py` | `run_in_executor(None, self._idle.start)` → `threading.Thread(daemon=True).start()`（idle.start 非阻塞，不需要 await） |
| `src/timetrace/capture/idle.py` | pynput listener 在 `.start()` 前设 `daemon=True`，确保进程退出时自动终止 |

---

## 遇到的问题与解决

### 问题1：`except*` 不捕获裸 `KeyboardInterrupt`

**现象**：`except* (KeyboardInterrupt, SystemExit)` 写了等于没写。  
**原因**：`except*` 是 ExceptionGroup 语法（PEP 654），只匹配 `ExceptionGroup` 实例，裸异常不命中。  
**解决**：改为 `except (KeyboardInterrupt, SystemExit)`。

### 问题2：uvicorn 在 finally gather 中二次抛出 SIGINT

**现象**：gather 清理过程中 uvicorn 的 `capture_signals().__exit__` 调用 `signal.raise_signal(captured_signal)` 产生第二次 `KeyboardInterrupt`，打断 gather，部分任务未完成清理。  
**解决**：根本上绕过这个问题——Ctrl+C 不再走 gather 清理路径，而是通过 `quit_event` 让 uvicorn 通过 `should_exit=True` 自己退出，`capture_signals` 的 exit 路径不再触发 `raise_signal`。

### 问题3：`shutdown_default_executor()` 挂死

**现象**：pynput listener 通过 `run_in_executor(None, idle.start)` 启动，`idle.start()` 实际上是快速返回的（启动 listener 线程后立即返回），但 executor 线程池里的 idle.start worker 线程会随 listener 一起在后台运行。`shutdown_default_executor()` 会等待所有 executor 线程，其中包括阻塞等待 pynput 事件的 listener 线程，导致永远等待。  
**解决**：去掉 `shutdown_default_executor()`；`idle.start()` 改用显式 daemon 线程启动（非 executor），不污染 default executor 的线程池。

---

## 知识清单

1. **`except*` vs `except`**：`except* E` 只匹配 `ExceptionGroup`，不捕获裸异常。在同一 `try` 块中不能混用 `except` 和 `except*`。

2. **uvicorn `capture_signals()`**：uvicorn 在 `serve()` 中通过上下文管理器接管信号处理。`__exit__` 时会调用 `signal.raise_signal(captured_signal)` 重新抛出被捕获的信号。如果此时在 `asyncio.gather()` 中，会产生第二次异常打断 gather。绕过方式：通过 `server.should_exit = True` 让 uvicorn 自己退出，避免进入 `raise_signal` 路径。

3. **asyncio + 多线程退出**：Python 进程在主线程结束后会等待所有非 daemon 线程。`daemon=True` 的线程在进程退出时被强杀。pynput listener 必须设为 daemon，否则会撑住进程。

4. **`signal.signal(SIGINT, handler)` 在 asyncio 中**：在 `loop.run_until_complete()` 运行期间，Python 信号在主线程的 bytecode checkpoint 处被处理。handler 中调用 `loop.call_soon_threadsafe()` 是安全的——它只写 self-pipe 唤醒 loop，不做阻塞操作。

5. **统一退出入口原则**：多路退出信号（Ctrl+C、托盘、SIGTERM 等）应路由到同一个 `quit_event.set()` 函数，而不是各自走不同的清理路径，避免路径差异带来的 bug。

6. **`call_soon_threadsafe` vs `call_soon`**：`call_soon_threadsafe` 是线程安全的，会写 self-pipe 唤醒阻塞中的 loop；`call_soon` 不是线程安全的，只能在 loop 线程内调用。两者都能在 loop 线程内调用，`call_soon_threadsafe` 只是多了一次 pipe 写，开销极小。

---

## 待办 / 遗留

- [ ] **后台静默运行**：目前仍依赖终端窗口。计划用 `pythonw` + 日志写文件实现无窗口运行（用户已确认待后续 Phase 处理）
- [ ] **app_name 全部显示 Unknown**：`_get_process_info` 的 `GetFileVersionInfo` fallback 对部分进程无效，需要补充 win32 API 调用方式
- [ ] **Phase B frontend**：B4 加载/错误/空态骨架屏、B6 生产构建 + FastAPI 挂载 dist + SPA fallback、B7 视觉打磨
