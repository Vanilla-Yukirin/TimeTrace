# P3b-3 CLI 工程化 — init / admin 子命令 + ServerAuth I/O 公开化

**日期：** 2026-05-17
**目标：** 落地双进程模式下的"开箱即用"操作面，让客户端首启 / 服务端 token 管理都有清晰、可测、可脚本化的 CLI 入口。

---

## 背景

P3a-5b 完成后，`timetrace-client` / `timetrace-server` 两个入口能跑起来了，但仍缺：

1. **客户端首启体验**：用户必须手写 `client.toml`，没人能记住所有字段名 + 格式
2. **服务端 token 管理**：默认 token 在首启日志里 dump 一次，没有列表 / 增删 / 轮转的命令
3. **headless 部署**：CI / Ansible / Docker / systemd 起服务时没法跑交互式向导

P3b-3 目的是把这三件事一次性补齐。**约束**：不引入新依赖，不做浏览器 admin UI（CLI over SSH 已够单人独占场景）。

---

## 决策汇总

| 决定 | 理由 |
|---|---|
| 子命令手写 dispatch（非 `argparse subparsers`） | 让 bare `timetrace-client` / `timetrace-server` 仍直接进 daemon 路径，子命令是 opt-in |
| 子命令 `run()` 全接受可注入的 `ask` / `confirm` / `out` | 测试零 stdin/stdout mock，单测 10+ 用例覆盖率高 |
| token 由 **服务端 mint**（非客户端 mint + 服务端登记） | 没有公开注册端点 = 无防滥用负担；单人 SSH 进 server 跑 30 秒命令即可 |
| `device_id` 客户端自动 mint + `auth_token` 服务端 mint = "用户名+密码" 模型 | token 轮转时 device_id 不变，服务端按 device_id 索引的历史记录连续 |
| `tokens.json` 落盘 POSIX `chmod 600` | 单点失效根凭证，Linux 多用户环境必须收紧；Windows NTFS 默认 ACL 已足够，no-op |
| 走 ServerAuth 公共 `read_tokens` / `write_tokens` API（非 admin_cmd 私下重新实现 JSON I/O） | 防 schema drift：未来给 TokenEntry 加字段只需改 auth.py 一处 |

**未采用方案**：

- **客户端 mint + 注册端点** → 必须公开 `/v1/auth/register`，需要 rate limit / verification / 审批队列，对单人场景纯负担
- **浏览器 admin UI** → 4-5 天工程量，要做 session/密码哈希/CSRF；CLI over SSH 体验等价且零成本
- **argparse subparsers** → bare 命令仍能进 daemon 不容易表达，对调用方 UX 反而绕

---

## 实现细节

### 1. `timetrace-client init` 交互式 / env 驱动两用

文件：`src/timetrace/client/init_cmd.py`

核心是 **pure-logic core** `fill_interactive(cfg, ask, confirm) -> ClientConfig`：

```python
def fill_interactive(cfg, ask, confirm):
    cfg.server.url = ask("Server URL", cfg.server.url) or cfg.server.url
    cfg.server.auth_token = ask("Auth token", cfg.server.auth_token) or cfg.server.auth_token
    # ... 8 个字段类似处理
    cfg.ensure_device_id()
    return cfg
```

`ask` 和 `confirm` 注入而来，默认实现走 stdin/stdout，测试时塞 `_Scripted(["", "", "https://new", ...])` 即可。

**子命令 dispatch**（`client/cli.py`）：

```python
def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "init":
        from timetrace.client.init_cmd import run as init_run
        sys.exit(init_run(args[1:]))
    if args and args[0] == "print-config":
        _print_config()
        return
    # 否则走 daemon 路径
```

**`--non-interactive` 模式**：完全跳过 prompts，纯靠 env vars + 现有 `client.toml` 文件值。专为 CI/Docker/systemd 首启设计。

**`--probe` 选项**：保存后 `GET /healthz` 一次，best-effort，**不影响 init 退出码**（错的 URL 不该把已写好的 client.toml 回滚）。

### 2. `ClientConfig` 吃 storage / capture / privacy 三段 + env 覆盖

文件：`src/timetrace/client/core/config.py`

把 `client.toml` 从 5 段扩到 7 段，所有运行时配置都进 client.toml。重写 `_render_toml()` 同步落盘。

`apply_env_overrides()` 接 9 个 `TIMETRACE_*` env vars，链式 API（返 self）：

