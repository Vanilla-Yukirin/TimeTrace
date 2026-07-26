# TimeTrace · 自托管的桌面活动记录与检索系统

- **归属**：个人项目
- **状态**：核心链路已可用并持续 dogfood，覆盖采集、可靠同步、存储、VLM 分析、时间轴、混合检索、五级摘要、内置 Agent 与 MCP
- **时间**：2026-04-09 至今，253 commits
- **GitHub**：[Vanilla-Yukirin/TimeTrace](https://github.com/Vanilla-Yukirin/TimeTrace)（private，MIT License）

## 简历推荐表述

> **TimeTrace · 自托管的桌面活动记录与检索系统**
> 面向 Windows 客户端与个人 Home Lab 的分离部署场景，客户端采集活跃窗口与关键帧，家庭服务器完成存储、AI 分析、检索与 Agent 服务。

- **后端与同步**：拆分 Windows 采集端与 Linux 服务端，通过本地 outbox、至少一次投递和服务端幂等支持断网重放；以单记录顺序屏障和连续前缀确认兼顾并发上传与事件顺序，并支持 LAN、公网及 SSH 隧道多端点切换。
- **AI 记忆与 Agent 接口**：将关键帧经本地 VLM 转为结构化描述，结合文本语义与图像近似检索；构建 5 分钟至周的五级摘要，提供内置工具调用 Agent，并通过带鉴权的 MCP 向外部 Agent 开放查询和受限分类修改。

## 项目定位与真实场景

TimeTrace 用于记录和找回个人在电脑上看过、做过的内容。Windows 客户端低打扰地采集活跃窗口、进程、标题与关键帧；服务端完成持久化、AI 描述、分类、分层摘要、文本与图像检索，并把这些真实活动数据提供给内置 Agent 和外部 MCP 客户端。

系统支持两种真实部署形态：

- **单机一体化**：在一台 Windows 电脑内运行采集、SQLite、VLM Worker、API 与时间轴，采集通过 `InProcessBackend` 直接写入本地服务，不经过 HTTP 与 outbox。
- **Home Lab 分离部署**：Windows 客户端只承担采集与离线缓存，Linux 家庭服务器统一承担数据库、图片存储、VLM、embedding、检索、Web UI、Agent 与 MCP；客户端根据网络环境在 LAN、公网 HTTPS 与 SSH 隧道端点之间选择路径。

“自托管”描述的是数据控制权和推理边界：活动数据、截图、VLM 与 embedding 默认运行在本人控制的电脑或家庭服务器上；公网节点只承担加密连接与转发。带宽问题来自移动终端向家庭服务器持续同步图片，与数据是否交给第三方云服务是两个独立问题。

## 主线一：后端与可靠同步

### Client / Server 边界

- 按依赖和部署边界拆分 `common / client / server`：`common` 维护配置、模型和 wire schema；`client` 负责 Windows 采集、隐私门、outbox 与上传；`server` 负责 API、SQLite、文件存储、Worker、检索、Agent 与 MCP。
- 以 `BackendClient` Protocol 隔离采集逻辑与传输实现：`InProcessBackend` 支撑单机直写，`OutboxBackend + HttpBackend` 支撑远程可靠同步，同一套采集服务无需维护两份业务逻辑。

### Outbox 与投递语义

- 客户端将活动元数据、截图和结束事件先写入 append-only `log.jsonl` 与独立 blob 文件，再由后台 sender 上传；断网或服务端重启期间采集继续进行，恢复后自动补发。
- 写入采用 blob 先落盘、日志后追加并分别 `fsync`；ack 状态和 compaction 通过临时文件、原子替换及安全重写顺序保证崩溃恢复。
- 传输采用至少一次投递。服务端以 `client_record_id` 保证活动记录幂等，以 `(record_id, sha256)` 保证截图幂等，使网络超时后的重放不会重复落库。

### 并发与顺序

- 通过滑动窗口并发隐藏高延迟链路的单请求 RTT。
- 对同一活动建立顺序屏障，保证“元数据 → 截图 → 结束事件”依次提交；不同活动之间可以并发。
- outbox 只有单一确认游标，因此并发任务完成后仅推进连续完成的前缀，避免越过失败项造成未发送数据被错误确认。

### 多路径连接

- 支持按优先级配置 LAN、公网 HTTPS 与 SSH 隧道端点，周期健康探测负责回切更高优先级路径，发送失败触发即时重选。
- SSH 隧道由客户端进程托管，向采集端提供本地 HTTP endpoint；底层可以连接家庭服务器或经中转路径到达的非公开服务端口。
- 截图在客户端保存为长边不超过 2560px 的 JPEG q88，并生成 640×400 JPEG q85 缩略图，降低持续图片同步的单条体积。

### 三类目标的明确边界

| 目标 | 已完成机制 | 作用 |
|---|---|---|
| 减少传输字节 | 截图缩放、JPEG 压缩、缩略图 | 减少单张图片大小 |
| 提高同步吞吐 | 更优端点选择、滑动窗口并发 | 隐藏 RTT 并使用更合适的网络路径 |
| 保证数据可靠 | 本地 outbox、重试、服务端幂等、单记录顺序屏障、连续前缀确认 | 支持断网重放并保持依赖顺序 |

## 主线二：AI 记忆与检索

### 采集与存储

- 每秒轮询活跃窗口，读取窗口标题、进程与应用信息；窗口切换稳定 1.5 秒后采集关键帧，同一窗口每 30 秒补帧，键鼠空闲 180 秒后暂停采集。
- SQLite 保存 `records / screenshots / analysis_results / summaries` 等结构化数据，JPEG 文件按日期写入文件系统。
- 截图在客户端计算 SHA-256 与 64-bit pHash；pHash 使用按日期分桶的 BK-tree 支持汉明距离近似匹配。

### VLM 与混合检索

- 异步 Worker 将关键帧交给 OpenAI-compatible VLM，按严格 JSON schema 生成关键词、摘要、详细描述与分类，并在完成后生成文本 embedding。
- 文本检索组合 FTS5 trigram 关键词召回与 embedding 余弦相似度；图像检索组合 pHash 视觉近似与查询图的 VLM 语义描述；多路结果以 RRF 融合。
- 实际 dogfood 使用家庭服务器上的 LM Studio、本地视觉语言模型与本地 embedding 服务，截图和活动文本在自托管环境中完成推理。

### 五级记忆摘要

- 以固定时间窗构建 `5 分钟 → 1 小时 → 6 小时 → 日 → 周` 五级摘要；5 分钟层读取原始帧描述，高层只读取下一级摘要，形成由细到粗的 summary-of-summaries。
- 所有时间窗采用确定性边界和 `(grain, scope_key)` 幂等键；父级统计由子级聚合，叙述生成按子级完成状态自底向上推进。
- 摘要保存时间范围、描述、重点、评价、分类统计与 source hash，支持按天/周获取概览，再向 6 小时、1 小时或 5 分钟窗口逐层下钻。

## 主线三：Agent 接口

### 内置 Agent

- `AgentRunner` 基于 OpenAI-compatible Chat Completions 实现有迭代上限的工具调用循环，通过 SSE 输出 reasoning、tool call、tool result、token 与 usage 事件。
- 工具覆盖活动检索、近期记录、应用统计、分类统计、时间范围统计、分层摘要查询与分类修改；回答基于真实数据库查询结果生成。
- 写权限限制为修改单条活动的分类标签，同时写入 feedback 审计记录；删除记录、修改原始截图等操作不在 Agent 写入面内。

### MCP

- FastMCP 以 ASGI 子应用挂载到 FastAPI `/mcp/`，使用 streamable HTTP 与 bearer token 鉴权。
- 当前向外部 Agent 提供 7 个工具：`search_activity / get_recent_activity / get_app_breakdown / get_category_stats / search_summaries / ask_agent / apply_label`。
- MCP 复用服务端检索和 Agent 工具实现，外部编码 Agent 可以查询个人活动、调用内置问答并提交受审计的分类修正。

## 隐私与访问控制

- capture 前置隐私门支持随时暂停、应用黑名单和窗口标题关键词过滤；命中规则的采集 tick 不生成活动记录或截图。
- 支持 metadata-only 模式：继续记录活动元数据，同时关闭截图与缩略图存储。
- Web UI、业务 API、缩略图和原图路由均置于登录或 bearer token 鉴权后；MCP 与 ingest 使用独立 bearer token。
- 本地 VLM 与 embedding 是默认 dogfood 形态；切换模型 endpoint 时，推理边界由配置显式决定。

## 关键数字与口径

- **253 commits**，2026-04-09 起持续开发。
- **产品代码约 2.18 万物理行**：后端 12,591 行、前端 9,231 行；测试约 8,185 行。
- **测试基线**：542 passed / 49 个测试文件。
- **真实数据**：36,024 条活动记录；最近 30 天中 18 天有采集数据，累计约 126 小时。该数字表示实际使用覆盖，不表述为连续运行一个月。
- **图片数据**：32,070 帧 / 12.73 GiB，覆盖约 44 天；工作日中位数约 0.55 GiB/日。
- **链路观测**：经公网中转返回家庭服务器的路径实测上行约 300KB/s，单个 5MB 历史图片请求约 40 秒；切换到 LAN/SSH 直连后，约 5000 个 outbox 积压项在数分钟内排空。两组数据来自不同网络路径，用于说明多端点选路价值，不作为代码优化前后对比。

## 面试展开

### 案例一：至少一次投递下的并发与顺序

拆分 client/server 后，公网高延迟和大图片会导致 outbox 积压。简单增加多个 sender 会破坏同一活动的“元数据 → 截图 → 结束事件”依赖关系，单一 ack 游标也不能越过先失败的条目。最终采用服务端幂等吸收重放，在客户端跨活动并发、同活动顺序执行，并只确认连续完成前缀；同时支持多端点探测与切换，让采集、上传和网络恢复相互解耦。

面试官可继续追问：

- **为什么选择至少一次而不是恰好一次？** 网络超时后客户端无法知道服务端是否已落库；持久化重试配合幂等键可以用更简单、可恢复的方式得到业务上的一次效果。
- **并发上传如何保持顺序？** 只约束同一 `client_record_id` 的前后依赖，不同记录可并发；确认游标只推进连续完成的队列前缀。
- **为什么个人项目仍需要 outbox？** 采集是持续产生数据的前台路径，家庭服务器和公网连接可能暂时不可用，本地持久化队列可以避免把采集可用性绑定到网络。
- **为什么隐私场景仍然关心带宽？** 数据目的地是本人控制的家庭服务器，但外出时终端仍需经过受限上行传输连续截图；数据控制权和链路容量是两个不同维度。

### 案例二：分层记忆如何控制 Agent 上下文

逐帧记录适合查证细节，不适合直接回答一周或一个月的问题。TimeTrace 将活动按五种时间粒度预聚合，Agent 可以先读取日/周摘要，再对相关窗口向 6 小时、1 小时、5 分钟下钻，最后才读取窄时间窗的原始记录。这样同时保留可追溯细节和长期查询能力。

面试官可继续追问：

- **为什么使用固定时间窗？** 边界确定、可幂等重建，父子关系清晰，适合统计聚合和增量更新。
- **为什么高层只读取子摘要？** 控制输入规模，使周级摘要的成本不随原始帧数量线性增长。
- **内置 Agent 与 MCP 有什么区别？** 内置 Agent 负责产品内自然语言问答和工具编排；MCP 是提供给外部 Agent 的标准化访问协议，两者复用同一批活动数据与受限写入规则。

## 产品定位

TimeTrace 的重点不是持续录制视频，而是把桌面活动转化为一套可可靠摄入、可分层压缩、可检索，并能安全提供给 Agent 的个人长期上下文。

| 参照产品 | 主要形态 | TimeTrace 的定位差异 |
|---|---|---|
| Rewind / Limitless | 商业化个人记忆产品 | 自托管部署、可审计数据管线、MCP 接口 |
| Windrecorder | 本地屏幕记录与检索 | 双进程可靠摄入、VLM 语义理解、五级记忆摘要 |
| OpenRecall | 跨平台本地活动记录 | Windows 窗口元数据、Home Lab 分离部署、受限写 Agent 接口 |

## 对外口径与状态边界

- “自托管”同时包含单机一体化与家庭服务器分离部署，不表述为数据始终只存在采集端。
- “可靠同步”指 outbox、至少一次投递、幂等与顺序约束；公网与 LAN 数据不组成性能优化箭头。
- “文本语义检索”由独立后端搜索端点提供；时间轴搜索入口当前使用 FTS5 关键词路径。
- “图像近似检索”指 pHash 汉明距离与 VLM 文本语义融合，不表述为通用目标检测或 OCR。
- “五级摘要”已落到 `summaries` 表、rollup/narrative builder 与查询工具，不等同于五层 Agent。
- “Agent 写权限”仅指分类修改与审计记录，不扩展到删除或修改原始活动数据。
- 项目代码当前以私有仓库维护，对外表述为个人项目或自托管系统。
- 后续工程增强与验收目标见 [timetrace-todo.md](timetrace-todo.md)；完成并验证后再迁入本文件的已完成事实。

## 代码与材料出处

- 代码仓库：`D:\Github\TimeTrace`
- 关键实现：`client/core/outbox*.py`、`client/core/backend.py`、`server/api/routes/ingest.py`、`server/summary/`、`server/agent/`、`server/mcp_layer/server.py`
- 架构材料：`infra/architecture/client-server-split.md`、`infra/PLAN-MULTIPATH-CLIENT.md`、`infra/PLAN-BETTER-AGENT.md`
- 运行数据与排查记录：`devlogs/infra/archive-202606021128-upload-diagnosis-lan-direct.md`、`devlogs/backend/archive-202606222322-memory-pyramid-phase1.md`
