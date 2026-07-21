# 开发路线图

> 返回 [Wiki 首页](../readme.md) · 当前待办看 [滚动 PLAN](../../devlogs/PLAN.md)
> 最后校准：2026-07-20。本文只描述阶段边界，不复制具体 TODO。

---

## 已完成主线

| 阶段 | 状态 | 已验证交付 |
|---|---|---|
| Phase 1 采集与回放 | ✅ | Windows 活跃窗口/关键帧采集、idle/隐私黑名单、SQLite WAL + 图片树、时间轴、pHash/BK-tree 图像相似 |
| Phase 1.5 本地 AI | ✅ | VLM 结构化描述、flat-6 分类、FTS5、文本 embedding、`/v1/search/text` 混合召回、分析任务状态机与熔断 |
| Phase 2 反馈与审计 | 🟡 | per-app 规则覆盖、`apply_label`、decision trace/建议/阶段时间戳、`/audit` 已上线；OCR 模糊和反馈回灌仍未完成 |
| Phase 3 客户端/服务端拆分 | ✅ | common/client/server 三层、HTTP ingest、bearer auth、outbox、多 endpoint、SSH tunnel、并发上传、容器化第一刀 |
| Phase 4 AI 上下文 | ✅ | Web Agent、报告、MCP、`query_stats`、`search_summaries`、粗到细上下文查询 |
| Phase 5 分层记忆 | ✅ 主链完成 | `5min→1h→6h→day→week` 指标级联、source_hash 重发、LLM 叙述、后台 rollup/narrate、回填 CLI、金字塔页面 |
| Phase 6 运维可见性 | 🟡 基础完成 | 登录、审计/金字塔/LLM 日志页面、后端 + SPA 自动发布；LLM 账本尚未覆盖 report 与 Web Agent streaming |

---

## 当前稳定化阶段

开发策略从“继续扩功能”切换为“恢复可控性 + 补可靠性闭环”：

1. **控制面**：`main` 作为唯一开发主干，`deploy` 只做发布指针；README、infra 与滚动 PLAN 保持一个事实口径。
2. **传输可靠性**：真机验证 endpoint failover、outbox 排空、隧道恢复与回切。
3. **隐私和生命周期**：落盘前 OCR/模糊、截图配额驱逐、恢复与不可逆边界测试。
4. **数据演进**：正式 schema version、旧库升级与崩溃恢复。
5. **单卡容量**：让 worker/narrative/report/agent 的 FIFO 排队和故障状态可见。

完成这些 gate 之前，不启动 Postgres/Redis/S3、deep-scan 多 agent、TUI 等扩张性工作。

---

## 下一条产品实验：Classifier V2

Classifier V2 尚未进入实现。当前只有预备稿与 225 帧的多模态 embedding 校准：保守阈值 `T=0.13` 有望以高 precision 复用近邻分类、节省约 30% VLM 调用，但距离只在近端可靠，且要防止错误种子扩散。

正确路线是：仓库内可复现实验 → shadow mode → precision/节省率验收 → 小流量跳过 VLM → 可回滚上线。未经过 shadow 验证前，现有规则 + VLM V1 保持不动。详见 [PLAN-CLASSIFIER-V2](../PLAN-CLASSIFIER-V2.md)。

---

## 明确延后

- PostgresDatabase / RedisQueue / S3BlobStorage
- Headless TUI 与通用多租户部署
- deep scan / 子 agent map-reduce
- 通用安装包、ghcr 与 release 自动化
- 图像向量大规模索引替换：在 numpy cosine 有真实性能瓶颈前不提前迁移

这些方向不是取消，而是当前个人本地规模没有足够证据证明值得增加系统复杂度。

---

## 验收口径

- 所有阶段必须同时有：代码、测试、运行配置、可观测入口和回滚/降级说明。
- “代码存在”不等于“上线”；“线上跑过一次”不等于“可靠性闭环”。
- 性能指标必须来自真机测量，不沿用早期目标值冒充现状。
- 具体未完成项只在 [滚动 PLAN](../../devlogs/PLAN.md) 维护，避免本页再次变成第二份 TODO。
