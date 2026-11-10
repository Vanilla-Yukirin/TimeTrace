# Outbox 确认写入失败的恢复

2026-11-10，北京时间。按用户要求合并 PR #25 至 `main`（`b9c2abc`），删除本地与远端 `codex/engineering-foundation`，从更新后的主线创建 `codex/outbox-ack-recovery`。`deploy` 仍为 `81a10b6`。没有部署、更新已安装客户端或修改生产数据。

## 故障与改动

原故障发生于服务端收件之后：客户端替换 `state.json` 被 Windows 拒绝，`PermissionError` 从 `ack_next` 穿透 Sender 与 TaskGroup，整个客户端退出。固定命名临时文件也可能受上次残留或外部文件占用影响。

确认失败现在保留当前条目，仅重试本地确认；不重复发送 HTTP，不触发 endpoint failover。退避沿用发送器配置，等待时释放 Outbox 锁，并响应停止/取消。控制页记录 `outbox_ack: PermissionError`，日志记录 operation、errno、winerror、尝试次数及恢复事件。永久受阻时数据继续待发送，不伪造确认。

扫描或压缩发生 I/O 错误后，发送器放弃旧快照并重新读取持久状态。压缩可能在失败前已经把游标重置为零，因此该路径允许幂等重放，不能直接确认旧快照的下一项。顺序协议错误和 JSON 损坏仍然报错。

`state.json` 和压缩后的 `log.jsonl` 使用独立的同目录临时文件，写入、flush、fsync 后替换；失败清理本次临时文件，目标内容保持完整。未改存储格式或执行迁移。

## 验收

- 临时目录真实文件覆盖串行/并发确认、持续失败后的停止/取消和重启重放、压缩重置游标后失败、协议错误传播。
- Windows 通过只读属性复现真实 WinError 5，通过 `CreateFileW` 拒绝删除共享复现文件占用；解除条件后原发送器继续排空，受阻期间仍可追加队列。
- 双容器在测试驱动的文件替换边界注入 WinError 5，运行真实客户端/服务端。故障期间控制接口可读、待发送数据保留、没有重复 HTTP；解除后队列排空，服务端记录和图片保持唯一。
- Ruff、全套 Windows 单元、前端 lint/TypeScript/build、Windows ZIP 和冻结 EXE 启动通过；测试计数统一见 PLAN。

最终端到端证据在 `artifacts/timetrace-e2e-3117ee039b/`，包含结果、JUnit、JSON 日志、错误期间状态快照、指标、浏览器 trace 和截图。日志显示两次确认失败和随后恢复，测试容器/网络/卷已清理。

初次 E2E 用重启前的控制台缓存数量做精确断言而失败，现改为重启后读取的队列数量；控制台统计本身保留既有五秒缓存。真实 Windows 用例验证 OS 行为，Linux 容器验证故障下整个应用的生命周期。

Windows 文件共享规则参考 [Microsoft CreateFileW 文档](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)。修复交付与远端 CI 由工作分支 PR 记录；本记录不代表安装包已发行或安装。
