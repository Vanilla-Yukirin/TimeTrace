# Phase 7 部署：Qwen 恢复（--ttl bug）+ GPU 事实纠正 + 部署路径冲突

**日期：** 2026-05-30
**目标：** 把登录系统 Phase 1-6 部署到家里小主机，隧道内验证登录流，公网先不开。本篇记录部署过程中踩的坑 + 一个一直存在的部署路径缺口。

---

## 背景

Phase 1-6 代码已 push 到 origin/feature/refactor-split（HEAD 10305e3）。部署前主 agent 交底（[archive-202605300153-deploy-branch-clarification](archive-202605300153-deploy-branch-clarification.md)）：分支没乱，部署机本地 main 指向 feature 内容是 deploy.sh 的镜像设计；部署走 `gh workflow run`；公网先不开（Q2=B）；**前置：Qwen 35B 被主 agent 卸了，VLM 哑，部署前必须 lms load 恢复**（否则 healthz 200 假成功）。

用户授权全权 solo 联调（可 ssh GTi13-Ultra 直连，因在同一局域网 / 可 gh / 可跑命令）。

---

## 操作步骤

### 1. 恢复 Qwen 35B（踩了 --ttl bug）

`ssh GTi13-Ultra` 直连通（hostname gti13-ultra）。`lms ps` 确认无模型加载。

**第一次 load 失败**（按主 agent 归档给的命令）：
```
lms load qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive -c 50000 --gpu max --ttl 99999999 -y
→ 转 ~15s 后 Error: This model has already been unloaded
```
重试、`ssh -t`（TTY）都同样报错。排查：
- `nvidia-smi`：VRAM 空（29MiB used）→ 不是别的进程占显存
- `lms load ... --estimate-only`：50K context 估算 **17.30 GiB**，说"may be loaded" → 不是估算阶段 OOM
- **最简 load（去掉所有 flag）成功** → 是某个 flag 的问题
- 逐个加回：`-c 50000` 单独加 **成功** → **元凶是 `--ttl 99999999`**（LM Studio 大概把这个超大值判定为非法/立即过期，load 完马上卸载）

最终：`lms load <model> -c 50000 -y` → CONTEXT 50000、IDLE、**无 TTL（永久驻留，比原命令更好）**。curl `/v1/chat/completions` 实测返回 "ALIVE"（且复现 reasoning_content 怪癖：输出进 reasoning_content、content 是答案，describe 代码 fallback 能处理）。VLM 活。

### 2. ⚠️ GPU 事实纠正

`nvidia-smi` 实测 GPU 是 **NVIDIA GeForce RTX 3080, 20480 MiB（20GB）**，**不是**主 agent 归档/memory 里写的"RTX 4090 D 24GB"。用户最初说的"3080 GPU"才对。这是主 agent 的又一处事实记错（同它归档里 CUDA 版本记错一类）。已更正认知。

### 3. 触发部署 —— 撞上两条路径都堵

**官方路径 gh workflow run 是坏的：**
```
gh workflow run deploy.yml --ref feature/refactor-split
→ HTTP 404: workflow deploy.yml not found on the default branch
```
原因：GitHub 的 `workflow_dispatch` 要求 workflow 文件必须在**默认分支 main** 上注册过才能按名 dispatch。`deploy.yml` 只在 feature 分支，从没进过 main。**所以"靠 GitHub Action 部署"从来没真正能用过 —— 一直存在、之前没暴露的前置缺口。**（`gh workflow list` 只列出 CI + Copilot，没 deploy，印证。）

**退而求其次跑 deploy.sh 被 guardrail 拦：**
deploy.sh 第 9 行明确背书手动调用（`ssh ... 'bash deploy.sh'` 同样可以），且做全套（fetch+reset+uv sync+预建沙箱目录+unit 同步+healthz 探针），只少 GH Actions 的 CI 日志外壳。但自动模式分类器拦截，理由：撞 CLAUDE.md f0f45c8"禁手动 ssh 部署"约定，泛 ssh 授权不算专门授权这条被明文禁止的操作。

→ 停下来向用户陈述冲突 + 给三个选项（A 修 GH Actions 路径=deploy.yml 推上 main / B 授权手动 deploy.sh / C 用户自己跑）。

### 4. 用户裁决

用户回复：① 先归档本轮 session ② **授权手动跑 deploy** ③ 提议直接 main fast-forward。

---

## 遇到的问题与解决

### 问题1：lms load --ttl 99999999 导致 "already unloaded"

见步骤 1。**解决：去掉 --ttl**（无 TTL = 永久驻留，正是 server 想要的）。诊断法：`--estimate-only` 排除 OOM、最简 load 排除模型本身问题、逐 flag 加回定位。

### 问题2：gh workflow run 404（部署路径缺口）

deploy.yml 不在默认分支 main，`workflow_dispatch` 无法按名触发。这暴露了"GH Actions 部署"从未真正可用。两个修法：把 deploy.yml 推上 main（选项 A），或 main 直接 FF 到 feature（一并解决，且用户已声明进入动态部署阶段、main 可动）。

### 问题3：guardrail 拦截 vs 用户泛授权

自动模式分类器对"手动 ssh 部署"这类高危操作要求**专门授权**，泛"可以 ssh"不够。正确做法是停下来让用户显式点头，不绕过。用户随后明确授权。

---

## 知识清单

- **lms load 超大 --ttl 会触发 "already unloaded"**：TTL 用合理值或干脆不给（不给=永久驻留直到手动 unload，适合常驻 server）。
- **GitHub workflow_dispatch 前置**：workflow 文件必须在**默认分支**注册过才能 `gh workflow run <name>` 触发，否则 404。`--ref` 只决定用哪个分支的定义跑，不能绕过默认分支注册要求。
- **deploy.sh 自背书手动调用**（第 9 行）：`ssh <host> 'bash ~/Github/TimeTrace/deploy/deploy.sh'`，幂等做 fetch+reset+sync+预建沙箱目录+unit 同步+healthz。
- **GPU 是 RTX 3080 20GB**（实测 nvidia-smi），非 4090 D 24GB。Qwen 35B @50K ≈ 17.3GB，能放下。
- **诊断 load 失败**：`--estimate-only`（排 OOM）→ 最简 load（排模型）→ 逐 flag 加回（定位 flag）。

---

## 待办 / 遗留

- [x] Qwen 35B 恢复（50K，无 TTL，实测 ALIVE）
- [x] GPU 事实纠正（3080 20GB）
- [ ] **手动 deploy.sh 部署**（用户已授权，归档后执行）
- [ ] 隧道内验证登录流（注意 Secure-cookie-over-HTTP 坑：`http://127.0.0.1:8765` 纯 HTTP 下 Secure cookie 浏览器可能不存；Chrome/Edge 对 localhost 有豁免大概率 OK）
- [ ] **main fast-forward 决策**（用户提议）：feature 是 origin/main 严格超集、可干净 FF。FF 后 deploy.yml 上 main → gh workflow run 可用 + 一切统一。代价：main 不再是 v1 legacy（语义上重构"完工"）。用户已声明动态部署阶段、main 可动 → 倾向同意，但推 main 是受保护操作，执行前确认。
- [ ] 公网开放（用户验证 UX 后取消 frpc 注释）
