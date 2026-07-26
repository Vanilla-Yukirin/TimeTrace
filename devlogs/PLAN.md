# TimeTrace 滚动 TODO / Plan

**最后更新：** 2026-07-21
**验证基线：** 工作树基于 `9060941`；本轮本地全量测试 542 passed（49 个测试文件）

> 本文是项目唯一的「现在做到哪、下一步做什么」入口，只保留未完成项、运行约束和近期顺序。
> 已完成过程移到 [`devlogs/README.md`](./README.md)；架构入口看 [`infra/readme.md`](../infra/readme.md)。
>
> 发生冲突时按：**真实代码与测试 → `infra/` 活文档 → 本 PLAN → 最新 devlog → 旧 devlog**。

---

## 一页现状

TimeTrace 已越过 MVP 和演示阶段，并完成过个人生产环境验证：Windows 客户端采集活跃窗口与关键帧，服务端在本地完成存储、VLM 描述与分类、混合检索、分层摘要，并通过 Web、REST 和 MCP 对 AI 暴露上下文。

> **当前运行态（2026-07-21 只读核验）：** 家中小主机实际已连续运行约 21 天，SSH/`vanilla` 登录可用，`timetrace-server.service` 为 active，机内 `127.0.0.1:8765/healthz` 返回 200，根分区约剩 173GB；三个 `frpc` 进程存在，但公网 `https://timetrace.yukirin.me/healthz` 返回 502，需继续定位 FRP/VPS 反代链路。本机采集客户端据 owner 口述已较长时间未运行，尚未核验其 outbox。恢复公网链路和完成发布前检查前，禁止把 `deploy` 分支向前推进。

| 子系统 | 当前状态 | 代码/运行事实 |
|---|---|---|
| 单进程桌面版 | ✅ 稳定默认 | `timetrace`：capture + API + worker + tray |
| 双进程采集 | ✅ 历史生产验证 | `timetrace-client` → outbox → 多 endpoint/SSH tunnel → `timetrace-server`；多 endpoint 是同一后端的不同网络路径，不是多服务端同步 |
| 分析与分类 | ✅ V1 上线 | 本地 VLM 产 `vlm_desc` + flat-6 分类；审计页可看队列、耗时、建议与 decision trace |
| 文本搜索 | ✅ 上线 | `/v1/search/text`：FTS5/LIKE + text embedding cosine + RRF；embedding 失败降级关键词-only |
| 图像搜索 | 🟡 可用、有债 | pHash + BK-tree + VLM 描述文本通道 + RRF；文本侧仍是 LIKE |
| 记忆金字塔 | ✅ 上线 | `5min→1h→6h→day→week` 指标级联、source_hash 重发、LLM summary-of-summaries |
| AI 上下文 | ✅ 上线 | Web Agent、报告、MCP 7 工具；`search_summaries` 支持粗→细下钻，`apply_label` 是唯一写工具 |
| 可观测性 | 🟡 主链上线 | `/audit`、`/pyramid`、`/llm-log`；账本覆盖 worker/narrative/ask_agent，尚缺 report 与 Web Agent streaming |
| 生产发布 | 🟡 后端活着、公网链路异常 | `deploy` push 触发 box 后端部署与 xcy SPA 发布两个独立 job；当前机内健康检查 200、公网 502 |
| 多设备 | ⚠️ 传输可用、身份缺失 | 两个客户端可向同一后端上传且各有 outbox；`X-Device-Id` 当前只写日志，未持久化、不可筛选，聚合统计也没有设备维度 |
| 用户界面 | 🟡 Web 已有、原生客户端 GUI 缺失 | 服务端已经是 FastAPI；已有 React/Vite SPA（时间轴、搜索、设置、Agent、报告、审计等），但没有设备管理页，双进程客户端也没有完整桌面配置/诊断界面 |
| Classifier V2 | ⚠️ 未实现 | 225 帧校准支持保守 KNN 加速器方向；正式设计、代码和生产验证均未开始 |

「已上线」表示代码已落地并在个人生产环境验证过，不代表已经完成通用产品化、跨机器性能基准或自动恢复长测。

---

## 当前最高优先级

### P0：恢复项目可控性

- [x] 用 2026-06-22 至 2026-07-20 的 devlogs 与真实代码重建 README / infra 首页 / 本 PLAN。
- [ ] 增加轻量文档漂移检查：阻止部署默认分支、关键入口路径和状态标记再次长期过期；动态测试数只在本文保留一份。
- [ ] 建立一页 owner runbook：启动/停止、健康检查、备份、回滚、发布、模型加载、积压排查各只有一个官方入口。
- [ ] 给 `main` / `deploy` 配置远端保护或等价检查，把“deploy 只接受已通过 CI 的 main fast-forward”从口头约定变成机器约束。

