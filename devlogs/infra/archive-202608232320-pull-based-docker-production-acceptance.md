# Pull-based Docker 首次正式发布验收

**时间：** 2026-08-23 23:20（Asia/Shanghai）

**范围：** PR #4 合并、不可变制品发布、部署机主动激活与线上验收

**发布提交：** `e8b2caea8c670fb8309e1094ceb17bceed81c79c`

## 结果

PR #4 已合并，`main` 通过常规 CI 后只把已审核提交 fast-forward 到 `deploy`。GitHub Actions 仅构建并发布同一 SHA 的 server + SPA 镜像，不连接部署机；部署机随后执行已安装的 `timetrace-update`，主动拉取并激活该镜像。首次正式 pull-based 发布成功，没有触发回滚。

## 制品证据

- main 合并 CI run `32647617993` 成功；`lint-and-test` 与 `frontend` 通过，`docker-image` 在 main push 上按设计跳过，未重复构建临时容器。
- `origin/main` 与 `origin/deploy` 最终均指向 `e8b2cae`，推进过程是祖先检查通过后的 fast-forward，没有强推或历史改写。
- deploy run [`32647814950`](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/32647814950) 成功；`resolve-ref` 固定触发提交，`build-image` 通过不可变标签检查后发布 GHCR SHA 镜像。

## 部署机验收

`timetrace-update` 从 `deploy` 解析到 `e8b2cae`，拉取镜像、提取 Compose/回滚脚本/更新器/SPA，并完成容器与 Web release 的原子切换。独立复核结果：

- 运行镜像精确匹配 `e8b2cae`；容器为 running + healthy、非 root 用户、host network、只读根文件系统。
- runtime 与 web 的 `current` 均指向同一 SHA；release 同时包含普通 Compose、rollback override、部署脚本、更新器与 SPA，已安装更新器的哈希和 release 内版本一致。
- SPA 有入口文件和版本化 assets；本机 nginx 首页与 `/healthz` 通过，公网首页、版本化 JS 资源与 `/healthz` 也通过。
- 原数据与 token 目录继续使用既有 bind mount；SQLite 文件非空，token 文件权限保持 `0600`，没有迁移或覆盖用户数据。
- 容器可访问宿主机 LM Studio `/v1/models`，返回 HTTP 200；旧 systemd 服务保持 inactive/disabled。
- dotenv normalizer 可在正式镜像中导入；没有残留 release staging、Web staging 或 rollback env 临时文件，更新锁已释放。

## 边界与后续

本次没有在正在使用的生产服务上故意制造失败，因此只确认回滚资产随镜像发布、脚本路径与 CI 覆盖存在，没有把“真实生产故障注入已通过”写成事实。后续应在独立维护窗口执行一次受控失败注入，确认旧容器、runtime/web 指针和环境快照能整体恢复。

Windows 客户端仍保持手工源码更新；安装包、脚本化更新与自动更新继续作为独立 TODO，不与本次服务端 Docker 发布混合。
