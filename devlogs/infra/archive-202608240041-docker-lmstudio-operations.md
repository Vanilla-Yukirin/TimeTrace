# Docker 正式发布与 LM Studio 双模式运维归档

**日期：** 2026-08-24

**目标：** 收口 TimeTrace 的生产网络与发布链路，完成 pull-based Docker 正式部署，并确认宿主机 LM Studio 模型配置与 GUI/llmster 双模式的运维边界。

---

## 背景

本次长会话从整理旧 agent-control 分支、未跟踪文档和滚动 TODO 开始，随后发现 TimeTrace 的运行事实已经与旧文档明显偏离：公网服务端已经迁到 `yukirin-server`，前端一度仍留在 xcy，Puck/FRP/GitHub Actions 的历史链路又使“代码在哪里、流量怎么走、谁负责部署”难以回答。

经过逐层核验，最终把长期模型收敛为：GitHub Actions 只发布不可变 GHCR 制品，不进入家庭网络；`yukirin-server` 主动拉取同一 SHA 的后端、SPA 和部署资产；nginx 与 Cloudflare Tunnel 提供同源公网入口；LM Studio/GPU 继续留在宿主机。

本文覆盖当前完整会话中的有效增量。更早的首次 Docker 切换与发布模型纠正分别见：

- [`archive-202608230244-docker-production-deployment.md`](archive-202608230244-docker-production-deployment.md)
- [`archive-202608231819-pull-based-docker-release-correction.md`](archive-202608231819-pull-based-docker-release-correction.md)
- [`archive-202608232320-pull-based-docker-production-acceptance.md`](archive-202608232320-pull-based-docker-production-acceptance.md)

---

## 操作步骤

### 1. 整理分支、文档与 TODO

先完成旧 agent-control 工作分支的 PR、review、合并与本地整理，确认两份未跟踪 devlog 的性质后，在新的 TODO 分支提交并推送。随后把“继续优化功能”降级，优先恢复可解释、可发布、可维护的运行态。

滚动 TODO 最终明确：服务端发布链路优先于多设备身份、Windows 客户端 GUI、自动更新与 Classifier V2；Windows 客户端安装包/自动更新保持独立 TODO，不与服务端 Docker 化混做。

### 2. 核验公网与内网拓扑

通过 SSH、nginx、FRP、Cloudflare Tunnel、旧 Actions 和宿主机服务状态逐层核对，确认最终生产拓扑：

```text
Browser
  -> Cloudflare Edge
  -> outbound Cloudflare Tunnel
  -> yukirin-server 127.0.0.1:8080 nginx
       -> SPA static files
       -> API proxy to 127.0.0.1:8765
```

Puck、xcy 和 GitHub Runner 均退出 TimeTrace 正式请求与激活链路。GitHub 无法稳定通过 FRP/SSH 进入家庭服务器不再是发布阻塞条件。

### 3. 将服务端改成 pull-based Docker 发布

实现并审查 PR #4，核心约束如下：

- `main` 运行常规 CI；PR 额外构建临时容器冒烟；main push 不重复构建 Docker。
- `deploy` 只接受已通过 CI 的 main fast-forward。
- `.github/workflows/deploy.yml` 只执行 `resolve-ref` 与 `build-image`，发布不可变 `ghcr.io/vanilla-yukirin/timetrace-server:<git-sha>`。
- 单一镜像同时携带服务端、React/Vite SPA、Compose、rollback override、部署脚本和根目录更新器。
- 部署机运行 `timetrace-update [<sha>]`，主动解析/拉取制品，以独占锁串行化激活并健康检查，失败恢复上一套 runtime/web release。
- 容器使用 host network、非 root 用户、只读根文件系统；原 SQLite、截图和 token 目录继续原位 bind mount。

根目录更新入口：

```bash
timetrace-update
timetrace-update <40-character-git-sha>
```

### 4. 处理 review 暴露的 dotenv 兼容问题

Codex review 指出：如果把传统 `.env` 直接按 Compose `format: raw` 读取，旧 dotenv 中的引号与转义会作为值的一部分进入容器，`/healthz` 又无法发现 VLM API key 已被污染。

最终处理方式：

- live 环境文件在首次迁移时由 `timetrace.common.dotenv_normalizer` 解析并规范化。
- rollback 的 Docker `Config.Env` 快照才使用 `format: raw`，确保字节稳定恢复。
- 保留字面 `$`、引号和转义语义，并增加真实容器 round-trip 覆盖。
- 相关修复提交包括 `f7f28e3`、`2532f61`、`05f9b16`；两份 devlog 最终由 `b6a9dd6` 一并归档。

