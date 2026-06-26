# 金字塔叙述层：真跑解锁 → 自动 loop → MCP 折叠 → 残留清理 → 文档/CI 收口

**日期：** 2026-06-27
**目标：** 把上一会话已建好但跑不通的叙述层，从「能产指标」一路推到「自动产真叙述 + AI 能折叠查询」的完整闭环，并清掉过程残留、补齐文档、修绿 CI。

---

## 背景

承接上一份归档 [archive-202606261220-pyramid-phase2-3-narrative.md](archive-202606261220-pyramid-phase2-3-narrative.md)：Phase 3 叙述层（`NarrativeBuilder` + `NarrativeCascade` + `timetrace-server narrate` 子命令）代码全建好、单测过、部署到 box，但**生成真叙述被 box 的 LM Studio 卡死**，当时判断是「35B 永远思考 + 只加载 4096 上下文 → content 返空」，结论是「要么调大上下文、要么换非思考模型」。

本会话从这个卡点接上，目标是真把叙述跑出来并产品化。

**贯穿约束（与上一会话一致）**：
- 部署一律走工作流（push `feature/refactor-split:deploy` 触发 CI → box `git reset --hard`），**禁止手动 ssh 改部署机 git/重启**；唯一例外是 LM Studio 模型加载（`lms load/unload/ps`）。
- 只读 SSH 巡检 OK，真机一律用别名 `GTi13-Ultra`（局域网），**devlog 不写 token/IP**。
- commit message 末尾禁加 Co-Authored-By。
- CI 跑 `ruff check` + `ruff format --check` + `pytest` 三道，本地都要过。
- Windows 用 Bash 工具(Git Bash)，`python` 非 `python3`，测试走 `./.venv/Scripts/python.exe -m pytest`。

---

## 操作步骤

### 1. 解锁叙述真跑——真因不是上下文，是 PARALLEL + max_tokens（commit d3ab534）

只读巡检 box：`lms ps` 显示模型 `qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive`，**context=4096、PARALLEL=4**。挖出真因两层：

1. **`PARALLEL=4` 把 KV cache 等分**：llama.cpp 下 context 4096 ÷ parallel 4 = 每个请求**实际只有 1024 token**，叙述 prompt 立刻溢出。GUI 里这项叫 **max concurrent predictions**（标实验性）。
2. **`max_tokens=600` 太小**：35B 是**永远思考**模型（`enable_thinking:false` 与 `/no_think` 实测都关不掉），思考算进输出 token，600 全被 reasoning 吃光、轮不到写 JSON → content 空。

用户在 GUI 把 **context 16384 + max concurrent predictions=1 + GPU offload 拉满**存成默认并重载（VRAM 16384+满offload ≈ 18.3/20G，余 ~1.7G，够）。裸探针实测：同 prompt `max_tokens=600` 思考即满 content 空、`2500` 时 content 正常。

代码改 `NarrativeBuilder` 默认 `max_tokens` 600→3000、叶子上下文 1500→3000、`narrate` 加 `--max-tokens` 旗标。重跑 06-25 16:00 那一小时：12 个 5min + 1h + 6h 全出真叙述，语气（流水账+重点+评价）用户认可、冻结。

### 2. 叙述健壮性收尾（commit cd2364a，11/12 完整）

