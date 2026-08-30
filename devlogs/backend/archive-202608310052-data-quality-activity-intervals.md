# 数据质量与活动区间正确性：从休眠根因到在线 lease

**日期：** 2026-08-31
**范围：** `devlogs/PLAN.md` 的 PR 1；不包含设备查询/多设备时间轴，也不查看生产截图。

## 结论

跨日长条的根因不在聚合层，而在采集端：Windows 休眠或事件循环长时间停顿后，旧代码用恢复后的墙钟关闭休眠前 record，把整段不可观测时间写成活动。服务端又无条件接受该结束时间；过去依赖 `db.init()` 扫描历史异常，只能在重启时事后遮盖问题。

本次把不变量改为：**record 只能覆盖客户端连续观测到的时间**。客户端、Outbox、服务端写边界和常驻维护共同执行该约束，历史修复则变成显式、可审计、备份优先的管理操作。

## 实现

### 客户端连续观测

- 1 秒 capture loop 同时记录 monotonic tick 与对应墙钟。
- 相邻 tick 超过 5 秒视为不可观测 gap：旧 record 结束于 gap 前最后 tick，清除窗口状态，恢复后新开 record。
- shutdown 同样使用最后可信观测时间，不使用任意的退出时刻。
- `BackendClient.close_record(..., ts_end_ms=...)` 显式携带边界；Outbox 在产生 close 条目时持久化该时间，之后重放不改写。

### 服务端写边界与在线 lease

- close 的候选区间必须满足 `0 <= ts_end - ts_start <= 5min`；异常请求保留已有可信边界，否则折叠为零时长，不猜测用户活动。
- ingest 新 record 时按客户端 `ts_start` 收口同设备旧 record，离线 Outbox 回放不会被服务端接收时间污染。
- 常驻 server 每分钟回收超过 5 分钟仍 open 的 activity lease；同设备存在可信后继时用其开始时间，否则折叠为点。
- `db.init()` 不再静默扫描或改写活动历史。

### 状态机与派生索引

- `vlm_done` 会清空旧 retry/error/lock 元数据。
- claim 时把历史开始证据写入 `started_at`，终态清理 `locked_at` 这个 live lease 后，审计页仍能计算真实 queue wait；repair 会先迁移旧锁时间再清锁。
- VLM 返回结构化 JSON 但所有描述字段为空时视为失败并重试，不再写成成功。
- 审计明确区分“无截图而正常 skip”和“有截图但描述为空”；显式 repair 只重排队后一类。
- summary 叙述保存采用 FTS delete+insert，强制重叙述不会累积重复索引行；显式 repair 可幂等重建全部 summary FTS。

## 历史修复入口

```bash
timetrace-server repair-data-quality
timetrace-server repair-data-quality --apply
```

默认仅输出聚合 dry-run。`--apply` 先用 SQLite online backup API 生成一致性快照，再在单事务中处理过期 open record、非法 closed span、成功态残留错误、有截图空 VLM 结果、空叙述与 summary FTS。命令不读取或删除截图，也不按模型文本猜测是否应删业务数据。

## 验证

- 覆盖休眠/停顿后恢复、普通切窗、显式 close 时间的 Outbox 保真、非法服务端边界、同设备 lease 回收与跨设备隔离。
- 覆盖空 VLM 输出重试、无图 skip 保留、成功态错误清理、空摘要修复、FTS 重写单行性、dry-run 不修复和 apply 先备份。
- 本地验证：全量 Python `622 passed, 5 skipped`；Ruff check/format 通过；Windows 0.1.0 安装包真实构建与校验通过；前端 lint/build 通过。合并前仍需 CI 与 PR review。

## 生产处理边界

代码合并并完成不可变镜像发布后，先在生产运行 dry-run，记录数量；再执行 `--apply` 并保留备份，随后重建/排空可重试派生任务并验证时间轴。疑似模型拒答、`error_final` 和原始截图不在本自动修复范围内。
