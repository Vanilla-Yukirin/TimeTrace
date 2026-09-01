# Windows 客户端发布闭环与仓库状态归档

**日期：** 2026-08-26  
**目标：** 将已完成本机安装验收的 TimeTrace Windows 客户端推进至 PR review、合并与登录自启动，并准确记录安装运行态及尚未完成的仓库清理。

---

## 背景

TimeTrace 服务端已先完成 Device ID、pull-based Docker 发布和本地 nginx/Cloudflare 同源入口；Windows 客户端随后增加了 loopback 控制面板，并从源码虚拟环境运行转向正式安装版。

首次打包、安装、托盘故障与 Uvicorn windowless 崩溃的完整排查见 [`archive-202608250055-windows-client-installer.md`](archive-202608250055-windows-client-installer.md)。该归档停在本地提交 `eb69565`、尚未 push 的阶段。本文作为追加记录，覆盖其后的自启动、PR #8、Codex review 修复、合并以及最终运行态，不修改旧归档。

相关前置运维归档：

- [`archive-202608240041-docker-lmstudio-operations.md`](archive-202608240041-docker-lmstudio-operations.md)
- main 中已合并的 `devlogs/infra/archive-202608260230-windows-client-release-acceptance.md`

本归档不包含 token、设备 ID、配置正文或内部凭据。对生产服务的描述引用本会话较早阶段的验收；最后一次状态核对只重新验证了 Windows 客户端与 Git/GitHub，不把未复查的生产服务写成实时确认。

---

## 操作步骤

### 1. 从本地安装成果进入正式发布分支

Windows 客户端安装基线提交：

```text
eb69565 feat(client): add Windows user install package
```

该实现提供 PyInstaller windowless EXE、当前用户级原子安装/更新、开始菜单启动与卸载入口、独立应用身份、单实例 mutex、轮转文件日志，以及继续保留 `%USERPROFILE%\TimeTraceData` 的程序/数据分离边界。

首次真实安装后又修复两项只会在打包环境中稳定暴露的问题：

- Uvicorn 默认 formatter 在 `sys.stderr=None` 时调用 `isatty()`，导致本地控制 API 的 TaskGroup 崩溃；改为 `uvicorn.Config(log_config=None)`。
- pystray 通过 `co_argcount` 检查动态菜单回调，原带默认捕获参数的 lambda 仍被视为三个参数；改成严格闭包工厂并补契约测试。

### 2. 增加 Windows 登录自启动

提交：

```text
8cdd814 fix(client): enable Windows autostart
```

安装脚本创建当前用户 Startup 快捷方式，卸载脚本同步删除。最终入口为：

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TimeTrace Client.lnk
  -> %LOCALAPPDATA%\Programs\TimeTrace\TimeTrace Client.exe
```

这里的“开机自启动”准确含义是：**当前用户登录 Windows 后自动启动**。它不是 Windows Service，也不会在用户登录前以系统后台服务身份运行。

### 3. 完成安装版真实验收

提交：

```text
71debad docs(client): record Windows installer acceptance
```

验收覆盖：

- 安装版单实例启动。
- 本地控制面板与 `/healthz`。
- 暂停、恢复与退出。
- 截图采集和公网上传。
- 公网暂时异常时 Outbox 持久化、重试和恢复排空。
- 再次运行安装脚本时旧进程优雅退出、目录原子替换、新进程恢复。
- 更新过程不改写本地配置与数据目录。

### 4. push 分支、创建 PR 并请求 Codex review

功能分支：

```text
codex/windows-client-install
```

PR：

```text
#8 feat(client): add Windows user installer
https://github.com/Vanilla-Yukirin/TimeTrace/pull/8
```

首次 `@codex review` 提出三项 P2 生命周期问题：

1. windowless 客户端应在解析配置前建立可用文件日志，否则格式错误可能静默退出。
2. 更新器不能只假设本地控制端口为 `8764`；自定义端口或控制面板损坏时仍需要有界的终止兜底。
3. 卸载快捷方式的 working directory 不能位于即将被卸载脚本删除的程序目录中。

### 5. 修复 review 问题并补文档

修复提交：

```text
35e946c fix(client): harden Windows lifecycle
444aa56 docs(client): record review hardening
```

实现要点：

- 在读取 `client.toml` 前先尝试 `%USERPROFILE%\TimeTraceData\logs`，失败时回退到临时目录的 TimeTrace 日志；配置加载失败也能留下诊断信息。
- 安装器先枚举目标客户端进程实际拥有的 loopback listener，逐个尝试受 CSRF 保护的 `/api/shutdown`；等待 12 秒后才进入强制终止，随后再做 5 秒有界确认。
- 强制终止只作为本地 UI 禁用或损坏后的最后兜底；未确认采集仍由持久化 Outbox 承担恢复。
- 卸载快捷方式的 working directory 改到安装目录外的父目录，避免当前目录句柄阻塞程序目录删除。

相关 review threads 全部 resolve，并在最新提交上重新请求 `@codex review`；第二轮未报告重大问题。

### 6. CI 通过并合并 PR #8

最终 CI 三项均为成功：

```text
lint-and-test  SUCCESS
frontend       SUCCESS
docker-image   SUCCESS
```

PR #8 于 `2026-08-25T19:04:32Z` 合并，merge commit：

```text
c378797cb36f505ebbc4088a19949a3648d2831b
```

合并后：

- `D:\Github\TimeTrace-client-web` 已切回 `main`。
- `main` 与 `origin/main` 同指 `c378797`。
- GitHub 当前没有开放 PR。
- PR 功能分支已不再作为当前开发入口。

### 7. 最终核验本机安装运行态

2026-08-26 最后一次实时检查结果：

```text
安装 EXE 存在：是
运行进程数：1
Startup 快捷方式存在：是
Startup 目标：已安装的 TimeTrace Client.exe
活动 endpoint：public
Outbox：0
```

因此当前运行的不是仓库 `.venv` 中的源码入口，而是：

```text
C:\Users\Yuki\AppData\Local\Programs\TimeTrace\TimeTrace Client.exe
```

本地控制 API `http://127.0.0.1:8764/api/status` 可访问，安装版、登录自启动、公网上传与 Outbox 恢复已形成可用闭环。