```python
def apply_env_overrides(self) -> ClientConfig:
    if v := os.getenv("TIMETRACE_SERVER_URL"):
        self.server.url = v
    if v := os.getenv("TIMETRACE_AUTH_TOKEN"):
        self.server.auth_token = v
    # ... 9 个 env vars
    return self
```

**优先级**：file → env 覆盖。`client/cli.py:main` 启动顺序：

```python
client_cfg = ClientConfig.load_or_default().apply_env_overrides()
```

这样 headless 部署可以 ship 基线 `client.toml`，每台机器 systemd `Environment=` 调参。

**walrus 操作符的安全语义**：`if v := os.getenv(...)` 空字符串当不存在处理，避免 `TIMETRACE_AUTH_TOKEN=""` 把已有 token 抹掉。与 init 流程的"留空 = 保留"语义对齐，两条路径不打架。

### 3. `timetrace-server` admin 子命令

文件：`src/timetrace/server/admin_cmd.py`

四个命令：

```
timetrace-server info                     # 资源路径 + 监听 addr + VLM 状态
timetrace-server tokens list              # full value masked，只显示后 8 位
timetrace-server tokens add <label>       # mint 新 token，完整值打印一次
timetrace-server tokens revoke <label|full|suffix-8>
```

**ambiguous match 拒绝**：`revoke <suffix-8>` 命中多个 token 时返回 1（不允许猜）。

**所有 mutating 操作后提示 restart**：

```
Restart timetrace-server for the new token to be accepted.
```

刻意**不做 hot-reload**。理由：file → memory sync 让它只发生在 `load_or_generate` 一处，避免 file-watcher 复杂度。

### 4. ServerAuth I/O 公开化（review-driven）

第一版 admin_cmd.py 复制了 `_read_tokens` / `_write_tokens` / `_harden_perms` / `_token_path` 等私有 I/O 函数，并 `from server.auth import _DEFAULT_TOKEN_DIR` 直接 reach in 私有常量。

第三方 review 指出这是 **schema drift 风险源头**：

> "如果哪天 auth.py 给 TokenEntry 加字段（比如 permissions / expires_at），admin_cmd.py 不会跟改 —— 写出来的文件 auth.py 读不回。"

修复（`auth.py`）：

```python
# 模块级常量改公开名（去掉下划线）
DEFAULT_TOKEN_DIR = ...
TOKEN_FILE_NAME = ...
TOKEN_PREFIX = ...

class ServerAuth:
    @staticmethod
    def token_path(token_dir: Path | None = None) -> Path: ...

    @classmethod
    def read_tokens(cls, token_dir: Path | None = None) -> list[TokenEntry]: ...

    @classmethod
    def write_tokens(cls, tokens, token_dir=None) -> Path:
        """落盘 + 自动 chmod 600，schema 升级仅需改这里"""

    @staticmethod
    def mint_token_value() -> str: ...
```

admin_cmd.py 全部退化成调 ServerAuth.* —— 删了 90 行重复实现。

---

## 测试增量

| 文件 | 用例数 |
|---|---|
| `tests/test_init_cmd.py` (新) | 10 |
| `tests/test_admin_cmd.py` (新) | 10 |
| `tests/test_client_config.py` (扩) | 7 → 13 (+6 测 storage/capture/privacy 三段 roundtrip + env override + 错误形 env 抛 ValueError) |

总 261 passed 全绿，ruff 全绿。

---

## 知识清单

- **CLI testability pattern**：纯 logic 函数接受 `ask` / `confirm` / `out` 三个 callable，默认实现走 stdin/stdout/print。零 stdin mock 也能跑 10+ 单测
- **ServerAuth 是 tokens.json schema 的单一所有者**：所有读 / 写都过它。admin tooling 也照样走，避免两份代码漂移
- **空 env var 当 unset 处理**：`if v := os.getenv(...)` 的 walrus 写法天然过滤空字符串，与 init 的"留空 = 保留"语义对齐
- **首启自动生成 default token + 立即落盘 + 日志显示一次**：管理员复制到 client 即可。无须额外注册流程

---

## 相关 commit

| Hash | 作用 |
|---|---|
| `d195202` | ClientConfig 吃三段 + env 覆盖 |
| `d176829` | init + admin CLI + tokens.json chmod 600 |
| `e82cba9` | review-driven：ServerAuth I/O 公开化 |