### P0.5：恢复运行，并把第二台电脑作为正式设备接入

推荐拓扑只有一个事实源，不做“两套服务端以后再合并”：

```text
Windows PC A（独立 device_id / token / outbox）──┐
                                                  ├── 一个 canonical timetrace-server ── 一个 SQLite + blob tree
Windows PC B（独立 device_id / token / outbox）──┘
```

只要小主机能稳定在线，它继续做 canonical server；若它无法常开，可以把服务端整体迁到另一台常开电脑，但任一时刻仍只保留一个可写事实源。当前没有双服务端复制、冲突解决或离线合库能力。若第二台电脑不是 Windows，现有采集客户端不能直接使用，它只能承担服务端/浏览器角色。

#### A. 先做只读恢复审计，不部署

- **已核验**：小主机 SSH 开放；旧 `GTi13-Ultra` alias 固定为无权登录的 `yuki`，部署用户 `vanilla` 可登录。`timetrace-server` active、机内 healthz 200、三个 frpc 进程存在、公网 healthz 502、磁盘剩余约 173GB；运行仓库为 `9060941`，工作树只有一个未跟踪的 `.env.bak.*` 文件。本轮未 fetch/pull/restart/写远端文件。
- **Next**：先修正文档/本机 SSH alias 的用户名，再只读定位“frpc 进程存在但公网 502”的断点；继续盘点数据库/截图体积、最后一条记录时间，以及两台电脑各自 outbox 的条数、最老记录与字节数。
- **Done when**：明确 canonical server 放在哪台机器；已有数据完成可恢复备份；健康检查通过；知道停机期间是否存在 backlog、当前存储余量和模型可用状态。
- **Evidence**：把命令、关键输出、时间戳和选定拓扑写入新的 `devlogs/infra/archive-*.md`；不把密钥、截图内容或公网凭据写进日志。
- **Rollback**：本阶段不改数据库、不拉代码、不重启部署；发现磁盘、时间或数据异常就停止恢复动作。

#### B. 第二台电脑接入前，先让设备身份成为数据

- **Next**：新增服务端 `devices` 注册表，至少包含不可变 `device_id`、用户自定义 `display_name`、`created_at/last_seen_at/revoked_at`；为 `records` 增加可空 `device_id`。先提供管理员注册/改名/停用设备的 API，再让 ingest 校验并持久化设备身份，不能继续把任意 `X-Device-Id` 只当作可信日志字段。
- **身份约束**：每台电脑使用独立 token，并把 token 与已注册设备绑定；请求头只能声明该 token 被授权的设备，不能靠修改 header 冒充另一台电脑。`device_id` 是机器身份且不随改名变化，`display_name` 是用户可修改的展示名。
- **读侧范围**：记录、搜索、统计、摘要、API、MCP 和未来设备管理页都支持设备筛选；旧数据统一归为 `_unknown_device`，除非有可靠证据，否则不猜它来自哪台电脑。
- **Done when**：A/B 两台机器上传的记录可分别查询；相同记录重放仍由 `client_record_id` 幂等去重；未注册、已停用或 token/device 不匹配的上传被明确拒绝；UI 和 MCP 返回值能说明记录来自哪台机器。
- **Evidence**：设备注册/API 权限测试、schema 迁移测试、双客户端 ingest 集成测试、token/device 冒充测试、按设备搜索/统计测试，以及旧库升级 fixture。
- **Rollback**：只做 additive nullable migration；设备过滤保持可选，回退旧版本时不重写或删除历史行。

#### C. 分阶段注册两台客户端

- **Next**：每台电脑使用各自的 `client.toml`、唯一 `device.id/name`、独立 bearer token 和独立 outbox；endpoint 优先级列表都指向同一个后端，只表示公网/隧道等不同到达路径。先接 A 验证一轮，再接 B。
- **Done when**：两台机器断网均可本地排队，恢复后分别排空；服务端无重复业务行；能撤销单台机器的 token 而不影响另一台。
- **Evidence**：每台机器的设备 ID、token label、outbox 前后状态、服务端按设备查询结果；不记录 token 本体。
- **Rollback**：停掉新增客户端并撤销它自己的 token；不要复制/共享 outbox 目录，也不要让两台机器共用 device ID。

#### D. 明确“双机同时使用”的统计语义

