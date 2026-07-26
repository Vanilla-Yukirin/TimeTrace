# LLM 账本 + 金字塔日视图两个面板，与前端发布工作流化

**日期：** 2026-07-20（会话跨 06-27 至 07-20，中间经过一次 context 压缩）
**目标：** 给前端加「LLM 请求日志」「金字塔日视图」两个面板；顺手把公网前端（timetrace.yukirin.me）三周没人更新的病根用 CI 工作流彻底治掉

---

## 背景

- 叙述层（narrative）刚在上一段会话跑通（见 [backend/archive-202606270722-narrative-loop-search-golive.md](../backend/archive-202606270722-narrative-loop-search-golive.md)），box 上 LM Studio 单卡 parallel=1，worker 图片分析 / 叙述 / 报告 / ask_agent 全部 FIFO 串行抢一个槽位。35B 是「永远思考」模型，思考 token 计入输出预算，但具体每类调用吃多少 token 只能靠猜——需要一本**统一账本**把每次 LLM 请求量化。
- 「金字塔」五层（5min→1h→6h→day→week）数据已经在 `summaries` 表里，但除了 admin CLI 没有地方能直观看到层级结构。
- 公网前端拓扑：浏览器 → xcy VPS（103.117.123.204:22000，nginx 静态 docroot `/var/www/timetrace.yukirin.me/`）→ API 经 frp 隧道回家里 box。**deploy.yml 只覆盖 box 后端**，前端发布是个纯手动 `npm run build && scp` 步骤——结果 6 月 5 日之后没人跑过，公网站点烂了三周。
- 会话期间用户把 box hostname 从 `gti13-ultra` 改名为 `yukirin-server`（SSH 别名同步换，旧别名 `GTi13-Ultra*` 弃用）。

---

## 操作步骤

### 1. 两个面板的方案选择（AskUserQuestion）

- **LLM 请求日志面板**：用户要求只看「何时开始/结束、token 数、内容大小」。之前只有 worker 的 `analysis_tasks` 队列状态，没有跨调用方的请求级账本 → 选定**新建统一账本**（而非复用现有表）。
- **金字塔面板**：用户要求竖着五列、小方块、按时间/父子排序、可滚动、一页一天 → 选定**共享时间轴对齐式**：块高 = 窗口时长，横向对齐即父子关系，一眼看出哪个父窗盖哪些子窗。

### 2. 统一 LLM 账本（commit `38a6f9a`，api 端点）

- 新建 `src/timetrace/server/llm_log.py`：`timed_chat_completion(client, *, caller, model, sink=None, prompt_chars=None, **create_kwargs)` 包装器 + `LLMRequestLog` dataclass + **进程级默认 sink**（`set_default_sink`，避免把 sink 一层层穿构造函数）。从 `response.usage` 取真实 token，含 `usage.completion_tokens_details.reasoning_tokens`（思考税）；出错时先落账再 re-raise，sink 自身异常吞掉不影响主流程。
- `server/db/sqlite.py`：新表 `llm_requests`（CREATE TABLE IF NOT EXISTS 进 `_SCHEMA`，索引 ts_start + caller,ts_start），`insert_llm_request` / `query_llm_requests`（caller/status/before_ts keyset 倒序翻页）/ `llm_request_stats`；另有 `get_summaries_overlapping(grain, start_ms, end_ms)`（`window_start < end AND window_end > start`，能捞到罩住当天的 week 窗）。
- 三个调用方接线：`OpenAINarrativeLLM.complete`（caller=`narrate`）、`VLMClient.describe`（caller=`worker_vlm`）、`ask_agent`（caller=`ask_agent`）。sink 在 `bootstrap.py::build_server_components` 里设默认。
- 新路由 `api/routes/llm_requests.py`：`GET /llm-requests`（limit≤500）+ `GET /llm-requests/stats`；`api/routes/summaries.py`：`GET /summaries?day=YYYY-MM-DD`（4AM 切割 `_day_bounds`，每 grain 查 overlapping，返回 description/evaluation/key_points/metrics_only/record_count/active_seconds/top_categories）。

### 3. 两个前端页面（commit `729fecc`）

