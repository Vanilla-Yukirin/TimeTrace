# P5 第一刀 — pyproject 平台标记 + Dockerfile + docker-compose

**日期：** 2026-05-17
**目标：** 让"在 Linux 小主机或容器里部署 server"立刻可行，无需等 PostgresDatabase / RedisQueue / S3BlobStorage 这些 P5 主体适配器全部到齐。

---

## 背景

P5 路线图原本捆绑了"容器化 + 可替换组件"两件事。但：

- 容器化是**当下就需要**的（小主机部署不想手动管 venv / systemd 一堆）
- 可替换组件需要先选型 + 跑 PoC（不阻塞容器化）

且现有 server 进程已经能独立跑（P3a-5b 完成），只差让 Linux `uv sync` 不卡在 pywin32 / mss 这些 Windows-only 包上。**先把容器化交付了，适配器留 P5 主体**。

---

## 决策汇总

| 决定 | 理由 |
|---|---|
| Windows-only 依赖加 `; sys_platform == 'win32'` 标记（不拆 extras） | uv sync 在 Linux 上自动跳过，单一 `dependencies` 块仍是"装我就能跑"语义 |
| `[project.optional-dependencies]` 桶预留（postgres / redis / s3 等） | 各桶都是空 placeholder，等真接入时解开注释 |
| `all` → `all-extras` 重命名 | 避免与未来真正的 `all` extras 字面命名冲突；当前无外部用户 zero-cost |
| 描述补 "Windows capture client + cross-platform server" | PyPI 元数据反映 Linux server 已 first-class，不再只描述 Windows |
| 多阶段 Dockerfile（builder + slim runtime） | 复用官方 `ghcr.io/astral-sh/uv:0.5.13` 二进制；runtime 镜像无编译器 |
| 非 root 用户 `timetrace` (uid 10001) | 限制 blast radius；data_dir 默认归属该用户 |
| `/data` + `/tokens` 双 bind-mount | 数据 (DB+screenshots) 与凭证 (tokens.json) 解耦，token 轮转不动数据卷 |
| `~/.config/timetrace-server` 软链到 `/tokens` | `ServerAuth.load_or_generate()` 零 docker-awareness；同份代码裸跑 / 容器跑都行 |
| HEALTHCHECK 用 stdlib `urllib`（不引 curl） | 减一个依赖；urllib 走 socket 探活语义等同 curl |
| docker-compose 默认 `127.0.0.1:8765:8765` loopback only | 永不裸 0.0.0.0 出公网；想公网必经 Caddy/nginx 反代 |
| `env_file: .env (required: false)` 用 compose 2.24+ 新语法 | 默认 .env 缺失也能起，VLM 自动 disable；fork 用户 compose 太老换 `env_file: - .env` 老写法 |

**未采用方案**：

- 拆 `dependencies` 成 `client` / `server` 两个 extras → 对单进程 `uv run timetrace` 反而绕（需手动 `--extra both`）。平台标记直接解决"Linux 自动跳过 Windows-only"
- 把 frontend dist mount 进镜像 → 与"frontend 永不公网"安全模型冲突，且 frontend 独立 vite/CDN 部署更合理

---

## 实现细节

### 1. pyproject.toml 平台标记

```toml
dependencies = [
    # === Cross-platform: server + client common ===
    "pydantic>=2.7.0",
    "structlog>=24.1.0",
    "python-dotenv>=1.0.0",
    "Pillow>=10.0.0",
    "numpy>=1.26.0",
    "httpx>=0.28.1",
    # === Server-only ===
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "python-multipart>=0.0.9",
    "aiosqlite>=0.20.0",
    "openai>=1.30.0",
    # === Windows-only (capture client) ===
    "pywin32>=306; sys_platform == 'win32'",
    "mss>=9.0.0; sys_platform == 'win32'",
    "pynput>=1.7.0; sys_platform == 'win32'",
    "pystray>=0.19.0; sys_platform == 'win32'",
]
```

Linux 上 `uv sync` 自动省四个 win32 包；Windows 仍装全集。

### 2. Dockerfile 多阶段 + 非 root

```dockerfile
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
ARG TT_UID=10001
ARG TT_GID=10001
RUN groupadd --system --gid ${TT_GID} timetrace \
 && useradd --system --uid ${TT_UID} --gid timetrace --create-home timetrace
WORKDIR /app
COPY --from=builder --chown=timetrace:timetrace /app /app
RUN mkdir -p /data /tokens \
 && chown -R timetrace:timetrace /data /tokens
ENV PATH="/app/.venv/bin:${PATH}" \
    TIMETRACE_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
RUN mkdir -p /home/timetrace/.config \
 && ln -sf /tokens /home/timetrace/.config/timetrace-server
USER timetrace
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request, sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).status == 200 else 1)"
CMD ["timetrace-server"]
```

**关键设计**：

- builder 阶段拉 uv + 同步 deps；runtime 阶段只 COPY 成品，剥离 uv 本体 + 编译工具
- `/home/timetrace/.config/timetrace-server` 是 `/tokens` 的 symlink。`ServerAuth.load_or_generate()` 写到 `~/.config/timetrace-server/tokens.json` 自动落到 bind-mount 的 `/tokens/tokens.json`，零 docker-aware 代码
- 数据 (`/data`) 与凭证 (`/tokens`) 分两个 mount。token 轮转 `docker cp` 一个文件，不动数据卷

### 3. docker-compose.yml

```yaml
services:
  timetrace-server:
    build:
      context: .
      dockerfile: Dockerfile
    image: timetrace-server:local
    container_name: timetrace-server
    restart: unless-stopped
    ports:
      - "127.0.0.1:8765:8765"      # loopback only
    volumes:
      - ./data:/data
      - ./tokens:/tokens
    env_file:
      - path: .env
        required: false             # compose 2.24+ 新语法
    healthcheck: ...
```

**端口 bind 到 127.0.0.1**：与 "Web UI 永不公网" 安全模型一致。想公网走 Caddy/nginx 反代 + TLS。

future P5 profile 占位（compose 文件底部注释），等真接 PostgresDatabase / RedisQueue 时解开。

### 4. .dockerignore

排除 frontend / data / tokens / tests / docs / .git / __pycache__，build context 从几百 MB 降到几十 MB，速度提升明显。

---

## 验证

```bash
uv sync                              # Windows 仍装全集
docker build -t timetrace-server .   # Linux build 不卡 pywin32
docker run --rm -p 8765:8765 timetrace-server  # 起服务
curl http://127.0.0.1:8765/healthz   # 200
```

测试 261 passed 全绿（容器化不影响逻辑）。

---

## 知识清单

- **平台标记 > extras 拆分**：单一 `dependencies` 用 `; sys_platform == ...` 自然分流，比 `--extra client/--extra server` 对单进程用户友好
- **Docker 多阶段 = builder 大 + runtime 小**：runtime 不需要 uv 本体 / git / 编译工具，COPY 成品即可。镜像体积可减 50%+
- **bind-mount symlink trick**：用 symlink 把"应用看到的路径"映射到"容器外的 mount 路径"，应用代码无须感知容器
- **HEALTHCHECK 用 stdlib urllib**：不引 curl 也能 probe HTTP；slim 镜像里默认就有 python，零额外
- **loopback bind 是安全模型的代码实现**：不靠 firewall 规则保平安，靠"根本不暴露"

---

## 相关 commit

| Hash | 作用 |
|---|---|
| `44d61b5` | pyproject 平台标记 + Dockerfile + docker-compose + .dockerignore |
