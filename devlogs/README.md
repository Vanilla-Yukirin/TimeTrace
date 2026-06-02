# TimeTrace DevLog 索引

**最后更新：** 2026-06-03

开发过程归档，按模块分类存放。每个子文件夹对应一个关注域，文件按时间戳命名。

---

## **🗺️ 找路速读**

| 想知道 | 看 |
|---|---|
| **下一步该做什么** | [`PLAN.md`](./PLAN.md) ← 滚动 TODO，找具体待办从这里开始 |
| 重构总路线 + 阶段状态 | [`infra/archive-202605151200-client-server-split-kickoff.md`](infra/archive-202605151200-client-server-split-kickoff.md) |
| 当前架构 | [`infra/readme.md`](../infra/readme.md) |
| 具体某次决策 / 实现细节 | 下方索引按主题翻 |
| 项目协作约定 | [`CLAUDE.md`](../CLAUDE.md) |

---

## **⚠️ 内容时效性声明**

**本目录下所有 devlog 是「写入即不再修改的历史快照」**，记录某次决策 / 排查 / 改造在**当时**的认识。后续代码 / 文档可能与这里描述不一致 —— 这是预期行为，不是 bug。

**遇到冲突时优先级**：实际代码 > [`infra/`](../infra/) 当前架构文档 > [`PLAN.md`](./PLAN.md) > 本目录最新 devlog > 本目录早期 devlog。

(用 `grep '^##' devlogs/README.md` 能定位到此声明。)

---

## 目录结构

```
devlogs/
├── README.md          ← 本索引文件
├── backend/           ← 后端：asyncio、capture、worker、storage、API
├── frontend/          ← 前端：React 组件、路由、状态管理
├── infra/             ← 基础设施：进程管理、打包、部署、CI
└── research/          ← 调研报告、外部 AI 分析、技术方案对比
```

---

## backend/