- `frontend/src/pages/LlmLogPage.tsx`：统计卡（总数/错误/prompt/completion/reasoning token/平均时长）+ caller 筛选 chips + 7 列表格（时间/caller/模型/时长/入-出-思考 token/字符数/状态），4s 轮询。
- `frontend/src/pages/PyramidPage.tsx`：共享时间轴——`COLUMNS` week(52px)/day(64)/6h(78)/1h(108)/5min(168)，`PX_PER_HOUR=92`，24h 高泳道，块按 `(window_start-dayStart)/DAY_MS*TOTAL_H` 定位；颜色取 top category，实线=已叙述、虚线=pending、暗色=metrics_only；右侧 DetailPanel 显示流水账/重点/评价；日导航 + 15s refetch。
- 路由 `/pyramid`、`/llm-log` + 侧边栏入口（Layers / Cpu 图标）。
- 上线后账本立刻量化出**思考税 ≈ 2500 reasoning tokens/次叙述调用**，验证了 per-grain max_tokens 预算（5min 4000 / 聚合 6000-7000）的必要性。

### 4. 修 CI flaky（commit `84e3ad6`）

`test_tokens_revoke_by_suffix` 偶发红：随机 base64url token 的末 8 位若以 `-` 开头，argparse 把它当选项 → SystemExit(2)，约 1/64 概率。修为确定的无横杠 token `tt_live_deadbeefcafe12345678`。

### 5. 发现公网站点三周没更新

用户问「我咋没在 timetrace.yukirin.me/dashboard 看到呢？」→ 查 xcy docroot 时间戳停在 6 月 5 日。**根因：前端发布是纯手动步骤，没人记得跑**。临时手动 build+scp 止血，同时开始设计永久方案。

### 6. 前端发布工作流化（commit `9060941`，用户给了详细 spec）

用户拍板工作流版（原话：「纯手动步骤没人记得跑——脚本版只是把手动变便宜，病根(遗忘)没治；工作流版才是把它从"要记得"变成"不可能忘"」），并明确 spec：

1. **别把 xcy root 钥匙塞进 GH secrets**——xcy 是公网反代 VPS，给 CI 的应该是专用低权用户 `ghdeploy`，只对 docroot 有写权限。泄了顶多换静态文件，不是丢整台反代机。
2. deploy.yml 里做**第二个并行 job** `publish-frontend`，与后端 job 互不阻塞，失败只挂自己。
3. **先传带 hash 的资产、最后覆盖 index.html**，不做 path filter。
4. 留 `deploy/publish-frontend.sh` 当紧急手动兜底，注释写明「正路是工作流」。
5. nginx 给 index.html 加 `Cache-Control: no-cache`。
6. xcy 上建低权用户需要 root，一次性顺手做掉。

实施：

- **xcy 一次性 root 设置**：`useradd ghdeploy`（uid 1000，无 sudo）；`~ghdeploy/.ssh/authorized_keys` 写入专用 ed25519 公钥，前缀 `no-agent-forwarding,no-port-forwarding,no-X11-forwarding`；`chown -R ghdeploy:ghdeploy /var/www/timetrace.yukirin.me`（顺手修正 docroot 上残留的杂散 uid）；确认 rsync 在场、无 AllowUsers 限制。上传 secrets 前本地实测：能登录、能写 docroot、`sudo -n` 被拒。
- **GH secrets**：`XCY_SSH_KEY`（私钥传完即删本地文件）、`XCY_KNOWN_HOSTS`、`XCY_TARGET=ghdeploy@103.117.123.204`。
- **`.github/workflows/deploy.yml`**：`publish-frontend` job——checkout 对应 ref → setup-node 22（npm cache）→ `npm ci --prefix frontend && npm run build --prefix frontend`（含 tsc，等于每次部署顺带把前端类型把关）→ webfactory/ssh-agent 挂 `XCY_SSH_KEY` → known_hosts 写 secret → `rsync -rtz --exclude index.html` 先传资产、`rsync index.html` 最后覆盖（`XCY_PORT=22000`，DOCROOT 同上）。同款 fork-guard `if:`，timeout 10min。secrets 文档头更新为 4 后端 + 3 前端。
- **`deploy/publish-frontend.sh`**：紧急手动兜底，同样的 build + rsync 顺序，头部注释「正路是工作流」，XCY/XCY_PORT/DOCROOT 可 env 覆盖。
- **`deploy/nginx-timetrace.yukirin.me.conf`**：新增 `location = /index.html { add_header Cache-Control "no-cache"; }`（hash 资产保持 immutable 长缓存）。scp 到 xcy + `nginx -t` + reload；验证 `/index.html` 和 SPA 深链接 `/pyramid` 都带上 no-cache——**以后普通刷新就能看到新版，不用再 Ctrl+Shift+R**。

