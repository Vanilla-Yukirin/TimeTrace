# VLM Prompt 名词性短语硬约束 + Infra 文档对齐

**日期：** 2026-05-04
**目标：** 修复 VLM 输出大量以「该截图展示了…」等元叙述句开头导致的 IDF 稀释问题；顺手把上一轮 VLM 实装（commit 06e6156）后未跟上的 infra 文档过期陈述对齐到当前事实。

---

## 背景

上一轮（commit 06e6156）完成了 VLM 实装、N 并发 worker、熔断器、原子 claim、`.gitignore` 补 `frontend-dist/`。该 commit 落地后跑了若干天，本轮 session 的入口是用户提了三个新问题：

1. **历史长记录残留**：仍存在长达 12 小时的 `pending_vlm` / `processing_vlm` 行（自动愈合是否失败？）—— 本轮搁置，留待下次定位
2. **`app_name` 没透给 VLM**：记录 "Unknown · 微信" 的 VLM 描述里只说"即时通讯软件"，没识别出微信。怀疑变量没传
3. **VLM 输出元叙述开头**：几乎每条 `vlm_desc` 都以"该截图展示了…"、"画面显示…"、"这是…" 开头。这些高频词会**稀释 BM25 / LIKE 通道里真实关键词的权重**——FTS5 中 IDF 趋近于 0，LIKE 中也会假命中

用户明确要求：先看 Q2 + Q3。

---

## 操作步骤

### 1. 诊断 Q2：window_title vs app_name 传递链

跟踪链：

| 文件 | 位置 | 现状 |
|---|---|---|
| `database.py::get_record_meta` | L556-579 | 只 SELECT `window_title` + `screenshot_path`，**完全没读 `app_name`** |
| `worker/loop.py::_handle_one` | L114 | 只把 `meta["window_title"]` 传给 `vlm.describe()` |
| `vlm/client.py::describe` | L167-168 | prompt 末尾注入 `窗口标题（仅供辅助参考，可能不准确）：{window_title}` |

**用户那条记录的真实情况**：
- `app_name = "Unknown"` —— `capture/window.py::_get_process_info` 的 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` 失败 fallback。微信、QQ 等客户端常见此现象（应用做反调试或权限提升）
- `window_title = "微信"` —— 实际**已经传给了 VLM**，但 prompt 里"**可能不准确**"的免责让模型对它持过度谨慎态度，宁可只说"即时通讯软件"

### 2. 诊断 Q3：元叙述开头的语法层根因

prompt 里 `description` 字段注释只写「100 字以内对画面的完整描述」。LLM 在中文图像描述任务上的强先验是「该截图/这张图/图中显示…」开头。**当所有记录都以这些词开头**，BM25 中这些词 IDF → 0，LIKE 中也会高频假命中。

### 3. 与用户对齐方案

我先给出诊断 + 两套修复方案，等用户拍板（不直接动手）。用户回应：

- **app_name 传递不做** —— 大部分 app_name 是 Unknown 时传了反而误导；OpenProcess fallback 是另一条独立链路，要修需另起一题
- **窗口标题措辞** —— 删掉"可能不准确"，保留"仅供辅助参考"
- **元叙述禁令** —— 直接修

### 4. 第一版改动（黑名单元叙述词）

[src/timetrace/vlm/client.py](../../src/timetrace/vlm/client.py)：

```python
'  "summary":     "...",     // 30 字以内的画面要点；直接陈述内容，不要以"该截图/这张图/图中/画面中/此图/此截图"等元叙述开头\n'
'  "description": "..."      // 100 字以内对画面的完整描述（…）；直接陈述内容，不要以"该截图/这张图/图中/画面中/此图/此截图"等元叙述开头\n'
```

窗口标题部分：

```python
text = f"{text}\n窗口标题（仅供辅助参考）：{window_title}"  # 删掉"，可能不准确"
```

[tests/test_vlm.py](../../tests/test_vlm.py) 加 1 条断言：检查 prompt 中元叙述黑名单关键字、保留"仅供辅助参考"、不再含"可能不准确"。

`uv run pytest tests/test_vlm.py -x -q` → 24 passed。

### 5. 用户验证：黑名单失效

用户实测新一批记录，列举仍出现的失败例：

> 画面为任务切换视图；画面显示 Microsoft Edge 浏览；展示 Clash Verge 软件界面；这是游戏《鸣潮》的启动界面；该画面展示了 QQ 软件的邮箱提醒功；开发者正在 VS Code 中使用 TimeTrace；截图展示了 TimeTrace 软件界面

模型换花样：黑名单禁了"该截图/这张图/图中/画面中/此图/此截图"，模型就用"画面为/画面显示/展示了/这是/该画面/开发者正在/用户正在"。**黑名单永远追不上同义词**。

用户给出方向：

> 我们要从更加根本的方法，规范说不要用这种语句开头。比如，要求第一句直接是一个"名词性短语"（而不是陈述句，这就会导致"截图是 xxx"）

### 6. 第二版改动（语法层硬约束 + few-shot 正反例）

把 prompt 改成 5 条硬性写作规范：

```
**summary 与 description 的硬性写作规范**：
1. 必须以名词性短语开头（直接命名画面主体），严禁以陈述句、判断句或动宾结构开头。
2. 严禁出现的开头句式（包括但不限于）：「该截图…」「这张图…」「图中…」「画面中…」
   「此图…」「此截图…」「画面为…」「画面显示…」「展示了…」「该画面…」「这是…」
   「截图展示…」「开发者正在…」「用户正在…」等任何以「截图/画面/图」为主语
   或以「这是/正在/展示」为谓语的开头。
