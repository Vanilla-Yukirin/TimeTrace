# 隐私策略

> 返回 [Wiki 首页](../readme.md)

---

## 设计原则

TimeTrace 的隐私设计遵循**本地优先、黑名单优先、默认最小暴露**原则：

1. 所有数据默认存储在本机
2. 外部接口（MCP / API）默认不返回原始截图
3. 用户可随时暂停采集或启用隐私模式

---

## Phase 1 隐私功能（必做）

### 黑名单

支持三种匹配维度：

| 维度 | 配置示例 | 说明 |
|------|---------|------|
| **应用名** | `["WeChat", "1Password"]` | 完全匹配应用名 |
| **标题关键词** | `["密码", "银行", "隐私"]` | 标题包含关键词则跳过 |
| **URL 域名** | `["banking.com", "paypal.com"]` | 浏览器 URL 域名匹配 |

```python
# src/timetrace/capture/privacy.py
def should_capture(ctx: CaptureContext, cfg: PrivacyConfig) -> bool:
    if cfg.paused:
        return False
    if ctx.app_name in cfg.app_blacklist:
        return False
    if any(kw in ctx.window_title for kw in cfg.title_keywords):
        return False
    return True
```

### 暂停模式

- 托盘图标一键暂停 / 恢复
- 暂停期间：不采集任何窗口事件、截图、键鼠数据
- `PrivacyConfig.paused = True` 时 `should_capture()` 直接返回 `False`

### 隐私模式（不存图）

- 采集窗口元数据（标题/应用/时间），但不保存截图
- `screenshots.privacy_level = "no_image"`
- 适合"我想知道在哪个应用花了多少时间，但不想留存画面"的场景

---

## Phase 2 隐私增强（规划）

| 功能 | 说明 |
|------|------|
| **区域模糊** | OCR/区域检测后对敏感区域（密码框、银行卡号等）打马赛克 |
| **全局模糊后 VLM** | 先全局高斯模糊再送 VLM，以 UI 结构识别为主，减少文字泄露 |
| **可配置 pipeline** | 用户自定义隐私处理顺序（黑名单 → 模糊 → VLM 降权） |

---

## 外部接口约束

| 约束 | 说明 |
|------|------|
| **不返回原始截图** | MCP 工具和 REST API 响应中不包含图片 URL 或 base64 数据 |
| **max_items 强制上限** | 超过限制时采样，防止批量暴露 |
| **本地监听** | API 绑定 `127.0.0.1`，不对外网暴露 |
| **token 鉴权** | 每次 MCP 连接需要本地 token 或一次性授权码 |

---

## 日志脱敏

- structlog 输出中不记录窗口标题全文（只记录应用名）
- 黑名单匹配结果记录 `privacy_skip=true`，不记录触发关键词

---

## 配置数据类

```python
# src/timetrace/config.py
@dataclass
class PrivacyConfig:
    paused: bool = False
    app_blacklist: list[str] = field(default_factory=list)
    title_keywords: list[str] = field(default_factory=list)
    store_images: bool = True   # False = 隐私模式（不存图）
```

---

## 相关文档

- [Capture Service（调用 should_capture）](../architecture/capture-service.md)
- [MCP Layer（外部接口隐私边界）](../architecture/mcp-layer.md)
- [风险分析（隐私风险缓解）](../engineering/risks.md)
