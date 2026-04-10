# 开发路线图

> 返回 [Wiki 首页](../readme.md)

---

## Phase 1 — MVP：记住并回放

**目标**：证明稳定采集、存储、回放、检索的可行性，无外部模型依赖。

### 必做功能

| 模块 | 具体内容 |
|------|---------|
| **Capture** | 活跃窗口切换事件 + 关键帧截图（带节流/补帧）；min/max 间隔；idle 段判定 |
| **Storage** | SQLite WAL 模式落库；图片文件系统（原图 + 缩略图）；配额删除（只删图不删记录） |
| **API** | records 查询、时间轴聚合、应用过滤、关键词匹配 |
| **UI** | 日历 + TimelineCanvas（基础版：单日、少轨道）+ 详情表 |
| **Privacy** | 托盘一键暂停；应用/标题黑名单；隐私模式（不存图） |

### 验收标准

- 7×24h 连续运行 7 天无崩溃
- CPU ≤ 5%，内存 ≤ 300 MB
- P95 查询 ≤ 300ms（1 天数据）
- 落库成功率 ≥ 99.9%

---

## Phase 1.5 — 轻智能：能检索与能摘要

**目标**：引入 VLM + Embedding，支持语义检索与自动摘要，上线 MCP 最小工具集。

### 功能增量

| 功能 | 说明 |
|------|------|
| **VLM 描述** | 每帧生成 20–50 字"画面任务描述"（不做 OCR 复刻） |
| **Embedding** | 对描述文本做向量嵌入（优先 numpy 暴力，再迁 Faiss） |
| **相似检索** | 文本查询 → top-k 相似帧，Search 页跳转定位时间轴 |
| **摘要** | 手动触发时间段摘要（100–200 字）+ 分类统计（系统算，不让模型瞎编） |
| **MCP** | 上线最小工具集：`list_categories` / `get_activity` / `search_activity` / `get_category_stats` |

### Analysis Worker 状态机（Phase 1.5）

```
captured → pending_vlm → vlm_done → pending_embed → embed_done → done
任意阶段失败 → error_retryable（带 retry_count/next_retry_at）或 error_final
```

---

## Phase 2 — 闭环：更准的分类与可迁移的上下文层

**目标**：规则 + 模型 + KNN 融合，用户反馈作为强样本，隐私增强，长期稳定性。

### 功能增量

| 功能 | 说明 |
|------|------|
| **规则/反馈引擎** | KNN 投票、强弱样本权重、decision_trace 完整化 |
| **浏览器细分** | URL 域名 + 画面描述融合策略（浏览器是"多用途应用"） |
| **用户反馈闭环** | 确认/修改→强样本→影响后续 KNN 分类 |
| **隐私增强** | OCR/区域检测后马赛克；或先全局模糊再送 VLM |
| **稳定性** | 长测、崩溃恢复验证、SQLite schema 迁移 |

### 可选后续功能

- 多设备合并（device_id + last_modified）
- 端侧轻量隐私检测
- 浏览器 URL 提取增强
- 增量 Faiss 索引维护
- 导出到 Markdown / 日记系统

---

## 技术债务清单

| 债务 | 说明 |
|------|------|
| 时间轴组件性能 | TimelineCanvas 大量帧时的渲染优化 |
| Schema 版本管理 | SQLite 迁移策略（Alembic 或手写 migration） |
| Worker 并发限流 | 多 worker 并发上限与 429 退避策略 |
| 向量存储抽象 | 统一 Faiss / sqlite-vec / file 后端接口 |

---

## 相关文档

- [执行摘要](summary.md)
- [架构总览](../architecture/overview.md)
- [分析 Worker](../architecture/analysis-worker.md)
