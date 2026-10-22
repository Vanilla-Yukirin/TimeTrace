# TimeTrace 私人 TS 迁移、PR #24 发布与验收

- 归档时间：2026-10-23 04:47，Asia/Shanghai。
- 范围：本次会话中的私人入口迁移、PR #24、生产镜像更新、Nginx 修复、验收与本地分支收尾。
- 本文是当时的操作快照。私人 tailnet 域名、设备 UUID、凭据和活动正文不进入本归档。

## 目标与最终链路

用户决定 TimeTrace 只通过 Tailscale 私网访问，关闭 Cloudflare 公网入口；保留现有网页登录与 Bearer 鉴权。前后端继续共用一个镜像、由主机 Nginx 分流。

```text
tailnet HTTPS :19445
  -> Tailscale Serve
  -> 127.0.0.1:19446 Nginx
       /          -> /srv/timetrace/web/current
       API / MCP  -> 127.0.0.1:8765 Docker server
```

MCP 客户端使用私人 HTTPS 基址后的 `/mcp/`。不要把该入口开启为 Funnel，也不要恢复旧公网回退。

## 迁移经过与关键决策

1. 用户在宿主机增加 Serve 入口，最初代理到原 Nginx 8080。用户希望避开常用端口，随后增加 loopback 19446，并把 Serve 的上游切到该端口。
2. MCP 原先只允许已有公共域名和 loopback authority。为兼容私人入口，在 Nginx 中仅对实际私人域名、外部端口和 listener 精确匹配：MCP Host 转为 `localhost:8765`，同源 Origin 对应转为 loopback Origin。其他来源保留原值，继续由后端拒绝。
3. 用户移除 Nginx 的 `127.0.0.1:8080` 监听。旧公网地址一度返回 Cloudflare 502/530，之后用户删除 TimeTrace published application route 及其 CNAME。
4. 普通 DNS 查询暂时仍返回缓存 A 记录；Cloudflare 与 Google 的 DNS-over-HTTPS 查询均返回 `NXDOMAIN`。最终验收时公网 DNS 仍为 `NXDOMAIN`，8080 无监听。
5. 本机安装版客户端的配置切到私人 HTTPS 地址：保留 Token、设备身份、采集与数据目录，禁用旧入口。本机控制面板显示 TS endpoint healthy，Outbox 排空、上传恢复。本次没有发布新的 Windows 客户端安装包。
6. 另一个设备的更新/迁移说明已单独交付给用户；该设备是否完成迁移不在本次验收范围。

## PR #24 与评审修复

