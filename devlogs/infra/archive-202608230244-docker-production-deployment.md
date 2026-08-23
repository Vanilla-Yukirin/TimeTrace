# TimeTrace 生产后端 Docker 化与 yukirin-server 切换

**日期：** 2026-08-23  
**分支：** `codex/docker-deployment`  
**PR：** #4（记录本文时仍 open，未合并）  
**最终生产 SHA：** `cf80394086def009109ab3f76cf255a713c3d5bc`

## 目标与边界

本次只容器化 `timetrace-server`。GPU、LM Studio、nginx 与 Cloudflare Tunnel 继续由宿主机管理；旧源码仓库 `~/Github/TimeTrace`、现有数据库、截图和 token 目录不迁移、不改名。生产 Compose 使用 host network，让容器里的 `127.0.0.1:1234` 仍能访问宿主机 LM Studio。

## 落地结果

- `deploy/Dockerfile`：Python 3.12 + uv 多阶段构建，项目非 editable 安装；运行阶段 UID/GID 1000、只读根文件系统、无额外 capability、stdlib `/healthz` HEALTHCHECK。
- `deploy/docker-compose.yml`：`network_mode: host`；原位挂载 `/home/vanilla/TimeTraceData` 与 `/home/vanilla/.config/timetrace-server`；配置独立到 `/srv/timetrace/config/timetrace.env`。
- `deploy/deploy-container.sh`：先 pull/校验镜像，再做切换。首次切换停止旧 systemd 后复制 SQLite、WAL、SHM 快照；新容器失败则恢复旧 systemd。后续容器版本失败则恢复上一个 Compose release。
- `.github/workflows/deploy.yml`：GitHub runner 用 `GITHUB_TOKEN` 构建并发布私有 GHCR SHA 镜像；通过 FRP SSH 上传不可变 Compose release；registry 凭据只写远端 `/tmp` 独立 `DOCKER_CONFIG`，完成后删除，不污染多功能服务器的全局 Docker 登录。
- 前端继续使用同一 SHA 构建，发布到 `/srv/timetrace/web/releases/<sha>` 并原子切换 nginx `current`。

## 数据与首次切换

- 现有数据目录与 token 目录均保持原位 bind mount，没有移动 13GB 数据树。
- 旧仓库 `.env` 首次一次性复制到 `/srv/timetrace/config/timetrace.env`，模式 `0600`、owner `1000:1000`；上线后逐字节比较与旧文件一致。
- 首次 stopped-service 快照位于 `/home/vanilla/TimeTraceData/db/pre-docker-d7b36a84c3eb662772de57bd62a4fbc8efcbaacf/`。
- 旧 `timetrace-server.service` 保留但已 `inactive + disabled`，可用于自动回滚。旧 uv launcher 在正常 SIGTERM 后返回 143，曾造成 systemd 假 `failed`；最终脚本在成功切换后执行 `reset-failed`，不再留下告警态。

## 验证证据

- 本地：`uv run pytest --tb=short` 542 passed；Ruff check/format、前端 lint/build、YAML 解析、22 个 Actions shell block 与部署脚本 Bash 语法均通过。
- PR CI run `32591439774`：`lint-and-test`、`frontend`、`docker-image` 三 job 全绿；Docker job 在 Ubuntu runner 真正启动 hardened container 并通过 `/healthz`。
- 最终生产 run `32591445065`：`resolve-ref`、`build-image`、`deploy`、`publish-frontend` 四 job 全绿。
- 宿主机独立验收：容器镜像为最终 SHA，`running + healthy`，user `1000:1000`，read-only rootfs，host network；`127.0.0.1:8765/healthz` 与 nginx `8080/healthz` 均 200；nginx 首页包含 `id="root"`；容器访问 LM Studio `/v1/models` 返回 200；runtime 与 web 两个 `current` 均指向最终 SHA；临时 GHCR 凭据目录为 0。
- 先前生成但未投入使用的 `~/.ssh/timetrace_github_deploy_ed25519{,.pub}` 未加入 `authorized_keys`/GitHub，Docker 链路稳定后已删除，不可恢复且不影响任何现有访问。

## 过程中发现的问题

1. 首次 `workflow_dispatch` 在解析阶段失败：job 级 `env` 不能引用 `runner.temp`。改用 `/tmp/timetrace-ssh-${github.run_id}-${github.run_attempt}-%C` 后解决；失败发生在 SSH 之前，生产未被触碰。
2. Windows CI 一次命中既有带宽限速计时 flaky（累计 sleep 1.857s，断言要求 2.0s）；相同代码本地和前一次 CI 均 542 passed，失败 job 重跑通过，本次不顺手修改业务测试。
3. GitHub runner 对若干 action 给出 Node 20 deprecated warning，但所有 job 正常完成；后续单独升级 action major/version，不与首次容器化切换混做。

## 明确未完成

Cloudflare Tunnel 仍直连 `127.0.0.1:8765`，因此公网 `/healthz` 是 200，但公网 `/` 仍是 FastAPI 404。宿主机 nginx 上的真实 SPA 已就绪；将 Tunnel origin 改为 `127.0.0.1:8080` 是下一项独立公网入口变更，不属于本次“Docker 完毕并在宿主机成功部署”的完成条件。
