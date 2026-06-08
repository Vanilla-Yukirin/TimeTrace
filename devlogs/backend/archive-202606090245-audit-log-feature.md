# 审计日志页（/audit）：规划 → Phase A 只读 → 置信度证伪 → Phase B 持久化阶段指标

**日期：** 2026-06-09
**目标：** 给 TimeTrace 加一个 LiteLLM-logs 风格的「逐条记录审计日志」页：一行一条、按时间倒序、行内不显图、展开看详情，实时显示每条记录的管线状态 + 活动/收到时间 + 各阶段延迟 + 旗标；并把 worker 先前算出又丢弃的分类依据持久化。

> 本会话前段的吉祥物/favicon/logo 换猫娘 + 调色工作已单独归档在 [frontend/archive-202606030433-mascot-favicon-rebrand.md](../frontend/archive-202606030433-mascot-favicon-rebrand.md)，本篇不重复，专收**之后**的 audit 日志特性全链路。

---

## 背景

- 用户截图 LiteLLM Dashboard 的请求日志，想要 TimeTrace 的对应物：**一条一条的审计日志**（不是时间轴可视化），用来**实时盯每条记录目前什么状态**（采集/排队/处理/完成/失败）、实际时间 vs 发过来时间、各种延迟、是否需要 VLM、是否分类完成。
- 关键动机后来被真机数据印证：box 上当时有 **3807 条记录卡在 pending_vlm 排队**（用户在重跑一批东西），audit 页正好能盯这个。
- 定位：本特性是「分层记忆金字塔」(infra/PLAN-BETTER-AGENT.md) 的**地基**——Phase B 填好的 `category_suggested/confidence/decision_trace/queued_at/done_at` 列，将来 rollup 直接复用。

---

## 操作步骤

### 1. 规划（research workflow → 用户拍板）

派 6 reader 并行摸 schema/worker/采集ingest时序/API/前端/派生指标，综合成方案。**最关键发现**：worker 不打任何阶段时间戳；`analysis_results` 的 `vlm_latency_ms/confidence/category_suggested/decision_trace/vlm_model` 列**都存在但没人写**；`updated_at` 被 embedding 写入污染、不能当完成时刻。→ 分两阶段：**A=只读现有数据**，**B=补埋点**。

AskUserQuestion 拍板：**先 A 再 B** / **双进程**（box 端确认 16789 条 100% 有 client_record_id → 上传延迟对每条有意义）/ **行内就地展开** / **4 秒轮询**。

### 2. Phase A —— 只读审计页（commit `84a96c4`）

- 后端 `query_audit_records`（DESC + `(ts_start,id)` 复合游标 + LEFT JOIN analysis_results + 截图滞后/计数子查询）；`GET /v1/audit/records`（`require_principal`）派生状态机（captured/queued/retry_waiting/processing/done/skipped_no_image/labeled/failed）+ 延迟 + 旗标；**读 `analysis_results.status` 为真值源**（records.status 不翻 processing）。
- 前端 `AuditPage` + `AuditRow`（行内展开、懒加载截图）+ `StatusChip` + `useAuditRecords`(4s 轮询、tab 隐藏停、keepPreviousData) + 侧栏「日志」入口。
- 479→ 全测通过。**对抗审查**(4 维)2 处 low 已修：无图 `needs_vlm` 按 `screenshot_count>0` 收敛终态、详情空串描述回退 `??`→`||`。
- 部署：后端走 `deploy.yml`（用户触发，快——`/v1/audit/records` 401 即上线、`/v1/nonexistent` 404 做判别）；前端 VPS scp（抖动链路 `SCP_OK=0` 但 tarball 实际完整，靠换入时 tar+index 校验 + 逐资产 200 + bundle 内 grep `审计日志` 验真）。

### 3. 「Phase B 是什么」—— 用户没听懂 + 推回来重新对齐

用户："我怎么没听懂。你是不是该更新你的记忆了。看 devlogs？plan？commit？grep？ssh 看服务器现状？**先别改代码**"。照做，撞真相：
- **「Phase B」是我自己编的内部分期、项目里没这词**——它的真名 = **PLAN.md 未完成项 #6**（`decide_category` 算出 confidence+trace 但 worker 在 `loop.py:223` 用下划线扔了）+ 两个延迟时间戳。
- 只读 ssh box（frp 抖、`sqlite3` CLI 没装→`python3 -readonly`）：16789 记录 **100% 双进程**、**3807 pending 积压**、`confidence`/`vlm_latency_ms` 写入数 **0/16789**、`queued_at`/`done_at` 列不存在。
- 写记忆纠正自己的「Phase A/B」脑补。

