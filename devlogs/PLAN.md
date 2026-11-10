# TimeTrace 当前状态与滚动计划

**最后核对：2026-11-10。** 工程底座 PR #25 已合入 `main`（`b9c2abc`），本地和远端已合并分支已清理。当前工作分支 `codex/outbox-ack-recovery` 修复确认游标写入失败导致客户端退出的问题，已完成本地 Windows 与双容器验收；修复尚未合入主线。未部署，未修改生产数据或已安装客户端。

本文是唯一的当前待办入口。架构看 [项目 Wiki](../infra/readme.md)，部署看 [部署手册](../infra/architecture/web-deployment.md)，历史过程看 [DevLog 索引](README.md)。事实冲突时以代码、对应提交的测试及有日期的运行证据为准；旧归档只说明当时的状态。

## 验证基线

| 对象 | 最近证据 | 边界 |
|---|---|---|
| 开发主线 | [PR #25](https://github.com/Vanilla-Yukirin/TimeTrace/pull/25) 合并至 `b9c2abc`；[合并后的 main CI](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/42775723309) 成功 | Outbox 修复基于该提交；远端修复验收以对应 PR 最新提交检查为准 |
| 本地单元测试（11 月 10 日） | Windows：717 passed / 5 skipped，含 4 个真实 Windows 只读/句柄共享故障用例；Linux：711 passed / 11 skipped；Outbox 定向回归 52 passed | 临时目录和合成数据；跳过真实 VLM 测试及不适用的平台用例 |
| 构建与静态检查（11 月 10 日） | Ruff、前端 lint/TypeScript/build、Windows ZIP 与冻结 EXE 启动、Actionlint 工作流检查通过 | 此处记录本地结果；远端 Actions 结果由 PR 检查保留 |
| 双容器 E2E（11 月 10 日 15:43） | 11 个场景通过，含确认替换被拒绝后恢复；应用链路 29.61 秒；证据 `artifacts/timetrace-e2e-3117ee039b/` | 模拟画面/模型及 OS 替换故障，真实应用/网络/存储/浏览器；测试容器、网络和卷已清理 |
| 发布分支与生产验收 | 11 月 10 日只读核对生产仍为 `81a10b6`；服务端近期分析和摘要持续推进 | 近期正常不代表历史覆盖完整；本轮不更新运行制品 |
| Windows 安装包 | 两台设备上报 v0.1.1；其客户端源文件与 `81a10b6` 一致 | 不能只根据发布日期判断客户端过时；代码签名和自动更新仍未完成 |

## 已完成的主线

| 范围 | 当前实现 | 证据入口 |
|---|---|---|
| 采集与离线发送 | Windows 窗口/关键帧、黑名单、暂停、Outbox、endpoint 选择与 SSH 隧道；单进程和分离模式均存在 | `src/timetrace/client/`、`tests/test_endpoints.py`、`tests/test_outbox_sender.py` |
| 活动区间正确性 | 观测停顿后按最后观测时间收口；可信 close 随 Outbox 重放；服务端有界 lease；修复 CLI 默认只读、apply 前备份 | `tests/test_capture_service.py`、`tests/test_data_quality_admin.py` |
| 设备身份与查询 | 设备注册/Token 绑定、改名与停用、设备筛选、多设备时间轴、逐设备统计和墙钟并集口径 | `tests/test_device_identity.py`、`tests/test_device_query.py`；设置页 `DeviceManager` |
| 分析与检索 | VLM 描述 + flat-6 分类；REST/MCP/Agent 共用文本检索；图搜为 pHash + 描述 embedding，缺 embedder 时退回词重叠 | `server/retrieval.py`、`server/api/routes/search.py` |
| 分层记忆 | 五层指标/叙述；设备级 `device_summaries`；设备叙述公平调度，全局按设备拼装 | `server/summary/`、`tests/test_device_pyramid.py` |
| Agent/MCP | 最新活动、粗到细摘要总览、精确下钻、稳定双向分页；缺记录不等于用户离线 | [MCP 契约](../infra/architecture/mcp-layer.md)、[PR #23](https://github.com/Vanilla-Yukirin/TimeTrace/pull/23) |
| 前端与客户端 | 时间轴性能与交互修复；设置页设备管理；Windows 托盘、回环控制台、安装器与标签驱动 Release | [W1–W6](infra/archive-202610220210-owner-review-w1-w6.md)、[安装包说明](../packaging/windows/README.md) |
| 私网部署 | Tailscale Serve → loopback nginx → 同 SHA SPA/API；GHCR 不可变镜像、主机主动更新与兼容门禁 | [PR #24 验收](infra/archive-202610230447-private-ts-pr24-deployment.md) |

W1–W6（PR #14–#21）及 PR #23/#24 已合入，且包含于最近验收的发布版本。原来的“设备查询待实现”“设备金字塔暂缓”“尚未部署”“缺 Release 工作流”等条目已移出待办。这里的完成不代表历史数据已全部修复、所有客户端均已更新或长期可靠性已验收。

## 本轮工程底座

按用户确认，先完成工程底座，再处理功能重构。规范与入口见 [工程验收文档](../infra/architecture/engineering-workflow.md)。

- 本地与 CI 统一执行 `python scripts/check.py all`，交付证据保存在 `artifacts/`。
- 两个容器跑真实采集、Outbox、传输、服务端、数据库、分析/摘要、MCP 和浏览器；画面与模型响应固定，验证断网、客户端终止、回执丢失和服务端重启。
- 服务端与 Windows 制品发布依赖整套 CI；远端分支保护配置仍需核验。
- JSON 日志保留异常组、请求 ID 与 worker 记录 ID；客户端轮转日志；服务端和客户端提供指标，新增 readiness。
- 工程底座已通过本地/远端验收并合入，见上表及 [工程底座记录](infra/archive-202611100308-engineering-foundation.md)。本轮补齐 Windows 文件替换拒绝验收；屏幕/托盘、远端分支保护与生产监控接入继续安排。

## 当前修复：Outbox 确认写入恢复

11 月 10 日故障为 `state.json.tmp → state.json` 的 `PermissionError [WinError 5]` 穿透 TaskGroup。原现场待发送数据仍保留，本轮没有操作该目录。工作分支现在只重试本地确认，成功前不推进游标，等待时释放队列锁并响应停止信号；扫描/压缩遇到磁盘错误后重新读取持久游标，避免沿用旧快照。控制页和日志显示存储错误及恢复过程。状态和日志替换改用独立临时文件，已有 JSONL/游标格式保持兼容。

Windows 真实 OS 故障、串行/并发确认、停止与重启重放、压缩失败恢复及双容器整体链路已验证。队列事务化和容量策略仍是后续结构调整；修复发布到已安装客户端需要独立发行步骤。过程见 [修复记录](infra/archive-202611101543-outbox-ack-recovery.md)。

## 已确认的问题与后续顺序

1. **P0：摘要 overview 会把内容不完整的历史窗标成 covered。** Yuki 10 月 19 日 04:00 至 26 日 04:00 周窗原始记录 17,581，摘要仅 10,275，差 7,306。当前判定依赖时间窗覆盖，需加入来源完整性校验，并用缺子窗 fixture 回归。
2. **P1：历史设备摘要和归属尚未统一。** 只读审计：Yuki 日摘要 19/76、Tohru 17/25、unknown 0/24；unknown 记录 45,189。全局缺 17 个日窗、6 月 22 日 10 个 5min 窗 pending、6 月 38 条 analysis error_final。10 月 22 日以来设备小时及以上摘要完整；历史整理另行限定范围、备份和验收。
3. **P1：前端依赖告警。** 11 月 10 日本地 `npm audit --omit=dev` 报 6 项：dompurify critical、react-router/react-router-dom/zod high、mermaid moderate、katex low。后续锁文件更新须重新执行浏览器 E2E；当前 CI 尚未增加依赖漏洞门禁。
4. **P1：客户端能力与状态整理。** 明确采集、可靠发送、暂停/恢复、容量和错误诊断的边界；先以端到端验收约束行为，再评估用 SQLite 事务队列替换 JSONL + 游标文件。

## 其他验收缺口

按以下顺序推进，每项保留独立证据，不把代码完成与生产数据完成混为一谈。

1. **补齐另一台 Windows 客户端与外部 MCP 消费端证据。** 服务端确认 Tohru v0.1.1 最近一次收件为 11 月 9 日 23:08，7 日内 14,753 条；其本机 endpoint 与 Outbox 尚未直接核验。Setsuna 消费端仍需刷新工具并验证 latest → overview → drill-down 和分页。
2. **历史数据整改。** 只读覆盖审计已发现上列缺口。修复或回填使用 `repair-data-quality --apply`、`backfill`、`narrate`，事先确定范围、备份与授权；不凭猜测给旧记录补设备身份。
3. **完成传输与恢复长测。** 已有 endpoint 选择、sender 重试和重放幂等测试；补齐多路径故障→切换→恢复回切→排空的整体证据，以及两台设备同时补传、长期离线容量和受控生产回滚演练。真机断网/故障注入单独安排，不能从单元测试推断生产长测完成。
4. **客户端发行。** 旧 v0.1.1 源码与现有生产客户端部分一致；本轮日志/指标/恢复改动进入后续新版本验收。发布需要对应授权。

验收至少记录时间、代码/制品版本、范围、观测结果和未覆盖项。原始截图、活动正文、凭据和私网访问地址不进入归档。

## 后续工程待办

| 优先级 | 工作 | 完成条件与边界 |
|---|---|---|
| P1 | 隐私管线 | OCR/区域检测/落盘前模糊尚未实现；同时覆盖单进程与 Outbox 路径，以临时图片验证异常/重试不落未脱敏图，保持 opt-in |
| P1 | 截图与 Outbox 容量 | 先量出按日增长、总字节、最老积压和磁盘预算；清理先 dry-run，再明确保留策略与恢复边界，不静默删除未上传记录 |
| P1 | 数据库迁移与恢复 | 兼容门禁已存在；仍需完整旧库 fixture、版本矩阵、迁移中断/重入和恢复演练，不把拒绝降级等同可自动降级 |
| P1 | 图搜 VLM 熔断 | `/v1/search/by-image` 尚未复用 `VLMHealthGate`；验证故障快速降级与恢复，保留 pHash/关键词通道 |
| P1 | 分支与文档约束 | 核验并补齐 main/deploy 的远端保护或等价 gate；增加轻量入口/链接/过期状态检查，避免再次出现多个“当前计划” |
| P2 | 设备管理补全 | 已有列表、改名、描述、版本、最近上传和停用；仍缺恢复启用及完整诊断/容量视图，不能重新按“从零建设设备页”排期 |
| P2 | 客户端易用性 | 托盘、回环控制台和手工安装/更新已实现；补首次配置向导、脱敏诊断导出、另一台机器安装验收；签名与自动更新独立评估 |
| P2 | 性能与容量基准 | 连续运行 CPU/内存、每日磁盘增长、查询 P95、单卡推理排队；向量索引替换先有真实规模基准 |
| P2 | 图标替换 | 等 owner 的素材/风格方案，清单见 [Owner Review §2](../infra/PLAN-OWNER-REVIEW-2026-10.md) |

## 技术债与研究边界

- **叙述重发**：`source_hash` 不含子叙述文本，手动重叙述后父层不会自动更新；目前需显式 `--force`。
- **图搜关键词退路**：`_desc_token_search` 使用 LIKE 词重叠，`_like_fallback` 仍是 LIKE；不要记成已全面切到 FTS。向量通道仍为 numpy cosine 扫描。
- **LLM 可观测性**：report 与 Web Agent streaming 尚未接入统一 `timed_chat_completion` 账本；补周窗、积压阈值、失败重试和单卡排队观测。
- **数据库并发**：单连接与全局锁的读写串行需要基准后再决定只读连接方案。
- **小型清理**：`capture_mode=fullscreen` 配置未接采集分支；不可恢复图像错误仍需明确终止重试；`server/mcp_layer/tools.py` 是未使用的旧桩，真实 MCP 注册在 `server.py`。
- **Classifier V2**：[预备稿](../infra/PLAN-CLASSIFIER-V2.md)不是生产实现。已有 225 帧实验，正式推进须补可复现评估、纯 VLM 基线、shadow mode、误分类防扩散与回滚判据。
- **明确延后**：Postgres/Redis/S3、TUI、deep scan/子 agent map-reduce、通用多租户部署。`timetrace-embserver` 已是独立入口，但未接主 worker/检索；切换 embedding 模型需另行处理维度和历史重嵌。

## 运行与发布约束

- 一个 canonical server、一套可写 SQLite + blob tree；多个 endpoint 只代表同一后端的不同到达路径，不做双服务端合库。
- `main` 为开发主干，`deploy` 为生产制品指针。先完成目标 SHA 的 CI，再经授权以 fast-forward 推进 `deploy`；镜像成功后由部署机执行 `timetrace-update [<sha>]`。不在部署机 pull/reset 源码或直接操作 Compose 绕过健康检查与回滚。
- 当前入口使用私人 Tailscale Serve，nginx 默认 `127.0.0.1:19446`，API 默认 `127.0.0.1:8765`；无旧公网回退，MCP Host/Origin 精确匹配，网页登录/Bearer 鉴权保留。
- 存储 UTC epoch ms；请求按 IANA 时区解析，摘要窗口使用服务端时区。时长是捕获到的活跃下限；墙钟并集与逐设备时长之和不能混称。
- rollup/narrative 默认关闭，运行环境通过对应开关启用。历史 35B 端点的 context/串行要求见 [CLAUDE.md](../CLAUDE.md)；实际模型、上下文与积压需现场核对。
- 生产数据修复/回填先只读审计、备份并取得对应授权。数据库备份不包含截图；恢复演练须单独覆盖 blob tree。

## 维护方式

本页保留当前状态、未完成项和证据日期；已完成过程进入新 devlog。`devlogs/**/archive-*.md` 不回改。架构页说明实际行为，设计稿明确标注实现范围；不另建重复待办清单。测试数量只在本页维护，其他入口链接到此处。