### 8. 核对 worktree、分支与未提交文档

“客户端发布完成”不等于“整个仓库只剩 main”。实时检查发现仍有三个 worktree：

```text
D:\Github\TimeTrace
  branch: codex/docker-production-acceptance
  HEAD: ec89f82
  dirty: devlogs/README.md + 三份追加归档

D:\Github\TimeTrace-client-web
  branch: main
  HEAD: c378797
  clean, synced with origin/main

D:\Github\TimeTrace-device-id
  branch: codex/device-identity
  HEAD: d095595
  clean, but retained after merge
```

本地还保留多个旧功能分支，远端也仍有若干已合并或历史 feature 分支。`deploy` 的本地引用还明显落后于 `origin/deploy`。这些不会影响当前安装版运行，但仓库卫生尚未收口。

---

## 遇到的问题与解决

### 问题 1：windowless 崩溃无法依赖 stderr 诊断

**现象：** PyInstaller 弹出 `AttributeError: 'NoneType' object has no attribute 'isatty'`，并以 TaskGroup 总异常退出。  
**原因：** windowed bootloader 没有标准错误流，Uvicorn 默认日志初始化仍假设 stderr 存在。  
**解决：** 本地 Uvicorn 禁用其默认 `log_config`，客户端在更早阶段建立自己的轮转文件日志和临时目录回退。

### 问题 2：托盘进程存在但图标没有正常建立

**现象：** 进程和通知身份存在，但日志出现 `tray.failed_to_start`。  
**原因：** pystray 对菜单回调参数数量的真实约束没有被宽松 mock 覆盖。  
**解决：** 使用严格闭包工厂，测试替身同步检查 `co_argcount`，并统一高对比图标与稳定 AppUserModelID。

### 问题 3：安装器依赖固定控制端口

**现象：** 用户修改本地控制端口后，更新器可能无法优雅退出正在运行的客户端。  
**解决：** 按客户端 PID 查找 loopback listener，依次执行受保护 shutdown；有界等待失败后才强制终止。

### 问题 4：卸载器从待删除目录启动

**现象：** Windows 可能因为当前目录仍指向程序目录而阻止卸载清理。  
**解决：** 将卸载快捷方式 working directory 设置到安装目录外的父目录。

### 问题 5：完成状态被“仓库干净”误概括

**现象：** PR 已合并、客户端已安装后，容易误以为只剩一个 main worktree。  
**解决：** 将运行态、安装态、PR/CI、worktree、本地分支和远端分支分开核对；当前只有 `TimeTrace-client-web/main` 是干净且同步的主线 worktree。

---

## 知识清单

- Windows 安装成功、当前进程来自安装目录、Startup 已配置是三个独立证据，需要分别检查。
- Startup 文件夹快捷方式意味着“登录后启动”，不等于系统服务或登录前启动。
- PyInstaller windowless 环境必须真实运行验证；源码测试无法覆盖 `stderr=None`、应用身份和托盘回调的全部差异。
- 客户端更新应按“发现实际 loopback 端口 → 优雅 shutdown → 有界等待 → 最后强制终止 → 原子目录切换”执行。
- Outbox 让短暂 502、更新窗口和最终强制终止保持 at-least-once 恢复边界；控制面板中的历史错误不等于当前仍失败。
- PR 合并、CI 通过、安装部署和真实产品验收必须分别陈述。
- `git status` 只描述当前 worktree；判断仓库是否整体干净还必须检查 `git worktree list`、所有本地分支和远端引用。
- devlog 是追加式历史快照。旧归档中的“尚未 push”“未启用自启动”等状态不要原地改写，应通过新归档纠正。

---

## 当前状态

- PR #8 已合并，merge commit 为 `c378797`，无开放 PR。
- Windows 正在运行正式安装版，单实例、本地控制 API 正常。
- 活动 endpoint 为 `public`，Outbox 为 `0`。
- 当前用户登录自启动已开启，快捷方式准确指向安装 EXE。
- `D:\Github\TimeTrace-client-web` 的 `main` 干净并与 `origin/main` 同步。
- 整个本地/远端 Git 尚未清理到“只剩 main”：仍有三个 worktree、多个旧本地分支和旧远端分支。
- 本归档加入后，原始 `D:\Github\TimeTrace` worktree 中保留 README 索引修改及三份未跟踪追加归档，尚未 commit。

---

## 待办 / 遗留

- [ ] 在独立授权下集中提交 `devlogs/README.md` 与三份未跟踪归档。
- [ ] 提交文档后删除已合并且不再使用的 `TimeTrace-device-id` 和旧生产验收 worktree。
- [ ] 审核后清理已合并的本地分支；远端分支删除作为单独动作确认，不与本地清理混做。
- [ ] 刷新本地 `deploy` 引用，发布时继续使用 `main -> deploy` fast-forward 约定，不从陈旧本地 `deploy` 推送。
- [ ] 后续发行阶段再增加代码签名、GitHub Release 制品与客户端自动更新；当前是可靠的手工原子更新。
- [ ] 如进程审计输出中曾展示 Cloudflare Tunnel run token，安排一次凭据轮换；本文不保留该值。
