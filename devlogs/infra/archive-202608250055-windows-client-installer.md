# Windows 客户端安装包与托盘故障归档

**日期：** 2026-08-25  
**目标：** 将 TimeTrace Windows 独立客户端从仓库源码运行改成可安装、可手工更新的正式 EXE，并验证托盘、本地控制面板、采集与上传链路。

---

## 背景

服务端 Device ID、Docker 发布与本地 Web 控制面板已先后完成；Windows 客户端当时仍通过仓库虚拟环境中的 `timetrace-client.exe` 手工启动。用户在 Windows 隐藏图标区域明确看不到 TimeTrace 托盘图标，并希望直接安装当前版本，不再继续用源码进程。

上一阶段的服务端发布与 LM Studio 运维见 [`archive-202608240041-docker-lmstudio-operations.md`](archive-202608240041-docker-lmstudio-operations.md)。本文只记录其后的 Windows 客户端分发增量。

本次约束：

- 使用当前用户级安装，不要求管理员权限。
- 保留 `%USERPROFILE%\TimeTraceData` 下的配置、Outbox、截图与日志。
- 不默认开启开机启动。
- 客户端安装包与服务端 Docker 发布保持独立分支与 PR 边界。
- 本次归档不记录 token、设备 ID、配置正文或其他凭据。

---

## 操作步骤

### 1. 核验源码进程与托盘身份

现场确认当时运行的是工作树 `.venv\Scripts\timetrace-client.exe`，最终由 uv 管理的 Python 解释器承载；本地控制面板监听 `127.0.0.1:8764`。

Windows `NotifyIconSettings` 里存在旧 Anaconda Python 与当前 uv Python 的 TimeTrace 记录，但没有独立 TimeTrace 程序身份。这解释了源码模式下通知区身份容易漂移，但还不能单独证明托盘线程真的成功。

仓库没有 PyInstaller、Inno Setup、NSIS 或 WiX 配置。最终选择最小可用方案：

```text
PyInstaller onedir
  + windowless TimeTrace Client.exe
  + 当前用户安装脚本
  + 开始菜单入口
  + 卸载脚本
  + 手工原子更新
```

安装目标统一为：

```text
%LOCALAPPDATA%\Programs\TimeTrace
```

用户数据继续留在：

```text
%USERPROFILE%\TimeTraceData
```

### 2. 建立独立安装分支

在干净工作树从当时的 `main` 创建：

```text
codex/windows-client-install
```

没有触碰另一工作树中原有的未提交 devlog。安装与托盘改造没有混进服务端部署分支。

### 3. 实现 Windows 桌面运行边界

新增 `src/timetrace/client/windows_runtime.py`，集中实现：

- 稳定 AppUserModelID：`VanillaYukirin.TimeTrace.Client`。
- 高对比度蓝色时钟图标，供 EXE 与 pystray 共用。
- Windows named mutex 单实例保护，防止重复点击产生第二个截图进程。
- `%USERPROFILE%\TimeTraceData\logs\client.log` 轮转文件日志；有控制台时同时输出到 stderr。
- 第二次启动时用窗口提示“客户端已经运行”。

托盘图标从原先的 16×16 透明细线钟表改成 64×64 蓝底白针高对比图标。

### 4. 实现构建、安装与卸载

新增：

- `packaging/windows/timetrace-client.spec`
- `packaging/windows/client_entry.py`
- `packaging/windows/build_assets.py`
- `scripts/windows/build-client.ps1`
- `scripts/windows/install-client.ps1`
- `scripts/windows/uninstall-client.ps1`
- `packaging/windows/README.md`

构建命令：

```powershell
pwsh -File scripts/windows/build-client.ps1
```

产物：

```text
dist/windows/TimeTrace Client/
dist/windows/TimeTrace-Client-windows-x64.zip
```

本地安装或更新：

```powershell
pwsh -File scripts/windows/install-client.ps1
```

安装器会：

1. 通过 `127.0.0.1:8764` 的 CSRF/session 保护接口请求旧客户端优雅退出。
2. 把新目录复制到同级 staging。
3. 将旧安装移动到 backup。
4. 原子切换 staging 到正式目录。
5. 创建“TimeTrace Client”和“Uninstall TimeTrace”开始菜单快捷方式。
6. 成功后删除 backup 并启动新 EXE；失败则恢复旧目录。

卸载只移除程序目录与快捷方式，明确不删除 `TimeTraceData`。

### 5. 解决构建时虚拟环境文件占用

第一次执行 `uv sync` 时失败：