PR #4 的所有 review threads 均已 resolve，最新 Codex review 没有重大问题。

### 5. 合并、发布并在宿主机激活

用户合并 PR #4 后：

1. main 合并 CI run `32647617993` 成功；main 上 `docker-image` 按设计跳过。
2. 重新 fetch 并确认 `origin/deploy` 是 `origin/main` 的祖先。
3. 仅执行 fast-forward：

   ```bash
   git push origin refs/remotes/origin/main:refs/heads/deploy
   ```

4. deploy run `32647814950` 成功发布 `e8b2caea8c670fb8309e1094ceb17bceed81c79c`。
5. 在 `yukirin-server` 执行：

   ```bash
   ~/.local/bin/timetrace-update
   ```

6. 验证容器、runtime/web 指针、SPA、nginx、公网、数据/token bind mount、旧 systemd、LM Studio 链路、更新器哈希与临时文件清理。

最终容器为 running + healthy，后端和 SPA 同指 `e8b2cae`；公网首页、版本化 JS 资源和 `/healthz` 均通过。SQLite 文件保持非空，token 权限保持 `0600`，没有迁移或覆盖用户数据。

### 6. 追加正式验收记录

滚动 PLAN 在发布后仍写着“PR 待合并/待发布”，因此创建 follow-up 文档 PR #5：

- commit：`ec89f825403c9700fc125ce1d9fbc1b78bdbebcb`
- PR：https://github.com/Vanilla-Yukirin/TimeTrace/pull/5
- `lint-and-test`、`frontend`、`docker-image` 三项 CI 全绿。
- Codex review：未发现重大问题。
- 当前仍保持 OPEN，未擅自合并。

### 7. 把 TimeTrace 的 VLM 切换到 Gemma

从 LM Studio `/v1/models` 查询实际模型 ID，确认存在：

```text
google/gemma-4-e4b
```

先对目标模型执行最小 chat completion canary，确认 HTTP 200 与响应模型 ID 正确；再把持久化配置从旧 35B 模型改为 Gemma：

```text
/srv/timetrace/config/timetrace.env
TIMETRACE_VLM_MODEL=google/gemma-4-e4b
```

修改前备份：

```text
/srv/timetrace/config/timetrace.env.pre-gemma-e8b2cae-20260824
```

随后固定当前 SHA 重新执行更新器：

```bash
~/.local/bin/timetrace-update e8b2caea8c670fb8309e1094ceb17bceed81c79c
```

容器内真实 VLM canary 返回 HTTP 200，进程环境中的模型名、LM Studio 响应模型均为 `google/gemma-4-e4b`；容器与公网继续健康。Gemma 当时的 LM Studio 驻留信息约为 6.33 GB、8192 context、parallel 4。

### 8. 核验 LM Studio GUI、llmster 与 runtime

宿主机更新到 LM Studio `0.4.21+2` 后，现场检查得到：

- `lms daemon status --json` 返回 `isDaemon:false`，表示当时提供服务的是 GUI 应用核心，不是独立 llmster。
- 独立 llmster `0.0.12+1` 已安装但未运行。
- 当前选中的推理 runtime 是 `llama.cpp-linux-x86_64-nvidia-cuda12-avx2@2.29.1`。
- `lms runtime update --all --dry-run` 显示另有三个未选中的 runtime 可从 `2.28.2` 更新到 `2.29.1`；当前 Gemma 使用的 CUDA12 runtime 已是 `2.29.1`。

用户说明实际使用方式：开机自启 llmster 无头服务；需要 GUI 时明确停止/kill 无头后台，再启动 GUI；两者不会同时运行。最终决定保持这套互斥双模式，不继续自动化切换。

---

## 遇到的问题与解决

### 问题 1：Actions 入站链路不稳定

**现象：** GitHub Runner 经 FRP/云服务器 SSH 到家庭服务器多次失败，部署 Secret 与目标也随着迁移发生漂移。

**原因：** 生产激活依赖跨公网入站链路，任何 FRP、VPS、安全策略或 SSH 状态变化都会阻塞发布。

**解决：** Actions 只发布 GHCR 制品，部署机通过出站 HTTPS 主动拉取；彻底移除生产 SSH/FRP job 与 Secret 依赖。

### 问题 2：dotenv 的 raw 解析会静默破坏 VLM 凭据

**现象：** 带引号或转义的旧 `.env` 在 Compose raw 模式下会把引号/反斜杠保留进实际值；服务健康探针仍可能成功。