### 4. 置信度证伪（用户："置信度是啥？模型输出？"）

读真代码：`loop.py:228` 给 `VlmPrediction(confidence=1.0)` **写死 1.0**——模型不输出置信度；`engine.py:74` 的 confidence = `top/(top+second)` 加权投票占比（rule 2.0、vlm 1.5）。代入只有三档：**1.0**（同意/单源）/ **0.571**（规则盖掉不同意的 VLM）/ **0.0**。**结论：它是「规则 vs VLM 有没有冲突」指示器、不是模型把握**。→ **推翻**我之前「达标阈值值得做」的说法；真正还有价值的只剩**延迟时间戳 + category_suggested + decision_trace**。更新记忆。

### 5. SSH 主机指纹核验（CapsWriter，顺手）

用户回家了（LAN 直连 `192.168.2.105`），rsync CapsWriter 到 box 时卡在首次连接的 ED25519 指纹确认。我用**已可信的 frp 路由**读 box 的 `/etc/ssh/ssh_host_ed25519_key.pub` 算指纹，与屏幕上 `SHA256:KIB6qAhNL4cCzPPzZfU2JJADRptkg1RfXgFNmN8cwRQ` 逐字符比对 → 一致（`root@gti13-ultra`）→ 放心 yes。

### 6. Phase B —— 持久化阶段时间戳 + 建议 + 投票明细（commit `2ead8a7`）

用户给了详细 spec（一笔独立自包含 commit，别跟金字塔 summaries/signals 搅一起）：
- **schema**：`analysis_results` 加 `queued_at`/`done_at`（可空 INTEGER，`_migrate` 幂等 ALTER 照 `text_embedding` 先例）。
- **DB 方法**：四个入 `pending_vlm` 的路径都记 `queued_at`（`mark_pending`/`requeue_skipped_for_vlm`/`mark_error_retryable`/`reclaim_stale_tasks`）；`transition→vlm_done` 记 `done_at`；`save_description(*, vlm_model, vlm_latency_ms)`；`set_category_final(*, category_suggested, confidence, decision_trace)` 用 **`ON CONFLICT ... COALESCE(excluded.x, x)`** —— 手动 `apply_label` 不传这些 → 保留既有 VLM trace 不抹成 NULL。
- **worker `loop.py`**：`describe()` 用 `time.monotonic` 掐表写 `vlm_latency_ms`；`getattr(self._vlm,"model",None)` 写 model；不再扔 `_conf/_trace`，存 `category_suggested=payload["category"]` + `json.dumps(trace)`。
- **路由**：派生 队列等待(`locked_at−queued_at`)/VLM耗时/总延迟(`done_at−created_at`)/端到端(`done_at−ts_start`)，返回 `decision_trace`；**砍 `classification_met`**。
- **前端**：详情「VLM建议→最终」(仅 trace 确为规则命中才标“规则改成”) + 投票明细(`TraceKV` 解析 JSON)；隐藏置信度%。
- 481 passed。**对抗审查**(4 维、17 agent、~87 万 token)3 处确认全修：① `reclaim_stale_tasks` 漏记 `queued_at`→ 崩溃恢复行 queue_wait 虚高；② `AuditRow` 类型 JSDoc 过期(还提已删的 classification_met)；③「规则改成」标签在用户反馈改标签(`feedback action=edit` 只改 category_final、不动 suggested/trace)时误判 → 改成 trace-aware（rule 命中且 cat 匹配才标“规则改成”，否则中性“改为”）。
- commit `2ead8a7` push 到 feature（sync）。**部署留给用户**（有回填在跑、时机用户定）。

---

## 遇到的问题与解决

### 问题1：Phase A 前端 VPS 部署 `SCP_OK=0` 但其实成功
**现象：** 抖动链路 scp 的尺寸回读反复 mismatch/fail，8 次 `SCP_OK=0`；但换入循环里 tar 解包成功、live bundle 变成新的。
**原因：** 失败的是 `wc -c` 回读（网络），不是数据传输；完整 tarball 实际落了。
**解决：** 换入时的 `tar -xzf` + `test -f index.html && ls assets/*.js` 才是真完整性闸；再逐资产 HEAD 200 + `curl bundle | grep 审计日志` 验代码确在 → 确认干净。

### 问题2：push GitHub 失败（clash 劫持）
**现象：** `git push` → `Could not read from remote repository`，5 次重试全败。
**原因：** clash fake-ip 劫持 github SSH（本项目老毛病），非代码/权限。
**解决：** 不硬刚网络，交用户手动 push（用户 `gh`/`git push` 第二次就成）。

