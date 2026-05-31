# 隐私策略

> 返回 [Wiki 首页](../readme.md)
> 相关：[Capture Service](../architecture/capture-service.md) · [登录鉴权系统](../architecture/auth-system.md) · [公网部署](../architecture/web-deployment.md)

---

## 设计原则

TimeTrace 的隐私设计遵循**本地优先、黑名单优先、默认最小暴露**：

1. 所有数据默认存在本机（即便公网部署，活动数据也只在家用小主机，不上云——见 [公网部署](../architecture/web-deployment.md)）
2. 对外接口（Web UI / MCP）置于鉴权门后，未登录 / 无 token 一律拒
3. 用户可随时暂停采集

---

## 已实装的隐私功能

### 黑名单（capture 前置门）

[`client/capture/privacy.py::should_capture`](../../src/timetrace/client/capture/privacy.py) 在每次采集 tick 前判定，命中任一即跳过：

| 维度 | 配置（`PrivacyConfig`） | 匹配语义 |
|------|--------------------------|----------|
| **暂停** | `paused: bool` | `True` 时直接全跳过 |
| **应用名** | `app_blacklist: list[str]` | `ctx.app_name` 完全匹配即跳过 |
| **标题关键词** | `title_keywords: list[str]` | `ctx.window_title` 包含任一关键词即跳过 |

```python
def should_capture(ctx: CaptureContext, privacy_cfg: PrivacyConfig) -> bool:
    if privacy_cfg.paused:
        return False
    if ctx.app_name in privacy_cfg.app_blacklist:
        return False
    if any(kw in ctx.window_title for kw in privacy_cfg.title_keywords):
        return False
    return True
```

被跳过的 tick 不产生任何 record / 截图。

### 暂停模式

- `PrivacyConfig.paused=True` → `should_capture` 直接 `False`，不采集任何窗口事件 / 截图
- 托盘可切换（capture 进程内）

## **⚠️ URL 域名黑名单未实装**

旧文档列过"URL 域名"黑名单维度，但当前 `PrivacyConfig`（[`common/config.py`](../../src/timetrace/common/config.py)）只有 `paused` / `app_blacklist` / `title_keywords` 三段，**没有 URL 域名匹配**。浏览器 URL 虽被采集进 record，但隐私门不基于它过滤。要按域名拦截目前只能靠标题关键词凑。

### 元数据模式（不存图）— 已实装

"采集活动元数据但不写截图"这档**已经做了**，靠 `PrivacyConfig.store_images`（[`common/config.py`](../../src/timetrace/common/config.py) 默认 `True`）：

- `store_images=False` → capture 的截图分支直接 `return`（[`client/capture/service.py:227`](../../src/timetrace/client/capture/service.py)），record 照常入库但不写截图 / 缩略图到磁盘
- 经 `client.toml` 的 `store_images` 字段配置（[`client/core/config.py`](../../src/timetrace/client/core/config.py)）

## **⚠️ DB 级 `privacy_level` 标记未实装**

旧文档描述过用 `screenshots.privacy_level='no_image'` 在 DB 里标记隐私级别。**这个 DB 标记没做**：代码里搜不到 `privacy_level` 列 / 任何写入路径。当前的"不存图"是 capture 侧直接不产生 screenshot 行（见上），而不是写一个带 `no_image` 标记的行。要做分级隐私标记需要给 `screenshots` 加列 + 写入路径。

---

## **⚠️ Phase 2 区域级隐私（OCR / 模糊）全部未实装**

以下是规划，**当前没有任何对应代码**：

| 功能 | 说明 | 状态 |
|------|------|------|
| 区域模糊 | OCR / 区域检测后对密码框、卡号等打码 | 未实装 |
| 全局模糊后送 VLM | 先高斯模糊再送 VLM，以 UI 结构识别为主减少文字泄露 | 未实装 |
| 可配置 pipeline | 用户自定义隐私处理顺序 | 未实装 |

截图当前是原图缩略图（`thumb_max_px` 缩放 + JPEG），不做任何脱敏后处理。

---

## 鉴权门：隐私边界已转移到 Web UI 登录层

> **重要变化**：早期文档把隐私边界描述为"本地 token / 一次性授权码 + 只监听 127.0.0.1"。引入[登录系统](../architecture/auth-system.md)后，对外访问的隐私门**主要落在 Web UI 的登录鉴权上**，而不只是网络监听面。