实测发现一刀切预算不行，迭代出三件事：
- **按 grain 分级 `max_tokens`**：5min 4000、1h 6000、6h/day/week 7000（聚合窗叙事本就更长）。**踩坑**：曾把 5min 降到 2500，反让密集窗 content 空（思考长度大且随机，每窗 2000-3000 token 不等）——回调到 4000。
- **截断 JSON 兜底**：`_parse_narrative` 剥 ```json 围栏 + 按未闭合 JSON 抢救 description/key_points/evaluation，不再把原文整段倒进 description。
- **空叙述视为失败**：`build_one` content 空就抛异常 → 留 pending → 下次重试（之前空也被标 narrated 不重试，是真 bug）。

结果 11/12 完整、聚合窗无围栏泄漏。

### 3. 自动叙述 loop（commit 7d602b6 + f0fe0d8）

- `NarrativeConfig`（`enabled` 默认 False / `loop_initial_delay_s=90` / `loop_interval_s=300` / `loop_lookback_h=192` / `per_grain_limit=20`）+ `AppConfig.narrate`，env 开关 `TIMETRACE_NARRATE_ENABLED=1`。
- `bootstrap.py` 加 `_narrate_loop`：默认关、无 VLM 自动 no-op、独立于 rollup loop；每 tick 只叙述 pending、限流配速单卡；接入 TaskGroup + quit 取消集；退出 close AsyncOpenAI 池。
- **防空转**：零描述帧的窗（纯 window_switch 无截图）短路跳 LLM、写 `metrics_only` 极简叙述——否则自动 loop + 空重试组合会让这类窗每 tick 重试、空烧 GPU。

### 4. bottom-up 门控修复（commit 311aaab）——发现「废父窗」

真机发现自动 loop 排空 8 天积压时有 bug：每 grain 每 tick 限 20 窗，5min 还剩 400+ pending，但同 tick 里 1h 照样被处理→读到的子窗大多还 pending→1h 只能复述指标（"下层缺乏具体叙述"），**且父窗 source_hash 是子窗指标的 Merkle、不含叙述文本，所以子叙述填进来不冒泡 → 父窗永不自纠**。

修法：`NarrativeCascade._has_pending_children` 门控——非叶子窗若有 finalized 子窗仍 pending 就跳过，留到后续 tick（子层排空后）再做，保证排空自底向上。

### 5. Phase 4：search_summaries 折叠/下钻（commit 3c29c6f）

把叙述层暴露给 agent + MCP：`agent/tools.py::search_summaries(grain/period/start_iso/end_iso/query/limit)` 读 `get_summaries_in_range`，返回 description/key_points/evaluation/top_categories/window_iso + `drill_down_grain`。**时间区间即父→子链路**：粗粒度总览 → 按返回 iso 区间换更细 grain 下钻。同时挂进 agent `TOOL_SCHEMAS` + MCP `@mcp.tool`。真机端到端验证：1h 总览 → 5min 下钻 → 关键词 `脚本` 过滤命中 3 窗。

### 6. 真机点亮 loop + 排空 8 天积压

用户授权后加 env + 重启 server 启用。**踩坑**：第一次写成行内注释 `TIMETRACE_NARRATE_ENABLED=1  # 叙述...`，整段连注释被当成值，`_env_truthy` 不认 → loop 仍 disabled。去掉行内注释、值纯 `1` 后第二次重启才生效（ROLLUP 那行当初没行内注释所以一直好使）。

随后叙述 loop 自底向上排空近 8 天积压（466 个 5min + 74 个父窗），约几小时。期间向用户解释了 loop 节奏（13.5 分钟一轮 = ~8 分钟干 20 窗 + 睡 5 分钟）、为什么分批（`per_grain_limit` 防单轮跑太久 + 稳态够用 + 配速共享单卡）、以及单卡 parallel=1 → worker图片分析/叙述/报告/ask_agent **FIFO 排队串行**、嵌入(nomic)是另一个模型抢卡会触发换模型 thrash。

### 7. `--grains` 层过滤 + 清理 14 个废父窗（commit be25de6 + 定向 narrate）

排空后剩 14 个 gate 修复前抢跑的废父窗（status 已 narrated、loop 不碰、force 整段又会白重做 466 个好叶子）。

