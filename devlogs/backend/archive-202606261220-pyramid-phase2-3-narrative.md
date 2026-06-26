# 金字塔 Phase 2-3：go-live → 时长语义 → MCP 修复 → source_hash 重发 → 叙述层（撞 LM Studio 墙）

**日期：** 2026-06-26
**目标：** 把"记忆金字塔"从 Phase 1（指标级联）继续推到 go-live + Phase 2（source_hash 级联重发地基）+ Phase 3（LLM 叙述层），并顺手修一组外部 bot 反馈的 MCP 可用性问题。

---

## 背景

承接 [archive-202606222322-memory-pyramid-phase1.md](archive-202606222322-memory-pyramid-phase1.md)：Phase 1 已把"金字塔"承重的**指标级联**（5min→1h→6h→day→week，纯 SQL、SUM-invariant）建好、单进程默认关。本会话把它真正开起来、并往上盖两层（重发地基 + 叙述层）。

**贯穿约束（沿用）**：
- commit 末尾禁加 Co-Authored-By 署名行。
- 不碰 `rules/engine.py` 投票逻辑、不实现 classifier-v2（用户圈了 fence）。
- 部署一律走 GitHub Actions 工作流（`git push origin <branch>:deploy`）；唯一例外 = LM Studio 模型加载。
- 只读 SSH 巡查 OK，用别名 `GTi13-Ultra`（局域网），devlog 不写 token/IP。
- Windows 环境：Bash 工具（Git Bash）；`python` 不是 `python3`；测试用 `./.venv/Scripts/python.exe -m pytest`。

---

## 操作步骤（按时序）

### 1. 时长语义决策 + 实装（commit 448f7d9）

撞真机数据（box 30 天，只读）定口径：所有时长 = **本机捕获的活跃下限（floor）**，不是总时间。
- `query_stats` 每个结果加 `semantics` 自描述块（definition / is_a_floor_because / how_to_phrase / aggregation）。
- `active_seconds` 改 **墙钟并集（union）**：`summary/metrics.py::active_wall_ms` 一次 interval-merge SQL，复用同一个 `_clamped_dur_sql` clamp，恒 `≤ Σ 分类秒数`；不进级联（union 跨窗不可加），读边每次 live 算。
- 覆盖数据坐实"设备覆盖才是低估主因"：30 天 720h 墙钟里只捕到 **126h（17.6%）**、18/30 天有记录、最大断档 70h。

### 2. go-live：把指标级联开起来

- box `.env` append 一行 `TIMETRACE_ROLLUP_ENABLED=1`（唯一手动 SSH 步；server 经 `EnvironmentFile=` + `load_dotenv` 读 .env）。
- `git push origin feature/refactor-split:deploy` → CI 21s 绿 → box 从两周前的 `2ead8a7` 追到新代码，重启读到开关。
- 验证：日志 `rollup.loop_started` + 首拍 `rollup.tick` 建 **132 个 5min + 16×1h + 6×6h + 2×day + 1×week**；回读数字 sane（06-22：3176 条、~10h 活跃、工作占大头）。

### 3. MCP 4 点可用性修复（外部 bot 反馈，commit 6db985f）

bot 用 MCP 拉全天数据时 `get_recent_activity` 上限 200 截断。对真代码核实后 4 点全成立：
1. `_MAX_LIMIT` 200→2000；
2. `get_recent_activity`/`search_activity` 加绝对时间窗 `start_iso`/`end_iso`（复用 `_parse_iso_local`，显式窗优先于 hours_back，新增 `_resolve_window`）；
3. `get_recent_activity` 透出 `cursor` + 返回 `next_cursor`（search 因 BM25 排序不加游标）；
4. `_fts_query` 多词：含空格按 token 拆、每个 ≥3 字当短语再 AND（`"judge" AND "replay" AND "history"`），命中任意顺序的全部词；无空格/CJK 仍当整短语。
- **真机实测**（拷贝 box 库到临时目录，绝不碰真库）：4 点全 OK。意外印证 #3 的价值——本地数据是 41 天前、超过 `hours_back` 30 天上限，**只有绝对时间窗够得着**。

### 4. backfill 子命令 + DB busy_timeout（commit 66e276c）