当前的访问控制现状：

- **业务路由全部鉴权**：`/v1/records` / `/search` / `/feedback` / `/thumbs` 等被 `require_principal` 守，需 cookie session（密码登录）或 bearer token
- **默认 admin/admin 首登强制改密**，且改密闸是后端 403（非浏览器客户端也绕不过）
- **登录限速**双层（nginx + server 内存 per-IP 锁定）防暴破
- **缩略图也鉴权**：`/thumbs/{path}` 从旧的 `StaticFiles` 公开挂载改成带 `require_principal` 的路由 + path-traversal 防护——原始截图不再是"知道 URL 就能拿"
- **MCP / ingest 仅 bearer**：机器对机器，token 是唯一入口；ingest 是写入面，连 cookie 都不接
- **`/docs` / `/openapi.json` 藏在登录后**，不向公网扫描器泄露 API 地图
- **监听仍 loopback-only**（8765），公网经 nginx + FRP 隧道，链路可一键掐断（见 [公网部署的应急止血](../architecture/web-deployment.md#数据泄露应急止血)）

token 的生命周期、cookie 属性、CSRF 防护等细节统一在 [登录鉴权系统](../architecture/auth-system.md)。

## **⚠️ "对外不返回原始截图"约束当前不成立**

旧文档写过"MCP / REST 响应不含图片 URL 或 base64"。现状是 **Web UI 本来就要展示缩略图**，所以 records 响应携带 `thumb_path`、`/thumbs` 路由会返回图片字节——只是都在鉴权门后。隐私保证从"接口不暴露图"转为"接口暴露图但需登录"。MCP 工具侧仍以文本上下文为主、不塞原始帧（`max_items` 采样上限仍在）。

---

## 日志：标题截断，非脱敏

structlog 日志（[`server/worker/loop.py`](../../src/timetrace/server/worker/loop.py)）对窗口标题做的是**截断到 80 字符**（`window_title[:80]`），**不是脱敏**——截断后的前 80 字符仍是明文标题，会落到 journal/syslog。

- capture 的 `privacy_skip` 日志只记 `app_name` + `title_len`（标题长度），**不记标题文本、不记触发的关键词**——这一条是真正的脱敏
- 但 worker / 其它路径打的诊断日志会带截断后的标题明文

所以"日志不含敏感标题"目前**只在 capture 跳过路径成立**；分析路径的标题是截断不是脱敏。日志文件本身的访问控制（journal 权限、`.env` chmod 600）是这里的实际防线。

---

## 本地 embedding 的隐私角色

文本 embedding 走自托管的 [embserver](../../src/timetrace/embserver/)（Qwen3-VL embedder，端口 8766，bearer key `tt_emb_`），**全本地推理**：

- 活动文本（VLM 描述等）算 embedding 时**不出本机**——不发给任何第三方 embedding API
- VLM 本身也接本地 LM Studio（Qwen3-VL），同理不外发截图 / 文本
- 这是"本地优先"在 ML 推理环节的落地：即便公网部署，推理也在家用主机上，云 VPS 只过转 TLS 流量

如果改用云端 embedding / VLM API（设了对应 `TIMETRACE_*_BASE_URL` 指向外部端点），那条链路的隐私边界就移交给了那个外部服务——这是 operator 的显式选择，配置即声明。

---

## 配置数据类

```python
# src/timetrace/common/config.py
@dataclass
class PrivacyConfig:
    paused: bool = False
    app_blacklist: list[str] = field(default_factory=list)
    title_keywords: list[str] = field(default_factory=list)
    store_images: bool = True          # False → 只记元数据不存图（已实装）
    mode: str = "off"                  # off | text_only | full —— P4 OCR/blur 前向占位，运行时尚未分支
```

（`store_images` 已生效；`mode` 是 P4 前向占位；无 URL 域名黑名单字段——见上文标注。）

---

## 相关文档

- [Capture Service](../architecture/capture-service.md) — 调用 `should_capture` 的采集层
- [登录鉴权系统](../architecture/auth-system.md) — 鉴权门 = 当前主隐私边界
- [公网部署与安全](../architecture/web-deployment.md) — 公网链路、应急止血
- [MCP Layer](../architecture/mcp-layer.md) — 外部接口的上下文边界
- [风险分析](../engineering/risks.md) — 隐私风险缓解
