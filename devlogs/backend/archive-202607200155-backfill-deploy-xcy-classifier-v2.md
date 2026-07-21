# 语义搜索接线 + 未分类回填 + 跨境部署修复 + 分类器 V2 方向

**日期：** 2026-07-20（归档日；会话实际跨 2026-06-09 ~ 2026-07-05 多次续接）
**目标：** 收尾语义向量检索生产接线（B 方案）、回填 box 上未分类记录、修复 GH→box 跨境部署断链、梳理分类器 V2 方向（只规划不实装）

---

## 背景

- 上一归档 [archive-202606050148-semantic-vector-search.md](archive-202606050148-semantic-vector-search.md) 把 `/v1/search/text` 双通道检索做完了（`4a74c26`），但**生产接线**（把 `embedding_client` 暴露到 `app.state`）刻意推迟到 audit agent 提交 app.py 之后，避免共享工作区打包对方改动。本会话第一件事就是收尾这 3-4 行。
- box（家里小主机）当时有约 4764 条 "described 但未分类" 的历史记录（旧 worker 时代遗留），用户授权回填。
- GH Actions → box 的部署经 2v4G 中继 frp 隧道（121.43.33.13:10089），此前一直间歇失败。
- 分类器当时刚删掉 KNN 投票（[archive-202606021128-llm-classification-6cat-knn-removal.md](archive-202606021128-llm-classification-6cat-knn-removal.md)），用户开始想 V2 长什么样。
- 金字塔/叙述层由**另一个 agent 并行推进**（348b9cf 指标级联 / 4183567 rollup loop / 6db985f MCP 修复等），本会话只relay报告、做冲突规避规划，那部分的完整叙事在 [archive-202606222322-memory-pyramid-phase1.md](archive-202606222322-memory-pyramid-phase1.md) 和 [archive-202606270722-narrative-loop-search-golive.md](archive-202606270722-narrative-loop-search-golive.md)。
- 公网前端 timetrace.yukirin.me = 静态 build 手动 scp 到 xcy VPS nginx docroot，deploy 工作流只管 box 后端。

---

## 操作步骤

### 1. B 方案接线：语义向量检索进生产（fa87ab8）

audit agent 提交后阻塞解除，改两处共 3-4 行：

- `src/timetrace/server/api/app.py`：`create_app(...)` 加参数 `embedding_client: EmbeddingClient | None = None`，挂 `app.state.embedding_client`（import 走 TYPE_CHECKING）
- `src/timetrace/server/bootstrap.py`：`create_app` 调用处传 `embedding_client=embedding_client`

全量测试 479 passed → commit `fa87ab8` → push。`/v1/search/text` 的 semantic 通道在生产从此可用（embed 失败仍优雅降级关键词-only）。

### 2. box 未分类记录回填（用户授权，先备份+canary）

用户暂停 client 后，ssh 到 box（当时别名 `GTi13-Ultra`；2026-07-05 起改名 `yukirin-server`，见第 7 节）执行回填：

1. **备份** DB（`/home/vanilla/TimeTraceData/db/timetrace.db`，WAL）
2. **Canary**：先放一小批 → 撞坑（见问题 1）
3. 修复后全量放行 4742 条（re-enqueue = `analysis_results.status='pending_vlm'` + `locked_at/next_retry_at/error_msg=NULL` + `retry_count=0`，镜像 `records.status`）
4. 监控至完成：24838 条记录全分类，`error_final=8`，`described_unclassified=0`

box 上**没有 sqlite3 CLI**，只读查询用 python3 标准库：

```bash
python3 -c "import sqlite3; db=sqlite3.connect('file:/home/vanilla/TimeTraceData/db/timetrace.db?mode=ro', uri=True); ..."
```

辅助脚本留在 box 的 `/tmp`：`tt_apply.py`（backup/canary/all 三模式）、`tt_verify_deploy.py`、`tt_diag.py`、`tt_prog.py`、`tt_rate.sh`。

### 3. 部署模型文档纠偏 + main 追平（bc923dc）

用户质问 CLAUDE.md 里 "origin/main 是冻结的 v1 legacy、pywin32 无平台 marker、Linux uv sync 会失败" 的说法（"我们不早就跑过工作流了？你再冻结个啥？"）。核实后确认是**过时残留**：origin/main 带 `pywin32>=306; sys_platform == 'win32'` marker、Linux sync 正常、且已是重构期近期代码。修 CLAUDE.md 两处（CI/CD 触发 = push 到 deploy 分支 + workflow_dispatch；删掉冻结论），commit `bc923dc`，把 main fast-forward 追平 feature/refactor-split（领先 96 commit 的干净 ff），用户授权后 `git push origin main`。此后 dev 在 main、部署推 `main:deploy`。

