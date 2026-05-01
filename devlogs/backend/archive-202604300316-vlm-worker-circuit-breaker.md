# VLM 接入与多并发熔断 Worker 实装

**日期：** 2026-04-30
**目标：** 把 Phase 1.5 的 VLM 真接入（OpenAI 协议、`.env` 配置），并把 Worker 从单循环升级为可重试 / 可熔断 / 多并发的稳定形态；同时修掉 SQLite commit race 与若干语义通道相关的运行时崩溃。

---

## 背景

上一轮 pHash 视觉路与 SearchPage 已上线，但语义通道空跑：

- `worker/loop.py::_describe()` 写占位字符串
- `api/routes/search.py::_describe_image()` 直接返回 None
- `analysis_results.vlm_desc` 全为 NULL

本轮把 VLM 真接入，并顺势把 Worker 升级为成熟形态：

- VLM 走纯 OpenAI 协议（`AsyncOpenAI` SDK），通过 `.env` 自填 key
- `extra_body={"enable_thinking": False}` 走 opt-in 模式（DashScope qwen / SiliconFlow Qwen3+ 等默认 thinking 开启的端点才需要；vanilla OpenAI 会 400 拒绝）
- 三段式结构化描述：`keywords`（数组）、`summary`（短摘要）、`description`（完整描述）
- Worker：`vlm_concurrency` 个并发消费者 + 全局 `VLMHealthGate` 熔断 + 自动指数退避重试

不在本轮：FTS5、OCR、SettingsPage 暴露 VLM 配置。

---

## 操作步骤

### 1. 新建 VLM 模块（client + health）

**[src/timetrace/vlm/client.py](../../src/timetrace/vlm/client.py)**

- `@dataclass VLMConfig`：`base_url` / `api_key` / `model` / `disable_thinking`，`from_env()` 读 4 个 `TIMETRACE_VLM_*` 环境变量
- `VLMClient.describe(image, window_title=None) -> dict`：JPEG 编码 → base64 → data URL → `chat.completions.create(response_format={"type":"json_object"})`，校验三字段
- `VLMClient.heartbeat() -> bool`：发 `"1+1=? 直接给出数字答案，不要解释。"` 纯文本，answer 含 `"2"` 即视作存活
- `_extra_kwargs()`：仅当 `cfg.disable_thinking=True` 时返回 `{"extra_body": {"enable_thinking": False}}`
- `format_description(d)`：`{description}\n\n摘要：{summary}\n\n关键词：{kws joined by 、}`

**[src/timetrace/vlm/health.py](../../src/timetrace/vlm/health.py)**

`VLMHealthGate` 状态机：

- 状态：`HEALTHY ↔ SLEEPING`
- HEALTHY 下连续 `fail_threshold=3` 次失败 → 切 SLEEPING
- SLEEPING 下：`acquire()` 走 `asyncio.Lock` + `next_probe_at` cooldown，确保**多 worker 并发时只有单个协程真正发心跳**
- 连续 `recover_threshold=3` 次心跳成功 → 切回 HEALTHY

### 2. Worker 重写（`AnalysisWorker`）

**[src/timetrace/worker/loop.py](../../src/timetrace/worker/loop.py)**

- `__init__(db, vlm, gate, cfg, storage_cfg)`：可选 VLM/gate/cfg
- `run()`：`vlm is None` → idle 不消费；否则 `asyncio.gather(*(_consume_loop(i) for i in range(cfg.vlm_concurrency)))`
- `_consume_loop`：`gate.acquire() → claim_next_task → _handle_one`
- `_handle_one`：成功 `save_description + transition('vlm_done') + gate.report_success()`；失败 → `gate.report_failure()` → `_fail()`
- `_fail()`：`retry_count >= max_retries` → `mark_error_final`；否则 `mark_error_retryable(retry_count+1, next_retry_at)` 退回 pending；退避公式 `min(60·2^(n-1), 600)` 秒

### 3. Database 加全局 `asyncio.Lock`

**[src/timetrace/storage/database.py](../../src/timetrace/storage/database.py)**

- 新增 `self._lock = asyncio.Lock()`，公开方法 `lock` property 给外部直接走 `db.conn` 的调用方使用
- 所有公开方法都包 `async with self._lock:`
- `_get_screenshots_for_record_unlocked`：私有内部方法（持锁前提），用于 `get_record_by_id` 内部复用避免锁重入死锁
- 公开方法 `get_screenshots_for_record` 仍保留（自己加锁）