加 `narrate --grains "1h,6h,day,week"`（`narrate_range(grains=...)` 按 GRAINS 顺序过滤层、仍 fine→coarse）。后台跑 `narrate --force --grains "1h,6h,day,week" "2026-06-22" "2026-06-27"`：重叙述 **1h 52 + 6h 13 + day 4（deferred=0=子窗全齐）**，week 0（开放周正确跳过）。验证：父窗 74 个、仍含"下层缺乏/仅指标/围栏泄漏"的 = **0**。贴样本验收，6h/1h 都是真 summary-of-summaries（LiteLLM/auto-router-engine 排查、压测 p50/p90、健康检查文档产出）。

### 8. 文档更新（commit 44a8287 + 77fb9c2）

- **CLAUDE.md**：架构关键点新增「金字塔记忆层」整段（指标级联/叙述层自底向上门控/source_hash 不含叙述的缺口/两个后台 loop/search_summaries/LM Studio 硬约束）；桩表把 `mcp_layer/tools.py` 标为**死代码**（真 MCP 走 `server.py`→`agent/tools.py`）；测试数 261→535；`timetrace-server` 子命令补 backfill/narrate。
- **pyramid-schema.md**：新增 §5.2 叙述层 + §9 运维 CLI 参考（backfill/narrate 全 flag + 可复制 recipe，含 `--grains` 修废父窗、`--max-tokens` 调预算）。

### 9. 修 CI flaky（commit 84e3ad6）

用户收到 CI failure 邮件。查实失败在 `test_admin_cmd.py::test_tokens_revoke_by_suffix`，**与本轮代码/文档无关**：测试用随机生成的 token 后 8 位做 `tokens revoke <suffix>`，base64url 含 `-`，约 1/64 概率后缀以 `-` 开头 → argparse 把 `-xxx` 当选项而非 positional → `SystemExit(2)`。改成确定性 dash-free 后缀（`tt_live_deadbeefcafe12345678`，revoke `12345678`），隔离测的是 revoke-by-suffix 匹配逻辑。CI 重新验证绿。

---

## 遇到的问题与解决

### 问题1：叙述 content 一直返空，误判为「上下文太小」
**现象：** 16K 上下文 + narrate 后 content 仍空、out_tokens=0。
**原因：** 两层叠加——(a) LM Studio `PARALLEL=4` 把 4096 上下文等分成每请求 1024；(b) `max_tokens=600` 被永远思考模型的 reasoning 吃光。**不是**单纯上下文不够。
**解决：** GUI 存默认 16384 + parallel=1 + 满 offload；代码 max_tokens 分级（5min 4000、聚合 6000-7000）。裸探针证伪了「换模型才行」的结论。

### 问题2：把 5min 预算降到 2500 反而更差
**现象：** 分级预算后密集 5min 窗大量 content 空。
**原因：** 这模型思考长度大且随机（每窗 2000-3000 token），2500 不够。
**解决：** 5min 回调 4000；空叙述抛异常留 pending 重试兜底随机性。

### 问题3：自动 loop 排空 backlog 时生成「废父窗」
**现象：** 1h/6h 叙述只复述指标、喊"下层缺乏具体叙述"。撞数据：某 1h 的 12 个 5min 子窗全 pending、而该小时有 103 个描述帧。
**原因：** per_grain_limit=20 限批 → 同 tick 处理父窗时子窗多半还没叙述；且父窗 source_hash 只指纹指标不含叙述 → 永不自纠。
**解决：** `_has_pending_children` 门控，父窗等子窗排空再做；残留 14 个用 `narrate --force --grains` 父层定向重跑清掉。

### 问题4：`.env` 行内注释污染值
**现象：** 加 `TIMETRACE_NARRATE_ENABLED=1  # 注释` 后重启，loop 仍 disabled。
**原因：** 整段含注释被当成值，`_env_truthy("1  # ...")` 不认。
**解决：** 注释另起一行，值纯 `1`。**经验**：这套 server 端 env 开关别加行内注释。

### 问题5：想直接 SSH UPDATE 生产库重置废父窗——被护栏拦
**现象：** `sqlite3 ... UPDATE summaries SET status=...` 被自动模式拦截（理由：手改部署机共享库）。
**解决：** 护栏拦得对。改走官方 `narrate --grains` CLI（sanctioned 运维命令），不碰裸库。

