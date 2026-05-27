# TimeTrace 滚动 TODO / Plan

**最后更新：** 2026-05-27

> 滚动文档，不是历史快照。完成的事项移到 devlog archive 归档，这里只留"未完成"和"未决策"。
>
> 找代码细节去 [`devlogs/README.md`](./README.md) 索引；找架构现状去 [`infra/`](../infra/)；本文档只回答 **"下一步该做什么"**。

---

## **🎯 当前主线：周三 demo sprint（截止 2026-06-03）**

目标 demo 故事（用户原话）：

> 「我可以问我本地的 Claude Code：『你看看我昨天做了什么 / 我昨天写了多久代码 / 我昨天在各个代码仓库里面分别花了多少时间』，它可以自己多次调用 search 接口，也可以直接问 ask_agent 接口（TimeTrace 内置 agent 自己搜整合后返回），都能得到精确结果。」

核心要求：**全流程跑通 + 本地优先（隐私）**。细节优化可放 todo，之后按重要性变可配置项。

### 模型栈（已部署在小主机 / LM Studio GUI + `lms` CLI）

| 角色 | 模型 | 已落地 | 接入方式 |
|---|---|---|---|
| **VLM 看截图 + Agent 推理** | `qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive`（35B MoE，激活 3B，**原生多模态**） | ✅ 磁盘 + 实测可看图 | OpenAI `/v1/chat/completions`（image_url 字段） |
| Text embedding | `text-embedding-nomic-embed-text-v1.5`（84MB） | ✅ 磁盘 | OpenAI `/v1/embeddings` |
| Image embedding (multimodal) | `text-embedding-qwen.qwen3-vl-embedding-2b` | ✅ 磁盘 | OpenAI `/v1/embeddings`（图文同空间） |

**重要**：Qwen3+ / Gemma3+ 全部**原生多模态**，无单独 VL 变体。同一个模型既看图又当 agent，VRAM 16.34GB / 20GB，留 3.6GB 给 embedding + KV cache 足够。LM Studio TTL 60min，IDLE 时模型常驻 VRAM。

LM Studio CLI 用法摘要见 `~/archives/LM-Studio/archive-202605171929-lms-cli-and-headless.md`（小主机上）。

### Sprint 路线（Day 0 = 今天 2026-05-27）

| Day | 任务 | 验收 |
|---|---|---|
| Day 0 (今晚) | 小主机 `.env` 配 LM Studio + worker prompt 改"读 metadata 生 vlm_desc" | 1 条新 record 落地后 `vlm_desc` 非空 |
| Day 1 | sqlite-vec 集成 + worker 加 text embedding 阶段 + 写库 | 新 record 同时落 `vlm_desc + text_embedding` |
| Day 2 | worker 加 image embedding 阶段（vl-embedding 跑 thumb） + 搜索升级（FTS5 trigram + vector 混合） | 搜"鸣潮" / "vscode" / 上传一张相似截图 → 都有结果 |
| Day 3 | MCP `search_activity / get_category_stats` 真实现（替换 stub） | Claude Code 接 MCP server，能 invoke search 拿到 JSON |
| Day 4 | MCP `ask_agent` 新工具：内部跑 35BA3B agent 自循环 search + 整合 | Claude Code 问"昨天写了多久代码"→ 单次 tool 调用拿到自然语言答案 |
| Day 5 | Claude Code skill 文档 + 前端搜索 UX + dress rehearsal | 端到端走一遍 demo 故事板 |
| Day 6 (周三) | **Demo!** | — |

### Demo 故事板（5 分钟）

1. **30s 开场**：屏幕显示托盘 + Timeline 页今日活动条，说"无感记录"
2. **1min 搜索 demo**：Search 页搜"vscode" / "鸣潮" / 上传一张截图反搜，命中
3. **2min MCP demo**：切到 Claude Code 终端，问"我今天在 TimeTrace 仓库花了多久"，看它 invoke `ask_agent` → 拿到 "约 2 小时 30 分，主要在 worker/loop.py" 类回答
4. **1min 架构口述**：client/server 分离 + 本地 LLM + 端到端隐私（不出小主机）
5. **30s 收束**：项目地址 + 余下 TODO 列表

---

## **⚠️ 不在 demo sprint 范围**（不删，定位"暂缓"）

- **OCR 管线**（原 P4 隐私核心）— 干完 sprint 再来
- **隐私模糊重编码**（PrivacyConfig.mode 接消费方）
- **Postgres / Redis / S3 适配器**（P5）— 数据量 / 体验都不需要
- **Headless TUI**（P6）— Linux 采集，nice-to-have
- **ghcr 自动发布 / release CI**（P7）— demo 不展示
- **token 轮转 / DEPLOY-CHECKLIST.md 清理 / init --non-interactive UX bug** — sprint 间隙塞