补齐 lock 的外部调用点：
- [src/timetrace/api/routes/feedback.py](../../src/timetrace/api/routes/feedback.py)
- [src/timetrace/api/routes/search.py](../../src/timetrace/api/routes/search.py)（`_bm25_search` / `_like_fallback`）
- [src/timetrace/mcp_layer/tools.py](../../src/timetrace/mcp_layer/tools.py)
- [src/timetrace/phash_index/index.py](../../src/timetrace/phash_index/index.py)

### 4. claim_next_task 原子化

旧版本是 SELECT + UPDATE 两步，多 worker 下有 race。改成单条：

```sql
UPDATE analysis_results
SET status = ?, locked_at = ?, updated_at = ?
WHERE record_id = (
    SELECT record_id FROM analysis_results
    WHERE status = ? AND (next_retry_at IS NULL OR next_retry_at <= ?)
    ORDER BY updated_at ASC LIMIT 1
) AND status = ?
RETURNING record_id, retry_count
```

并把 in-flight 状态从 `processing_<kind>`（旧：`processing_pending_vlm`）改为 `processing_<base>`（新：`processing_vlm`，`base = kind.removeprefix("pending_")`）；这样 `reclaim_stale_tasks` 反向映射时不会拼出 `pending_pending_vlm` 的死状态。

新增 `_processing_to_pending(status)` helper：兼容**新（`processing_vlm`）+ 老（`processing_pending_vlm`）**两种 in-flight 写法，老库不会被永久孤儿。

### 5. mark_error_final / mark_error_retryable 同步 records.status

review 中点出 `mark_error_final` 只动 `analysis_results.status`，导致 UI（驱动自 `records.status`）永远停留在 pending_vlm。修复：两表同时更新，并清掉 `locked_at`。

### 6. main.py：load_dotenv + 实例化 + try/finally 清理

**[src/timetrace/main.py](../../src/timetrace/main.py)**

```python
from dotenv import load_dotenv
load_dotenv()  # 必须在 AppConfig() 之前，让 VLMConfig.from_env() 看到值

# ...
async with asyncio.TaskGroup() as tg: ...
finally:
    if vlm_client:
        try: await vlm_client.aclose()
        except Exception: logger.warning("vlm.aclose_failed", exc_info=True)
    try: await db.close()
    except Exception: logger.warning("db.close_failed", exc_info=True)
```

`finally` + 独立 try/except 是必要的：TaskGroup 抛 `ExceptionGroup` 时旧版 `await db.close()` 在 `finally` 外，会被跳过。

### 7. search route 接入真 VLM

**[src/timetrace/api/routes/search.py](../../src/timetrace/api/routes/search.py)**

- 删除本地 `_describe_image` stub
- 取 `request.app.state.vlm_client`，对每张上传图调 `vlm_client.describe(img, window_title=uf.filename)`
- `VLMError` → log info + 跳过；其它 → log warning + 跳过；都失败 → `semantic_status = "unavailable"`

### 8. .env 与依赖

- 新增 [.env.example](../../.env.example)：`TIMETRACE_VLM_BASE_URL` / `TIMETRACE_VLM_API_KEY` / `TIMETRACE_VLM_MODEL` / `TIMETRACE_VLM_DISABLE_THINKING`
- [pyproject.toml](../../pyproject.toml)：`openai>=1.30.0` 从 `[project.optional-dependencies] analysis` 移到 `dependencies`；新增 `python-dotenv>=1.0.0`

### 9. 测试

- [tests/test_vlm.py](../../tests/test_vlm.py)：23 个 case，覆盖 `VLMConfig.from_env`、`format_description`、`VLMClient.describe/heartbeat`（mock create）、`VLMHealthGate` 全状态机
- [tests/test_vlm_smoke.py](../../tests/test_vlm_smoke.py)：5 个联网 smoke，`pytest.mark.skipif(not TIMETRACE_VLM_API_KEY)`，`uv run pytest` 默认自动跳
- [tests/test_worker_pipeline.py](../../tests/test_worker_pipeline.py)：success / retry / error_final / 无截图跳过 / 并发 atomic claim / claim 返回 retry_count（共 6 个）
- [tests/test_storage.py](../../tests/test_storage.py)：补 `mark_error_final` 镜像 `records.status` 断言、补 `processing_pending_vlm` 旧状态恢复测试
- [tests/test_search.py](../../tests/test_search.py)：补 `_StubVLMClient` + 语义通道命中测试

最终：92 / 92 全绿，ruff check + format 干净。

### 10. 运行期 bug 修复

按用户上报的崩溃日志依次修：

#### 10a. SQLite `cannot commit transaction - SQL statements in progress`

aiosqlite 用单连接，并发的 capture / multi-worker / API 在同连接上 commit 会互相打架。修复：步骤 3 的全局 lock。