```text
error: failed to remove file `...\.venv\Scripts/timetrace-client.exe`:
另一个程序正在使用此文件，进程无法访问。 (os error 32)
```

原因是旧源码客户端正从同一个虚拟环境运行。处理前先读取本地状态，确认 Outbox 中仍有可恢复的待上传项；随后通过客户端自身 `/api/shutdown` 做优雅退出，等待进程完全结束，再执行：

```powershell
uv sync --group dev
```

Outbox 未确认项仍保留在磁盘，重新启动后继续发送，没有通过强杀或删除文件绕过占用。

### 6. 首次构建与安装

PyInstaller 首次成功生成带 `0.1.0.0` 版本信息的 windowless EXE 和约 39 MB ZIP。安装前目标目录不存在，因此第一次安装只创建新程序目录和开始菜单，不覆盖旧安装。

安装脚本保留了 `client.toml` 的哈希，源码进程退出后只剩一个安装版 `TimeTrace Client.exe`。

### 7. 用户弹窗暴露 windowless 崩溃

首次安装后，用户看到了 PyInstaller 的“Unhandled exception in script”窗口。关键原始报错：

```text
ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)

File "uvicorn\logging.py", line 42, in __init__
AttributeError: 'NoneType' object has no attribute 'isatty'

ValueError: Unable to configure formatter 'default'
```

根因：PyInstaller windowed bootloader 按设计将 `sys.stderr` 设为 `None`，而 Uvicorn 默认日志格式器在构造 `Config` 时调用 `stderr.isatty()`。本地控制面板任务因此抛异常，TaskGroup 取消其余 capture/sender 任务，最终由 PyInstaller 弹出总异常窗口。

修复：本地控制 API 创建 `uvicorn.Config` 时显式设置：

```python
log_config=None
```

客户端已经有自己的轮转文件日志，因此无需让 Uvicorn重新配置 root logger。

### 8. 文件日志同时揭示托盘真因

首次安装版日志还出现：

```text
tray.failed_to_start
ValueError: <function TrayIcon._build_controller_endpoint_menu.<locals>.<lambda> ...>
```

pystray 会读取回调的 `__code__.co_argcount`，只接受最多两个参数。原动态 endpoint 菜单使用：

```python
lambda icon, item, index=endpoint.index: ...
```

虽然 `index` 有默认值，但仍被计入三个参数，pystray 在构造菜单时直接拒绝。这才是“没有托盘图标”的直接代码根因；Python 通知身份漂移只是附加问题。

修复方式是使用闭包工厂，为 label、action、checked 分别生成严格的 1/2/1 参数回调，并在测试替身中模拟 pystray 的参数数量检查，防止普通 mock 再次漏掉真实约束。

### 9. 回归测试、重建与重新安装

新增/补充测试覆盖：

- named mutex 拒绝第二实例并在释放后允许再次获取。
- 图标 RGBA/透明边缘/中心高对比。
- 文件日志真实写入。
- `sys.stderr=None` 时本地 Uvicorn socket 仍能启动并返回 `/healthz`。
- endpoint 动态菜单回调满足 pystray 的参数契约并能执行切换。

完整测试结果：

```text
609 passed, 5 skipped, 2 warnings
```

`ruff check`、相关文件格式检查、PowerShell 三个脚本语法检查和 `git diff --check` 均通过。全目录额外格式检查指出两个与本分支无关的既有测试文件格式漂移，本次没有借机改动它们。

### 10. 验证公网重试与真实手工更新

修正版启动后，本地控制面板 `/healthz` 正常，暂停→恢复均成功持久化，Windows 通知区注册路径已变成独立 `TimeTrace Client.exe`。

启动期间公网 ingest 曾短暂返回 Cloudflare `502 Bad Gateway`。Outbox 没有丢弃该项，而是在下一次尝试恢复：

```text
outbox_sender.send_failed  attempt=1  HTTP 502
outbox_sender.recovered    attempts=2
后续 POST /v1/ingest/*     HTTP 200
```

最终 Outbox 排空到 `0 项 / 0 B`，活动端点为 `public`。

随后重新运行安装脚本做真实更新演练：旧安装版 PID 优雅退出，程序目录原子替换，新 PID 启动并恢复 `/healthz=ok`；配置文件哈希保持不变。这同时验证了“首次安装”和“以后手工更新”两条路径。

Windows 界面观察工具无法把任务栏通知区域作为独立窗口捕获，因此没有通过盲点坐标声称视觉确认；验收依据是独立 EXE 的 `NotifyIconSettings` 记录、`tray.thread_started`，以及最新启动日志中 `tray.failed_to_start=0`。

