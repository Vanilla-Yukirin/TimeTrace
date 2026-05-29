# TimeTrace DevLog 索引

**最后更新：** 2026-05-30

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

---

## frontend/

| 文件 | 摘要 |
|------|------|
| [archive-202604300228-image-lightbox.md](frontend/archive-202604300228-image-lightbox.md) | 基于 Radix Dialog 实装截图放大 Lightbox：弹层放大 + 左右键切换 + 底部信息条 + 动画；处理索引越界、按钮嵌套语义、margin 覆盖等 review 反馈，并同步 `infra/architecture/web-ui.md` |

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
| [archive-202605300220-public-deploy-vps-nginx-frp.md](infra/archive-202605300220-public-deploy-vps-nginx-frp.md) | 公网部署搭建：选香草云 HK VPS（CN2 GIA 38ms）+ Cloudflare DNS-only 子域名 + nginx 两阶段 certbot 签证书 + frpc 加 18765→8765 隧道，端到端 152ms 打通；随即发现 5 路由公网裸奔真实泄露截图/记录，注释 frpc timetrace-api 段止血（保留 nginx/证书） |
| [archive-202605300221-login-system-impl-phase1-6.md](infra/archive-202605300221-login-system-impl-phase1-6.md) | 登录系统实现 Phase 1-6：auth_users/sessions 表 + bcrypt + UserStore + cookie/bearer 双通道（CookiePrincipal\|BearerPrincipal）+ /thumbs(FileResponse) /mcp(纯 ASGI middleware) 业务路由全 gate + 前端登录流/守卫/401 跨 tab + admin tokens CRUD(.mcp.json 弹窗)；顺手修 .gitignore `lib/` 吞掉 frontend/src/lib 的大坑；373 passed |
| [archive-202605300222-phase7-deploy-qwen-restore.md](infra/archive-202605300222-phase7-deploy-qwen-restore.md) | Phase 7 部署：Qwen 35B 恢复踩 `--ttl 99999999` bug（去掉即好，无 TTL 永久驻留）+ 纠正 GPU 实为 RTX 3080 20GB 非 4090D；gh workflow run 404（deploy.yml 不在默认分支 main，GH Actions 部署路径从未可用）+ deploy.sh 被 guardrail 拦 → 用户授权手动部署 + 提议 main FF |

---

## research/

_暂无归档_
