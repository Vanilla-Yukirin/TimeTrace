# Windows 客户端 v0.2.2 安装包发布

2026-11-11 完成 Windows 客户端桌面版与安装向导发布。采集端的桌面窗口、截图策略和软件设置随 v0.2.0 交付。v0.2.2 将安装入口改为双击运行的 Setup.exe。

## 发布记录

| 标签 | 提交 | 结果 |
| --- | --- | --- |
| `v0.2.0` | `b4f28cb` | 发布桌面版和 ZIP 安装制品。[Release](https://github.com/Vanilla-Yukirin/TimeTrace/releases/tag/v0.2.0) · [发布 CI](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/42897817909) |
| `v0.2.1` | `d2380f3` | 首装测试发现向导初始化时过早读取 `{app}`，发布门禁拦下制品。[失败记录](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/42943741448) |
| `v0.2.2` | `df121c7a5688a7a3e80ed817934324d92b49f0c6` | 将自启动预选移到任务页初始化后，从 `WizardDirValue` 读取安装目录。2026-11-11 02:21:18 UTC 发布。[Release](https://github.com/Vanilla-Yukirin/TimeTrace/releases/tag/v0.2.2) · [发布 CI](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/42944786152) |

安装器使用固定版本的 Inno Setup 编译为单文件 EXE，按当前用户安装。覆盖升级前从系统托盘退出客户端，向导沿用已有自启动选择。卸载入口在 Windows 设置的「已安装的应用」，配置、截图和待上传队列保存在独立的 `TimeTraceData` 目录。

## 验收证据

- Windows 本地静态检查与构建通过，单元测试 726 通过、5 跳过。前端 lint、TypeScript 和构建通过。
- 本地双容器端到端 13 项通过，场景用时 32.96 秒。包含 Outbox 确认写入失败恢复、带图入队、客户端设置、摘要迟到记录和浏览器验收。证据目录 `artifacts/timetrace-e2e-80d8f4f04a/`。
- Windows CI 在独立用户环境执行首装、旧 ZIP 覆盖升级、自启动启停和保留、系统卸载以及合成配置与队列保留。[主线 CI](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/42944786169) · [正式制品验收](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/42944786152/job/130276185593)
- 下载后的 `TimeTrace-Client-Setup-x64.exe` 为 36,729,786 字节，文件版本 `0.2.2.0`。SHA-256 为 `df31c6030411a25376f2298b19a4e41901b377c9da9d8ca7bdbac99f5709f109`，与发布附件和 GitHub 制品摘要一致。[安装包](https://github.com/Vanilla-Yukirin/TimeTrace/releases/download/v0.2.2/TimeTrace-Client-Setup-x64.exe) · [SHA-256 文件](https://github.com/Vanilla-Yukirin/TimeTrace/releases/download/v0.2.2/TimeTrace-Client-Setup-x64.exe.sha256)

发布范围为 Windows 采集客户端。归档时远端 `deploy` 指向 `81a10b6155cad84292a4fb6ba976e5cc90962e2e`。已安装设备的升级状态待运行核验。安装包签名和自动更新仍列在 [滚动计划](../PLAN.md)，使用说明见 [Windows 安装包](../../packaging/windows/README.md)。