- `timetrace-server backfill <start> <end> [--pause]` 一次性子命令（admin_cmd + cli），调 `MetricsCascadeBuilder.backfill` 补历史天/周指标。
- DB 加 `PRAGMA busy_timeout=5000`：独立 backfill 进程与活 server 的 rollup loop 写同库时等锁、不立刻 "database is locked"；写仍靠 `UNIQUE(grain,scope_key)` 幂等。

### 5. CI 修绿（commit a2b1fc2）

收到 CI 失败邮件。真因：CI 跑 `ruff format --check`（我本地只跑 `ruff check`），`tools.py`/`sqlite.py`/`mcp_layer` + 旧漂移的 `rollup.py` 格式不达标——CI 从 06-08 就一直红。`ruff format` 一键修。**教训：本地必须同时跑 `ruff check` + `ruff format --check`。**

### 6. 嵌入/分类/VLM 三连诊断（撞真机，纠了一串误判）

- **嵌入堆积**：box 上 VLM 描述跟得上（pending 几十条），堆的是**嵌入 1.1 万条**（继承 VLM base_url、共用 LM Studio :1234）。曾因 LM Studio "just one model" 设置→换模型 thrash→嵌入只跟 68%。**用户自己关掉该设置修复**。独立 Qwen3-VL embserver（:8766、torch）代码齐了但**没在 box 部署**。
- **"分类停产"是假象**：13053 条 unclassified **全是 window_switch 无截图事件**，loop.py:183 短路跳过 VLM（by design，只 heartbeat 关键帧截图）。有截图记录分类到最新都正常；VLM（35B）在 box 正常加载；某时段无新记录 = 采集端被 Ctrl-C。
- 救 window_switch 分类（精度真瓶颈）= classifier-v2 该干的（从邻近关键帧传播分类），按 fence 暂缓。

### 7. Phase 2 切片 2a：source_hash 级联重发地基（commit 1bc7bec）

- 每个摘要算输入指纹存 `source_hash`：叶子哈希贡献帧的 `(id|ts_start|ts_end|app|category_final)`；上卷 Merkle 式哈希孩子的 `(scope_key, source_hash)`，子哈希一变沿级联向上传播。
- `upsert_summary` 变成**重发触发器**：冲突重建时只有 source_hash 变了才 bump `source_version` + 重置 status（重入叙述队列）；同输入幂等重建保留叙述进度。
- 洞见：**backfill 子命令天然就是手动重发巡检**（重建即重算指纹），所以自动巡检 loop 暂时无触发（分类卡住、老数据没在变），可延后。

### 8. Phase 3：叙述层 builder + narrate（commit 59fc9b1 → d8b2bea）

- `NarrativeBuilder.build_one`：把闭合窗口嚼成 **流水账 + 重点提炼 + 评价**（结构化 JSON）。叶子读窗内逐帧描述；上卷读子摘要 description（summary-of-summaries）。LLM 接口注入、可 mock。
- 用户拍板：**每层都调 LLM、但只在窗口闭合后**；口吻=流水账+重点+评价，3 字段够。
- `NarrativeCascade.narrate_range` 自底向上（5min→周，父见新子叙述）+ `timetrace-server narrate <start> <end> [--limit] [--force]` 子命令。
- 确认：标准时间指标独立于叙述往上传（一个日报行同时有 metrics_json + description/evaluation）。

### 9. 撞 LM Studio 墙（多次 fix 后停手）

想出真样本，narrate 在 box 上一路撞墙，逐个修：
- 上下文溢出（4096）→ 只喂有描述的帧 + 字符预算 1500 + max_tokens 降到 600 + 单窗 try/except 不中断（29971fd / 154a5ba）。
- content 返空 → 怀疑思考模型，加 `disable_thinking` + `narrate --force`（e5ac47d）。
- **直接探针验死**：`qwen3.6-35b-...-aggressive` 是**永远思考**模型，`enable_thinking:false`（extra_body）**和** `/no_think` **都关不掉**；答案只在思考完后进 content。4096 上下文里大叙述 prompt + 思考 → content 没空间 → 空。**纯 infra 墙，非代码 bug。停手，钉记忆。**

---

## 遇到的问题与解决

### 问题1：CI 从 06-08 一直红
**现象：** 收到失败邮件。**原因：** CI 跑 `ruff format --check`，本地只跑了 `ruff check`，4 文件格式漂移（含我没碰的 `rollup.py` 旧漂移）。**解决：** `ruff format src/` 一键修（a2b1fc2）。教训：本地补跑 `ruff format --check`。