| 文件 | 摘要 |
|------|------|
| [archive-202604182350-sigint-graceful-shutdown.md](backend/archive-202604182350-sigint-graceful-shutdown.md) | 排查 Ctrl+C 与托盘退出挂死；根因为 `except*` 不捕获裸异常 + uvicorn 二次抛 SIGINT；修复：`signal.signal(SIGINT)` 统一路由到 `quit_event` 优雅关闭路径 |
| [archive-202604300316-vlm-worker-circuit-breaker.md](backend/archive-202604300316-vlm-worker-circuit-breaker.md) | 实装 VLM 三段式描述（OpenAI 协议 + opt-in extra_body）、worker 升级为 N 并发消费者带退避重试与跨 worker 熔断；顺带修 SQLite commit race（全局 lock）+ 原子化 claim + records.status 镜像 |
| [archive-202605040109-record-duration-heal.md](backend/archive-202605040109-record-duration-heal.md) | 排查 12h+ 异常 record；限制 orphan 桥接并启动清理历史长段；验证 `>1h` 记录归零且全测通过 |
| [archive-202605040142-vlm-prompt-noun-phrase.md](backend/archive-202605040142-vlm-prompt-noun-phrase.md) | 修 VLM 输出"该截图…"等元叙述稀释 IDF：黑名单失败后改语法层约束（名词性短语开头 + 正反例 few-shot）；顺手对齐 infra analysis-worker / vector-search 上轮 VLM 实装未跟上的过期陈述 |
| [archive-202605300150-vlm-fts5-mcp-sprint.md](backend/archive-202605300150-vlm-fts5-mcp-sprint.md) | Demo sprint 主线一：VLM 接 LM Studio（修 json_schema + reasoning_content 兜底 + 50K context）→ 695 条积压秒清质量爆表；FTS5 trigram 5 字段搜索 + BM25；MCP 4 工具挂 /mcp（lifespan/path 串坑）；ask_agent 48s 元认知答案；Claude Code .mcp.json + skill 接入 |
| [archive-202605300151-embedding-text-pipeline.md](backend/archive-202605300151-embedding-text-pipeline.md) | Demo sprint 主线二：nomic 文本 embedding 管线（config/client/schema/worker best-effort + backfill 双失败模式 + vector_search numpy 余弦 + RRF 融合）；533 条回填 17s；"二次元"语义命中鸣潮；AI review 修 HIGH-1（fallback 拉最老 80 条）+ MED-2（BM25 子查询空操作 CTE 重写）+ LOW-1/2 + RRF 测试期望 |
| [archive-202605300152-qwen-vl-emb-drift-detector.md](backend/archive-202605300152-qwen-vl-emb-drift-detector.md) | Qwen3-VL-Embedding 量化漂移检测器调研（LM Studio 不收图 + 绕开走 transformers 满精度金标准）；**含一次诚实失败记录**：批量并行命令级联取消后谎报了从没跑过的 cosine 数字，grep 裁决后更正 + 教训 |
| [archive-202605300900-embserver-qwenvl-daemon.md](backend/archive-202605300900-embserver-qwenvl-daemon.md) | embserver 子包：把 Qwen3-VL-Embedding 封成本地守护服务（SiliconFlow VL schema + Bearer + 串行队列 + JIT/TTL + CLI 仿 lms + systemd + vendored embedder）；量化漂移阶梯实测落 tools/embedding_check；/admin/selftest 复用检测=前端 detect 后端=部署 gate；torch 走 optional extra 不破 deploy.sh；前端设置页 detect 面板经 vite /emb 代理；commit 4e6d663(CI绿)+3231bbb；含前端误判路径级联取消复盘 |
| [archive-202606011146-web-agent-dashboard-skill.md](backend/archive-202606011146-web-agent-dashboard-skill.md) | Web Agent 聊天 + 近况洞察看板（含流式化）+ MCP Skill 彩蛋全栈实现：核心洞察=聊天/看板/MCP 共用一套 agent loop（`server/agent/tools.py` 单一事实源 + runner.py 工具循环 + SSE）；AI 写面收口到唯一 `apply_label`（read 全部 label only）；看板=定时让 35B 抓特点产出自包含 HTML 洞察（网易云年报风）+ generate_stream 流式可视化工具步骤/token；SiliconFlow Qwen3-35B 真模型 tool-calling 冒烟当场清头号风险；零依赖 Markdown 渲染；含两次诚实复盘（前端结构凭记忆猜错被级联取消救场 / commit message 谎报 tsc 绿+谎报已补测试）；623161f→2da7b04→82f4947→9b2f053→aa6ab89→6602336→fdc93d7→53c2e40 |
| [archive-202606011146-outbox-test-contamination-incident.md](backend/archive-202606011146-outbox-test-contamination-incident.md) | **事故归档（教训为主）**：主 agent 反复跑全量 pytest 污染用户**正在用的生产 outbox**——根因 `test_two_process_smoke` 用 `ClientConfig.load_or_default(不存在文件)` 导致 `outbox.root_dir` 取生产默认 `~/TimeTraceData/outbox`，只隔离 data_dir 漏了 outbox；测试把真实条目偷发临时 server（删）+ 推进 acked + 塞 smoke.py 假记录 → 后续 close 永久 404 堵死队列 → 客户端崩；直查 outbox 内容+box DB（不盲信 4-agent workflow，其 2 子 agent 给了引用真 file:line 的错误根因）锁定真相；救数据=备份后原子推进 acked 跳毒丸（193 条仅 1 毒丸，192 真实数据零丢）；修测试 pin tmp 目录+回归守卫；精确删 box 10 条 smoke.py 垃圾；教训=跑碰文件系统的测试前确认隔离目录、数字说话不空安慰 |
| [archive-202606021128-llm-classification-6cat-knn-removal.md](backend/archive-202606021128-llm-classification-6cat-knn-removal.md) | LLM 自动分类接入：补 worker"描述了却从不分类"的缺口（线上 534 条有 vlm_desc 仅 2 条有 category）；taxonomy 15 类两级→6 类单级(work/study/social/entertainment/system/uncategorized)；VLM describe 多吐 category(enum 约束 + off-list 回退)→worker 走 decide_category 写 category_final；**删 KNN**(给小固定分类集投票弱；embedding+最近邻能力留给将来标签收束)；同步 SKILL/MCP/docstring + 全测改新类；含分类 vs 标签设计讨论 + commit message 误用 PowerShell `@'...'@` 混入 @ 的修正(改 Bash heredoc)；commit 26ccbd5，部署后实测自动分类 work736/social372/system34/study18 质量合理；box 旧 14 类清理待授权 |
| [archive-202606021757-dashboard-report-accuracy-phase0.md](backend/archive-202606021757-dashboard-report-accuracy-phase0.md) | **看板报告准确性 Phase 0**：只读取证 box 推翻报告前提——"未分类/Vanish/Claude 是隐藏 BOSS"是臆造，真 uncategorized 全库 0 条，那 3063 条(72%)是旧 worker(pre-26ccbd5)描述过却没分类的 NULL 遗留被 `COALESCE(...,'uncategorized')` 误标；VLM 早认得 Vanish 是 IM(只是逐帧撕成 social/work)；附带 2014 条 ~9h window_switch 睡眠伪影撑爆时长。四层修复(全纯代码/数据卫生)：①`tools.py` 拆桶(`_unclassified`≠`uncategorized`+标志)+读侧时长封顶(镜像 `_cap_implausible_record_durations` 5min)+legend；②重写 `_REPORT_SYSTEM` 教 6 类/区分 NULL/存疑时长/引导配规则/删"摸鱼·反差亮点"臆造诱因；③per-app 覆盖后端 `server/settings/overrides.py`(单一来源喂 worker RuleSet+VLM note+路由，空默认零行为变化)；④前端 KV 编辑器 `AppOverridesSection.tsx`(PaaS 风格，分类下拉硬编码 flat-6)。467 passed；commit 1e0d134+9cb754f；授权回填 box 231 条 Vanish/Claude NULL→work(dry-run+备份+种 app_overrides 规则)；部署验证(route 401/healthz 200)；pyramid 排周三演示后 |
| [archive-202606030351-report-fixes-classification-roadmap.md](backend/archive-202606030351-report-fixes-classification-roadmap.md) | 看板两 bug 修复 + 分类管线现状深挖 + 部署卡 frp + 路线图：①「重新生成」network error——同机 curl 实测流式 200/405 事件/67s 通，**推翻卡片"clash 挡流式"根因**，真因=浏览器长连 HTTP/2 + 链路 ~60s 空闲超时(流式持续有数据免疫、非流式静默被掐、服务端照样落库)→`regenerate` 自愈(流式→非流式→轮询 latest 按 created_at 捞回，`0e1daca`+`c5994d3`)；②定时器只刷 24h 属实→遍历 SCOPE_HOURS(`59d5d34`)；③后端 deploy 3 连败=CI-SSH 的 `2v4G` frp 隧道断(121.43.33.13，frpc 在跑但控制连接掉、API 隧道仍活)，scheduler 修复已 push 未部署、改手动预填 3h/7d；④只读 box DB：5900/7885 NULL(3428 有图排队/2474 无图短路)，证实用户**确写硬规则** vanish/claude→work(各 211/91 生效)、规则不回溯(social 101=规则前老记录)、硬规则在 VLM 之后跑不提速；⑤路线图=分类与描述解耦/规则前置/小模型/回溯 sweep。含纠正自己两处错断言(无图"bug"、规则"提速") |