3. 正例：「微信桌面客户端深色模式群聊界面，左侧会话列表含 Damian 与文件传输助手…」
4. 反例：「该截图展示了一款深色模式下的桌面即时通讯软件界面…」「这是微信PC客户端…」
        「画面显示 Microsoft Edge 浏览…」
5. 把视角放在画面内容本身，不要把「截图/画面」当成被描述的对象。
```

三个收紧维度：
- **语法层** — 名词性短语开头 / 禁陈述句、判断句、动宾结构开头
- **黑名单兜底** — 列出 14 种已观察到的禁开头句式
- **Few-shot 对比** — 正例 + 三条反例（覆盖用户列举的真实失败模式）

测试断言改为检查 `"名词性短语"` / `"陈述句"` / `"正例"` 出现 + 5 个新反例 token（"该截图""画面显示""这是""展示了""正在"）+ 仍保留"仅供辅助参考"。

`uv run pytest tests/test_vlm.py -x -q` → 24 passed。

### 7. 用户追问：infra 是否需要更新？要不要 archive？

我答：
- **infra 不为 prompt 文案专门加** —— 这是实装细节，不是端到端架构事实
- 但顺手发现上一轮 VLM 实装后 infra 有 **4 处旧陈述未跟上**：
  - [analysis-worker.md:96](../../infra/architecture/analysis-worker.md#L96) "_describe() 目前是桩代码"
  - [analysis-worker.md:106-107](../../infra/architecture/analysis-worker.md#L106-L107) Provider 协议字段写的是 `{activity, scene, summary, keywords}`（旧四字段），还有过期本地路径 `D:\Code\20260419测试阿里vlemb`
  - [vector-search.md:65](../../infra/storage/vector-search.md#L65) 还在说"四字段"
  - [analysis-worker.md] 当前实现示例还是单消费者循环

用户："按事实优化 infra"。

### 8. Infra 大改

**[infra/architecture/analysis-worker.md](../../infra/architecture/analysis-worker.md)** 几乎重写：

| 段落 | 改动 |
|---|---|
| 职责 | 改为：原子 claim（RETURNING）/ 并发消费者 / 三字段 / 失败回 pending_vlm / 熔断器 / status 镜像 |
| 状态机 | 删掉虚构的 `error_retryable` 独立状态，改为「失败回 pending_vlm + retry_count」+「重试用尽 → error_final 镜像 records.status」 |
| Claim 机制 | 替换为真正的 `UPDATE…IN(SELECT…LIMIT 1)…RETURNING` 单语句，引用 `Database._lock` 与 `_reclaim_loop` |
| 错误重试 | 表格按 `_handle_one` / `_fail` 实际行为重写，附 backoff 公式与默认值（base=60s, max=600s, max_retries=5） |
| 当前实现 | 替换桩代码示例为真实 `_consume_loop` + `_handle_one` 流程 |
| Provider 抽象（删除） | 替换为「VLM 客户端」新章节：实际 `VLMClient` 接口、`extra_body` opt-in 语义、prompt 名词性短语规范的动机（IDF 稀释）。删除过期本地路径 |
| 熔断器（新增） | `HEALTHY ↔ SLEEPING` 状态机、`asyncio.Lock` + `next_probe_at` 单协程探活、首次 probe 推迟一个 interval |
| 性能约束 | 默认 vlm_concurrency=2、payload 长边 1280px + JPEG q=80、退避默认值 |

**[infra/storage/vector-search.md](../../infra/storage/vector-search.md)** 中改：

| 段落 | 改动 |
|---|---|
| 语义通道章节标题 | "Phase 1.5 到位" → "VLM 描述 + LIKE（已实装） / FTS5 BM25（待做）" |
| 字段 | 四字段（`activity / scene / summary / keywords`）→ 三字段（`keywords / summary / description`） |
| 拼接形态（新增） | 给出 `format_description()` 实际产出的多行 LIKE-friendly 文本结构 |
| 当前查询路径 | "vlm_desc 全为 NULL" → LIKE 已生效，FTS5 改为「计划」段（schema 沿用三字段） |
| 写作规范关联（新增） | 解释 prompt 强制名词性短语开头是为避免 IDF 稀释 |

`uv run pytest -x -q` → 101 passed。

---

## 遇到的问题与解决

### 问题 1：黑名单永远追不上同义词

**现象：** 第一版禁了 6 个词（"该截图/这张图/图中/画面中/此图/此截图"），用户实测仍有 7 种新失败模式（"画面为""画面显示""展示了""这是""该画面""开发者正在""用户正在"）

**原因：** LLM 在中文图像描述任务上的元叙述模板有大量同义形式，黑名单本质是猫鼠游戏

**解决：** 第二版改用**语法层约束 + few-shot 对比**：
- 强制「第一句必须是名词性短语」
- 严禁「以陈述句、判断句、动宾结构开头」
- 给一条具体正例 + 三条具体反例（覆盖 user 列举的真实失败模式）
- 黑名单作为兜底，但不是主约束

### 问题 2：Python 字符串字面量嵌套引号语法错误

**现象：** 第二版第 5 条规范初版写为：

```python
"5. 把视角放在画面内容本身，不要把"截图/画面"当成被描述的对象。\n"
```

Python 解析器把这一行的双引号嵌套理解为：`"5. ...不要把"` + 标识符 `截图/画面` + `"当成..."` → SyntaxError

**解决：** 把内层双引号换成中文引号 `「」`。也是因为 prompt 前面段落都已用 `「」` 风格，统一了排版。

### 问题 3：PowerShell GBK 终端渲染乱码

**现象：** `uv run python -c "from timetrace.vlm.client import _DESCRIBE_PROMPT; print(_DESCRIBE_PROMPT)"` 输出全是乱码

**原因：** Windows PowerShell 默认 codepage GBK，Python UTF-8 输出在终端被错码

**解决：** 不影响实际运行，pytest 断言通过即证明字符串内容正确（覆盖了"名词性短语"/"陈述句"/"正例"/5 种新反例 token）。终端渲染问题不在本轮修复范围。

### 问题 4：上一轮 VLM 实装的文档尾巴

**现象：** commit 06e6156 把 worker 从单消费者桩代码升级为 N 并发 + 熔断器 + 原子 claim + 三字段 VLM，但 infra 文档没跟上：

- analysis-worker.md 还说 `_describe()` 是桩
- analysis-worker.md 的 Provider 协议示例还是旧四字段 + 过期本地路径
- vector-search.md 也还在说四字段

**原因：** 上一轮归档时只动了 devlog，没回头校对 infra 架构文档

**解决：** 本轮顺手对齐。教训：实装级 commit 落地后应同步检查 infra 是否有过期陈述

---

## 知识清单

### LLM 输出格式约束方法学

| 层级 | 例子 | 适用场景 | 局限 |
|---|---|---|---|
| 黑名单（关键词层） | 「不要以"该截图"开头」 | 已知**少量、有限**的禁忌词 | 同义词无穷，模型会换花样 |
| 语法层约束 | 「必须以名词性短语开头」「禁陈述句开头」 | 整类输出模板要换 | 需要模型有足够中文语法理解 |
| Few-shot 正反例 | 给一条正例 + 三条具体反例 | 模型学具体形态 | 反例不能太多，否则会反向学 |
| Schema/JSON 强约束 | `response_format={"type": "json_object"}` | 字段层结构 | 字段内容仍受先验影响 |

**经验**：先想语法层约束 + few-shot，再考虑黑名单。黑名单是 gap-fill，不是主约束。

### Prompt engineering 的下游可观测代价

VLM 输出的 `vlm_desc` 直接喂给 LIKE / FTS5 BM25 检索。当某些词在所有记录中高频出现：
- BM25 IDF → 0，这些词的搜索权重**塌陷**
- LIKE 假命中率上升（搜"截图"返回全库）
- 真正的关键词（应用名、人名、文档名）权重被稀释

→ Prompt 设计阶段就要考虑下游检索通道的 token 分布，不只是"读起来通顺"。

### infra 文档与 commit 不同步的尾巴

实装级 commit 落地时，往往：
- 测试覆盖了 → 行为对
- devlog 写了 → 工作记录有
- **infra 架构文档没动** → 后人读到的是上一阶段陈述

可以加进归档 checklist：实装级 commit 后跑一遍 `grep -l "<旧概念>" infra/`，按当下事实校对。

### Python 字符串嵌套引号

- 单文件 prompt 字符串里若有混合引号，统一用 `「」`（中文引号）省事
- `"...""..."` 双引号嵌套必报 SyntaxError
- `r"..."` raw 字符串也救不了引号嵌套，只是不解析转义

### 状态机文档的常见错误：虚构状态

旧 analysis-worker.md 把 `error_retryable` 写成独立状态，实际代码里**根本没这个状态**——失败是回写到 `pending_vlm` + `retry_count` + `next_retry_at`，靠 claim 查询的 `next_retry_at <= now` 守卫到点重新 claim。文档作者把"动作"误写成了"状态"。校对状态机文档时要直接 grep 代码里的所有 status 字面量。

---

## 待办 / 遗留

- [ ] **Q1（用户搁置）**：仍有长达 12 小时的记录，自动愈合是否失败？需要查 `_reclaim_loop` 的实际命中率与 `processing_*` 行的存量
- [ ] **app_name 大量 Unknown**：`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` 在微信、QQ 等客户端上失败。fallback 思路：用 `GetClassName(hwnd)` + `GetWindowText` 启发式映射回 friendly name；或直接从 `window_title` 末尾启发提取
- [ ] **图像读取失败仍走 5 次 retry**：`Image.open` 抛异常的不可恢复场景应直接 `mark_error_final`，不消耗 retry 配额（已在 `infra/architecture/analysis-worker.md` 错误重试表标 TODO）
- [ ] **commit 本轮改动**：4 个文件待 commit（`vlm/client.py` / `tests/test_vlm.py` / `infra/architecture/analysis-worker.md` / `infra/storage/vector-search.md`）
