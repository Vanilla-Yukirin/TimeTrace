# 风险分析与缓解措施

> 返回 [Wiki 首页](../readme.md)

---

## 风险矩阵

| 风险 | 影响 | 可能性 | 缓解措施 |
|------|------|--------|---------|
| 隐私泄露 | 高 | 中 | 本地优先；外部 API 不返原图；黑名单前置；日志脱敏 |
| 性能过高（CPU/内存） | 高 | 中 | min/max 间隔；缩略图优先；采集与分析分离；WAL + 小事务 |
| 分类误判 | 中 | 高 | 规则层兜底；用户反馈强样本；规则层优先 + VLM 选类加权投票（decide_category）；decision_trace 可回溯 |
| 存储膨胀 | 中 | 高 | 配额策略：删图不删记录；保留描述与向量；UI 提示 |
| 前端复杂度 | 低 | 高 | 渐进式 UI（历史规划：Phase 1 单日视图，统计推后）；现状：时间轴 / 搜索 / 统计报表 / Agent 问答 / 登录设置均已上线 |
| Worker 崩溃卡死 | 中 | 低 | locked_at 超时回收；TaskGroup 异常捕获；日志记录 |

---

## 隐私风险

**场景**：截图包含密码、银行账户、个人通讯等敏感信息。

**缓解**：
1. **黑名单优先**（已实装）：应用/进程/标题关键词 → 不采集（`client/capture/privacy.py:12-20` `should_capture()`）
2. **暂停模式**（已实装）：托盘一键暂停，不留任何记录（`should_capture()` paused 短路）
3. **不存图模式**（已实装）：只记录元数据，不保存截图（`PrivacyConfig.store_images`，消费点 `client/capture/service.py:229`）
4. **外部接口不返原图**（已实装）：MCP/API 响应仅含描述/统计（`server/mcp_layer/server.py`，工具返回元数据/聚合/文本，不回传原图字节）
5. **区域模糊**（Phase 2）：OCR 检测后对敏感区域打码

---

## 性能风险

**场景**：截图频率过高或 Worker 并发过多导致 CPU 持续高占用。

**缓解**：
1. `min_capture_interval_s = 2–3s` 防止高频切窗爆炸
2. `max_capture_interval_s = 20–60s` 限制补帧频率
3. 采集层（Capture）与分析层（Worker）异步解耦
4. Worker 以 `vlm_concurrency` 个消费协程限制最大并发
5. 优先加载缩略图，避免 UI 频繁解码大图
6. SQLite WAL 模式 + 小事务，减少写锁争用

---

## 分类误判风险

**场景**：浏览器、IDE 等"多用途应用"被错误分类。

**缓解**：
1. **规则层兜底**：人工编写的确定性规则优先级最高
2. **用户反馈强样本**：`user_edit` 权重 5.0，一次纠错影响后续分类
3. **decision_trace 可回溯**：`decide_category` 内部已构造 decision_trace 对象（含 candidates/signals），但当前**未持久化**到 `analysis_results.decision_trace` 列（worker 拿到 trace 后即丢弃，该列恒为 NULL）

## **⚠️ §4 置信度阈值未实装**

下方"置信度阈值"为未落地的设想：当前 `confidence` 不持久化（`decide_category` 返回值被 worker 丢弃，`analysis_results.confidence` 恒为 NULL），前端 `CategoryBadge.tsx` 也没有 0.5 阈值的"待确认"态（只有"未分析"与"展示百分比"两态）。原文保留备查。

4. **置信度阈值**：confidence < 0.5 时 UI 展示"待确认"状态

---

## 存储膨胀风险

**场景**：长时间运行后截图占用数十 GB 磁盘空间。

**缓解**：
1. 配额策略：超限时删图（软删除），保留元数据 + pHash 指纹（视觉相似检索仍可用）
2. 删图前提示用户："图片缺失将影响后续 VLM 重新分析"
3. 缩略图比原图小 10–20×，优先用于 UI 渲染
4. 日志按天滚动，保留最近 N 天

---

## 崩溃恢复

**场景**：进程崩溃后 Worker 任务处于 `processing_*` 状态，无法被重新 claim。

**缓解**：
1. `locked_at` 字段 + 超时检测：定期扫描超过阈值（如 5 分钟）的 processing 任务，重置为 `pending_*`
2. TaskGroup 异常捕获：单个任务失败不影响其他任务
3. SQLite 事务保证：崩溃时事务回滚，不会产生半写记录

---

## 相关文档

- [隐私策略](../privacy/strategy.md)
- [采集参数推荐值](capture-params.md)
- [Analysis Worker（状态机与重试）](../architecture/analysis-worker.md)
- [测试与验收标准](testing.md)
