# 文件系统布局

> 返回 [存储总览](overview.md) | [Wiki 首页](../readme.md)

---

## TimeTraceData 目录结构

默认路径：`%USERPROFILE%\TimeTraceData\`（即 `Path.home() / "TimeTraceData"`）

```
TimeTraceData/
├── db/
│   └── timetrace.db               ← SQLite 主数据库（单文件）
│
├── screenshots/
│   └── YYYY/
│       └── MM/
│           └── DD/
│               └── <YYYYMMDD_HHMMSS>_<record_id>.png   ← 截图原图
│
├── thumbs/
│   └── YYYY/
│       └── MM/
│           └── DD/
│               └── <YYYYMMDD_HHMMSS>_<record_id>.jpg   ← 缩略图（用于时间轴 hover）
│
└── logs/
    └── timetrace-YYYYMMDD.jsonl    ← 结构化日志（structlog 输出）
```

---

## 设计说明

| 目录 | 说明 |
|------|------|
| `db/` | SQLite 单文件，便于整体备份（复制一个文件即可） |
| `screenshots/YYYY/MM/DD/` | 按日期分桶，避免单目录文件过多（OS 性能问题） |
| `thumbs/YYYY/MM/DD/` | 缩略图独立目录，方便批量清理（比原图小 10–20×） |
| `logs/` | 按天滚动，保留最近 N 天（可配置） |

---

## 文件命名规则

文件名格式为 `{YYYYMMDD_HHMMSS}_{record_id}`，时间戳取自截图实际拍摄时刻：

```
screenshots/2026/04/10/20260410_143022_550e8400-e29b-41d4-a716-446655440000.png
thumbs/2026/04/10/20260410_143022_550e8400-e29b-41d4-a716-446655440000.jpg
```

时间戳前缀固定 15 位，保证文件名按字典序排列即为时间顺序；后缀 UUID 保证唯一性。

SQLite 中 `screenshots.path` 与 `screenshots.thumb_path` 存储相对路径（相对 `TimeTraceData/`）。

---

## 存储配额策略

1. **配置项**（`settings` 表）：
   - `storage.max_image_days`：图片保留天数（默认 30 天）
   - `storage.max_image_gb`：图片最大占用（默认 10 GB）

2. **清理规则**：超配额时优先删除最旧的图片（软删除 `screenshots.deleted_at`），保留记录元数据与向量。

3. **提示机制**：接近配额时在托盘图标或 UI 设置页显示警告，不强制自动删除。

---

## 配置来源

`src/timetrace/config.py` — `StorageConfig` 数据类：

```python
@dataclass
class StorageConfig:
    data_dir: Path = Path.home() / "TimeTraceData"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "timetrace.db"

    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "screenshots"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"
```

---

## 相关文档

- [存储策略总览](overview.md)
- [数据库 Schema](schema.md)
- [打包部署（数据目录隔离）](../architecture/packaging.md)