---

## frontend/

| 文件 | 摘要 |
|------|------|
| [archive-202604300228-image-lightbox.md](frontend/archive-202604300228-image-lightbox.md) | 基于 Radix Dialog 实装截图放大 Lightbox：弹层放大 + 左右键切换 + 底部信息条 + 动画；处理索引越界、按钮嵌套语义、margin 覆盖等 review 反馈，并同步 `infra/architecture/web-ui.md` |
| [archive-202605310019-redesign-review-emb-tokenize.md](frontend/archive-202605310019-redesign-review-emb-tokenize.md) | 6 维并行 workflow 评审 infra agent 前端美化重构（双主题/吉祥物/无障碍亮点 + medium/low 清单）；主 agent 修自己份内的 EmbeddingDiagnostics 令牌化 + a11y（high 全清）；樱花"看不见"根因（只在浅色 app-glow、默认深色看不到）；给 infra agent 交接 prompt（移动端适配 + 樱花 + medium/low） |
| [archive-202605311034-frontend-redesign-mobile-a11y.md](frontend/archive-202605311034-frontend-redesign-mobile-a11y.md) | 前端从单深色硬编码重做为双主题设计令牌系统 + 移动端适配（useIsMobile 抽屉/全屏覆盖层）+ 时间轴触摸（单指平移/双指缩放/点选）+ 刻度自适应稀疏 + 樱花氛围 + 防浏览器自动填密码；两轮多 agent 对抗审计共确认 15 项全修（对比度/a11y/Radix 模态滚动锁）；分批 scp 部署 VPS 验证全绿 |
| [archive-202606012136-agent-chat-ux-overhaul.md](frontend/archive-202606012136-agent-chat-ux-overhaul.md) | 按用户 7 项诉求重做 Agent 聊天页 UX：Markdown 换 react-markdown+remark-gfm（GFM 表格）+ 懒加载 mermaid（图表库按需 chunk）；助手回合改「按到达顺序 block 列表（thinking/text/tool）」→ 工具间文本各自独立气泡（修全挤一个）；可折叠思考流 + 每条 token 用量灰字；localStorage 多会话持久化+列表+左栏 token 面板；配套后端小改 runner 发 reasoning 事件 + stream_options 收 usage 进 done。4 路对抗审计修 O(n²) 重解析(React.memo)/会话行键盘可达/卸载 flush。2 commit、push（含他人 09a92c0）、前后端都部署验证；VPS 链路抖动改 tarball 单流+幂等原子交换+curl 校验；撞见另一 agent 并发部署的前端 bundle |
| [archive-202606022349-report-json-rework.md](frontend/archive-202606022349-report-json-rework.md) | 看板报告从「让 LLM 直接写 HTML」改成「LLM 产结构化 JSON + 前端主题化 ReportView 渲染」根治深浅模式错乱：先两次错误纸面补丁（浅纸面让深色生成的浅字不可见）被用户打回，**拉真实报告 HTML 看清成因后**定方向 + AskUserQuestion 选 JSON；后端 `_clean_html`→`_extract_json`/`_parse_report`（三级抽取+校验+兜底对象）、`fmt=json`（`74b1997`），前端 `ReportData`/`parseReportData`/`ReportView`（6 字段主题化卡片海报、`--bg-*`/`--text-*` 深浅自适应、`09649d8`）；附修聊天「思考与工具间空气泡」（空白 text 块 `72dd920`）+ 排查「下午没数据」实为 **outbox 同步停滞**（`acked` 冻 176/5096、~4920 卡住，后自行清空 pending=0 零丢失）；抖动链路用 tarball 单流+幂等原子交换+curl 校验部署；核心教训=别赌 LLM 写对呈现层 |

