# TimeTrace DevLog 索引

**最后更新：** 2026-05-17

开发过程归档，按模块分类存放。每个子文件夹对应一个关注域，文件按时间戳命名。

---

## **⚠️ 内容时效性声明**

**本目录下所有 devlog 是「写入即不再修改的历史快照」**，记录某次决策 / 排查 / 改造在**当时**的认识。后续代码 / 文档可能与这里描述不一致 —— 这是预期行为，不是 bug。

**遇到冲突时优先级**：实际代码 > [infra/](../infra/) 当前架构文档 > 本目录最新 devlog > 本目录早期 devlog。

(用 `grep '^##' devlogs/README.md` 能定位到此声明。)

---

## 目录结构

```
devlogs/
├── README.md          ← 本索引文件
├── backend/           ← 后端：asyncio、capture、worker、storage、API
├── frontend/          ← 前端：React 组件、路由、状态管理
├── infra/             ← 基础设施：进程管理、打包、部署、CI
└── research/          ← 调研报告、外部 AI 分析、技术方案对比
```

---

## backend/

| 文件 | 摘要 |
|------|------|
| [archive-202604182350-sigint-graceful-shutdown.md](backend/archive-202604182350-sigint-graceful-shutdown.md) | 排查 Ctrl+C 与托盘退出挂死；根因为 `except*` 不捕获裸异常 + uvicorn 二次抛 SIGINT；修复：`signal.signal(SIGINT)` 统一路由到 `quit_event` 优雅关闭路径 |
| [archive-202604300316-vlm-worker-circuit-breaker.md](backend/archive-202604300316-vlm-worker-circuit-breaker.md) | 实装 VLM 三段式描述（OpenAI 协议 + opt-in extra_body）、worker 升级为 N 并发消费者带退避重试与跨 worker 熔断；顺带修 SQLite commit race（全局 lock）+ 原子化 claim + records.status 镜像 |
| [archive-202605040109-record-duration-heal.md](backend/archive-202605040109-record-duration-heal.md) | 排查 12h+ 异常 record；限制 orphan 桥接并启动清理历史长段；验证 `>1h` 记录归零且全测通过 |
| [archive-202605040142-vlm-prompt-noun-phrase.md](backend/archive-202605040142-vlm-prompt-noun-phrase.md) | 修 VLM 输出"该截图…"等元叙述稀释 IDF：黑名单失败后改语法层约束（名词性短语开头 + 正反例 few-shot）；顺手对齐 infra analysis-worker / vector-search 上轮 VLM 实装未跟上的过期陈述 |

---

## frontend/

| 文件 | 摘要 |
|------|------|
| [archive-202604300228-image-lightbox.md](frontend/archive-202604300228-image-lightbox.md) | 基于 Radix Dialog 实装截图放大 Lightbox：弹层放大 + 左右键切换 + 底部信息条 + 动画；处理索引越界、按钮嵌套语义、margin 覆盖等 review 反馈，并同步 `infra/architecture/web-ui.md` |

---

## infra/

| 文件 | 摘要 |
|------|------|
| [archive-202605151200-client-server-split-kickoff.md](infra/archive-202605151200-client-server-split-kickoff.md) | 客户端 / 服务端架构分离重构的"宪法"：决策汇总、目标架构、wire 协议、鉴权 / outbox / 隐私管线设计、P0–P8 分阶段路线图。所有后续重构 PR 应能追溯到其中某个阶段 |
| [archive-202605161000-deployment-architecture.md](infra/archive-202605161000-deployment-architecture.md) | 部署架构决策 v1：目标 = 家里小主机（NAT），云服务器只做 FRP SSH 跳板；选择理由（带宽 / 流量 / 所有权）、Auth 双层（浏览器密码 + 设备 token）、Web UI 永不公网（看页面走 ssh -L）、Fork 安全策略 |
| [archive-202605161015-cicd-workflow.md](infra/archive-202605161015-cicd-workflow.md) | CI/CD 工作流设计 v1：仅 workflow_dispatch 手动触发、Secrets 清单与 fork 安全 repo guard、deploy.sh / systemd unit 职责、失败 rollback 与演进路线 |
| [archive-202605171500-p3b3-cli-design.md](infra/archive-202605171500-p3b3-cli-design.md) | P3b-3 CLI 工程化：init/admin 子命令拆分 + dispatch、ClientConfig 吃 storage/capture/privacy 三段 + 9 个 TIMETRACE_* env 覆盖、ServerAuth token 公开 I/O API 防 schema drift |
| [archive-202605171501-outbox-compaction-and-review-fixes.md](infra/archive-202605171501-outbox-compaction-and-review-fixes.md) | Outbox compaction crash-safe 顺序（state 先于 log，最坏 at-least-once replay 而非 silent loss）+ capture 抽 _safe_close_record helper + ctypes 两段式 buffer + bootstrap 类型收窄 |
| [archive-202605171502-packaging-and-container.md](infra/archive-202605171502-packaging-and-container.md) | P5 第一刀：pyproject sys_platform 标记让 Linux 自动跳过 Windows-only 依赖、Dockerfile 多阶段非 root + /data + /tokens 双 mount + symlink 让 ServerAuth 零 docker-aware、docker-compose loopback only |

---

## research/

_暂无归档_
