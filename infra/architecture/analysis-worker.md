# Analysis Worker

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

- 从 SQLite 中原子 claim `pending_vlm` 任务
- 执行 VLM 描述（20–50 字）、embedding 生成、基础分类建议
- 写回 `analysis_results`、`embeddings`（或向量层）并更新 `records.status`
- 失败任务按策略重试 / 降级 / 入死信（dead-letter）

---

## 状态机

```
captured
  └─ mark_pending() ──────────────────── pending_vlm
                                              │
                                    claim + VLM describe
                                              │
                                          vlm_done
                                              │
                                    embedding generation
                                              │
                                          embed_done
                                              │
                                      classify / rules
                                              │
                                            done

任意阶段失败：
  ├─ error_retryable  (retry_count / next_retry_at)
  └─ error_final      (不再重试，保留错误原因)
```

---

## Claim 机制（原子性保证）

```python
# database.py: claim_next_task()
SELECT record_id FROM analysis_results
WHERE status = 'pending_vlm'
  AND (next_retry_at IS NULL OR next_retry_at <= now)
ORDER BY updated_at ASC
LIMIT 1

# 原子更新为 processing_vlm，记录 locked_at
UPDATE analysis_results
SET status = 'processing_vlm', locked_at = now
WHERE record_id = ?
```

- **超时回收**：`processing_*` 超过阈值时，后台定期将其重置为 `pending_*`，防止 Worker 崩溃后任务永久卡死。
- **优先级**：可在 `ORDER BY` 中加 `priority DESC` 支持手动触发任务优先。

---

## 错误 / 重试策略

| 场景 | 策略 |
|------|------|
| 云端模型 HTTP 失败 | 指数退避 + 抖动；达到最大次数转 `error_final`，保留错误原因 |
| 429 / 限流 | 更长退避；降低并发上限 |
| 图片文件缺失 | 标记 `missing_asset`，不重试；保留 records 元信息 |
| 不可恢复异常 | 标记 `error_final`；写入 `error_msg` |

---

## 当前实现

`src/timetrace/worker/loop.py`：

```python
class AnalysisWorker:
    async def run(self) -> None:
        while True:
            processed = await self._process_next()
            if not processed:
                await asyncio.sleep(0.2)  # 无任务时轮询间隔

    async def _process_next(self) -> bool:
        task = await self._db.claim_next_task(kind="pending_vlm")
        if task is None:
            return False
        try:
            desc = await self._describe(task)
            await self._db.save_description(task["record_id"], desc)
            await self._db.transition(task["record_id"], "vlm_done")
        except Exception as exc:
            await self._db.mark_error_final(task["record_id"], str(exc))
        return True
```

> **注意**：`_describe()` 目前是桩代码，返回占位字符串。真实实现（调用 OpenAI / 本地模型）在 Phase 1.5 开发。

---

## Provider 抽象（Phase 1.5）

Worker 通过 `Provider` Protocol 与模型交互，不绑定具体厂商：

```python
class Provider(Protocol):
    async def vlm_describe(self, image_path: str, title: str) -> str: ...
    async def embed(self, text: str) -> list[float]: ...
```

默认实现对接 **OpenAI 兼容协议**（`POST /v1/chat/completions` + `POST /v1/embeddings`），可通过配置切换到任何兼容端点（OpenAI、Azure、本地 Ollama 等）。

---

## 性能约束

- Worker 不限制 CPU 峰值，但需通过**并发上限**（semaphore）避免常驻 CPU 升高
- 核心约束：对用户交互无感（优先级低于采集服务）
- 并发建议：
  - **VLM 推理**：`vlm_concurrency = 1–2`（API 限流 + 单卡显存限制）
  - **Embedding 生成**：`embed_concurrency = 2–4`（批量化，比 VLM 更轻量）
  - Phase 1.5 初始以 1 并发启动，按实际 API 限速和机器负载调整

---

## 相关文档

- [存储 Schema（analysis_results 表）](../storage/schema.md)
- [规则/反馈引擎](rule-engine.md)
- [向量检索层](../storage/vector-search.md)
- [开发路线图（Phase 1.5）](../overview/roadmap.md)