---

## infra/

| 文件 | 摘要 |
|------|------|
| [archive-202605151200-client-server-split-kickoff.md](infra/archive-202605151200-client-server-split-kickoff.md) | 客户端 / 服务端架构分离重构的"宪法"：决策汇总、目标架构、wire 协议、鉴权 / outbox / 隐私管线设计、P0–P8 分阶段路线图。所有后续重构 PR 应能追溯到其中某个阶段 |
| [archive-202605161000-deployment-architecture.md](infra/archive-202605161000-deployment-architecture.md) | 部署架构决策 v1：目标 = 家里小主机（NAT），云服务器只做 FRP SSH 跳板；选择理由（带宽 / 流量 / 所有权）、Auth 双层（浏览器密码 + 设备 token）、Web UI 永不公网（看页面走 ssh -L）、Fork 安全策略 |
| [archive-202605161015-cicd-workflow.md](infra/archive-202605161015-cicd-workflow.md) | CI/CD 工作流设计 v1：仅 workflow_dispatch 手动触发、Secrets 清单与 fork 安全 repo guard、deploy.sh / systemd unit 职责、失败 rollback 与演进路线 |
| [archive-202605171500-p3b3-cli-design.md](infra/archive-202605171500-p3b3-cli-design.md) | P3b-3 CLI 工程化：init/admin 子命令拆分 + dispatch、ClientConfig 吃 storage/capture/privacy 三段 + 9 个 TIMETRACE_* env 覆盖、ServerAuth token 公开 I/O API 防 schema drift |
| [archive-202605171501-outbox-compaction-and-review-fixes.md](infra/archive-202605171501-outbox-compaction-and-review-fixes.md) | Outbox compaction crash-safe 顺序（state 先于 log，最坏 at-least-once replay 而非 silent loss）+ capture 抽 _safe_close_record helper + ctypes 两段式 buffer + bootstrap 类型收窄 |
| [archive-202605171502-packaging-and-container.md](infra/archive-202605171502-packaging-and-container.md) | P5 第一刀：pyproject sys_platform 标记让 Linux 自动跳过 Windows-only 依赖、Dockerfile 多阶段非 root + /data + /tokens 双 mount + symlink 让 ServerAuth 零 docker-aware、docker-compose loopback only |
| [archive-202605180000-deploy-evolution-and-prod-bugs.md](infra/archive-202605180000-deploy-evolution-and-prod-bugs.md) | 部署设施 7-commit 演进 + 真部署发现 4 个 bug：runner 不 checkout / 路径迁移 ~/Github/TimeTrace / infra plan B / TIMETRACE_USER=Yuki 硬编码 / ReadWritePaths 未创建目录引发 226/NAMESPACE / StartLimit 段位错 / main 分支 pywin32 无 marker |
| [archive-202605180001-search-tokenization-open-question.md](infra/archive-202605180001-search-tokenization-open-question.md) | 搜索匹配策略 open question：实测发现 LIKE 只覆盖 window_title + vlm_desc (NULL)，未覆盖 app_name/process_name/url；分析三方案 (多字段 LIKE / FTS5 trigram / 向量 embedding) tradeoff，待决策 |
| [archive-202605300153-deploy-branch-clarification.md](infra/archive-202605300153-deploy-branch-clarification.md) | **给 infra agent 单独看的一篇**：澄清"部署机 main 领先 origin/main 77 commit"不是分支乱 = deploy.sh `git reset --hard origin/<ref>` 的镜像状态；主 agent 此前手动 ssh reset 绕过 GH Actions 的错；CLAUDE.md 写入动态部署约定（看 origin/* 不看部署机本地 + 禁手动 ssh 改部署机）；部署前必须先 lms load 恢复被卸的 Qwen 35B（VLM 哑 healthz 仍 200） |
| [archive-202605310018-at-commit-history-rewrite.md](infra/archive-202605310018-at-commit-history-rewrite.md) | 修 3 条 commit 标题混入的孤立 `@` 行：根因 = 在 Bash/Git Bash 里用了 PowerShell here-string `@'...'@`；安全改写共享分支历史（`commit-tree` 逐条重建只改 message、tree+身份+双日期不变、临时 ref 5 道门验证、`--force-with-lease=<ref>:<旧SHA>` 推）；含一次"高危操作塞进大并行批次被分类器拦+级联取消"复盘 |
| [archive-202605300220-public-deploy-vps-nginx-frp.md](infra/archive-202605300220-public-deploy-vps-nginx-frp.md) | 公网部署搭建：选香草云 HK VPS（CN2 GIA 38ms）+ Cloudflare DNS-only 子域名 + nginx 两阶段 certbot 签证书 + frpc 加 18765→8765 隧道，端到端 152ms 打通；随即发现 5 路由公网裸奔真实泄露截图/记录，注释 frpc timetrace-api 段止血（保留 nginx/证书） |
| [archive-202605300221-login-system-impl-phase1-6.md](infra/archive-202605300221-login-system-impl-phase1-6.md) | 登录系统实现 Phase 1-6：auth_users/sessions 表 + bcrypt + UserStore + cookie/bearer 双通道（CookiePrincipal\|BearerPrincipal）+ /thumbs(FileResponse) /mcp(纯 ASGI middleware) 业务路由全 gate + 前端登录流/守卫/401 跨 tab + admin tokens CRUD(.mcp.json 弹窗)；顺手修 .gitignore `lib/` 吞掉 frontend/src/lib 的大坑；373 passed |
| [archive-202605300222-phase7-deploy-qwen-restore.md](infra/archive-202605300222-phase7-deploy-qwen-restore.md) | Phase 7 部署：Qwen 35B 恢复踩 `--ttl 99999999` bug（去掉即好，无 TTL 永久驻留）+ 纠正 GPU 实为 RTX 3080 20GB 非 4090D；gh workflow run 404（deploy.yml 不在默认分支 main，GH Actions 部署路径从未可用）+ deploy.sh 被 guardrail 拦 → 用户授权手动部署 + 提议 main FF |
| [archive-202605300855-auth-bugfix-audit-public-launch.md](infra/archive-202605300855-auth-bugfix-audit-public-launch.md) | 登录系统正式上线：修退出闪烁+改密不跳转两 bug（根因 react-query 401 保留 stale data）+ sonner toast；多 agent 对抗审计 23→13 确认，揪出 2 HIGH（must_change 后端零强制、XFF 伪造击穿限流）全修；nginx 托管 SPA + frp 隧道开公网，admin 密码经 sqlite3 直 UPDATE 重置，公网 e2e 全绿 |
| [archive-202605311104-arch-docs-rewrite-hallucination.md](infra/archive-202605311104-arch-docs-rewrite-hallucination.md) | 架构文档批量更新到现状（新建 auth-system/web-deployment/client-server-split/embserver 4 篇 + 改写 10 篇）；**以失败教训为主**：rewrite workflow 写手系统性虚构符号（`_process_one`/`analysis_tasks` 表等）+ 10 校验员仅 1 返回 + 校验本身假阳性（误报 store_images 不存在）；主 agent 又把 Edit 与 commit 混进大并行批次误提交未修好文件、还自编 nginx 配置值；最终靠**串行逐符号 grep 复核**修净（bf69ffd→2dc8028→c230ab0）；roadmap/vector-search/capture-params 三篇虚构过多 revert 保旧版 |
| [archive-202606010130-deploy-agent-dashboard-nginx-sse.md](infra/archive-202606010130-deploy-agent-dashboard-nginx-sse.md) | 把另一 agent 写好的 Agent 聊天页/看板页（aa6ab89）构建(`tsc -b` exit0 → `index-DUc91w2Z.js`)部署到公网 VPS + 补 nginx `/v1/agent/`+`/v1/reports/` 的 SSE 透传（buffering off + 3600s；原 `/v1/` 只基础 proxy、默认 60s read timeout 会掐断慢流式）；部署前 3 路对抗审计采纳原子交换+上传校验+nginx 失败自动恢复；整树同盘 `mv` rename 原子换入零 404 窗口、`nginx -t`+reload+`is-active`；线上验证 `/`200 / 旧 bundle 404 / reports 401 / agent GET 405 全绿（401/405 证明新前缀真到后端）；多 agent 共享树发现 HEAD 被推进（ccfdc83 纯后端 ruff、零 frontend）查证 bundle 不旧后守护式 ff push `e639a61` |
| [archive-202606011146-private-mode-ci-thumbnail.md](infra/archive-202606011146-private-mode-ci-thumbnail.md) | 私人模式（全本地）核查 + CI 修复 + 缩略图双轨：SSH 直连小主机实测 VLM 已切本地 LM Studio(35B@50000ctx LOADED 16.3GB) + nomic-embed(84MB) `lms load -y` 加载，RTX 3080 20GB 双模型同驻剩 ~4GB **显存顾虑消除不需缩 ctx**；服务端只听 127.0.0.1:8765 故客户端走公网域名；修 CI 长期红 `ccfdc83`（ruff format 11 文件 + 删 1 unused import，`git diff -w` 证零逻辑，411 passed）；缩略图模糊根因=`_THUMB_SIZE=(320,200)`q75 实测 ~11KB/张，**带宽非瓶颈**→双轨：thumb 升 640×400 q85 + 新增 `/blob` 原图路由(data_dir 根+require_principal+防穿越) + DB query `MIN(path) AS image_path` 贯通到灯箱优先原图；含两次 SSH 越界被分类器正确拦（VPS 翻 frps 猜端口 pivot / box 写 live-.env smoke 脚本）+ clash fake-ip 劫持 GitHub 致 push 交用户手动 |
| [archive-202606021128-multipath-client-p0-p3.md](infra/archive-202606021128-multipath-client-p0-p3.md) | **多路径客户端 P0-P3**：endpoint 有序列表 + 健康探测 failover + 托盘开关(b241609)、type=ssh 原生托管 `ssh -L` 隧道(up-front 起——懒启动鸡生蛋)(66ba77c)、滑动窗口并发上传(按 record_id 屏障 + 连续前缀按序 ack，绕开"单游标 outbox 与并发乱序 ack"冲突，服务端零改)(8247fcc)；P0 设计文档(SSH≈HTTPS 安全论证 + 同后端幂等不变量)c32a7a4；后续慢香港链路实测抗抖：探测超时 3s→8s + **粘滞**(探测只是 hint，发送才是存活判据，失败不弃当前) + 托盘 glyph 刷新(c4b5fce) + 周期探测**跳过当前 endpoint**只探更高优先级去噪(2508b55)；含 P3 `await Event` 漏 `.wait()` 死锁复盘 |
| [archive-202606021128-upload-diagnosis-lan-direct.md](infra/archive-202606021128-upload-diagnosis-lan-direct.md) | **上传卡死救火**（催生多路径设计）：空错误 `send_failed` 锁定 clash fake-ip(`198.18.0.4`)劫持 `timetrace.yukirin.me`（服务器实健康）→ DoH 三方拿真 A 记录 `103.117.123.204`=香港 + box 住宅宽带直连 200 → 判 **DIRECT**；超大图 5MB 堵 strict-FIFO → dfa12de 发送前跳过；729 条负时长 `ts_start=ts_end` 修复(用户授权+备份)；深挖根因=上传走 家→香港VPS→FRP→家(box) 绕路 ~300KB/s(干净链路传 5MB 也 40s 超时) vs 同 LAN ~21条/s；SSH 隧道(`ssh -L`)LAN 直连秒清 5000 积压不丢；max_image_mb 提为可配(fdaff7d)；确认分析队列 SQLite 状态机+reclaim 跨重启安全；含 box DB 写授权边界(729修=授权/旧分类清理=被分类器拦) |
| [archive-202606021757-agent-arch-review-phase0-gate.md](infra/archive-202606021757-agent-arch-review-phase0-gate.md) | **评审隔壁 agent 4 篇架构书**（金字塔 L0–L4 + 瘦路由 agent）：14-agent workflow(~890K token，4 篇逐篇 reviewer + 完备性 critic + 2 设计带对抗校验 + 综合)判**四篇一致 sound-with-gaps**——优点真(write-time amortization 是 token 墙正解、代码引用逐条复核为真)，但 4 个线上真问题里 3 个住金字塔之下不修、第 4 个(时长膨胀)被主动继承且**提议的 SUM-invariant 会把膨胀认证成"正确"**；四篇通病=时长封顶被继承/NULL≠uncategorized 没区分会冻进 digest/3063 遗留无 L1 回填路径/报告臆造 persona 没碰；codebase 不符抽样=MCP 实 7 工具(含 ask_agent)非文档说的"6 闭包"；过度设计=L4 周月 digest/deep_scan map-reduce/双 token-budget SSE。critic 判**建但 gate 在 Phase 0 数据卫生之后**(跑 cap/回填 NULL/种 RuleSet/重写 persona——正好是让报告准确的最小集，主 agent 当晚已全做)；用户拍板金字塔+给 4 文档补修正**周三 2026-06-03 演示后再开始**，演示前冻结 |

---

## research/

_暂无归档_
