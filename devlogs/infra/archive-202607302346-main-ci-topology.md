# 前端审查收口、主干合流与部署拓扑复核

**日期：** 2026-07-30
**目标：** 审查尚未提交的全站前端优化，在保留可回退基线后修复行为问题、分段提交并合入 `main`，验证普通 CI，同时重新厘清客户端、后端、前端发布与公网访问四者的真实关系。

---

## 背景

本次会话开始时，工作区已有一轮较完整的全站前端优化：新增 design token 与 `.tt-*` 交互类、建立 `components/ui/` 原子组件、统一导航数据源、补齐加载/错误/空态、优化 Pyramid 移动端体验，并对非首屏页面做路由级代码分割。改动已在本地通过前端 lint/build，但尚未提交。

用户希望先分段 commit 保存基线，再做独立 review 与必要修复，避免审查过程把一批总体质量较好的改动“修炸”。原实现归档见：

- [`../frontend/archive-202607302310-frontend-ui-polish.md`](../frontend/archive-202607302310-frontend-ui-polish.md)

该归档是实现完成时的历史快照；本文是后续审查、Git 合流、CI 与部署拓扑核验的补充记录，不回写旧 archive。

---

## 操作步骤

### 1. 评估原始未提交代码

对用户交付时的原始版本给出的结论是：**整体约 8/10，架构方向正确，属于 Accept with fixes，而不是需要推倒重写。**

主要优点：

- design token、原子组件、导航单一数据源、路由懒加载等方向正确；
- 改动范围基本收敛在前端，抽象粒度合理；
- 同时考虑加载/错误/空态、移动端、双主题与无障碍；
- TypeScript、lint、build 已通过，具备较好的工程基线。

构建工具未覆盖到的行为风险主要有：

- 一次性 token 对话框仍可能被 Escape 关闭；
- 应用覆盖保存成功后“未保存”状态不能立即清除；
- Pyramid 移动端详情长内容存在裁切/滚动风险；
- token 撤销二次确认可能长期保持 armed；
- 删除按钮组合 class 后出现 danger/ghost 样式冲突。

### 2. 分段保存基线并修复审查问题

按职责边界将原始改动拆成 7 个基线 commit：

1. `34c936d feat(ui): 建立设计令牌与原子组件`
2. `950ca18 refactor(navigation): 统一导航与应用外壳`
3. `f98b77d feat(frontend): 优化金字塔与日志页面`
4. `22b9230 feat(frontend): 优化核心数据页面`
5. `f86a380 feat(frontend): 优化管理与认证体验`
6. `891b28d perf(frontend): 拆分非首屏路由`
7. `6235952 docs(frontend): 归档全站界面优化`

随后修复审查发现的问题：

- `TokenCreatedDialog` 迁到 Radix Dialog，禁止外部点击和 Escape 丢失一次性 secret，并保留焦点圈闭；
- `AppOverridesSection` 保存成功后同步更新 query cache、清除本地 rows，使 dirty 状态即时消失；
- 删除操作统一使用 `Button variant="danger"`，消除 class 冲突；
- `TokenManager` 撤销确认增加 3 秒自动过期，并补首次列表加载失败提示；
- `PyramidPage` 移动详情迁到 Radix Dialog bottom sheet，显式约束高度并允许长内容滚动。

修复 commit：

8. `c6e498d fix(frontend): 修正审查发现的交互问题`

修复后重新运行前端 lint/build，均通过；最终 review 结论从 **Accept with fixes** 提升为 **Accept**。

### 3. 先推送功能分支

当时当前分支为：

```text
codex/todo-roadmap-review
```

用户先要求 push，因此执行：

```powershell
git push origin codex/todo-roadmap-review
```

远端功能分支更新到：

```text
c6e498d383c4533f1195f4f5bf8482ef823c482b
```

该操作只保存功能分支，不触发 `main` CI，也不触发生产部署。

### 4. 重新核验真实部署拓扑

仓库文档与实际流量存在冲突：

- `CLAUDE.md` 仍称 xcy nginx 是公网入口；
- `.github/workflows/deploy.yml` 的确把 SPA 构建产物发布到 xcy 的 `/var/www/timetrace.yukirin.me`；
- 但实时公网请求证明，正式域名并未经过 xcy SPA，而是通过 Cloudflare Tunnel 直接到达家中 FastAPI。

实时探针：

```text
GET https://timetrace.yukirin.me/
404 {"detail":"Not Found"}

GET https://timetrace.yukirin.me/healthz
200 {"status":"ok"}
```

响应头包含 `Server: cloudflare`。根路径返回 FastAPI 风格 JSON 404、`/healthz` 正常，说明公网域名当前直达后端，未命中 xcy 的 SPA。

当前应分成三条链路理解：

#### 客户端上传

```text
Windows timetrace-client
  -> 本机 Outbox
  -> 多 endpoint / LAN / SSH 或 HTTPS 路径
  -> 家中 yukirin-server 的同一个 timetrace-server
```

客户端运行在 Windows 采集设备，不部署在 VPS；本次没有读取生产 `client.toml` 或 Outbox，因此未确认当前客户端进程和积压状态。

#### CI/CD 发布

```text
后端：GitHub Actions -> 2v4G FRP SSH -> 家中 Ubuntu 小主机
前端：GitHub Actions -> xcy -> /var/www/timetrace.yukirin.me
```

2v4G 是部署 SSH 中继，不是正式网页入口。

#### 公网访问

```text
浏览器 -> Cloudflare Edge -> Cloudflare Tunnel
       -> yukirin-server:127.0.0.1:8765
       -> FastAPI
```

这条链路绕过 xcy，导致“前端发布成功，但正式域名访问不到前端”。Puck 当前不在 TimeTrace 的正式链路中。