顺带清理：`43e0dbd` 把 `.claude/scheduled_tasks.lock` 加进 `.gitignore`（**只加这一行**，`.claude/` 下的 skills/ 和 settings.local.json 是 tracked 的，不能整个目录忽略）。commit message 含中文引号在 Git Bash 下会 GBK 乱码 → 改走 `git commit -F .git/COMMIT_TT_MSG.txt`。

### 4. 跨境部署修复：HK xcy 隧道 + GH secrets

**用户诊断**（纠正我此前"安全组规则到期"的猜测）：境内阿里云 2v4G 会掐境外 IP 的入站，GH runner 在境外 → `Connection closed by 121.43.33.13:10089`。

修法（按用户指定"别走日本，走 xcy"）：

1. box `~/.config/frp/xcy.toml` 加 `[[proxies]] name="ssh-GTi" localPort=22 remotePort=10022`——xcy 防火墙全放（ufw inactive、iptables ACCEPT）；xcy:10089 是 RDP 不是 ssh
2. 用户曾担心 10022 撞 xcy 自己的 sshd——实际 xcy sshd 在 22000，不撞，维持 10022
3. 更新 3 个 GH secrets：
   - `DEPLOY_TARGET_USER_HOST=vanilla@103.117.123.204`
   - `DEPLOY_TARGET_PORT=10022`
   - `DEPLOY_KNOWN_HOSTS=[103.117.123.204]:10022 ssh-ed25519 <公钥>`
4. 部署验证：Phase-B（audit 日志的 `queued_at/done_at` 列 + latency/suggested/trace 写入）在 box 上线；回填批次因走 raw SQL 绕过 `mark_pending`，`queued_at` 为 NULL（符合预期）

此后 deploy 工作流走 GH runner → HK xcy:10022 → box sshd，绕开境内掐境外那一段。

### 5. 分类逻辑答疑 + 分类器 V2 方向（只规划）

用户问"1分/3分/5分/7分投票还在吗"。澄清现状（`src/timetrace/server/rules/engine.py`）：KNN 投票已删；`SOURCE_WEIGHTS`（user_edit 5.0 / user_confirm 3.0 / rule 2.0 / vlm 1.5）只在用户介入过才有意义；默认路径是纯 VLM 单候选 → `confidence = top/(top+second)` 恒为 1.0。

用户头脑风暴 V2：删投票做减法、纯 VLM+prompt、**KNN 改当加速器**（先用嵌入算近邻，距离足够近就跳过 VLM；阈值要拿真实数据校准）、聊天式纠错、批量重置重打分、"梦游模式"自纠（挂靠金字塔夜间 rollup）。关键纠正：我以为 KNN 缺图像嵌入是"鸡生蛋"问题，用户指出**多模态嵌入模型 qwen3-vl-embedding-2b 已在 box LMS 上跑着**（TimeTrace 库存的 text_embedding 是 nomic 纯文本，另一回事）。

随后派研究任务做校准（结果在 `D:\Temp\qwen-vl-emb-lab\knn_study\RESULTS.md`，仓库外）：225 帧真实数据、2048 维、cosine 距离 → **T=0.13 时 precision 0.955（均衡）/ ~0.988（按生产分布加权），约省 30% VLM 调用**；嵌入距离整体不是强分类器（类间重叠 20.7%），只在近端可靠 → 定位只能是加速器；同 app 跨活动会错（Terminal/Edge/Telegram）→ 需要 app_name 一致性护栏。

产出 `infra/PLAN-CLASSIFIER-V2.md`（**刻意不 commit**）：标注"⚠️ 预备稿 · 不执行 · 仅记录方向" + 写作时间戳 2026-06-09 02:46，三方向：① 纯 VLM + 确定性硬覆盖；② KNN 加速器（校准阈值 + cache-poison 风险）；③ 分层纠错（聊天纠错走 `apply_label` / 批量重置复用 re-enqueue / 梦游模式=金字塔夜间 rollup）。

用户同场拍板的前端减法：删掉投票明细展示（"先做减法，不做加法"）。

### 6. MCP 可用性 4 点反馈 → 冲突规避移交

