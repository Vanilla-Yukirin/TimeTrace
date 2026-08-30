# 数据质量 PR review 补充：采样时间保真

**日期：** 2026-08-31
**关联：** PR #11，承接 `archive-202608310052-data-quality-activity-intervals.md`

## 问题

capture 在一次 tick 开始时采样墙钟，但旧实现直到 `submit_record()` 内部才生成 `ts_start`。如果持久化、HTTP 或事件循环本身停顿超过 gap 阈值，下一次 tick 会用上次观测墙钟关闭该 record，而 record 的开始时间却落在慢调用完成之后，最终触发负区间并被服务端归零。

## 修复

- `BackendClient.submit_record(..., ts_start_ms=...)` 显式接收 tick 的观测时间。
- `InProcessBackend`、`HttpBackend` 和 `OutboxBackend` 都原样保留该时间；未显式传入时仍兼容旧调用方，使用当前时间。
- capture 的 switch 与 heartbeat 路径均把同一次 tick 的 `wall_ms` 作为 record 开始边界。
- 新增慢提交回归测试，并分别验证单进程、HTTP、Outbox 三条路径不会改写采样时间。

## Review 中同时修复的问题

首轮 review 发现显式数据修复可能把只有人工分类、从未进入 VLM 的记录重新排队。修复逻辑与 dry-run 审计现在都会排除这类权威人工标签，避免后续模型覆盖人工决定。

## 验证

- 聚焦链路：`55 passed`
- 全量 Python：`626 passed, 5 skipped`
- Ruff check 与 format check：通过
- `git diff --check`：通过
