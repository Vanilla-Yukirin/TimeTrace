# Windows 客户端 v0.3.0 发布归档

2026-11-11 14:08:46（北京时间）发布 Windows x64 安装包。标签 `v0.3.0` 指向提交 `bd953d4f9e6ce4cc50e54e617cffbde9496593cd`。[版本改动](https://github.com/Vanilla-Yukirin/TimeTrace/compare/v0.2.2...v0.3.0) · [GitHub Release](https://github.com/Vanilla-Yukirin/TimeTrace/releases/tag/v0.3.0)

## 相比 v0.2.2 的改动

- 首次启动时，点击状态页「服务端连接」里的「尚未设置」即可进入配置。填写地址和设备 Token 后可测试连接，再点「保存并重连」。日常调整从「软件设置 → 服务端连接」进入。
- Windows 客户端移除 `init` 和 `print-config` 命令，保留现有连接配置与待上传队列。保存成功后更新运行中的连接设置。
- 透明小黑图标用于 Windows 窗口左上角、侧栏、任务栏、托盘、快捷方式及 Setup.exe。网页标签页的同款 favicon 已随源码合入。

## 验证记录

| 检查 | 结果 |
| --- | --- |
| 本地单测与构建 | Windows 713 通过、5 跳过。Ruff、前端 lint / TypeScript / 构建和本地 Windows 包校验通过。单测报告为 `artifacts/unit.xml`。 |
| 本地双容器端到端 | 14 个场景通过，场景用时 33.66 秒。覆盖首次连接后截图上传、Outbox 确认落盘失败恢复、带图入队和摘要迟到记录。结果为 `artifacts/timetrace-e2e-d0741d040e/result.json`。 |
| 主线 CI | Windows 与 Linux、前端、双容器 E2E 和质量门禁通过，于 14:02:37 完成。[主线 CI](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/42969252642) |
| 标签发布 CI | 主线通过后于 14:03:29 触发。同一提交的质量门禁、Windows x64 打包、校验文件生成、首装、旧 ZIP 覆盖升级、自启动保留、卸载和合成数据保留全部通过。[发布 CI](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/42969760393) · [正式安装包验收](https://github.com/Vanilla-Yukirin/TimeTrace/actions/runs/42969760393/job/130356519233) |

以上时间均为北京时间。两次远端运行的 `docker-image` 仅在 PR 触发时执行。

## Windows 安装包

- [下载 TimeTrace-Client-Setup-x64.exe](https://github.com/Vanilla-Yukirin/TimeTrace/releases/download/v0.3.0/TimeTrace-Client-Setup-x64.exe)
- [下载 SHA-256 校验文件](https://github.com/Vanilla-Yukirin/TimeTrace/releases/download/v0.3.0/TimeTrace-Client-Setup-x64.exe.sha256)
- 下载后实测大小为 37,115,461 字节，EXE 文件版本为 `0.3.0.0`。SHA-256 为 `994a2cc2ff5bc305dc10d65f66467410b550dc6d906107a0f1feb988d5f6b57c`，与校验文件和 GitHub 制品摘要一致。

升级前从托盘退出客户端，再双击 Setup.exe。安装和升级说明见 [Windows 安装包文档](../../packaging/windows/README.md)。

本次发布范围为 GitHub 上的 Windows 安装包。归档时远端 `deploy` 指向 `81a10b6155cad84292a4fb6ba976e5cc90962e2e`。发布与归档均未执行部署或生产数据写入，已安装设备的升级状态未作核验。