### 11. 提交本地分支

最终本地提交：

```text
eb69565 feat(client): add Windows user install package
```

提交后工作树干净。用户本轮只授权构建与安装，没有授权 push 或创建 PR，因此分支保留在本地，没有擅自发布到 GitHub。

---

## 遇到的问题与解决

### 问题 1：源码托盘身份漂移

**现象：** Windows 通知区只记录 Python 路径，没有稳定应用身份。  
**处理：** 为打包进程设置固定 AppUserModelID，并把 EXE 与托盘图标统一成高对比度蓝色时钟。

### 问题 2：运行中的源码入口阻塞依赖同步

**现象：** `uv sync` 无法替换正在使用的 `.venv\Scripts\timetrace-client.exe`。  
**处理：** 先读 Outbox 状态，通过受保护本地 API 优雅退出，再继续同步和打包。

### 问题 3：windowless EXE 中 Uvicorn 访问空 stderr

**现象：** `AttributeError: 'NoneType' object has no attribute 'isatty'` 导致整个 TaskGroup 失败。  
**处理：** `uvicorn.Config(log_config=None)`，复用应用自己的文件日志。

### 问题 4：单元测试没发现 pystray 回调参数错误

**现象：** 简单的 `_MenuItem` mock 接受任意 lambda，真实 pystray 却拒绝三个 `co_argcount`。  
**处理：** 改成闭包工厂，并让测试替身复刻 pystray 的严格参数检查。

### 问题 5：公网瞬时 502

**现象：** 第一个旧 Outbox 项上传遇到 Cloudflare 502，控制面板留下最近错误。  
**处理：** 不手工跳项；依赖 Outbox 指数退避重试，第二次成功并继续排空全队列。最近错误字段是历史记录，不代表恢复后仍在失败。

---

## 知识清单

- PyInstaller `console=False` / windowed 进程中的 `sys.stdout`、`sys.stderr` 可能为 `None`；第三方组件的默认日志配置必须做真实打包冒烟，源码测试不足以证明兼容。
- 托盘图标是否存在至少有三层：托盘线程是否构造成功、Windows 是否登记通知图标、应用身份是否稳定。只查注册表或只看进程都不够。
- pystray 按 `co_argcount` 检查回调，而不是按“必填参数数量”检查；带默认捕获参数的 lambda 仍可能超限。
- 客户端更新前应优先走 `/api/shutdown`，让 capture 关闭当前 record、sender 保留未确认项；Outbox 的 at-least-once 语义承担更新窗口恢复。
- 程序目录与用户数据必须分离。原子替换 `%LOCALAPPDATA%\Programs\TimeTrace` 不应触碰 `%USERPROFILE%\TimeTraceData`。
- “安装成功”与“发布完成”是两个状态：本机手工安装已经可用，但代码签名、GitHub Release、同 SHA 兼容检查与自动更新仍未实现。
- 任务栏无法被自动观察工具可靠定位时，应使用注册状态和应用日志做证据，并明确视觉验证边界，不盲点坐标。

---

## 当前状态

- Windows 客户端已安装并运行于 `%LOCALAPPDATA%\Programs\TimeTrace`。
- 本地控制面板 `http://127.0.0.1:8764` 健康。
- 公网活动端点正常，Outbox 已排空。
- 开始菜单包含启动与卸载入口。
- 安装与手工原子更新均做过真实演练，用户数据未改变。
- 分支 `codex/windows-client-install`，提交 `eb69565`，本地工作树干净。
- 可分发 ZIP 大小 `38,991,295` bytes，SHA-256：

  ```text
  6839D457CD4CDE62AF3E4E35203A561A2E8DED29AC5AA3266FAC0908A9046DBF
  ```

---

## 待办 / 遗留

- [ ] 经用户授权后 push `codex/windows-client-install`，创建 PR 并请求 `@codex review`。
- [ ] PR 合并后按目标 commit/SHA 重新构建正式制品，避免把工作树构建物当成长期发行件。
- [ ] 增加代码签名与 GitHub Release 制品发布。
- [ ] 设计客户端版本/服务端兼容检查与自动更新器；当前只有可靠的手工原子更新。
- [ ] 由用户决定是否启用开机启动；当前安装器明确不自动开启。
- [ ] 用户在通知区做一次人工视觉确认；如仍不可见，再检查 Windows 图标缓存/显示策略，不先删除旧通知记录。
