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

## 相似检索

拆成"视觉"与"语义"两条正交通道，刻意**不**使用图像 embedding（避免换模型导致整库失效）。

| 通道 | 方案 | 决策 | 理由 |
|------|------|------|------|
| **视觉** | pHash (64-bit DCT) + BK-tree（按天分桶） | ✅ Phase 1 已落地 | 模型无关、可复现；换 VLM 不需重算；汉明度量天然适配 BK-tree |
| **视觉** | Faiss IndexBinary / ANN | ❌ | Faiss 为欧式/余弦优化，汉明支持使用成本不划算；BK-tree 在本体量更轻 |
| **视觉** | 图像 embedding (e.g. qwen3-vl-embedding) | ❌ | 换模型即整库失效；2560 维存储代价高；放弃语义泛化换"可迁移" |
| **语义** | FTS5 多字段 BM25 over VLM 描述 | ✅ Phase 1.5 计划 | SQLite 原生，零新依赖；每列独立 IDF 可做字段加权 |
| **语义** | 文本 embedding (e.g. text-embedding-v3) + Faiss | ⏸ 留作后续评估 | 实验中 Hybrid 比纯 BM25 只提升 ~1%（见 `D:\Code\20260419测试阿里vlemb`）；先观察 BM25 不足再引入 |
| **融合** | Reciprocal Rank Fusion (k=60) | ✅ Phase 1 已落地 | 不依赖分数量纲，温和偏好多通道共识，不枪毙单通道强项 |

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
| `numpy` | pHash 的 DCT-II 矩阵运算（核心依赖） |
| `python-multipart` | FastAPI 接收参考图上传 |

---

## 相关文档

- [架构总览](../architecture/overview.md)
- [打包部署](../architecture/packaging.md)
- [相似检索层](../storage/vector-search.md)