- **Next**：先写口径再改聚合：全局 `active_seconds` 可继续按时间区间并集计算；按 app/category 的时长必须能按设备拆分，并明确合并值会把两台机器同时工作的时间相加、可能大于全局墙钟时间；摘要记录覆盖了哪些设备。
- **Done when**：同一时段 A/B 同时活跃的 fixture 能稳定解释全局墙钟、逐设备墙钟和 app/category 合计三种数字；Web/MCP 标签不会把合计误称为“真实经过时间”。
- **Evidence**：重叠区间测试、API 契约样例、UI/MCP 文案截图或响应 fixture。
- **Rollback**：新维度与筛选均保持向后兼容；无法确认口径时只显示逐设备数据，不展示误导性的合并总数。

#### E. 给长期离线补上 backlog 护栏

- **Next**：量出每台电脑一天的实际 outbox 增长，为“服务端离线 N 天”设置容量预算；增加 backlog 条数、字节数、最老年龄提示，并测试限速排空。
- **Done when**：小主机离线时客户端仍可在预算内采集；接近磁盘阈值时有明确提示/降级策略；恢复后不会因两台机器同时排空压垮 SQLite、图片 IO 或分析 worker。
- **Evidence**：临时 outbox 长时间模拟、排空吞吐和磁盘曲线；生产只做只读容量盘点。
- **Rollback**：限速与提示可关闭；不得通过静默删除未上传记录解决 backlog。

#### F. 部署必须经过主机 readiness gate

- **Next**：在推进 `deploy` 前人工确认小主机已开机、FRP/SSH 可达、磁盘充足、备份存在、模型状态明确；随后把这些检查固化成 workflow preflight，并评估为后端/前端增加独立的手动开关。
- **Done when**：后端主机离线会在改变生产指针前被阻止；操作者能明确只部署后端、只发布前端或两者都做；两个结果不会被误报为一个整体成功。
- **Evidence**：离线/在线两种 preflight 测试和 workflow run 记录。
- **Rollback**：preflight 误判时保持 `deploy` 不变；不要为了让 workflow 变绿而跳过主机检查。

在完成 B 之前，第二台客户端可以用于短时实验，但记录会与第一台混在一起且无法事后可靠拆分，不建议作为正式日常运行方式。

### P1：先补可靠性闭环

#### 1. 客户端 endpoint 故障转移（下一项，只做这一项）

- **Next**：先用临时 outbox + fake endpoints 写自动化故障/恢复测试；通过后再申请一次真机隧道中断授权。
- **Done when**：当前 endpoint 失败后同一条记录由下一 endpoint 成功接收；acked 游标只前进不回退；高优先级 endpoint 恢复后能切回；全程无记录丢失或重复业务行。
- **Evidence**：测试输出、sender 结构化日志、前后 outbox state、服务端 `client_record_id` 查询。
- **Rollback**：恢复原 client.toml/隧道；不手改 outbox，依赖 at-least-once replay 与服务端幂等。

#### 2. 隐私管线

- **Next**：先写“原图不得落盘”的数据流与失败不变量，覆盖单进程和 outbox 两条 capture 路径，再决定 OCR/区域检测模型。
- **Done when**：`privacy.mode=full` 下任何成功、异常、重试路径都只持久化模糊图；`text_only/off` 行为有回归测试。
- **Evidence**：tmp 目录文件枚举测试 + 图像内容断言，不拿生产截图做实验。
- **Rollback**：新模式保持 opt-in；回退到现有黑名单/暂停机制，不迁移历史图。

#### 3. 截图配额

- **Next**：只做 read-only usage report，量出按日增长、图片总量和按 age/size 的候选集；本阶段不删除文件。
- **Done when**：dry-run 与真实目录字节数一致；正式驱逐先标 `deleted_at`、再删文件，元数据/pHash/文本上下文仍可查；中断可重跑。
- **Evidence**：临时目录集成测试 + 生产只读报告；任何生产删除另行授权并先备份。
- **Rollback**：驱逐功能默认关闭；删除前保留候选清单，恢复能力取决于备份而非数据库软删标记。

#### 4. SQLite 迁移与恢复

- **Next**：盘点当前 `_migrate()` 的所有隐式版本，写旧库→当前 schema 的版本矩阵，不先引入新框架。
- **Done when**：旧库升级、迁移中断、重复启动和降级拒绝均有临时 SQLite 集成测试；每次迁移可识别版本且在事务边界内。
- **Evidence**：固定旧 schema fixtures + `PRAGMA user_version`/等价版本断言。
- **Rollback**：迁移前备份 DB；不承诺自动 downgrade，失败时恢复备份并运行旧二进制。

#### 5. VLM/LLM 单卡保护

