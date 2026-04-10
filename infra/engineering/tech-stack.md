# 技术选型对比

> 返回 [Wiki 首页](../readme.md)

---

## 数据库

| 选项 | 部署形态 | 优点 | 风险 / 代价 | 决策 |
|------|---------|------|-----------|------|
| **SQLite** | 单文件、serverless | 免部署、事务、便于分发；支持 WAL 并发 | 并发写受限，需控制写事务粒度 | ✅ **选用** |
| DuckDB | 单文件（分析强） | 聚合分析舒适 | 事件写入/状态机场景不适合作为主库 | ❌ |
| Postgres | 服务化 | 并发强、生态成熟 | 违背"免部署/单机分发"目标 | ❌ |

---

## 前端框架

| 选项 | 优点 | 风险 / 代价 | 决策 |
|------|------|-----------|------|
| **React + Vite** | 组件化 UI + 快速构建；适合本地面板 | 需自研 TimelineCanvas | ✅ **选用** |
| Next.js | 路由与 SSR 能力强 | 对本地单机面板偏重，SSR 价值不高 | ❌ |
| Vue | 学习成本低、生态成熟 | 与既定 React 技能栈不一致 | ❌ |

---

## 打包方案

| 选项 | 优点 | 风险 / 代价 | 决策 |
|------|------|-----------|------|
| **PyInstaller** | Python 分发成熟；one-folder/one-file | one-file 启动慢；需处理数据目录权限 | ✅ **选用** |
| Electron | Web 技术栈统一 | Chromium 体积与内存资源成本高 | ❌ |
| Tauri | 体积与资源优；Rust 后端 | 工程门槛高；MVP 阶段不必要 | Phase 2 可选 |

---

## 后端框架

| 选项 | 决策 | 理由 |
|------|------|------|
| **FastAPI + Uvicorn** | ✅ **选用** | ASGI 异步；自动 OpenAPI 文档；Python 生态一致 |

---

## 向量检索

| 选项 | 决策 | 理由 |
|------|------|------|
| **numpy 暴力** | Phase 1.5 原型 | 零依赖，快速验证 |
| **Faiss** | Phase 1.5–2 主推 | 高效、成熟、支持大规模 |
| **sqlite-vec** | Phase 2 可选 | 向量与元数据同库，但 pre-v1 有风险 |

---

## Python 工具链

| 工具 | 用途 |
|------|------|
| `uv` | 包管理（现代、快速） |
| `ruff` | Lint + 格式化（替代 flake8 + black） |
| `pytest` + `pytest-asyncio` | 测试框架 |
| `hatchling` | 构建后端 |
| `structlog` | 结构化日志（JSON Lines） |
| `pydantic` | 数据验证与序列化 |
| `aiosqlite` | 异步 SQLite 访问 |

---

## 相关文档

- [架构总览](../architecture/overview.md)
- [打包部署](../architecture/packaging.md)
- [向量检索](../storage/vector-search.md)