### 问题3：frp 隧道抖 + box 无 sqlite3 + uv run 锁文件
- frp（用户在外网）抖得厉害，简单命令能过、稍长就 `Connection closed`→ 重试 6~8 次撞窗口。
- box 没装 `sqlite3` CLI（项目用 aiosqlite）→ 改 `python3 - <<'PY' ... sqlite3.connect("file:...?mode=ro",uri=True)`。
- `uv run` 想重 sync venv 但 `timetrace-client.exe` 被占用（os error 32）→ 加 `--no-sync` 跳过同步直接跑 ruff/pytest。

### 问题4：「Phase B」脑补名词 + 置信度误框
**现象：** 我拿自编的「Phase A/B」当用户该懂的，又把「置信度达标」当值得做的。
**原因：** 没对着 PLAN/真代码说话、凭规划印象。
**解决：** 撞 PLAN #6 + `engine.py`/`loop.py:228` 真代码，证实置信度是投票占比写死 1.0、非模型概率，**当场推翻自己**并更新记忆。教训进 [[feedback_verify_against_real_data]]。

---

## 知识清单

- **审计页可观测性设计**：状态真值源是 `analysis_results.status`（records.status 不翻 processing）；派生延迟分两类——服务端单时钟干净的（队列等待 `locked_at−queued_at`、总延迟 `done_at−created_at`）和跨客户端/服务端时钟会偏移的（上传延迟 `created_at−ts_start`、端到端 `done_at−ts_start`，单进程 client_record_id 为空 → 标 N/A 不伪造 0）。
- **置信度真相**：VLM 不输出置信度（`loop.py:228` 写死 `confidence=1.0`）；`engine.py:74` confidence=`top/(top+second)` 投票占比，实际只 1.0/0.571/0.0，是「规则vs VLM 冲突」指示器、**别拿来当分类质量阈值**。
- **SQLite UPSERT 保留语义**：`ON CONFLICT(pk) DO UPDATE SET x = COALESCE(excluded.x, x)` —— 新值为 NULL 时保留旧值（手动 relabel 不抹 VLM trace），worker 传真值时覆盖。
- **`_migrate` 幂等加列**：`PRAGMA table_info` → set → `if col not in cols: ALTER TABLE ADD COLUMN`；新列同时加进 `_SCHEMA`(新库) 和 `_migrate`(老库)，加在末尾对齐 append 顺序、可空、只对新数据生效。
- **SSH 主机指纹核验**：经一条已 known_hosts 信任的备用路由（如 frp alias）`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`，与首连提示的 SHA256 比对，比盲敲 yes 靠谱。
- **抖动链路部署判真**：`SCP_OK=0` ≠ 坏；以「换入端 tar 解包+关键文件校验」为完整性闸，再逐资产 200 + bundle 内容 grep 验真。
- **环境绕坑**：box 无 sqlite3→`python3 -readonly`；`uv run` 锁 exe→`--no-sync`；frp 在外网抖→重试撞窗口；GitHub push 被 clash 劫持→交用户手推。
- **box 真机基线（2026-06-09）**：16789 记录 100% 双进程；当时 3807 pending 积压（用户重跑）；Phase B 前 confidence/vlm_latency 零写入、queued_at/done_at 列不存在。

---

## 待办 / 遗留

- [ ] **部署 Phase B**（`2ead8a7`，已 push 未部署）—— 用户定时机（有回填在跑）。走 `feature/refactor-split:deploy` 自动部署：重启时 `_migrate` 幂等加 `queued_at/done_at`（对 16789 行安全）；回填从 pending_vlm 续跑会成为**第一批有完整时延数据的记录**；前端需一起 VPS 部署才看到「投票明细/VLM建议」。部署后盯 `journalctl --user -u timetrace-server.service` 看 vlm_done 正常、error_final 不飙。
- [ ] **分层记忆金字塔下一笔**：summaries/signals 表 + rollup，直接复用本笔填好的 `category_suggested/confidence/decision_trace/queued_at/done_at` 列（设计见 infra/PLAN-BETTER-AGENT.md）。
- [ ] 旧记录无法回填这些列（时刻已过）→ 审计页永久显「—」，这是诚实的、非 bug。

> 敏感信息核查：本会话无 token/key/密码明文外泄；SSH 指纹 `SHA256:KIB6q...` 是**公钥指纹**(非私钥、非密钥)，frp IP/别名属自有拓扑，均无需脱敏。