**解决：** live 文件单独做 dotenv 语义解析与安全规范化；raw 仅用于 rollback 的 Docker 环境快照。

### 问题 3：健康轮询第一次连接被拒绝

**现象：** 容器重建后首次 `curl 127.0.0.1:8765` 返回：

```text
curl: (7) Failed to connect to 127.0.0.1 port 8765 after 0 ms: Could not connect to server
```

**原因：** 第一次轮询发生在应用开始监听前。

**解决：** 更新器继续按既定次数重试，随后容器达到 healthy 并完成发布；不是最终部署失败。

### 问题 4：Windows PowerShell 把 curl 多行输出当成数组

**现象：** 公网页面实际包含 `id="root"`，但直接对外部命令输出使用 `-notmatch` 得到错误结论。

**原因：** PowerShell 将多行 stdout 表示为字符串数组，`-notmatch` 会返回所有不匹配元素，而不是对完整 HTML 判断。

**解决：** 先用 `-join "`n"` 合并，再执行正则与资源 URL 验证。

### 问题 5：PowerShell 经 SSH 发送 heredoc 带 CRLF

**现象：** Bash 报告 heredoc 终止符不匹配，Python canary 已成功却在尾部把 `PY` 当变量/语句再次执行。

**原因：** PowerShell pipeline 写入 SSH 时重新产生 CRLF，Bash 看到的是 `PY\r`。

**解决：** 后续 Python 验证改为 `docker exec -i ... python -`，让 Python 直接从 stdin 读取；简单 Bash 检查使用单行远端命令。

### 问题 6：`lms version` 不是有效的语义版本查询

**现象：** `lms version` 输出 CLI 帮助和 commit，而不是运行核心版本。

**解决：** 使用 `lms daemon status --json` 判断实际活动核心及 `isDaemon`，使用 `lms runtime ls` 查看推理 runtime。

### 安全处理

一次只读进程盘点包含了 llama-server 命令行中的临时本地内部凭据。该值没有写入本文、devlog 索引或任何提交内容；归档仅保留版本与进程角色结论。

---

## 知识清单

- TimeTrace 正式发布分两阶段：GitHub 生成不可变制品，部署机主动激活；制品发布成功不等于生产已经切换。
- 配置文件不是写死在镜像中。生产 VLM 配置位于 `/srv/timetrace/config/timetrace.env`，修改后应通过 `timetrace-update <current-sha>` 重建，不直接操作 Compose。
- `.env` live 迁移与 Docker rollback snapshot 是两种语义：前者需要 dotenv 解析，后者需要 raw 字节稳定。
- `/healthz` 只说明 TimeTrace API 活着，不证明 VLM 模型、API key 或推理响应正常；模型变更必须增加真实 completion canary。
- LM Studio GUI 与 llmster 是独立程序，共享模型/runtime 存储，但不会自动互相升级、自动接管或组成主备。
- `lms daemon status --json` 的 `isDaemon` 能区分当前是独立 llmster 还是 GUI 核心。
- runtime 是第三层独立版本：用 `lms runtime ls` 和 `lms runtime update --all --dry-run` 检查，不能只看 GUI 版本。
- JIT 开启时 `/v1/models` 会列出已下载模型，不代表它们都驻留显存；`lms ps` 才表示当前已加载模型。
- 同机 GUI/llmster 互斥模式可用，但切换窗口内 VLM 暂时不可用；用户当前接受该人工运维边界。

---

## 当前状态

- TimeTrace 正式镜像：`e8b2caea8c670fb8309e1094ceb17bceed81c79c`。
- 容器、runtime/web 指针、nginx、公网 SPA 与 `/healthz`：已验收。
- VLM 模型：`google/gemma-4-e4b`，容器内真实 canary 已通过。
- LM Studio：GUI `0.4.21+2`；独立 llmster 已安装、按用户习惯与 GUI 互斥使用。
- 文档 PR #5：OPEN，三项 CI 通过，Codex review 无重大问题。

---

## 待办 / 遗留

- [ ] 由用户决定是否合并 PR #5；不得擅自合并。
- [ ] 在独立维护窗口做一次受控生产失败注入，验证旧容器、runtime/web 指针和环境快照整体恢复。
- [ ] Windows 客户端安装包、脚本化更新与自动更新继续作为独立 TODO。
- [ ] 后续若不再满意人工 GUI/llmster 切换，再设计显式切换脚本；当前按用户决定保持现状。
- [ ] 视磁盘策略决定是否清理旧的未选中 LM Studio runtime；执行前先 dry-run，不影响当前 CUDA12 `2.29.1`。