#### 10b. pydantic `invalid utf-8 sequence`

API 返回 `screenshots.*` 时，新增的 `phash BLOB` 列被一起序列化，pydantic JSON 编码 binary 失败。修复：`_get_screenshots_for_record_unlocked` 改为显式列名 SELECT，排除 `phash`。

#### 10c. 半开区间 ts_range 被默默丢弃

`(start, end)` 任一为 None 时 search 走全量。修复：`(start or 0, end or 2**62)`。

但这导致 PHashIndex 用 `range(start, end)` 迭代爆栈（5e13 个值）。再修：改为遍历 `self._buckets` 的现有 keys 而非区间内每个 bucket key。

### 11. Review 反馈与修复

收到 r-c 评审，关键修复：

- **claim/reclaim 命名死结**（P1）：`pending_<base>` ↔ `processing_<base>` 改写，`_processing_to_pending` 处理新老两套
- **extra_body 无脑发 400**（P1）：改 opt-in；`disable_thinking` 默认 False；测试断言"默认不带 extra_body / 配置打开才带"
- **mark_error_final 不同步 records**（P2）：两表 + 清 locked_at
- **TaskGroup finally 跳过**（P3）：try/finally 包住 + 独立 try/except 包 aclose
- **smoke 测试硬编码 extra_body**：改 `**client._extra_kwargs()`
- **client.py docstring 与新行为不符**：重写顶部文档块

### 12. 提交

commit 消息（用户采用建议）：

```
feat(vlm): 接入 VLM 与并发熔断 worker

1、新增 vlm/client + vlm/health：OpenAI 协议、可选 thinking-off、heartbeat 心跳
2、worker 重写为 N 并发消费者，含指数退避重试、最大重试 + 跨 worker 熔断
3、database 全局 asyncio.Lock 修复 commit race，原子化 claim_next_task
4、search 语义通道接入真 VLM，main.py 在 AppConfig 前 load_dotenv
```

commit hash: `06e6156`。

### 13. 收尾：补 .gitignore

提交后用户发现 `git status` 仍有 `AGENTS.md` 与 `frontend-dist/` 两个 untracked 项。

- `AGENTS.md`：review 时点出"内容已过期+与 CLAUDE.md 重复"，故意未加入 commit，等用户决定（删除 / 同步 / 改产物）。
- `frontend-dist/`：先前我误读了 `git check-ignore -v` 输出（pattern 列为空，对应 .gitignore 第 203 行实际是空行），以为已被忽略。实际 .gitignore 中根本没有 `frontend-dist` 这一项。

修复：在 [.gitignore](../../.gitignore) 末尾追加：

```
# Frontend build output
frontend-dist/
```

---

## 遇到的问题与解决

### 问题 1：SQLite 多协程 commit 互殴

**现象：** 启动跑十几秒后随机崩溃 `OperationalError: cannot commit transaction - SQL statements in progress`。

**原因：** aiosqlite 设计上是单连接 + 后台线程串行化 SQL，但多个协程同时持有 cursor + 调 commit 时，commit 会卡在"还有别的 cursor 没关"上。Capture / N 个 worker / API 三路并发会触发。

**解决：** Database 加全局 `asyncio.Lock`，所有公开方法 `async with self._lock:` 包起来；外部直接读 `db.conn` 的调用点（4 处）也通过 `db.lock` property 显式加锁。读吞吐有损耗但 TimeTrace 写频率低（几次/秒），实测无感。

### 问题 2：claim 与 reclaim 状态名错位导致永久孤儿

**现象：** 旧 build 失败后留下的 `processing_pending_vlm` 状态行，下次 reclaim 用 `replace("processing_", "pending_", 1)` 拼出 `pending_pending_vlm`，新 build 的 `claim_next_task("pending_vlm")` 永远找不到。

**解决：** 两件事一起做：
1. 新 claim 写 `processing_<base>`（去掉 `pending_` 前缀），所以 reclaim 拼出来一定是 `pending_<base>`
2. 加 `_processing_to_pending` helper 兼容两种历史前缀；reclaim 调用它而不是 naive replace

### 问题 3：extra_body 在 vanilla OpenAI 上 400

**现象：** smoke test 配 `https://api.openai.com/v1` + `gpt-4o-mini` 时直接 400 `unknown parameter: extra_body.enable_thinking`。

**原因：** OpenAI 严格校验 body 参数，不像 DashScope/SiliconFlow 那样宽松转发未知字段。

**解决：** `disable_thinking` 改 opt-in（默认 False）；`_extra_kwargs()` 仅在 True 时返回 `extra_body` dict。`.env.example` 在该项注释里写明用法。

