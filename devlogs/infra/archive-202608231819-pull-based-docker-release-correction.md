# TimeTrace pull-based Docker 发布链路纠正记录

**日期：** 2026-08-23

**性质：** 对早期 Docker 首次切换快照的追加纠正；不改写旧归档

## **⚠️ 最终发布模型已不再使用 CI 入站部署**

本文纠正
[archive-202608230244-docker-production-deployment.md](archive-202608230244-docker-production-deployment.md)
中关于最终 GitHub Actions 拓扑和凭据边界的描述。旧文记录的是首次容器切换时的阶段性实现与验收，
其中“Actions 通过 SSH 上传并激活 release”“远端临时 `DOCKER_CONFIG`”以及四个生产 job 的说法，
不再代表 PR #4 最终代码。

最终长期模型如下：

```text
push deploy / workflow_dispatch
  -> GitHub Actions 构建 server + SPA + 部署资产
  -> 发布不可变 GHCR <git-sha> 镜像
  -> 不连接部署机

部署机手动执行 timetrace-update [<git-sha>]
  -> 通过出站 HTTPS 解析并拉取镜像
  -> 提取 Compose、激活脚本与 SPA
  -> 健康检查后原子切换 runtime/web current
  -> 失败或收到 HUP/INT/TERM 时恢复完整旧状态
```

## 最终实现约束

- `main` 只运行常规 CI；PR 额外构建临时容器做冒烟，不发布生产镜像。
- `deploy` 是生产制品指针；发布工作流只有 `resolve-ref` 与 `build-image` 两个 job。
- 工作流发布前检查 SHA tag 尚不存在；标签已存在或 registry 状态无法确认时失败关闭，避免覆盖不可变制品。
- 部署激活由宿主机发起，不要求 GitHub runner、SSH 隧道或生产机私有源码 clone 可用。
- `timetrace-update` 使用宿主机独占锁串行化 release 提取、Compose 切换与回滚，避免并发部署互相覆盖状态。
- 镜像同时携带后端、SPA、Compose 资产、更新器和公开的 TimeTrace skill 文档；容器冒烟覆盖 `/healthz` 与 `/skill`。
- 首次迁移可以没有旧 `.env`；SQLite 快照在同文件系统临时目录完成后原子发布。
- 容器 SIGTERM 进入服务统一 `quit_event`；部署脚本在终止信号下恢复旧容器或旧 systemd、runtime 链接、SPA 链接及 unit 启用状态。

## 验证口径

最终 PR 验证应以最新 HEAD 的三项检查为准：

- Windows `lint-and-test`
- Linux `frontend`
- Linux `docker-image`（真实构建并启动 hardened container）

首次切换归档中的生产运行证据仍可作为当时状态的历史记录，但不再用于说明后续发布如何触发或凭据如何传递。

## 后续操作

PR 合并后，生产发布顺序是先把已审核的 `main` fast-forward 到 `deploy`，等待 GHCR SHA 镜像发布成功，
再由部署机执行 `timetrace-update`。Windows 客户端的脚本化更新、安装包与自动更新仍作为独立 TODO，
不与本次服务端容器发布混合。
