# Windows 客户端安装版发布验收

**时间：** 2026-08-26
**分支：** `codex/windows-client-install`
**范围：** Windows 当前用户安装包、托盘、本地控制台、采集上传、Outbox 恢复和登录自启动。

## 最终形态

- PyInstaller `onedir`、windowless EXE，不要求目标电脑安装 Python，也不要求管理员权限。
- 程序安装到 `%LOCALAPPDATA%\Programs\TimeTrace`，配置、Outbox、截图和日志继续保存在 `%USERPROFILE%\TimeTraceData`。
- 安装/更新先调用受 CSRF 保护的本地退出 API，再原子替换程序目录；失败时恢复上一目录。
- 创建 Start Menu 启动/卸载快捷方式，并默认创建当前用户 Startup 快捷方式；`-NoAutostart` 可显式关闭登录自启动。
- 托盘和 `http://127.0.0.1:8764` 共用现有 controller，只包裹 capture/outbox/sender，不建立第二套运行状态。
- 卸载只删除程序目录和快捷方式，不删除用户数据。

## 本轮证据

1. `uv run pytest`：`609 passed, 5 skipped`。
2. `uv run ruff check .` 与 `git diff --check` 通过。
3. `scripts/windows/build-client.ps1` 使用 PyInstaller 6.22.2 成功生成目录包与 ZIP。
4. 覆盖安装后，构建 EXE 与安装 EXE 的 SHA-256 完全一致；安装版单实例进程和 `/healthz` 正常。
5. Startup 快捷方式存在，目标和工作目录均指向本次安装目录。
6. 运行日志出现 `tray.thread_started`、`capture_service.started` 和 `client.local_ui_started`，无托盘线程异常。
7. 验收期间公网入口短暂返回 502/530；服务端容器和本机 Nginx 一直健康，客户端把任务留在 Outbox。Tunnel 恢复后，积压自动降到 `0 项 / 0 字节`，证明更新前后的恢复链路没有丢任务。
8. 通过本设备鉴权查询服务端最近两小时记录：69 条中 50 条带缩略图，最新详情存在 `screenshot_count=1`，确认不是只上传窗口元数据。

## 边界与后续

- 当前是本机/手工分发包，不是公开 Release；尚未代码签名，也没有自动更新器。
- 浏览器 UI 自动化因无法可靠识别已有 Edge 窗口 URL 被安全机制终止；控制台由真实 HTTP 状态验证，托盘由运行日志与回调测试验证，视觉图标仍应在合并前由当前桌面快速确认。
- 下一阶段先补首次注册/配置和脱敏诊断导出，再评估 GitHub Release、签名与自动更新；不要把这些扩大到本次安装闭环 PR。