### 问题 4：phash BLOB 让 pydantic 序列化崩溃

**现象：** `/v1/records/{id}` 返回 500，错误是 `PydanticSerializationError: invalid utf-8 sequence`。

**原因：** screenshots 表新增 `phash BLOB` 列，`SELECT *` 把 8 字节二进制带进了响应字典，pydantic JSON 编码不接受非 UTF-8 binary。

**解决：** `_get_screenshots_for_record_unlocked` 改为显式列名 SELECT，排除 `phash`。

### 问题 5：误读 git check-ignore 输出

**现象：** 执行 `git check-ignore -v frontend-dist/`，输出 `.gitignore:203: frontend-dist/`，我以为已被忽略。

**真相：** `git check-ignore -v` 格式是 `<source>:<linenum>:<pattern><TAB><pathname>`。我看到的"pattern"列其实是空（第 203 行就是空行），输出根本没有真匹配。`grep frontend-dist .gitignore` 返回零结果才是权威信息。

**解决：** 给 .gitignore 真加上规则。

---

## 知识清单

### aiosqlite 单连接的并发模式

- 默认单连接，靠后台线程串行化 SQL
- 多协程在同一连接上 commit 会互相打架（"SQL statements in progress"）
- 简单解：全局 `asyncio.Lock`；复杂解：每协程独立连接 + WAL 模式
- TimeTrace 选简单解，吞吐够用

### 原子化 claim（SQLite 3.35+）

```sql
UPDATE table SET status = 'processing_x', locked_at = ?
WHERE record_id = (SELECT record_id FROM ... LIMIT 1)
  AND status = 'pending_x'
RETURNING record_id, retry_count
```

`UPDATE ... WHERE ... IN (SELECT ... LIMIT 1) ... RETURNING` 比"SELECT 然后 UPDATE 老 id"少一次 race window；多 worker 并发时只有一个 UPDATE 能命中。

### 熔断器单协程探活

```python
async def acquire(self):
    async with self._lock:
        if state == HEALTHY: return True
        now = clock()
        if now < next_probe_at: return False  # cooldown
        next_probe_at = now + interval
    # 出锁做心跳，并发的其它 acquire 看到 cooldown 直接 False
    ok = await client.heartbeat()
    async with self._lock: ...
```

关键：cooldown 时间戳在加锁里推进，心跳本身在锁外做。这样既不串行化所有 acquire（不阻塞业务），又保证多 worker 同一周期只有一次真心跳。

### OpenAI 扩展字段 opt-in

`extra_body` 是 OpenAI Python SDK 的扩展点，会原样塞到请求 body 里。

- DashScope qwen / SiliconFlow Qwen3+：默认 thinking ON，需要 `extra_body={"enable_thinking": False}`
- vanilla `api.openai.com`：严格校验，未知字段直接 400
- 因此**永远 opt-in**，配置开关比 default-true 安全

### TaskGroup 与 finally

`asyncio.TaskGroup` 抛 `ExceptionGroup` 时，`async with` 块**正常退出**逻辑不走，但 `finally` 走。所以清理代码必须在 `try / finally` 里，不能在 `async with` 后裸写。

### Conventional Commits 中文 subject

- `<type>(<scope>): <subject>` 格式
- subject 用中文动宾，2-6 词，不超过 12 汉字
- body 用 `1、2、3、` 分点，每行一个动作

---

## 待办 / 遗留

- [ ] **search 路径未走熔断器**：`search_by_image()` 调 `vlm_client.describe()` 时只 `except VLMError: continue`，没 `gate.acquire()` / `gate.report_failure()`。VLM 端点挂了时，前端搜索仍会反复打挂掉的 endpoint。修复方向：把 `app.state.gate` 暴露给 search route，调用前 acquire、失败后 report_failure。
- [ ] **AGENTS.md 决议**：当前内容是 CLAUDE.md 的拷贝且包含已过期的"桩代码说明"段落（_describe() 已实装）。建议删掉或与 CLAUDE.md 同步。
- [ ] **图片加载失败也走 5 次重试**：`_load_image()` 抛 `FileNotFoundError` 时也走 backoff 5 次。建议判别"截图被外部删除"这类不可恢复错误直接 `mark_error_final`。
- [ ] **search 测试缺 VLMError 分支**：当前只有 stub 成功路径，没测 stub 抛 `VLMError → semantic_status='unavailable'`。
- [ ] **infra/architecture/web-ui.md 的 modified 状态**：不是本轮改的，可能是另一轮编辑遗留，需要用户检查。