验证：push feature + deploy，run 28813718613 两个 job（deploy + publish-frontend）全绿；xcy docroot 里 index.html 属主 ghdeploy、时间戳为 CI 运行时刻、bundle `index-vSiPr3dl.js`、97 个 assets——CI 自己完成了首次发布。

### 7. SSH 改名同步

用户把 box 改名 `yukirin-server`，记忆文件 `reference-box-ssh-strategy.md` 同步更新（用户后来又补了 xcy:10022 隧道备用路由），MEMORY.md 索引补上缺失行。

---

## 遇到的问题与解决

### 问题1：XCY_KNOWN_HOSTS 第一次填错

**现象：** CI 首次跑 publish-frontend 时 host key 校验失败。
**原因：** 抓 `ssh-keyscan` 输出时把注释行（`# ...`）当成了 key 行填进 secret。
**解决：** 重新用实际的 `[host]:port ssh-ed25519 AAAA...` 行覆盖 secret。

### 问题2：「面板是不是之前写过？」——用户记混

用户提 LLM 请求日志面板时问「这个是不是之前写过？」。核查后确认：之前只有 worker 任务队列（`analysis_tasks`）和 audit 日志页（只读），**没有**跨 narrate/worker_vlm/ask_agent 的统一请求级账本，是新建不是复用。

### 问题3：公网前端三周 stale 的定性

一开始当一次性事故处理（手动 scp 补上），用户追问后定性为**流程病**：手动步骤必然被遗忘，止血不等于治病。最终方案对齐项目自己的部署纪律（CLAUDE.md「部署一律走工作流」）。

---

## 知识清单

- **安全发布静态站点的顺序**：带 hash 的资产先传、index.html 最后覆盖——任何时刻访客拿到的要么是旧页+旧资产（都还在，rsync 不删）要么是新页+新资产（已就位），不存在半更新窗口。配合 `index.html: Cache-Control no-cache` + `/assets/ immutable`，普通刷新即见新版。
- **CI 用低权用户发布**：专用 `ghdeploy`（无 sudo、key 禁转发三件套、只 owns docroot），root 钥匙不进 GH secrets。爆炸半径从「丢整台反代机」缩到「换静态文件」。
- **统一账本模式**：`timed_chat_completion` 包装器 + 进程级默认 sink（`set_default_sink`）——不用改每个调用方的构造函数链；sink 异常吞掉、业务异常落账后 re-raise，账本永远不影响主流程。真实 token 从 `response.usage` 拿，思考模型的 `reasoning_tokens` 在 `usage.completion_tokens_details` 里。
- **思考税实测**：box 的 35B 永远思考模型，每次叙述调用约 2500 reasoning tokens——per-grain max_tokens 预算（4000/6000/7000）就是按这个定的。
- **ssh-keyscan 输出要滤注释行**：`ssh-keyscan -p 22000 -t ed25519 <host>` 的 stdout 混着 `#` 注释，填 known_hosts secret 时只取 key 行。
- **argparse flaky**：随机 base64url 串若以 `-` 开头会被当选项，测试里要么用确定值要么显式 `--` 分隔。
- **Stream 调用暂不入账**：报告生成 + web `/agent` 聊天走 streaming（usage 在最后一个 chunk），本次未接线，是已知缺口。

---

## 待办 / 遗留

- [ ] **streaming AgentRunner 记账**：报告生成 + `/agent` web 聊天的 LLM 调用还没进 `llm_requests`（streaming，usage 在尾 chunk），用户已知情、明确延后
- [ ] 指标历史回填（metrics backfill）：卡在 window_switch 记录无分类，等 classifier-v2 + source_hash 自动重算（`infra/PLAN-CLASSIFIER-V2.md` 只是预备文档，不动手）
- [ ] 已知缺口（可选）：子窗叙述文本变化不会自动触发父窗重做（source_hash 只指纹指标/分类）；可加「子窗 updated_at 新于父窗 → 父窗 stale → 重叙述」检测
- [ ] 面板视觉迭代：等用户在公网站点实际看过 `/pyramid` 和 `/llm-log` 后反馈
- [ ] classifier-v2 + 嵌入切 Qwen3-VL embserver / 删 nomic：按围栏继续 defer
