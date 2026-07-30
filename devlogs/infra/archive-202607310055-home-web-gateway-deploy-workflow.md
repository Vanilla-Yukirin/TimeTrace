# SPA 发布迁回家庭网关：不可变 release + 原子切换

**日期：** 2026-07-31
**状态：** 仓库实现与本地验证完成；尚未 push、尚未推进 `deploy`、尚未切换 Cloudflare Tunnel

---

## 背景

实时拓扑复核确认：

- 正式域名已经由 Cloudflare Tunnel 直接送到 `yukirin-server:127.0.0.1:8765`；
- GitHub Actions 仍把 SPA 发布到 xcy；
- 因此公网 `/healthz` 正常，但 `/` 绕过 SPA、返回 FastAPI JSON 404。

`yukirin-server` 已先行完成本机 Web gateway 的一次性准备：

```text
127.0.0.1:8080 nginx
├─ /、/assets                  → /srv/timetrace/web/current
└─ /v1、/mcp、/healthz 等路径 → 127.0.0.1:8765
```

静态目录采用 release 布局：

```text
/srv/timetrace/web/
├─ current -> releases/bootstrap
└─ releases/bootstrap/index.html
```

Cloudflare Tunnel 此时仍指向 `http://localhost:8765`，所以仓库改造和首次 SPA 发布可以在不改变正式公网入口的情况下完成。

---

## 决策

1. Puck 和 xcy 退出 TimeTrace 的目标运行链路。
2. 公网运行流量使用 Cloudflare Tunnel → 本机 nginx。
3. GitHub Actions 后端与前端复用现有 `DEPLOY_*` FRP SSH 链路，不新增前端 Secrets。
4. 后端 deployment + healthz 成功后，才发布同一不可变 SHA 的 SPA。
5. SPA 不直接覆盖活动目录：上传到 `releases/<sha>`，验证后用 `mv -T` 原子替换 `current` 软链接。
6. 切换后的 nginx 冒烟失败时，工作流自动恢复上一条 `current` 软链接。
7. 生产机仍是 deployment mirror，不在生产仓库创建开发 clone、修改 workflow 或手工更新 Git。

---

## 仓库改动

### `.github/workflows/deploy.yml`

- 删除 `XCY_SSH_KEY`、`XCY_KNOWN_HOSTS`、`XCY_TARGET` 的工作流依赖；
- `publish-frontend` 改用 `DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS`、`DEPLOY_TARGET_*`；
- `publish-frontend` 从与后端并行改为依赖 `deploy`；
- runner 构建 `frontend-dist/` 后发布到 `/srv/timetrace/web/releases/<sha>`；
- 验证 `index.html`、`assets/`、Nginx `/healthz` 和真实 SPA root；
- 原子切换 `current`，失败时自动回滚上一 release；
- 不自动清理旧 release，先保留人工回滚能力。

### `deploy/publish-frontend.sh`

紧急手工兜底同步为同一套家庭网关 release 协议，默认目标改为 SSH alias `yukirin-server-2v4G`。正常路径仍是 GitHub Actions。

### `deploy/nginx-timetrace.yukirin.me.conf`

把仓库内过期的 xcy 80/443 + FRP 18765 模板替换为当前家庭网关事实：

- 仅监听 `127.0.0.1:8080`；
- 静态根为 `/srv/timetrace/web/current`；
- API/MCP 反代到 `127.0.0.1:8765`；
- Cloudflare 提供公网 TLS，家里不绑定 80/443；
- `/mcp` 使用相对 308 跳转，避免泄露内部 loopback scheme/port；
- streaming 路由关闭 buffering 并保留长超时。

### CI 与活文档

- `ci.yml` 增加前端 `npm ci`、lint、build job；
- `CLAUDE.md`、`README.md`、`infra/overview/summary.md`、`devlogs/PLAN.md` 更新为 Cloudflare Tunnel + 家庭 nginx + 同 SHA release 模型；
- 历史 xcy devlog 不回写，保留其发生时的事实。

---

## 验证

本地已完成：

```text
git diff --check                                      PASS
PyYAML parse ci.yml + deploy.yml                      PASS
bash -n deploy/publish-frontend.sh                    PASS
npm ci --prefix frontend                              PASS
npm run lint --prefix frontend                       PASS
npm run build --prefix frontend                      PASS
```

前端构建产出真实 `index.html` 与 hashed `assets/`。Vite 仍报告既有的大 chunk warning；`npm ci` 报告 lockfile 当前存在 8 个依赖漏洞（1 low、1 moderate、6 high），本次没有擅自运行可能升级依赖的 `npm audit fix`。

仓库 Nginx server block 与 `yukirin-server` 当前已启用配置保持一致；新增内容主要是版本化部署说明。

---

## 尚未执行

- 未 push 当前分支；
- 未创建 PR；
- 未推动 `origin/deploy`；
- 未让 Actions 首次发布真实 SPA release；
- 未把 Cloudflare Tunnel 从 `localhost:8765` 切到 `localhost:8080`；
- 未删除 GitHub 的 `XCY_*` Secrets；
- 未清理 xcy 上的旧 SPA/nginx/FRP 配置；
- 未处理生产仓库中的 `.env.bak.*`。

正确上线顺序：

1. PR 合入 `main`，等待 Python + frontend CI 全绿；
2. readiness gate 后 fast-forward `main → deploy`；
3. 等待后端部署与家庭 SPA release job 全绿；
4. SSH 只读核验 `current` SHA、本机首页/API/MCP；
5. Cloudflare Tunnel 切到 `http://localhost:8080`；
6. 公网验证首页、登录、API、MCP 和流式接口；
7. 观察稳定后再删除 `XCY_*` Secrets、退役 xcy TimeTrace 配置。

回滚：

- SPA：把 `/srv/timetrace/web/current` 原子指回上一 release；
- 公网入口：把 Cloudflare Tunnel 指回 `http://localhost:8765`。
