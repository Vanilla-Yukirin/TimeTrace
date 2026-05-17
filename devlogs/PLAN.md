# TimeTrace 滚动 TODO / Plan

**最后更新：** 2026-05-18

> 这份文档是**滚动**的，不是历史快照。完成的事项移到对应 devlog archive 里归档，这里只留"未完成"和"未决策"。
>
> 找代码细节去 [`devlogs/README.md`](./README.md) 索引；找架构现状去 [`infra/`](../infra/)；本文档只回答"**下一步该做什么**"。

---

## 重构总体进度（P0–P7）

| 阶段 | 状态 | 还差什么 | 详见 |
|---|---|---|---|
| P0 工程基建 | ✅ | — | kickoff devlog |
| P1 目录重组 | ✅ | — | kickoff devlog |
| P2 / P2.5 接口抽象 | ✅ | — | kickoff devlog |
| P3a HTTP + Outbox | ✅ | — | kickoff devlog |
| P3a-5b 双入口接线 | ✅ | — | kickoff devlog |
| P3b Auth + Init | ✅ | — | [archive-202605171500-p3b3-cli-design](infra/archive-202605171500-p3b3-cli-design.md) |
| P3c 部署设施 | ✅ 已 production verified | — | [archive-202605180000-deploy-evolution-and-prod-bugs](infra/archive-202605180000-deploy-evolution-and-prod-bugs.md) |
| P4 隐私管线 | ⚠️ 仅周边硬化 | OCR + 分类 + 模糊主体；模型选型 | 见下方 § P4 |
| P5 容器化 + 适配器 | ⚠️ 容器完成 | Postgres/Redis/S3 三个适配器 | 见下方 § P5 |
| P6 Headless TUI | ❌ | 整个 phase | 见下方 § P6 |
| P7 分发自动化 | ❌ | 整个 phase | 见下方 § P7 |

完整 phase 描述见 [`infra/archive-202605151200-client-server-split-kickoff.md`](infra/archive-202605151200-client-server-split-kickoff.md)（重构宪法）。

---

## 当前最 actionable 的 5 件事（按精力成本排序）

1. **配 VLM `.env` 到小主机** — 5 分钟，立即解锁 search 语义通道 + vlm_desc 列。`scp .env vanilla@121.43.33.13:~/Github/TimeTrace/.env` + `systemctl --user restart`
2. **搜索字段覆盖扩展（方案 A）** — 1 小时，SQL 加 3 个 LIKE 字段（app_name / process_name / url）。详见 [search-tokenization-open-question](infra/archive-202605180001-search-tokenization-open-question.md) § 方案 A。**没决策不动**
3. **删 DEPLOY-CHECKLIST.md** — 0 分钟，本地清理。`.git/info/exclude` 已配，git 不会提示
4. **轮转可能泄露的 token** — 1 分钟，`ssh GTi... 'uv run timetrace-server tokens revoke default && uv run timetrace-server tokens add default'`。**不强求**，威胁极低
5. **修 init --non-interactive UX bug** — 30 分钟，`--non-interactive` 遇到已存在文件应直接 fail（或隐含 --force），不该弹 confirm

---

## P4 隐私管线（已决策待执行 vs 待决策）

### 已落地（周边硬化）

- Outbox compaction 自动回收
- `_safe_close_record` helper 统一吞错
- ctypes 长路径两段式 buffer
- PrivacyConfig.mode 字段已定义（off / text_only / full）但 **没有 consumer**

### 待用户决策

| 项 | 状态 | 详见 |
|---|---|---|
| OCR 模型选型 | ❌ PaddleOCR det+rec / RapidOCR / 其他？ | kickoff devlog § P4 |
| 隐私分类小模型 | ❌ 选 OpenAI 开源 / 自训 / 其他 | kickoff devlog § P4 |
| 性能 budget | ❌ ≤500ms / 张 (CPU)？GPU 可选？ | kickoff devlog § P4 |

### 待执行（决策后）

- `src/timetrace/client/privacy/` 目录建立
- OCR → 分类 → 模糊 → 重编码 4 段 pipeline
- PrivacyConfig.mode 接入消费方
- 模型下载脚本（不进 git）
- 测试集（含信用卡号 / 邮箱 / 密码框）

---

## P5 容器化 + 适配器

### 已落地

- Dockerfile 多阶段非 root
- docker-compose loopback only
- pyproject 平台标记 + extras 占位桶

详见 [archive-202605171502-packaging-and-container](infra/archive-202605171502-packaging-and-container.md)。

### 待执行

