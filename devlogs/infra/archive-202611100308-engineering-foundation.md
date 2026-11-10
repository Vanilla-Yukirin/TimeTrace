# 工程底座本地落地与验收

时间：2026-11-10 03:08，北京时间。工作分支 `codex/engineering-foundation`，基于 `cfbc31ba1469748178fa765145afe42faca7398a` 的未提交工作树。本轮范围由用户确认：先做双容器端到端、本地与 CI 同一入口、日志和指标，再处理功能重构。没有推送、发布、部署或修改生产数据，也没有替换本机已安装的客户端。

## 交付

- `scripts/check.py` 提供 `lint`、`unit`、`frontend`、`e2e`、`all`，本地和 GitHub Actions 共用。CLAUDE 明确代码交付必须运行完整检查；AGENTS 保持转引。
- `qa/e2e/compose.yml` 建立独立双容器环境。服务端使用生产 Dockerfile 构建的应用镜像；客户端运行真实 CaptureService、Outbox、Sender 和控制接口，仅系统画面输入与上游模型响应由 fixture 提供。
- 端到端实际经历断网采集、强制结束客户端、同队列恢复、服务端收件后丢回执、重复发送、模型短暂失败、分析与 embedding、摘要循环、MCP 分页、服务端容器重启，以及浏览器登录/首次改密/时间轴图片/设备页/暂停恢复。
- CI 新增 Windows/Linux 矩阵、E2E 证据上传和汇总 gate；服务端制品与 Windows 标签发布复用 CI。工作流静态校验通过；远端 Actions 与分支保护尚未验证本次改动。
- 三个应用入口统一 JSON 日志，客户端保留轮转文件和完整异常组；请求 ID、worker 记录 ID 便于关联。日志过滤机器凭据、请求 URL 和窗口标题，首次 token 只记录文件位置。
- 服务端新增 readiness 和受鉴权保护的 Prometheus 指标，客户端在现有回环接口提供指标。指标包含分析积压、摘要状态/进度、后台任务、已接入账本的 LLM 请求，以及客户端待发送数量/字节/年龄和活动时间。

## 验收证据

最终本地证据目录：`artifacts/timetrace-e2e-96aa1d60bd/`，包含 `source.json`、`result.json`、`junit.xml`、`compose.log`、`client-process.log`、服务端/客户端 metrics、浏览器 trace、时间轴/设备页/控制页截图。结果为通过；日志中实际找到 `e2e-observation` 请求 ID，窗口标题为 `[redacted]`。测试容器、内部网络和命名卷已清理。

Windows/Linux 单元测试、Ruff、前端 ESLint/TypeScript/build、Windows 安装包与冻结 EXE `--help`、Actionlint 均通过。计数和最近基线统一见 [PLAN](../PLAN.md)。原始日志与测试制品在忽略提交的 `artifacts/`，可使用同一检查入口重建。

首轮端到端发现 Linux 本地控制接口在进程被强制终止后立即重启会遇到端口占用；增加 POSIX `SO_REUSEADDR` 后，恢复与重传场景通过。Windows 继续使用 `SO_EXCLUSIVEADDRUSE`。

## 后续问题

本轮没有修复用户报告的 Windows `state.json.tmp → state.json` 权限异常，也没有替换 JSONL Outbox。完整崩溃日志和恢复测试环境已经就位，下一阶段需要处理确认写入失败及事务队列设计。

只读审计确认历史摘要存在来源不完整但 overview 返回 `covered` 的问题，以及历史设备摘要、unknown 归属和旧分析错误。具体缺口及整改顺序维护在 PLAN。前端生产依赖审计也有高危/严重告警，锁文件修复需要重新执行浏览器 E2E。

Windows 真机屏幕/托盘/NTFS 文件占用、另一台客户端本地状态、长时间离线、多路径切换、旧库迁移恢复和真实模型质量仍需各自的验收。生产 Prometheus/Grafana 接入属于后续部署步骤。

工程规范：[开发、验收与可观测性](../../infra/architecture/engineering-workflow.md)。
