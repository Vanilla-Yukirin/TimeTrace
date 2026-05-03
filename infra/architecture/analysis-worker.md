# Analysis Worker

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

- 从 SQLite 中原子 claim `pending_vlm` 任务（`UPDATE … WHERE … IN (SELECT … LIMIT 1) … RETURNING`）
- 启动 `vlm_concurrency` 个并发消费者协程，共享同一个 `Database` 与 `VLMHealthGate`
- 执行 VLM 结构化描述（`keywords` / `summary` / `description` 三字段），拼成 LIKE-friendly 文本写入 `analysis_results.vlm_desc`
- 失败按指数退避回 `pending_vlm`（retry_count++ / next_retry_at），超过 `max_retries` 转 `error_final`
- 跨 worker 共享熔断器：连续失败到阈值切 SLEEPING，靠心跳探活恢复 HEALTHY
- 镜像 `analysis_results.status` 到 `records.status`，避免 UI 上记录卡在 `pending_vlm` / `processing_vlm`

> **不含 embedding 生成**：文本语义检索走 VLM 描述 + （未来）FTS5 BM25（见 [相似检索层](../storage/vector-search.md)），视觉相似检索由采集侧的 pHash 给出，Worker 不承担向量化工作。

---

## 状态机

```
captured
  └─ mark_pending() ──────────────────── pending_vlm
                                              │
                                    claim → processing_vlm
                                              │
                                       VLM describe
                                              │
                                          vlm_done
                                              │
                                      classify / rules
                                              │
                                            done

VLM 调用失败：
  ├─ retry_count < max_retries
  │     → 回写 pending_vlm，retry_count++、next_retry_at = now + backoff
  │       （当前 claim 查询有 next_retry_at <= now 守卫，到点自动被重新 claim）
  │
  └─ retry_count >= max_retries / 不可恢复异常
        → error_final，记录 error_msg，并镜像到 records.status
```

`error_final` 不是独立的"死信"队列——状态名就是终结标识，配合 `records.status` 镜像让 UI / API 也能看到该记录已放弃。

---

## Claim 机制（原子性保证）

多 worker 并发下需要单语句完成"挑一行 + 抢占"，否则 SELECT-then-UPDATE 之间会有竞态。当前实现用 SQLite 3.35+ 的 `UPDATE…RETURNING` 单语句完成原子 claim：

```sql
-- database.py: claim_next_task(kind="pending_vlm")
UPDATE analysis_results
SET status = 'processing_vlm', locked_at = ?, updated_at = ?
WHERE record_id = (
    SELECT record_id FROM analysis_results
    WHERE status = 'pending_vlm'
      AND (next_retry_at IS NULL OR next_retry_at <= ?)
    ORDER BY updated_at ASC LIMIT 1
) AND status = 'pending_vlm'
RETURNING record_id, retry_count;
```

- 内层 SELECT 决定候选行，外层 `WHERE … AND status='pending_vlm'` 防住已被别的 worker 抢走的行
- 整个语句由 `Database._lock`（asyncio.Lock）串行化（aiosqlite 单连接共享，必须顺序提交）
- **超时回收**：`main.py::_reclaim_loop` 每 60s 把 `processing_*` 状态超过阈值的行回写 `pending_*`，防 Worker 崩溃后任务永久卡死
- **优先级**：当前按 `updated_at ASC` 公平消费；如需手动优先级可在 `ORDER BY` 加 `priority DESC`

---

## 错误 / 重试策略

实际逻辑见 [src/timetrace/worker/loop.py](../../src/timetrace/worker/loop.py)::`_handle_one` / `_fail`。

| 场景 | 状态变迁 | 备注 |
|------|---------|------|
| `VLMError`（网络 / 解析 / 字段缺失） | `report_failure` → `mark_error_retryable` 或 `mark_error_final` | 退避：`min(backoff_base_s * 2**(retry-1), backoff_max_s)`；默认 base=60s, max=600s, max_retries=5 |
| 跨 worker 连续失败到阈值 | 熔断器切 SLEEPING | 所有 worker `acquire()` 直接 False，`pending_vlm` 不被消费，等心跳恢复 |
| 图片读取失败（`Image.open` 抛异常） | 同上走 retry 链 | TODO：图像缺失类不可恢复错误应直转 `error_final`，不消耗 retry 配额 |
| `record vanished before VLM call` | `mark_error_final` | meta 查询拿不到记录，跳过重试 |
| 重试用尽 | `mark_error_final`（含 `max_retries_exceeded:` 前缀） | `records.status` 同步镜像 |