---

## 重构总体进度（背景）

| 阶段 | 状态 | 还差什么 |
|---|---|---|
| P0–P3 工程 / 重组 / 接口 / HTTP / Auth / 部署 | ✅ production verified | — |
| P4 隐私管线 | ⚠️ 周边硬化；主体 demo 后做 | OCR + 分类 + 模糊（暂缓） |
| P5 容器化 + 适配器 | ⚠️ 容器化完成；适配器暂缓 | Postgres/Redis/S3 |
| **P3.5 (新增) 本地 LLM + Embedding + MCP 全链路** | 🟡 **sprint 进行中** | 见上方 Sprint 路线 |
| P6 Headless TUI | ❌ 暂缓 | — |
| P7 分发自动化 | ❌ 暂缓 | — |

完整 phase 描述见 [`infra/archive-202605151200-client-server-split-kickoff.md`](infra/archive-202605151200-client-server-split-kickoff.md)。

---

## Pivot 决策（2026-05-27 拍板，要回滚就改这里）

| # | 决策 | 理由 |
|---|---|---|
| 1 | `vlm_desc` **不依赖 VL chat**，改用 35BA3B 读 `window_title + app_name + process_name + url` 生 | LM Studio 仓库无 VL chat 模型；35BA3B 文本能力强；窗口标题信息密度足够；便宜 10× |
| 2 | 图像理解走 `qwen3-vl-embedding-2b`（不生文字描述，只生向量） | 适合"以图搜图" / 视觉相似度；不需 chat completion 接口 |
| 3 | 文本检索叠 `nomic-embed-text-v1.5` + FTS5 trigram | 文本向量做语义召回，FTS5 做关键词精确，混合排序 |
| 4 | 向量库**先用 numpy 内存索引**，不立刻上 sqlite-vec | 当前 records 量（≤100K）暴力 cosine 够快；sqlite-vec 留作"数据量大了再说"。**有时间就直接上 sqlite-vec** |
| 5 | MCP 新增 `ask_agent` 工具：内部跑 35BA3B agent 自循环 search | 给 Claude Code 一个"高级"接口；外部 agent 看到单次 tool 调用拿自然语言答案，不必自己 orchestration |
| 6 | Claude Code 端写 skill 引导何时调 search、何时调 ask_agent | 不写 skill 的话，Claude Code 不知道这俩工具存在 |

---

## 长期债务（散落各 devlog 提过的）

- ~~**MCP tools.py** stub~~ — sprint Day 3 收
- **VLM 熔断器盲点**：search 路径只 except VLMError，没走 gate.acquire，VLM 挂了反复打端点（详见 [devlogs/backend/archive-202604300316-vlm-worker-circuit-breaker.md](backend/archive-202604300316-vlm-worker-circuit-breaker.md)）
- **app_name Unknown 残留**：实测 416 PID 拿到 242 真名（58%），剩 174 是系统进程权限不允；可选 GetClassName / window_title 启发式
- **BackendClient TYPE_CHECKING 仍 reach into server**：`InProcessBackend` 在类型注解里 import `server/storage/database.Database` + `server/phash_index/index.PHashIndex`。in-process 适配器本质，不是真问题
- **CLAUDE.md 测试数同步**：曾标 17，实 23 文件 / 261 用例。每次大改后顺手更新

---

## 文档 / 约定（持续维护）

- **devlog 写完不改**：archive 文件是历史快照；纠正/补充另写新 archive
- **deprecation 警告格式**：`## **⚠️ 一句话标题**`（H2 + 加粗紧贴 emoji），`grep '^## \*\*⚠️' infra/ devlogs/` 可枚举
- **PLAN.md (本文档) 是滚动的**：每次会话结束 / 阶段完成时更新
- **小主机 vs 本机 hostname**：小主机 SSH alias = `GTi13-Ultra-2v4G`，云端 = `2v4G`
- **小主机 archive 索引**：`~/archives/README.md` + 子目录（`LM-Studio/`, `Linux-Setup/` 等）

---

## 关联入口

- [`devlogs/README.md`](./README.md) — 所有 devlog archive 索引
- [`infra/readme.md`](../infra/readme.md) — 架构 wiki 索引
- [`infra/archive-202605151200-client-server-split-kickoff.md`](infra/archive-202605151200-client-server-split-kickoff.md) — 重构宪法（P0–P7 路线）
- [`CLAUDE.md`](../CLAUDE.md) — 项目协作约定速读
- [`README.md`](../README.md) — 用户向使用流程
- `D:\archives\TimeTrace-Deployment\` — 私人部署手册（不进 git）
- 小主机 `~/archives/LM-Studio/archive-202605171929-lms-cli-and-headless.md` — LM Studio 操作 SOP
