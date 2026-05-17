# 打包与部署

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## **⚠️ 本页描述 v1 单进程架构，与 P3c/P5 第一刀之后的现状不一致**

主要变更：
- `[project.scripts]` 三入口：`timetrace`（单进程）/ `timetrace-client` / `timetrace-server`
- Windows-only 依赖（pywin32 / mss / pynput / pystray）加 `; sys_platform == 'win32'` 标记，Linux `uv sync` 自动跳过
- 新增 `Dockerfile` 多阶段非 root + `docker-compose.yml`（loopback only bind）+ `.dockerignore`
- `[project.optional-dependencies]` 桶：`client-priv` / `server-pg` / `server-redis` / `server-s3` / `headless` / `all-extras`（占位待 P5/P6 接入）
- 部署三件套：[`deploy/deploy.sh`](../../deploy/deploy.sh)、[`deploy/timetrace-server.service`](../../deploy/timetrace-server.service)、[`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml)（workflow_dispatch + fork-safe repo guard）
- 部署目标：家里小主机 `~/Github/TimeTrace/`，通过云 FRP 反向隧道单跳 SSH

**当前事实**：
- 代码：[`pyproject.toml`](../../pyproject.toml)、[`Dockerfile`](../../Dockerfile)、[`docker-compose.yml`](../../docker-compose.yml)、[`deploy/`](../../deploy/)、[`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml)
- 设计：[`archive-202605161000-deployment-architecture.md`](../../devlogs/infra/archive-202605161000-deployment-architecture.md)、[`archive-202605161015-cicd-workflow.md`](../../devlogs/infra/archive-202605161015-cicd-workflow.md)、[`archive-202605171502-packaging-and-container.md`](../../devlogs/infra/archive-202605171502-packaging-and-container.md)

整页重写计划在 P5 完整（PostgresDatabase / RedisQueue / S3BlobStorage 真接入）+ ghcr 镜像推送上线后进行。

---

## 目标

- **本地可分发**：一键安装/运行、托盘图标入口、可选开机自启
- **最少依赖**：用户不需要安装 Python / Node
- **升级策略**：配置与数据迁移（schema version）不丢失

---

## 推荐方案：PyInstaller

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| **one-folder（默认）** | 可执行文件 + 资源目录 | 开发 / 测试 / 调试（推荐） |
| **one-file（可选）** | 单个 `.exe` | 分发给普通用户（启动更慢） |

**one-file 注意事项**：
- 启动时解压到临时目录，首次启动较慢
- 调试不便；需要处理数据目录与权限（`sys._MEIPASS`）

---

## 打包入口

```python
# src/timetrace/main.py
if __name__ == "__main__":
    start_capture()
    start_worker()
    start_api()
    start_tray()   # Phase 1 新增：pystray 托盘
```

---

## 替代方案对比

| 方案 | 优点 | 风险 / 代价 | 适用阶段 |
|------|------|-----------|---------|
| **PyInstaller** | Python 分发成熟；支持 one-folder/one-file | one-file 启动慢；需处理数据目录与权限 | **当前推荐** |
| **Electron** | Web 技术栈统一（Python + Electron 可混用） | Chromium 体积与内存资源成本高 | 不推荐 |
| **Tauri** | 体积与资源更优；Rust 后端 + 任意前端 | 工程门槛更高；对 MVP 阶段不必要 | Phase 2 可选 |

---

## 目录结构（one-folder 输出示例）

```
dist/timetrace/
├── timetrace.exe          ← 主入口
├── _internal/             ← 依赖库（PyInstaller 生成）
└── (用户数据在 %USERPROFILE%/TimeTraceData/)
```

用户数据目录**不打包进 dist**，存放在 `%USERPROFILE%/TimeTraceData/`（见 [文件布局](../storage/file-layout.md)）。

---

## 数据迁移策略

- `settings` 表中存储 `schema_version` key
- 启动时检查版本，按需执行 migration SQL
- 升级前备份 `timetrace.db`（可自动或提示用户）

---

## 相关文档

- [存储文件布局](../storage/file-layout.md)
- [工程化技术选型对比](../engineering/tech-stack.md)