### 问题2：一串误判被真机数据否掉
**现象：** 以为"分类停产 / VLM 没起 / 嵌入是独立通道"。**原因：** 凭印象。**解决：** 撞 box —— VLM 正常、unclassified 是 window_switch 无截图 by design、嵌入共用 LM Studio、独立 embserver 没部署。延续上一会话"先撞真机再下结论"的纪律。

### 问题3：叙述真跑不出来（终极墙）
**现象：** narrate 报 400 Context exceeded / content 空。**原因：** box 模型永远思考（关不掉）+ 只加载 4096 上下文，思考吃光 token 预算。**解决（待用户）：** 调大 LM Studio 上下文（16K+，注意显存 ~85% 满）或换非思考模型，然后 `narrate --force` 重跑，**无需改代码**。

### 问题4：GitHub SSH push 间歇失败
**现象：** `git push` 偶发 "repository exists" / TLS timeout。**解决：** for 循环重试 2-3 次即过（网络抖动，非配置）。

---

## 知识清单

- **时长口径**：本机捕获活跃下限；`active_seconds`=墙钟并集（读边 live，不入级联因 union 跨窗不可加）；指标级联走 Σ-own-span（可加）。设备覆盖（17.6%）才是低估主因。
- **部署机制**：`git push origin <b>:deploy` → CI（~20s）→ box `git reset --hard`。`.env` gitignored、survive reset。开关/模型加载属 deploy 之外，可手动 SSH。
- **MCP 读路径**：实现在 `server/agent/tools.py`（不是 mcp_layer/tools.py 那个老桩）；`query_records` 早有 `cursor`（ASC keyset）；`_fts_query` 被 records_fts + summaries_fts 共用。
- **source_hash 重发**：Merkle 指纹 + `upsert_summary` 冲突时按 hash 变化 gate status/version；backfill = 手动重发巡检。
- **LM Studio 坑**：`qwen3.6-35b-aggressive` 永远思考，`enable_thinking:false`/`/no_think` 均无效；4096 上下文对 CJK 大 prompt 不够（CJK ~2 token/char）。叙述要真跑必须调大上下文或换模型。
- **CI**：`.github/workflows/ci.yml`(windows-latest) 跑 `ruff check` + `ruff format --check` + `pytest`。本地两个 ruff 都要跑。
- **测试纪律**：tmp 隔离 + 哨兵年（2099 未闭合走 live、2001 已闭合走 digest）；CLI 子命令测试用 sync 函数（`_cmd_*` 内部 `asyncio.run` 不能嵌在运行中的 loop）。

---

## 待办 / 遗留

- [ ] **叙述层真跑**（最高）：调大 box LM Studio 上下文(16K+)或换非思考模型 → `narrate --force` 重跑出真样本，再据此调 prompt 口吻。代码全 done（到 e5ac47d）。详见记忆 `project_narrative_blocked_lmstudio_context`。
- [ ] **指标历史 backfill**：阻塞在分类（仅 71%，13053 window_switch 无截图）。等 classifier-v2 修分类 + source_hash 自动重算后再填，否则冻 29% `_unclassified`。
- [ ] **classifier-v2**：给无截图 window_switch 记录分类（从邻近关键帧传播），按 fence 暂缓；交接提示词已在会话中写好。
- [ ] **嵌入选型**：立 Qwen3-VL embserver 作唯一嵌入、删 nomic（用户本意），等历史嵌入追上 + 显存腾挪后执行。
- [ ] **叙述层后续切片**：claim 队列泛化（并发拉活）、自动 narrate loop（gated）+ 自动重发巡检、Phase 4 MCP folding + search_summaries（依赖叙述真跑起来）。

---

## 本会话 commit 链（均在 feature/refactor-split）

`448f7d9`(时长语义) → `092be70`(MCP plan) → `6db985f`(MCP 4 修复) → `1dd910f`(plan标完成) → `66e276c`(backfill+busy_timeout) → `a2b1fc2`(CI format) → `1bc7bec`(source_hash 重发) → `59fc9b1`(叙述核心) → `d8b2bea`(narrate 子命令) → `29971fd`/`154a5ba`(上下文防溢出) → `e5ac47d`(关思考+force)。box 已部署到 e5ac47d。
