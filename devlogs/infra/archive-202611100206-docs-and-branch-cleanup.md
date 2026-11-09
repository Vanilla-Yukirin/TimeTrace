# 文档现状校准与已合并分支清理

- 时间：2026-11-10 02:06，Asia/Shanghai。
- 范围：用户要求核对上游、整理多余分支、更新现状文档并去除过时内容。
- 本次未改应用代码、未发布制品、未激活生产版本、未访问生产数据，也未回改旧 archive 文件。

## 上游与分支

`git fetch --prune origin` 后，主 checkout 的 `main` 与 `origin/main` 均为 `3ccaeb763635fd7588b6f16cda9b9a5856eea11f`，ahead/behind 为 0/0。只有一个 worktree，无初始未提交改动。`deploy` 与 `origin/deploy` 为 `81a10b6155cad84292a4fb6ba976e5cc90962e2e`，与主线之间仅差最后一笔文档归档。

删除以下远端分支前均用祖先关系确认已完整合入 `origin/main`，删除使用预期 SHA 检查与原子推送，防止并发新增提交被删：

| 分支 | 删除前 SHA |
|---|---|
| `codex/agent-activity-contract` | `74232b543328484552d5cfd668e97c4be30fe364` |
| `codex/private-gateway` | `678cb2e7fc173eeea71b8c70ebb3f1bc576c8d43` |

本地 `backup-before-reword-090221` 含两个不在主线祖先链上的旧提交，但 `git cherry origin/main backup-before-reword-090221` 两条均为 `-`，说明补丁已有等价实现。先创建并验证本地标签 `archive/backup-before-reword-090221` 指向原来的 `b931e86ac4b5ef6eaec026c19b9156421eaebf89`，再删除备份分支；标签未推送。

如需恢复原备份分支，可执行：

```bash
git branch backup-before-reword-090221 archive/backup-before-reword-090221
```

清理后重新 fetch 并用 `git ls-remote --heads origin` 验证，本地和远端均只保留 `main`、`deploy`。生产指针未推进。

## 文档校准

- `devlogs/PLAN.md` 收敛为验证基线、已完成主线、验收缺口和后续工程事项。移除已完成的设备查询/金字塔建设、旧发布等待状态和重复执行序列；保留全历史回填、另一台客户端及消费端迁移等未获完成证明的事项。
- README 增加 Windows Release 入口，明确标签版本早于当前主线的可能性；详细私人部署移到既有 `infra/architecture/web-deployment.md`，补齐更新器必需的兼容门禁安装步骤。
- Wiki 首页、路线图、摘要、打包与鉴权/隐私/MCP 页面同步当前 Tailscale 拓扑。修正“整个项目 Windows Only”“仅三个 CLI”“没有设备管理页”“没有 Release 自动化”等旧表述。
- `AGENTS.md` 继续只转引 `CLAUDE.md`，项目指令只改后者。澄清当前 PLAN/索引可更新，具体历史 archive 不回改。
- 旧 MCP 计划整理为演进记录；Agent、Classifier V2、报告计划明确设计/历史边界。移除根目录未被引用的 `sse-plan.md`：其路径与“只需考虑单进程”的前提已过时，实际时间轴仍使用受控轮询；未声称 SSE 实时通知已实现。旧稿可从本次整理前的 Git 历史读取。

## 验证与限制

- 整理前代码 SHA 的 [CI](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/39853898418) 为 success；[Windows v0.1.1](https://github.com/Vanilla-Yukirin/TimeTrace/releases/tag/v0.1.1) 确有 ZIP 和 SHA-256 附件。
- `git diff --check` 通过；修改后的文档本地链接、标题锚点和代码围栏检查通过。
- 源码、测试、工作流及部署脚本未修改，本次未重新运行应用测试。
- 生产状态只引用 [2026-10-23 验收](archive-202610230447-private-ts-pr24-deployment.md)，不将历史快照表述为今天在线；Windows Release 不代表已经安装到每台设备。