外部用户反馈 MCP 接口：limit 太低、无分页、无绝对时间窗、中日英混合搜索坏。逐条对真实代码核实（`src/timetrace/server/agent/tools.py` / `mcp_layer/server.py` / `db/sqlite.py`）：

- `_MAX_LIMIT=200`（tools.py:35）→ 提到 2000
- `db.query_records` 有 `cursor` 参数但没暴露 → 加 `cursor`/`next_cursor`
- 只有 `hours_back` 没有绝对窗 → 加 `start_iso`/`end_iso`（抄 `query_stats` 里 `_parse_iso_local` 的现成模式）
- `_fts_query`（sqlite.py:287）把整句多词包成**一个连续短语** → CJK+英文混排必挂；修法 = 按空白切 token 后 AND 多个短语

因为这四个点全落在 pyramid agent 正在改的文件上，**我只写 spec 交给用户转发，不动代码**，避免撞车。后续 agent 以 `6db985f` 落地（详见 pyramid Phase 2-3 归档）。

### 7. box 改名 + client outbox 事故排查 + 公网前端决策

- **改名（2026-07-05）**：hostname `gti13-ultra` → `yukirin-server`。SSH config：`yukirin-server`=LAN 192.168.2.105、`yukirin-server-2v4G`=frp 121.43.33.13:10089、`2v4G`=中继本机、`xcy`=VPS 本机（103.117.123.204:22000）；xcy:10022→box:22 隧道**没有 config 别名**，裸写 `ssh -p 10022 vanilla@103.117.123.204`。已同步进记忆 `reference-box-ssh-strategy`。
- **6-12 无数据虚惊**：QQ bot 问"今天有什么内容"答不上来 → 排查 = ThinkBook 的 client outbox 积压 ~26h（06-11 21:49 → 06-12 23:34），localhost:8765 端点依赖 `ssh -L` 隧道 + 笔记本睡眠；**自愈**（967→186 行）。client.toml 同时配了 localhost 和公网两个端点，故障转移行为未验证——留作待办。
- **时长口径误判纠正**：pyramid agent 怀疑 clamp/ts_end 导致时长低估，我拿真机数据推翻：clamp 从不触发（全库 0 条 >5min），真因是采样切片模型（平均 ~11.5s 切片、间隙/idle 不计）+ 设备覆盖（后续 pyramid 归档把口径钉成"本机捕获活跃下限"）。
- **公网前端烂 3 周**：另一个 agent 手动 `npm run build` + scp 把 timetrace.yukirin.me 更新到 729fecc（/pyramid + /llm-log 上线），并指出根因 = deploy 工作流只管 box 后端、前端发布全靠人记。用户问"工作流版还是脚本版，你定一个"→ **我选工作流版**：病根是遗忘不是贵，脚本版治不了；且对齐 CLAUDE.md "部署一律走工作流"。给了 4 条 spec（见知识清单）。

---

## 遇到的问题与解决

### 问题1：canary 撞 "Context size has been exceeded"，VLM 健康门 SLEEPING

**现象：** 回填 canary 批次大量失败，VLMHealthGate 连续失败 3 次后进入 SLEEPING。
**原因：** LM Studio `lms load` 默认 context = **4096**，大截图 prompt 直接溢出。
**解决：** 重新加载模型 `lms load qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive -c 8192 --parallel 2`。KV cache = context × parallel，8192×2 与旧 4096×4 显存持平；parallel=2 对齐 worker 的 `vlm_concurrency=2`。健康门靠探针自动恢复。**canary 先行救了全量 4742 条**——若直接全放，全部得失败一遍。
**后续：** 此坑已写进记忆 `project-lmstudio-server-not-autostart`；再往后叙述层又撞了同族问题（PARALLEL 切分上下文），见 [archive-202606270722-narrative-loop-search-golive.md](archive-202606270722-narrative-loop-search-golive.md)。

### 问题2：CLAUDE.md 部署描述过时，我向用户复述了错误结论

**现象：** 我说"origin/main 冻结 v1、Linux uv sync 会失败"，用户当场质疑。
**原因：** 照搬了仓库里的过期文档，没验证。
**解决：** 核实 origin/main 实际状态（有 pywin32 marker、代码很新、feature 干净 ff）→ 修文档 + ff main + push。**教训（已在记忆里）：对系统机制下结论前先撞真机数据。**

### 问题3：Git Bash 下中文 commit message 编码损坏

