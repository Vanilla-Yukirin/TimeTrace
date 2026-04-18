# SSE 实时推送方案 — 实现计划

## 目标

捕获服务每写入一条新记录，前端时间轴在 1 秒内自动更新，不依赖定时轮询。

---

## 整体数据流

```
CaptureService._save_screenshot()
    └─ await db.insert_record(...)
    └─ notify_event.set()          ← 新增：写完就通知

GET /v1/events (SSE 长连接)
    └─ 等待 notify_event
    └─ 推送 data: new\n\n          ← 立刻推

EventSource('/v1/events')
    └─ onmessage → queryClient.invalidateQueries(['records'])
    └─ React Query 触发 refetch → 时间轴更新
```

---

## 后端改动（3 处）

### 1. `src/timetrace/api/app.py` — 创建全局 Event 对象

在 app 创建时挂一个 `asyncio.Event` 到 `app.state`，供 capture service 和 SSE 端点共享。

```python
# app.py lifespan 或 startup 事件里
app.state.new_record_event = asyncio.Event()
```

**注意**：`asyncio.Event` 是非线程安全的，必须在同一个事件循环里使用。capture service 和 FastAPI 同在 `asyncio.TaskGroup` 里，所以天然共享同一个 loop，无需额外桥接。

---

### 2. `src/timetrace/capture/service.py` — 写完记录后 set()

在 `_save_screenshot` 末尾（`mark_pending` 之后）加一行通知。

```python
async def _save_screenshot(self, record_id, hwnd, now):
    # ... 现有逻辑 ...
    await self._db.mark_pending(record_id)
    if self._notify_event is not None:
        self._notify_event.set()   # 通知 SSE 端点有新数据
```

`CaptureService.__init__` 接收可选的 `notify_event: asyncio.Event | None = None`，由 `main.py` 注入。

---

### 3. `src/timetrace/api/routes/events.py` — 新增 SSE 端点

```python
from sse_starlette.sse import EventSourceResponse
import asyncio

@router.get("/events")
async def stream_events(request: Request):
    event: asyncio.Event = request.app.state.new_record_event

    async def generator():
        while True:
            if await request.is_disconnected():
                break
            # 等待新记录，最多 25 秒（心跳保活，防止代理超时断开）
            try:
                await asyncio.wait_for(asyncio.shield(event.wait()), timeout=25)
                event.clear()
                yield {"data": "new"}
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": ""}   # 心跳，保持连接

    return EventSourceResponse(generator())
```

**依赖**：`sse-starlette`（已是 FastAPI 生态常用包，`uv add sse-starlette`）。

---

## 前端改动（2 处）

### 1. `frontend/src/hooks/useRecordEvents.ts` — 新建 hook

```ts
import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'

export function useRecordEvents() {
  const qc = useQueryClient()
  useEffect(() => {
    const es = new EventSource('/v1/events')
    es.onmessage = () => {
      qc.invalidateQueries({ queryKey: ['records'] })
    }
    es.onerror = () => {
      // 浏览器会自动重连，不需要手动处理
    }
    return () => es.close()
  }, [qc])
}
```

### 2. `frontend/src/App.tsx` — 挂载一次

```tsx
export function App() {
  useRecordEvents()   // 全局挂一次，整个 session 有效
  return ( /* 现有 JSX */ )
}
```

**移除**：`useRecords.ts` 里的 `refetchInterval: 30_000`（SSE 接管实时刷新，轮询降级为兜底）。或者保留一个较长的 `refetchInterval: 120_000` 作为断线后的兜底。

---

## main.py 注入

```python
# 创建 Event，注入到 app.state 和 CaptureService
notify_event = asyncio.Event()
app.state.new_record_event = notify_event

capture_service = CaptureService(
    ...,
    notify_event=notify_event,
)
```

---

## 边界情况

| 场景 | 处理方式 |
|------|---------|
| 前端断线/刷新 | `EventSource` 浏览器自动重连，重连后立刻拿到最新数据（React Query 的 refetch） |
| 后端重启 | SSE 连接断开，浏览器重连，`refetchInterval` 兜底保证数据最终一致 |
| 多个浏览器标签页 | 每个 tab 各自一条 SSE 连接，独立 invalidate，互不影响 |
| Vite 代理 | `vite.config.ts` 已有 `/v1` → `8765` 代理，SSE 自动通过，无需额外配置 |
| 高频切窗（每秒多次） | `event.set()` 是幂等的，多次 set 只触发一次 clear+推送，不会 flood 前端 |

---

## 实现顺序

1. `uv add sse-starlette`
2. 后端：`app.py` 创建 Event → `service.py` 注入 + set() → `events.py` 新建端点 → `main.py` 串联
3. 前端：`useRecordEvents.ts` → `App.tsx` 挂载 → `useRecords.ts` 移除/调大 refetchInterval
4. 验证：切换窗口，观察前端时间轴是否在 1 秒内更新

---

## 不在本次范围内

- 记录详情的实时刷新（点开某条记录时 VLM 结果异步返回）— 可后续用同一 SSE 通道扩展 `event_type` 字段实现
- 多用户/多进程场景 — 当前单进程架构不需要考虑