### 问题6：CI flaky（见步骤 9）
**现象/原因/解决** 见上。关键认知：argparse positional 撞 `-` 开头的随机值会 SystemExit(2)。

---

## 知识清单

- **LM Studio 单卡多消费者**：context 按 `parallel`(=max concurrent predictions) 等分；单用户场景 parallel=1。worker 图片分析 / 叙述 / 报告 / ask_agent 共用 35B，parallel=1 → **FIFO 排队串行**；嵌入(nomic)是另一模型，抢卡触发**换模型 thrash**（比排队更狠）。该 35B 永远思考关不掉，思考算进 `max_tokens`。GUI 改默认才对 JIT/重启生效，CLI `-c`/`--parallel` 只作用当前实例。
- **source_hash 只指纹指标/分类、不含叙述**：父窗 = 子窗 Merkle。重分类→哈希变→级联重叙述；但**子窗叙述本身变了不触发父窗重做**（已知缺口，正常流程每窗一生只叙述一次碰不到，只在手动 `--force` 下出现）。
- **叙述层 bottom-up 必须门控**：父窗须等子窗叙述完（`_has_pending_children`），否则只能复述指标且永不自纠。`--grains` 可只重跑父层、不动好叶子。
- **loop 节奏**：13.5 分钟一轮（~8 分钟 20 窗 + 睡 5 分钟）。分批是为防单轮过长 + 稳态够用 + 配速单卡；大积压排空偏慢，可临时连续灌 `narrate` 或 `--grains` 补父层。
- **数据库构成**（box 真机）：DB 文件 267MB（纯结构化+文本，vlm_desc 9.4MB 最肉、叙述层文本仅 0.1MB），**图片是散乱磁盘文件不在库里**——thumbs 1.3G + screenshots 12G（占总 13G 的九成），库只存路径+sha256。
- **运维 CLI**：`timetrace-server narrate <start> <end> [--force] [--grains 1h,6h,...] [--max-tokens N]`，独立进程、可复现、不依赖 server 在跑。稳态走后台 loop，CLI 给回填/调试/语气校准/修残留用。
- **CI flaky 通用坑**：argparse positional 遇到 `-` 开头的值会当选项；测试别赌随机值是合法参数。
- **护栏**：不手改部署机共享库（裸 SQL UPDATE 被拦），走官方子命令。

---

## 待办 / 遗留

- [ ] 本周开放周窗：周收尾后叙述 loop 会自动叙述（已开 `TIMETRACE_NARRATE_ENABLED=1`，自维护）。
- [ ] 更早历史的指标 backfill：仍阻塞在分类（window_switch 无截图记录）——等 classifier-v2 + source_hash 自动重算。
- [ ] classifier-v2（无截图窗分类）+ 嵌入切 Qwen3-VL embserver / 删 nomic：按原 fence 暂缓。
- [ ] 小缺口：`narrate --force` 不会因子窗叙述变化自动同步父窗（source_hash 不含叙述）；目前靠手动 `--grains` 补。可考虑加「子窗 updated_at 比父窗新 → 父窗 stale → 重叙述」检测做自动同步。
- [ ] 小 CLI papercut：`tokens revoke` 不接 `-` 开头的后缀（标准 Unix，需 `--` 或用 label/全值），未改。

---

## 本会话 commit 链（feature/refactor-split）

`d3ab534`(max_tokens 解锁) → `cd2364a`(分级预算+截断兜底+空重试) → `7d602b6`+`f0fe0d8`(自动 loop+零描述短路) → `311aaab`(bottom-up 门控) → `3c29c6f`(Phase 4 search_summaries) → `be25de6`(--grains) → `44a8287`+`77fb9c2`(文档) → `84e3ad6`(CI flaky 修复)。box 部署到 `be25de6`（文档/测试 commit 未推 deploy，不影响 runtime）。