**现象：** `git commit -m "…中文引号…"` 报 `fatal: ... outside repository`。
**解决：** message 写入 `.git/COMMIT_TT_MSG.txt`，用 `git commit -F`。

### 问题4：部署 "Connection closed by 121.43.33.13"

**现象：** push 到 deploy 分支后工作流 SSH 阶段失败。
**原因：** 境内阿里云掐境外 GH runner IP（用户诊断，推翻我先前"安全组临时规则到期"的猜测）。
**解决：** 见操作步骤 4——HK xcy:10022 隧道 + 3 个 GH secrets。已写进记忆 `project-deploy-frp-10089-firewall`。

### 问题5：共享文件冲突风险（MCP 修复 vs pyramid agent）

**现象：** 用户问"这部分你会不会和 agent 那边的修改冲突？"
**解决：** 会——四个修复点全在 agent 活跃文件上。**我只规划+写 spec，用户转发给 agent 执行**（`6db985f` 落地）。同类分工原则后来也用在公网前端工作流化上。

---

## 知识清单

- **LM Studio KV cache 预算**：`context × parallel`，两者可互换着调。worker 并发几路就给几个 parallel，别用默认 4096×4 跑大截图/长 prompt。
- **box 无 sqlite3 CLI**：只读查库用 `python3 -c "sqlite3.connect('file:...?mode=ro', uri=True)"`。
- **re-enqueue 未分类记录**的完整字段集合：`analysis_results.status='pending_vlm'` + `locked_at/next_retry_at/error_msg=NULL` + `retry_count=0`，并镜像 `records.status`；绕过 `mark_pending` 的 raw SQL 批次 `queued_at` 会是 NULL。
- **跨境部署**：境内云（阿里云）会掐境外 IP 入站；GH runner 在境外 → 部署链路要经 HK 中转（xcy:10022→box:22 frp）。改的是 3 个 GH secrets，不动工作流文件本身。
- **SSH 别名地图（2026-07-05 版）**：`yukirin-server`=LAN、`yukirin-server-2v4G`=frp 10089、`2v4G`=中继本机:22、`xcy`=VPS 本机:22000；xcy:10022 隧道无别名。
- **KNN 加速器校准结论**（qwen3-vl-embedding-2b，cosine，225 帧）：T=0.13 → precision ~0.988（生产加权）、省 ~30% VLM；嵌入距离只在近端可靠，别当分类器用；同 app 跨活动要加 app_name 一致性护栏。
- **分类现状**：KNN 投票已删，SOURCE_WEIGHTS 只在用户介入后生效；单候选 confidence 恒 1.0，别再把它当模型置信度展示。
- **公网前端工作流化 spec**（转给实现 agent，按分工不由我动手）：
  1. xcy 上建**专用低权用户**（如 `ghdeploy`）只对 docroot 可写，别把 root 钥匙放 GH secrets；新增 `XCY_SSH_KEY`/`XCY_KNOWN_HOSTS`
  2. deploy.yml 加第二个 job `publish-frontend` 与后端并行：runner 上 `npm ci && npm run build` → scp 到 docroot，**先传带 hash 的资产、最后覆盖 index.html**
  3. 触发跟 deploy 分支 push 走，不做 path filter（一分钟级幂等，比猜 diff 可靠）
  4. 留 `deploy/publish-frontend.sh` 作紧急手动兜底；nginx 对 `index.html` 加 `Cache-Control: no-cache`
- **Git Bash 中文 commit message** 用 `git commit -F <文件>`，别用 `-m`。
- **多 agent 分工原则**：落在别人活跃文件上的需求，写 spec 移交，不动代码。

---

## 待办 / 遗留

- [ ] **公网前端工作流化**：方向已定（工作流版 + 4 条 spec），等实现 agent 落地；xcy 低权用户那步需要 root 一次性操作
- [ ] **分类器 V2**：`infra/PLAN-CLASSIFIER-V2.md` 预备稿（刻意未 commit）；校准数据已备（T=0.13）；正式设计未启动；前端投票明细删除待定
- [ ] **client 故障转移未验证**：OutboxSender 在 localhost 隧道死掉时是否 fallback 到公网端点（6-12 那 26h 积压的教训）
- [ ] **金字塔 go-live 后续**：历史 backfill 等分类回填、叙述层开放周窗（详见 pyramid 两份归档）
- [ ] **可选**：给 xcy:10022 隧道加 ssh config 别名（如 `yukirin-server-xcy`）
