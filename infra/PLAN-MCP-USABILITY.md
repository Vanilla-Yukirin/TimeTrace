# MCP 可用性修复记录

## **⚠️ 早期修复已落地，旧分页与发布方案已被更新**

本页原为 2026-06-23 的四项修复计划。对应实现已进入主线；旧 `feature/refactor-split` 分支、测试计数、工具默认值及部署等待状态不再作为执行依据。当前接口以 [MCP 契约](architecture/mcp-layer.md) 和 [server.py](../src/timetrace/server/mcp_layer/server.py) 为准，待办只维护在 [PLAN](../devlogs/PLAN.md)。

## 原问题与演进

| 原问题 | 已实现方向 | 10 月后的读取契约 |
|---|---|---|
| 单次 limit 不足以覆盖一天 | 扩大可读范围并提供分页 | 检查 `has_more`，逐页读取；不能把第一页末尾当最新活动 |
| 只能相对时间查询 | 增加显式起止时间窗 | 使用带时区的范围；分页固定窗口和入库高水位 |
| 同时间戳记录的游标不稳定 | 初版 record ID 游标 | 时间戳 + ID 复合排序，支持 ASC/DESC，兼容旧游标 |
| 多词查询被当作连续短语 | 调整 FTS 查询构造 | REST/MCP/Agent 统一文本检索，短查询仍有 LIKE 退路 |

“刚刚做什么”调用 `get_latest_activity`；时段概览调用 `get_activity_overview`；精确摘要下钻调用 `search_summaries`；逐条回放才使用 `get_recent_activity`。最新采集、客户端联系、入库和分析状态各有语义，均不能直接证明用户本人在线或离线。

## 后续实现与验收

- [Agent 活动读取契约](../devlogs/backend/archive-202610222147-agent-activity-contract.md)
- [摘要回退范围修复](../devlogs/backend/archive-202610222353-summary-fallback-boundaries.md)
- [PR #24 部署验收](../devlogs/infra/archive-202610230447-private-ts-pr24-deployment.md)

历史 embedding 切换、回填排期和跨 Agent 分工不再保留为本页待办；是否迁移模型、补历史摘要或调整外部消费端，按当前 PLAN 与实际环境决定。