- **Next**：先让 `/v1/search/by-image` 复用现有 `VLMHealthGate.acquire()`，补 sleeping/open/recovery 三种路由测试。
- **Done when**：VLM 故障时搜索快速降级且不持续打端点；恢复后 semantic 通道重新工作；worker 行为不回归。
- **Evidence**：fake VLM 调用次数、响应状态和现有 worker gate 测试。
- **Rollback**：仅撤回路由 gate 接线；pHash/keyword 通道始终保留。

### P2：补齐产品界面（后续计划，当前不实现自动更新）

这不是从零搭前端：服务端已经是 FastAPI，仓库已有独立 React/Vite SPA。原则是复用现有服务边界，不再创建第二套后端或重复实现采集/发送逻辑。

#### 1. 服务端设备管理 Web UI

- **Next**：先完成 P0.5-B 的设备 API，再在现有 SPA 增加“设备”页面：注册/命名/停用、last seen、最近上传、客户端版本、outbox/backlog 摘要和连接异常。
- **Done when**：不 SSH、不手改配置即可完成两台电脑的注册、改名、停用和状态确认；所有写操作有鉴权、确认和审计记录。
- **边界**：FastAPI 继续只提供 API/OpenAPI；React/Vite SPA 继续独立构建和部署，不把管理页面塞进服务端模板系统。

#### 2. Windows 客户端 GUI

- **Next**：先做薄 GUI 技术 spike，只包裹现有 capture/outbox/sender 核心：设备名、服务端地址、连接状态、暂停/恢复、最后上传、backlog、诊断导出和安全退出；不要重写采集链路。
- **Done when**：普通用户无需命令行即可完成首次注册、启动/暂停、确认上传状态和导出脱敏诊断；关闭窗口与“停止采集/退出进程”的语义明确。
- **技术决策 gate**：比较“沿用 Python 的轻量 GUI”与“Tauri 等原生壳”在安装体积、系统托盘、权限、崩溃隔离和复用现有 Python core 上的成本，做小原型后再定，不现在锁框架。
- **明确延期**：安装器、自动更新、代码拉取和多机版本编排暂不设计；第一阶段仍允许每台电脑手动拉取代码并自行启动。

#### 3. 客户端/服务端契约

- GUI 只调用稳定的本地 client service/core 接口，不能直接改 outbox 文件或 SQLite。
- 服务端 UI 只通过受鉴权 API 管设备；设备注册、token 绑定和上传协议先于界面落地。
- 客户端版本、协议版本和能力列表作为设备 heartbeat 元数据，为未来兼容检查留接口，但本阶段不承担自动更新。

### P3：再决定 Classifier V2

已知实验：`qwen3-vl-embedding-2b`、225 帧、cosine 阈值 `T=0.13` 时近邻复用精度约 0.955（balanced）/ 0.988（按生产分布加权），预计可省约 30% VLM；距离只在近端可靠，并且必须加 `app_name` 一致性护栏。

正式实现前必须先完成：

- [ ] 把实验数据、采样方法、类别分布与错误样例搬进仓库内可复现的评估脚本。
- [ ] 写正式设计：纯 VLM 基线、pHash 近重复、embedding KNN 加速器、用户硬覆盖、缓存投毒失效策略。
- [ ] 先 shadow mode 只记录“若复用会怎样”，不改变生产分类；达到 precision 门槛后再灰度跳过 VLM。
- [ ] 明确 V1 加权投票的迁移/回滚方案，不能直接删掉现有 decision trace。

预备方向见 [`infra/PLAN-CLASSIFIER-V2.md`](../infra/PLAN-CLASSIFIER-V2.md)，它仍然是 draft，不是执行依据。

---

## 有价值但不抢主线的技术债

| 优先级 | 项目 | 当前事实 |
|---|---|---|
| Medium | 图搜文本通道 LIKE → FTS5 MATCH | `_bm25_search` 名字与实现不符，是已知残留 |
| Medium | 向量索引规模化 | 当前个人数据规模可接受；增长前先做 10K/50K/100K 基准，再决定 sqlite-vec/pgvector |
| Medium | 叙述重发语义 | `source_hash` 指纹指标/分类，不含子叙述；手动重叙述后父层不会自动自纠 |
| Medium | 周窗叙述与 backlog 运维 | 自动 loop 已有；需把周窗策略、积压阈值、失败重试写成可观测运维规则 |
| Medium | LLM 账本覆盖 | report 与 Web Agent streaming 仍绕过 `timed_chat_completion`，页面不是全调用账本 |
| Medium | 历史金字塔覆盖 | 线上增量已正常；历史 metrics/narrative 覆盖率尚未形成可重复审计与完成判据 |
| Medium | `capture_mode=fullscreen` | 配置存在但采集循环没有分支；要么实现，要么删除死配置 |
| Low | 不可恢复图像错误 | image load 失败应直接 `error_final`，避免浪费 retry 配额 |
| Low | 旧 MCP stub | `server/mcp_layer/tools.py` 无调用方，确认后删除，减少 agent 误读 |
| Deferred | Postgres/Redis/S3、TUI、通用发行 | 当前个人本地部署没有证据需要；需求出现前不扩张 |

