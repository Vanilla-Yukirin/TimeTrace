# 存储策略总览

> 返回 [Wiki 首页](../readme.md)

---

## 三层存储

| 层 | 技术 | 存储内容 | 特点 |
|----|------|---------|------|
| **结构化元数据** | SQLite（单文件） | records、screenshots 元信息、analysis_results、feedback、settings | Serverless / zero-config，事务，便于分发 |
| **文件系统** | 本地目录 | 截图原图（PNG）、缩略图（JPG） | 支持"删图不删记录"；I/O 与 SQLite 解耦 |
| **向量层** | Faiss / numpy / sqlite-vec | embedding 向量，支持相似检索 | Phase 1.5 引入；见 [向量检索](vector-search.md) |

---

## SQLite 配置要点

```python
# 启动时执行（database.py 中已配置）
PRAGMA journal_mode = WAL;      -- 读写并发更友好
PRAGMA synchronous = NORMAL;    -- 性能与安全平衡
```

**WAL 模式建议**：
- 适合"多读少写，小事务"场景 ✓
- 避免超大事务（会使 WAL 文件膨胀并减慢写入）
- 明确事务边界，减少长事务对读的阻塞

**`synchronous=NORMAL` 行为细节**：
- fsync 主要发生在 **checkpoint** 阶段，而非每次 commit
- 比 `FULL` 模式快，但存在极小的断电丢数据风险（checkpoint 前的未落盘数据）
- 对本地时间追踪应用可接受（最坏情况：丢失最近几条记录）

---

## 生命周期管理

| 策略 | 说明 |
|------|------|
| **只删图不删记录** | 图片超配额后删除原图，保留 records + analysis 元信息 + embedding 向量 |
| **缩略图优先** | UI 时间轴 hover 使用缩略图，避免频繁解码大图 |
| **配额提示** | 图片文件夹超过阈值时提示用户，不强制自动删除 |
| **删除标记** | `screenshots.deleted_at` 软删除，便于统计与恢复 |

---

## 并发写入建议

- Capture Service 与 Analysis Worker 可能同时写 SQLite
- WAL 模式已大幅缓解读写冲突
- 写操作使用**小事务**（单条 insert / update），不批量累积
- 若出现 `SQLITE_BUSY`：短退避重试（10–50ms 抖动，最多 3 次）
- **可选**：连接级 `busy_timeout`（`PRAGMA busy_timeout = N`）让 SQLite 在锁冲突时自动 sleep 累计到 N ms 后才返回错误，比应用层手动重试逻辑更简单；当前实现使用应用层退避，二者选其一即可

---

## 相关文档

- [数据库 Schema](schema.md)
- [文件系统布局](file-layout.md)
- [向量检索层](vector-search.md)
- [打包部署（数据目录位置）](../architecture/packaging.md)