### 5. Fast-forward 合入并推送 `main`

用户进一步明确：TimeTrace 的日常开发主干是 `main`，希望将本次工作直接合入主干。执行：

```powershell
git switch main
git pull --ff-only origin main
git merge --ff-only codex/todo-roadmap-review
git push origin main
```

合并为纯 fast-forward，无 merge commit。实际进入 `main` 的是 9 个 commit：

- 本次 8 个前端/归档 commit；
- 功能分支原先已有的 `a64afc9 docs(project): 补充项目说明与改进路线`。

最终状态：

```text
origin/main   = c6e498d383c4533f1195f4f5bf8482ef823c482b
origin/deploy = 90609415eaf2972fb36ff0913b1f1dd2aed9c975
```

`deploy` 未移动，因此这次操作没有部署后端，也没有发布生产前端。

### 6. 等待并检查普通 CI

`main` push 触发 GitHub Actions run：

- Run：[`30557073502`](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/30557073502)
- Job：`lint-and-test`
- 结论：`success`

逐步结果：

```text
Install uv            success
Set up Python         success
Install dependencies  success
Lint (ruff)           All checks passed
Format check (ruff)   94 files already formatted
Run tests             537 passed, 5 skipped in 117.65s
```

日志中只有非阻断的 runner/tooling 警告：

- Node `punycode` deprecation warning；
- runner 的 `~/.local/bin` PATH 提示；
- `actions/checkout@v4`、`astral-sh/setup-uv@v4` 仍以 Node 20 为 target，被 runner 强制放到 Node 24。

这些警告不来自业务代码，不影响本次 CI 结论。

---

## 遇到的问题与解决

### 问题 1：首次 push 的目标分支与用户预期不同

**现象：** 用户说“先 push”，当时工作区位于 `codex/todo-roadmap-review`，因此默认推送了同名远端功能分支；用户随后说明日常希望完成的工作进入 `main`。

**原因：** “push 当前分支”和“将成果落入开发主干”是两个不同动作，首次确认时只明确了前者。

**解决：** 解释 `main` 是开发主干、`deploy` 是生产指针，再经用户确认，用 `--ff-only` 将整条功能分支合入并推送 `main`。功能分支 push 没有造成生产影响。

### 问题 2：文档中的公网入口与实时流量不一致

**现象：** 工作流和文档让人以为 xcy 同时托管 SPA 并作为公网入口，但公网 `/` 返回后端 JSON 404。

**原因：** Cloudflare Tunnel 的 ingress 已直接指向家中 `127.0.0.1:8765`，绕过 xcy；FastAPI 又不托管 SPA。

**解决：** 将“前端发布位置”“后端部署位置”“正式公网访问路径”拆成三条链路分别核验，不再把发布成功等同于流量已接入。

### 问题 3：PowerShell 把 `@{u}` 当成哈希字面量

**现象：**

```text
ParserError: Missing '=' operator after key in hash literal.
```

**原因：** PowerShell 解析 Git revision `@{u}` 时将其识别为 hashtable 语法。

**解决：** 对完整 revision 加引号：

```powershell
git rev-list --left-right --count '@{u}...HEAD'
git log --oneline '@{u}..HEAD'
```

### 问题 4：等待 GitHub Actions 的命令超时

**现象：** 短 timeout 下运行 `gh run watch` / `Start-Sleep` 被本地工具以 exit 124 终止。

**解决：** 改为按 Run ID 周期查询：

```powershell
gh run view 30557073502 --json status,conclusion,jobs,url
```

结束后再读取 job log，检查测试数量和 warning，而不是只看绿色总状态。

---

## 知识清单

- **TimeTrace 分支职责：** `main` 是日常开发主干；`deploy` 是生产指针。普通 `main` push 只触发 CI，只有推动 `deploy` 或手动 dispatch 部署工作流才会改变生产。
- **功能分支 push 是安全备份，不等于合入主干：** 当前分支已有 upstream 时，裸 `git push` 默认只更新同名远端分支。
- **发布位置不等于流量入口：** SPA 可以已经 rsync 到 xcy，但 Cloudflare Tunnel 仍可绕过 xcy，把正式域名直接送到 FastAPI。
- **同一不可变 SHA 发布前后端是正确约束：** `deploy.yml` 的后端与前端 job 都使用 resolver 固定的同一个 SHA；当前问题不是版本不一致，而是公网流量没有命中前端发布位置。
- **普通 CI 当前只覆盖 Python：** `ci.yml` 检查 Ruff 与 pytest，不包含 `frontend` 的 lint/build。本次前端是在提交前本地验证通过。
- **CI 绿不代表上线：** 本次 `main` CI 全绿，但 `origin/deploy` 保持不变，生产仍是旧版本和旧拓扑。

---

## 待办 / 遗留

- [ ] 由用户确认目标拓扑：是否保留 Cloudflare Tunnel、是否让 xcy 退出 TimeTrace、是否采用家中 nginx/Caddy 托管 SPA 并反代 FastAPI。
- [ ] 修正 `CLAUDE.md` 及相关当前架构文档中“xcy 是正式公网入口”的过期表述。
- [ ] 考虑给 `.github/workflows/ci.yml` 增加前端 `npm ci`、`npm run lint`、`npm run build`。
- [ ] 在正式发布前人工冒烟：双主题、按钮 hover/focus、Pyramid 移动 sheet、token 创建、登录/改密。
- [ ] 拓扑问题解决并完成验证后，再决定是否执行 `git push origin main:deploy`；本文记录时明确未部署。
- [ ] 视需要删除已合入的本地/远端 `codex/todo-roadmap-review` 分支。