| 项 | 优先级 | 备注 |
|---|---|---|
| PostgresDatabase (asyncpg) + schema 迁移 | 低 | 当前数据量 SQLite 够用 |
| RedisQueue | 低 | InMemoryQueue 够用 |
| S3BlobStorage / DualBlobStorage | 低 | 单设备本地存够用 |
| docker-compose profile 真接入 (`--profile pg`) | 低 | 跟 PostgresDatabase 一起 |
| CI 加 server 镜像 build 步骤 | 中 | 想 ghcr push (P7) 前必备 |

---

## P6 Headless TUI（整个 phase 未启动）

目标：Linux 小主机也能采集（process-only，无截图）。

- `src/timetrace/client/tui/` 整个目录
- `ProcessCapturer` 用 psutil 抓 top N + 白名单
- `[project.scripts]` 加 `timetrace-headless` 入口
- pyproject `[project.optional-dependencies] headless = ["psutil"]` 真填上
- Linux 真机 24h 稳定测试

**触发条件**：小主机自己需要"我用了什么"记录的时候再做。当前用户主要场景是 Windows 桌面，TUI 是 nice-to-have。

---

## P7 分发自动化（整个 phase 未启动）

| 项 | 触发条件 |
|---|---|
| ghcr 自动推 server 镜像 | docker-compose stack 稳定 + 想给别人 try |
| Release CI: tag → wheel + image | 想给别人 pip install timetrace |
| 自动 rollback on healthz fail | 部署跑稳 ≥ 5 次手动后 + 加自动触发前 |
| CHANGELOG 自动化 | 第一个公开版本前 |

短期都不做。**先把 P4 OCR / P5 适配器中选 1-2 个推进**，再考虑分发。

---

## 长期债务（散落各 devlog 提过的）

- **MCP tools.py** `get_category_stats()` / `search_activity()` 仍是 Phase 1.5+ stub
- **VLM 熔断器盲点**：search 路径只 except VLMError，没走 gate.acquire，VLM 挂了会反复打端点（详见 [devlogs/backend/archive-202604300316-vlm-worker-circuit-breaker.md](backend/archive-202604300316-vlm-worker-circuit-breaker.md)）
- **app_name Unknown 残留**：实测 416 PID 拿到 242 真名（58%），剩 174 是系统进程权限不允；可选 GetClassName / window_title 启发式
- **BackendClient TYPE_CHECKING 仍 reach into server**：`InProcessBackend` 在类型注解里 import `server/storage/database.Database` + `server/phash_index/index.PHashIndex`。这是 in-process 适配器的本质，HttpBackend 上线后 InProcessBackend 仅测试用，不是真问题
- **frontend-dist/ 已在 .gitignore** ✅（之前 AI review 误报）
- **CLAUDE.md 测试数同步**：曾标 17，实 23 文件 / 261 用例。每次大改后顺手更新

---

## 搜索匹配策略 — Open Question

**这是当前最大未决技术问题**。详见 [archive-202605180001-search-tokenization-open-question](infra/archive-202605180001-search-tokenization-open-question.md)。

三个候选：

| 方案 | 工作量 | 解决程度 |
|---|---|---|
| A. 多字段 LIKE | 10 行 | 80% 直觉命中（"Weixin" 类）|
| B. FTS5 + trigram tokenizer | 1-2 天 | CJK 子串 + BM25；与 P4 OCR 同期做最经济 |
| C. 向量 embedding 语义检索 | 1-2 周 | "聊天" → "微信" 真语义；ROI 不明确 |

**当前推荐**：A 立刻做（成本可忽略），B 跟 P4 OCR 合并节奏，C 长期再说。等用户拍板。

---

## 文档 / 约定（持续维护）

- **devlog 写完不改**：archive 文件是历史快照；纠正/补充另写新 archive
- **deprecation 警告格式**：`## **⚠️ 一句话标题**`（H2 + 加粗紧贴 emoji），`grep '^## \*\*⚠️' infra/ devlogs/` 可枚举
- **PLAN.md (本文档) 是滚动的**：每次会话结束 / 阶段完成时更新

---

## 关联入口

- [`devlogs/README.md`](./README.md) — 所有 devlog archive 索引（按 backend / frontend / infra / research 分类）
- [`infra/readme.md`](../infra/readme.md) — 架构 wiki 索引 + 阶段速览表
- [`infra/archive-202605151200-client-server-split-kickoff.md`](infra/archive-202605151200-client-server-split-kickoff.md) — 重构宪法（P0–P7 路线滚动更新）
- [`CLAUDE.md`](../CLAUDE.md) — 项目协作约定速读
- [`README.md`](../README.md) — 用户向使用流程
- `D:\archives\TimeTrace-Deployment\` — 私人部署手册 / 操作记录（不进 git）
