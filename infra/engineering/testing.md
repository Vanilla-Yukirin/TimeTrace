# 测试与验收标准

> 返回 [Wiki 首页](../readme.md)

---

## 单元测试（当前）

测试文件位于 `tests/`，使用 `pytest` + `pytest-asyncio`：

| 文件 | 覆盖范围 |
|------|---------|
| `tests/test_storage.py` | Database 初始化、insert_record、mark_pending、claim_next_task、schema 迁移 |
| `tests/test_api.py` | FastAPI 路由、/healthz、/v1/records |
| `tests/test_privacy.py` | `should_capture()` 黑名单、暂停模式 |
| `tests/test_rules.py` | `decide_category()` 规则匹配、KNN 投票、decision_trace 格式 |
| `tests/test_phash_index.py` | pHash 计算、BK-tree 范围搜索 vs 暴力扫比对、PHashIndex 日桶过滤、`from_db` roundtrip、旧 DB 加 `phash` 列的幂等迁移 |
| `tests/test_search.py` | `/v1/search/by-image` 视觉 / 语义 / 融合通道、多图上限、半开时间窗口、LIKE 元字符转义、`/v1/apps` 与 `/v1/records` 的 apps/categories 多选 |

运行测试：
```bash
uv run pytest
# 或
uv run pytest -v --tb=short
```

---

## 功能验收标准

| 功能点 | 验收条件 |
|--------|---------|
| 时间轴回放 | 任意选择某天，可加载时间轴并定位到任意时间点 |
| 搜索过滤 | 按时间范围 / 应用 / 分类 / 关键词筛选返回正确结果集 |
| 以图搜图（视觉） | 上传参考图后返回 pHash 距离升序的相似截图；搜索结果可"跳转时间轴"并高亮定位 |
| 隐私模式 | 暂停后无新记录写入；黑名单应用的截图 0 条 |
| 数据恢复 | 进程强制终止后重启，pending 任务可被正确 reclaim；pHash 索引从 SQLite 亚秒级重建 |
| 导出 summary | 指定时间段触发摘要，返回 100–200 字文本 |

---

## 性能验收标准

| 指标 | 目标 | 测量方法 |
|------|------|---------|
| CPU 均值 | ≤ 5% | Windows 任务管理器 / 7 天平均值 |
| 内存常驻 | ≤ 300 MB | 任务管理器 / 工作集（Working Set） |
| P95 查询延迟 | ≤ 300ms | 1 天数据（≥10,000 events），多次重复查询取 P95 |
| 截图高频切窗 | 无明显卡顿 | 手动快速切换 10 个窗口，主观无感 |

---

## 稳定性验收标准

| 场景 | 验收条件 |
|------|---------|
| 7 天持续运行 | 无崩溃，CPU/内存未超标 |
| 断网运行 | Worker 退避重试后不影响 Capture 正常采集 |
| 限流 / 模型异常 | Worker 进入 `error_retryable`，不影响 API 响应 |
| 重启恢复 | 重启后 pending 任务继续，无数据丢失 |

---

## 准确率验收标准（Phase 2）

| 指标 | 目标 | 说明 |
|------|------|------|
| 分类 Top-1 准确率 | ≥ 80% | 以用户反馈标签为 ground truth |
| 误判可解释性 | 100% | 每条记录有完整 decision_trace |
| 误判可纠正性 | 100% | 用户修改后下次同类记录分类改善 |

---

## 本地运行命令

```bash
# 开发模式运行
uv run timetrace

# 运行测试
uv run pytest

# Lint 检查
uv run ruff check src/

# 格式化
uv run ruff format src/
```

---

## 相关文档

- [风险分析](risks.md)
- [开发路线图（验收节点）](../overview/roadmap.md)
- [执行摘要（验收标准原文）](../overview/summary.md)
