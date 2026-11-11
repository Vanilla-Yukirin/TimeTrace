# 私人部署与运维

> 返回 [Wiki 首页](../readme.md) · [当前状态与待办](../../devlogs/PLAN.md)
> 按 `81a10b6` 后的部署脚本和 2026-10-23 验收归档校准，文档整理日期：2026-11-10。运行态需现场核对。

## 当前拓扑

```text
tailnet 浏览器 / Windows client / MCP 消费端
  → Tailscale Serve（私人 HTTPS）
  → 127.0.0.1:19446 nginx
      /          → /srv/timetrace/web/current（SPA）
      API / MCP  → 127.0.0.1:8765（Docker server）
                       → 原位 SQLite / blob / Token 目录
                       → 宿主机 LM Studio
```

只有 server 容器化。Compose 使用 host network 访问宿主机 LM Studio 的 `127.0.0.1:1234`；GPU、LM Studio、nginx 与 Tailscale 留在宿主机。现有数据和 Token 原位 bind mount，不复制数据库或截图来建立第二套服务端。

TimeTrace 已撤掉 Cloudflare 公网路由和旧 8080 listener，不保留公网回退，也不启用 Funnel。具体迁移与验收见 [PR #24 归档](https://git.yukirin.me/patchouli-lib/archive/src/branch/main/timetrace-deployment/202610230447-archive-private-ts-pr24-deployment.md)。主机上其他服务是否仍使用 Cloudflare 与本项目无关。

## 部署资产

| 入口 | 职责 |
|---|---|
| [deploy.yml](../../.github/workflows/deploy.yml) | `deploy` push / workflow_dispatch 构建不可变 GHCR SHA 镜像，不连接部署机 |
| [Dockerfile](../../deploy/Dockerfile) | server + 同 SHA SPA + 部署资产，非 root 运行 |
| [docker-compose.yml](../../deploy/docker-compose.yml) | host network、原位数据/配置挂载、容器限制 |
| [timetrace-update.sh](../../timetrace-update.sh) | 主机解析目标 SHA、拉镜像、独占锁、兼容检查与发布入口 |
| [deploy-container.sh](../../deploy/deploy-container.sh) | 快照、runtime/web 切换、健康检查和失败恢复 |
| [compatibility-guard.sh](../../deploy/compatibility-guard.sh) | 数据库最低兼容版本门禁 |
| [nginx 模板](../../deploy/nginx-timetrace.yukirin.me.conf) | loopback SPA/API 同源入口，普通 API 保留外部端口，MCP 精确映射 |
| [check-web-gateway.sh](../../deploy/check-web-gateway.sh) | loopback 网关检查，默认 19446，可覆盖 URL/Host |

## 首次安装

以下为 Linux 部署机的一次性准备；在仓库检出目录中以未来执行更新的部署用户运行，涉及 sudo 的步骤由有对应权限的操作者完成。

1. 确认 Docker 可用、部署用户可执行 `docker version`，并核对现有数据库、截图、Token、环境配置与备份。
2. 创建发布目录，安装更新器和它必需的兼容门禁：

```bash
sudo install -d -m 755 /srv/timetrace
sudo install -d -o "$USER" -g "$(id -gn)" -m 700 \
  /srv/timetrace/runtime /srv/timetrace/config
sudo install -d -o "$USER" -g "$(id -gn)" -m 755 /srv/timetrace/web
install -d -m 700 "$HOME/TimeTraceData" "$HOME/.config/timetrace-server"
install -d "$HOME/.local/bin"
install -m 644 deploy/compatibility-guard.sh \
  "$HOME/.local/bin/timetrace-compatibility-guard.sh"
install -m 755 timetrace-update.sh "$HOME/.local/bin/timetrace-update"
```

3. 依据 [nginx 模板](../../deploy/nginx-timetrace.yukirin.me.conf)填写实际私人域名和 HTTPS 端口的精确 MCP maps。备份并安装站点配置，`nginx -t` 成功后 reload；将 TimeTrace 的 Tailscale Serve 上游指向 `127.0.0.1:19446`。不要覆盖主机其他 Serve 规则或 nginx 站点。
4. 补齐 `/srv/timetrace/config/timetrace.env`、Token 和数据目录。首次迁移可解析旧仓库 `.env`，按 Compose 安全的单引号 dotenv 规则保存原值；不要手工展开字面 `$` 或把凭据提交到 Git。
5. 若 GHCR 包需要鉴权，先以仅含 `read:packages` 的凭据执行 `docker login ghcr.io`，然后按下节发布/激活已验证版本。

更新器不自动覆盖宿主机 nginx 或 Tailscale 配置。普通 API 的 `Host` / `X-Forwarded-Host` 应使用 `$http_host` 保留外部端口；MCP 使用专用精确映射，未知 Host/Origin 继续交给后端拒绝。不要用通配符或关闭 CSRF/重绑定保护代替配置。

## 日常发布与回滚

长期分支为 `main`（开发）与 `deploy`（制品发布指针）。生产发布和主机激活需要对应授权；文档修改和分支整理不自动触发发布。

1. 确定主线的完整目标 SHA，并等待该 SHA 的 CI 通过。
2. 确认远端 `deploy` 是目标 SHA 的祖先，只做 fast-forward：`git push origin <完整目标SHA>:refs/heads/deploy`。
3. 等待 deploy workflow 成功发布 `ghcr.io/vanilla-yukirin/timetrace-server:<sha>`。SHA tag 不允许覆盖。
4. 在部署机核对磁盘、数据/Token、备份与模型后运行 `timetrace-update <完整目标SHA>`；`timetrace-update` 无参数时读取远端 `deploy`。
5. 核验镜像 revision、runtime/web 两个 `current` 指向、API/SPA、私人 HTTPS、登录写请求及 MCP 鉴权边界。

更新失败由更新器恢复上一套后端和 SPA。人工回滚也使用 `timetrace-update <已知完整SHA>`，并接受数据库兼容门禁；不能保证任意旧镜像可读取升级后的数据库。不要在生产 checkout 执行 pull/reset，或直接 `docker compose up` / 重启旧 systemd unit 绕过标准入口。

旧 [deploy.sh](../../deploy/deploy.sh) 和 [server unit](../../deploy/timetrace-server.service) 仅作历史/应急参考。保留旧资产不代表应恢复旧运行路径；真实主机是否还存在该 unit 需核对。

## 健康检查、备份与积压

| 事项 | 只读检查 / 操作入口 | 判断边界 |
|---|---|---|
| API / 网关 | `/healthz`、`deploy/check-web-gateway.sh`；可用 `TIMETRACE_GATEWAY_URL` / `TIMETRACE_GATEWAY_HOST` 覆盖 | loopback 成功不证明私人 HTTPS、登录写操作或 MCP 全部正常 |
| 发布一致性 | 镜像 revision 与 runtime/web 的 `current` | 分支推进不等于主机已经激活 |
| 客户端 | 本机 `127.0.0.1:8764` 控制台、设置页设备信息、latest 接口 | 最近联系/采集年龄不是用户在线状态 |
| 分析与摘要 | Web 审计、金字塔、LLM 账本；overview 的覆盖缺口和 pending/open 状态 | 账本尚未覆盖所有 LLM 调用；有数据不等于叙述完成 |
| 模型 | 宿主机 `lms ps` 与实际端点配置 | 模型变更需独立授权；不按旧文档假定当前模型 |
| 数据审计 | 当前运行版本的 `timetrace-server repair-data-quality`（不加 `--apply`） | 默认只读，不查看/删除截图 |
| 修复 / 回填 | `repair-data-quality --apply`、`backfill`、`narrate` | 先确定范围和授权；apply 自带在线备份，其他写操作须预先准备一致性快照 |

数据库使用 SQLite online backup 或停服务后的完整快照，不直接复制活跃的单个 DB 文件忽略 WAL。快照需校验完整性，并单独备份 blob tree、配置与 Token；数据库备份不包含截图。备份材料按敏感数据保存，不进公开仓库。受控停机、故障注入和生产恢复另行安排。

## cookie_secure 与 INSECURE_COOKIE

私人 HTTPS 继续使用默认 Secure cookie、强制首次改密和 CSRF 检查。仅在本地 HTTP 开发时可设置 `TIMETRACE_INSECURE_COOKIE=1`；该开关也影响 CSRF Origin guard，不能用于正式私网 HTTPS 来绕过配置问题。具体实现见 [鉴权系统](auth-system.md)。

## X-Real-IP 信任边界

nginx 以 `$remote_addr` 覆盖写入 `X-Real-IP`；服务端不以客户端可伪造的 X-Forwarded-For 左端作为限速身份。当前私人入口模板保留 loopback TCP peer，不再信旧 Cloudflare 的 `CF-Connecting-IP`。在另行验证客户端 IP 映射前，请求共用 loopback 登录限速桶；后端继续仅监听回环。

## CA 与镜像维护

Python 依赖要求 `certifi>=2026.10.19`，具体版本及哈希锁在 `uv.lock`；Debian runtime 的 `ca-certificates` 基线为 `20250419~deb12u2`。CI 在镜像中离线检查两套 CA 可加载且达到基线。后续证书库更新需要刷新依赖并重建镜像；宿主机更新不会自动改变镜像，不关闭 TLS 校验。

## 数据泄露应急止血

按实际事件与授权处理：先停用 TimeTrace 对应的私人 Serve 路由或访问权限，避免影响其他服务；再撤销泄露的 Token/会话并检查访问记录。Web 管理接口的 Token 撤销即时生效；CLI 修改 Token 文件后需要让运行服务重新加载，不能把改文件等同已经撤销。不要使用已经退役的 Cloudflare 路由当作当前断路器，也不要把关闭入口当作已恢复泄露数据的保密性。

## 历史与相关文档

- [PR #24 私网迁移和部署验收](https://git.yukirin.me/patchouli-lib/archive/src/branch/main/timetrace-deployment/202610230447-archive-private-ts-pr24-deployment.md)
- [首次 pull-based 发布验收](https://git.yukirin.me/patchouli-lib/archive/src/branch/main/timetrace-deployment/202608232320-archive-pull-based-docker-production-acceptance.md)
- [早期部署架构（历史）](https://git.yukirin.me/patchouli-lib/archive/src/branch/main/timetrace-deployment/202605161000-archive-deployment-architecture.md)
- [鉴权系统](auth-system.md) · [API](api-server.md) · [隐私策略](../privacy/strategy.md)