---

## 分支与发布模型

目标状态只有两个长期分支：

| 分支 | 职责 | 规则 |
|---|---|---|
| `main` | 唯一开发主干、CI 基线 | 日常提交/短 feature PR 最终都回这里；必须保持可测试 |
| `deploy` | 生产发布指针 | 只接受 `main` 的 fast-forward；push 即触发后端 + 前端发布，不直接开发 |

短期 feature 分支只服务一个具体改动，完成后合回 `main` 并删除。历史长分支 `feature/refactor-split` 在 main 追平后进入退役流程。

每次发布必须按固定闸门执行：

1. 变更先进入 `main`，记录待发布 commit SHA。
2. 等待**该 SHA** 的 main CI 全绿，不能 push main 后立即推进 deploy。
3. 运行主机 readiness gate：确认小主机在线、FRP/SSH 可达、磁盘/备份/模型状态正常；任一不满足就停止，保持 `deploy` 不动。
4. 确认 `origin/deploy` 是该 main SHA 的祖先，只做 fast-forward：`git push origin main:deploy`。
5. deploy workflow 让后端与 `publish-frontend` 使用同一个不可变 SHA 独立执行；两个 job 必须都成功，不能用前端成功掩盖后端失败。
6. 验证 `/healthz`、公网首页和至少一个受鉴权 API，再宣布发布完成。

部署机只是 `origin/deploy` 的镜像，不在部署机上手工 `pull/reset/restart`。生产发布统一走 `.github/workflows/deploy.yml`；LM Studio 模型装载是工作流之外的唯一运维例外。

---

## 生产硬约束

- LM Studio 35B 端点必须 `context >= 16384` 且 `max concurrent predictions = 1`；该模型无法可靠关闭 thinking，叙述 token 预算不能随意压低。
- rollup/narrative 后台循环默认关闭；生产需显式设置 `TIMETRACE_ROLLUP_ENABLED=1`、`TIMETRACE_NARRATE_ENABLED=1`。
- 时长口径是「本机捕获到的活跃下限」，不是完整人生时间；设备未开机/未采集是主要低估来源。
- 原始截图主要在文件系统，数据库体积不能代表总存储；历史实测约 267MB DB 对 12GB 截图。
- 任何生产 DB 写入必须先 dry-run/备份并获得用户授权；优先使用 `backfill`、`narrate --force --grains` 等正式 CLI。

---

## 近期里程碑

1. **Control Baseline**：文档、分支和发布默认值统一；任何新 agent 能在 10 分钟内回答“系统怎么跑、哪里是真代码、现在最危险的是什么”。
2. **Runtime Recovery**：只读盘点小主机、现有数据与两台电脑的 outbox，选定唯一 canonical server，恢复健康检查但不顺手发布新版本。
3. **Device Identity**：持久化设备身份、补齐筛选与统计语义，再把第二台 Windows 客户端正式注册进来。
4. **Reliability Gate**：先完成 endpoint failover；再依次推进隐私不落原图、截图配额与迁移恢复，每项都提供上述证据和回滚记录。
5. **UI Productization**：在既有 FastAPI + React/Vite 上补设备管理页，再为 Windows client 做薄 GUI；自动更新继续延期。
6. **Classifier V2 Go/No-Go**：先 shadow 评估，再决定是否让 KNN 跳过 VLM；若收益或可靠性不足，明确关闭该方向。

---

## 本轮事实来源

- [`archive-202606222322-memory-pyramid-phase1.md`](backend/archive-202606222322-memory-pyramid-phase1.md)
- [`archive-202606261220-pyramid-phase2-3-narrative.md`](backend/archive-202606261220-pyramid-phase2-3-narrative.md)
- [`archive-202606270722-narrative-loop-search-golive.md`](backend/archive-202606270722-narrative-loop-search-golive.md)
- [`archive-202607200155-backfill-deploy-xcy-classifier-v2.md`](backend/archive-202607200155-backfill-deploy-xcy-classifier-v2.md)
- [`archive-202607200155-frontend-publish-workflow.md`](infra/archive-202607200155-frontend-publish-workflow.md)