PR：[fix(deploy): 私人 TS 网关与可重建 CA 修复](https://github.com/Vanilla-Yukirin/TimeTrace/pull/24)。

| 提交 | 内容 |
| --- | --- |
| `6d9292d1ba0036b3f7d06cea8d901a21f091dd75` | 网关检查默认 19446，地址/Host 可覆盖；把检查脚本加入镜像部署资产；更新 Nginx 模板、部署文档与 CA 构建基线 |
| `678cb2e7fc173eeea71b8c70ebb3f1bc576c8d43` | 保留普通 API 的外部 Host 端口，补充 Cookie CSRF 和各 proxy location 的头部回归测试 |
| `81a10b6155cad84292a4fb6ba976e5cc90962e2e` | PR #24 的 merge commit，作为本次正式发布版本 |

Codex 首轮发现 P1：普通 API 的 `Host $host` 会丢掉外部 `:19445`，而浏览器 Origin 保留该端口。secure-cookie 模式下的 Origin/Host 精确比较因此拒绝登录后的修改操作。

修复为普通 API 的 `Host` 与 `X-Forwarded-Host` 使用 `$http_host`；MCP 继续使用专用精确映射。测试复现丢端口时 403、保留端口后改密 204，并确认错误域名/端口仍返回 403。

- 首批相关测试：86 passed。
- CSRF 修复后相关测试：53 passed。
- 最新提交的 Windows lint/test/打包、frontend、docker-image 三项 CI 均成功。
- Codex 对 `678cb2e` 复审完成，无新问题；原评审线程已 resolve。
- PR 跟进自动任务 `timetrace-pr-24-ci` 已暂停。

## CA 修复为什么必须入库

部署前正在运行的是临时镜像 `local/ca-fixed-timetrace-server:20261023-f88b0c782205`，基于上一正式版本做了额外证书库更新。直接用旧仓库重新构建，会丢失这部分修复。

PR #24 将两套独立证书库的基线纳入构建：

- `pyproject.toml` 要求 `certifi>=2026.10.19`，`uv.lock` 锁定 `2026.10.19` 及制品哈希；没有连带升级其他包。
- Debian runtime 显式安装/更新 `ca-certificates`，并要求版本不低于 `20250419~deb12u2`。
- CI 在真实镜像中离线检查两套 CA 库的版本、可加载性与 TLS 校验设置。该检查不是所有外部 TLS 站点或证书链的完整安全审计。
- 最终生产容器中的检查通过：system=`20250419~deb12u2`、certifi=`2026.10.19`。

后续安全更新仍需要刷新依赖并重建镜像。宿主机更新 CA 不会自动更新镜像/venv 的证书库。

## 合并、备份与发布

PR 在北京时间 2026-10-23 04:32 合并。用户明确授权合并、发布、主机更新和 Nginx 修复；此前等待阶段没有提前部署。

发布固定的 merge SHA，避免指针漂移：

```bash
git push origin 81a10b6155cad84292a4fb6ba976e5cc90962e2e:refs/heads/deploy
# 镜像发布成功后，在部署主机执行：
timetrace-update 81a10b6155cad84292a4fb6ba976e5cc90962e2e
```

- [生产镜像发布 run 39849851818](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/39849851818)：success。
- 镜像：`ghcr.io/vanilla-yukirin/timetrace-server:81a10b6155cad84292a4fb6ba976e5cc90962e2e`。
- Digest：`sha256:fa1e5e2ad43eff2ae66be012db7fb3ff1c9faf2bd3b413f9c9d357bcec4f419e`。
- OCI revision、运行镜像及 runtime/web 两个 `current` 均核对到上述 merge SHA。

切换前用 SQLite online backup 保存一致性快照，并执行 `PRAGMA quick_check`：

- 数据库/配置/Token/旧 updater/Nginx 快照：部署用户的 `$HOME/TimeTraceData/db/pre-release-20261023-pr24/`，包含 `receipt.json`。
- 数据库快照大小：1,129,054,208 bytes。
- 数据库 SHA-256：`045dd6574ad809fdc058b24572f5029b34ded806ce0f6003fdcb0f1f896f59a8`。
- 备份时：records=174694、devices=2、summaries=8219、device_summaries=468。
- 切换前的临时 CA 修复镜像保留在主机本地，不做镜像清理。

标准更新器完成原子切换、健康检查及兼容门禁提交。启动时短暂的连接拒绝发生于容器重建期间，随后恢复。旧 systemd user unit 不存在，`reset-failed` 输出了 unit not loaded；它是受允许的清理步骤，更新器最终成功。

## Nginx 的宿主机修复

主机 `/etc/nginx/sites-available/timetrace` 不会被镜像更新器自动覆盖，因此需要独立应用修复。

给用户的 sudo 脚本先检查原文件哈希，只替换 9 处普通反代的 Host 和 9 处 X-Forwarded-Host，保留私人 MCP maps、listener、其他站点。先备份，写入后 `nginx -t`、reload；任一步失败还原原配置。

用户执行的命令：

```bash
sudo python3 /home/vanilla/timetrace-fix-nginx-port-pr24.py --apply
```

- 脚本 SHA-256：`360f7bfb8c827c4efe4aac78135ca579b96054383078fba84546ddf6f71cd8aa`。
- 原配置 SHA-256：`fc994bcb2933a9eea3bf067fb6c8169fcc5d721fac41dd21a0de8c8d4d3de1dc`。
- 修复后配置 SHA-256：`fe43158e4f95c775817dd5aabb19b7d9576805ea1b3041e90fe323074956b7e4`。
- Root 备份目录：`/var/backups/timetrace-pr24-host-port-ci1bribt/`。
- 用户返回 nginx test successful；随后服务器哈希回读一致。

本次是最小端口修复，没有用仓库模板覆盖整个生产配置。现场仍有旧 Cloudflare 注释/real-IP 指令；没有将其误记为已同步模板的全部内容。

## 实际验收结果

| 项目 | 证据与结果 |
| --- | --- |
| 容器 | 新 SHA、non-root `1000:1000`、read-only rootfs、host network 与原 bind mounts 均核对通过 |
| 数据与配置 | `quick_check=ok`；验收时 records=174714、devices=2；原环境文件与 Token 文件哈希一致 |
| SPA/API | loopback 8765 与 19446 的 `/healthz` 成功；Nginx 首页包含 SPA root；Edge 私人 HTTPS 设置页正常 |
| 登录后修改 | 用户在 Edge 登录后，临时修改本机设备描述并还原；04:36:34 和 04:36:41 的两次浏览器 PATCH 均为 200；数据库回读确认设备名称、描述、启停状态与备份一致 |
| MCP 安全边界 | 正式 TS HTTPS `/mcp/`：合法请求/同源 Origin 成功；无 Token=401、错误 Origin=403、错误 Host=421 |
| 最新活动 | 返回两台注册设备及旧的未知设备分组；`presence=unknown`，不推断用户本人在线/离线 |
| 分级摘要 | 两台设备选定历史区间的 overview 均 covered；窗口不重叠、不越界，contained 搜索不带回范围外摘要 |
| 分页 | ASC/DESC 各两页通过；记录不重复、顺序与固定时间范围正确 |
| 公网与旧端口 | DNS-over-HTTPS 返回 NXDOMAIN；8080 没有监听 |

本次没有打开原始截图。活动内容与凭据没有写入验收输出或归档。

归档时只读复核曾短暂读到 Docker health=`starting`；随后探针在 04:46:27–04:46:28 成功，状态为 `healthy`、FailingStreak=0。容器仍为同一 ID、RestartCount=0、StartedAt=04:35:51；没有因该标记主动重启服务，原因未进一步调查。

## Setsuna 交接与遗留边界

服务端部署期间未修改 Setsuna。用户随后选择把交接 prompt 自己发给她，已确认发出；本机没有代发消息或修改其配置。

交接要求：改用私人 `/mcp/`，沿用现有 Token，从实际 MCP 运行环境验证 TS；刷新工具列表，验证设备列表、latest、按设备 overview、summary 下钻和游标分页。数据新鲜度、设备最后上传和模型分析状态均不能用来直接判定用户本人在线。

截至本归档，没有收到 Setsuna 的执行/测试回执，不能声称她已完成迁移或已纠正所有回答。另一台 Windows 设备也仍需其自身迁移验收。

## 本地 Git 收尾

`main` 与本地 `deploy` 已 fast-forward 到 `81a10b6`；只有主 checkout，无额外 worktree。清理下列已被 `main` 完整包含的本地开发分支，历史提交仍可由主线追溯：

| 分支 | 清理前 SHA |
| --- | --- |
| `codex/agent-activity-contract` | `74232b543328484552d5cfd668e97c4be30fe364` |
| `codex/client-local-web-ui` | `7fc880dd11519932d8def28243e01d9172bc5a22` |
| `codex/data-quality-activity-intervals` | `a0081b6e8387669dae2a2cfce0eda65f4ac6029c` |
| `codex/device-data-quality-roadmap` | `ebac87ceea974f71d51b0fcdeb98780f58adf393` |
| `codex/docker-deployment` | `b6a9dd6dbe534864583461e7ea937a2692f374f2` |
| `codex/home-web-gateway-deploy` | `28e4f5629533293537c0eba52192922c344daa48` |
| `codex/private-gateway` | `678cb2e7fc173eeea71b8c70ebb3f1bc576c8d43` |
| `codex/todo-roadmap-review` | `c6e498d383c4533f1195f4f5bf8482ef823c482b` |
| `codex/windows-client-github-release` | `efc9dda7258429b0ba86f6a08ea61a9553d9646c` |

保留 `backup-before-reword-090221`（`b931e86ac4b5ef6eaec026c19b9156421eaebf89`）：它包含主线未收录的历史备份提交。只清理本地已合并分支及已不存在的远端跟踪引用，不删除 GitHub 分支、其他资产或主机备份。

归档与索引在 `main` 做本地文档提交；不推进 `deploy`，不触发额外镜像发布。