---

## 当前实现

[src/timetrace/worker/loop.py](../../src/timetrace/worker/loop.py)：

```python
class AnalysisWorker:
    async def run(self) -> None:
        if self._vlm is None or self._gate is None:
            # 没配 .env API key → 闲循环，pending_vlm 任务原地排队
            while True:
                await asyncio.sleep(_DISABLED_IDLE_INTERVAL_S)
        n = max(1, self._cfg.vlm_concurrency)
        await asyncio.gather(*(self._consume_loop(i) for i in range(n)))

    async def _consume_loop(self, worker_id: int) -> None:
        while True:
            if not await self._gate.acquire():        # SLEEPING → 等心跳恢复
                await asyncio.sleep(self._gate.probe_interval_s / 2)
                continue
            task = await self._db.claim_next_task(kind="pending_vlm")
            if task is None:
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue
            await self._handle_one(task, worker_id)   # describe → save → transition
```

`_handle_one` 拉 `get_record_meta`（取 `window_title` + 第一张未删除截图路径）→ `Image.open` → `vlm.describe(img, window_title=...)` → 成功调 `save_description(format_description(payload))` + `transition('vlm_done')`，失败走 `_fail` 进重试 / 终结分支。

---

## VLM 客户端

[src/timetrace/vlm/client.py](../../src/timetrace/vlm/client.py) 是一个对 OpenAI Chat Completions 协议的薄包装（`AsyncOpenAI`），可对接任何兼容端点（OpenAI、DashScope qwen 系、SiliconFlow、Ollama、自部署 vLLM…）。

```python
class VLMClient:
    async def describe(self, image: Image.Image, window_title: str | None = None) -> dict
    # 返回校验后的 {"keywords": list[str], "summary": str, "description": str}

    async def heartbeat(self) -> bool
    # 纯文本 "1+1=?" → 含 "2" 即视为存活
```

- `response_format={"type": "json_object"}` 强制 JSON 输出
- `extra_body={"enable_thinking": False}` 是**按需 opt-in** 的——vanilla `api.openai.com` 拒绝未知 body 字段会 HTTP 400；DashScope qwen / SiliconFlow Qwen3+ 默认 thinking=ON 需要这个开关。由 `TIMETRACE_VLM_DISABLE_THINKING` 控制
- prompt 里 `summary` / `description` 强制以**名词性短语开头**，禁止"该截图/这张图/画面显示/这是/正在…"等元叙述句式——目的是避免高频元叙述词把 BM25 / LIKE 通道里的真实关键词稀释掉

模型默认 `gpt-4o-mini`，全部三件套（`base_url` / `api_key` / `model` / `disable_thinking`）走 `.env`，见 [.env.example](../../.env.example)。

---

## 熔断器（VLMHealthGate）

[src/timetrace/vlm/health.py](../../src/timetrace/vlm/health.py) 跨所有 worker 共享，把瞬时网络抖动、模型限流、临时故障收敛为有限状态机：

```
HEALTHY ──── consecutive_failures >= fail_threshold (默认 3) ────► SLEEPING
   ▲                                                                  │
   │                                                                  ▼
   └──── consecutive_probe_successes >= recover_threshold (3) ◄── heartbeat (每 30s 一次)
```

- `acquire()` 在 SLEEPING 下立即返回 False，所有 worker 不再 claim 任务，避免雪崩
- 心跳由 `asyncio.Lock` + `next_probe_at` 时间戳保证**同一时刻只有一个 worker 真在探活**，其他并发 acquire 立即返回 False
- 心跳就是个最便宜的纯文本 chat（`1+1=?`），不带图，避免在故障期烧 token
- 切 SLEEPING 时把首次 probe 排到一个完整 interval 之后，避免立即对失败端点二连击

---

## 性能约束

- 核心约束：对用户交互无感（优先级低于采集服务）
- 并发：`vlm_concurrency` 默认 2（[WorkerConfig](../../src/timetrace/config.py)），受 API 限流与单帧 latency 共同制约
- 单帧 payload 在客户端做了**长边 1280px 上限 + JPEG q=80** 的压缩，控制上传体积与 token 成本
- 其他 worker 配置（默认值）：`max_retries=5`、`backoff_base_s=60`、`backoff_max_s=600`

---

## 相关文档

- [存储 Schema（analysis_results 表）](../storage/schema.md)
- [规则/反馈引擎](rule-engine.md)
- [相似检索层](../storage/vector-search.md)
- [开发路线图（Phase 1.5）](../overview/roadmap.md)
