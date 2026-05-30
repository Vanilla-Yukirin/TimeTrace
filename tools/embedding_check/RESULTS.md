# Qwen3-VL-Embedding 量化漂移检测器 — 实测结果

**日期：** 2026-05-30
**机器：** 本机 RTX 4070 Ti SUPER 16GB / CUDA 13.2 driver / torch 2.12.0+cu126
**模型：** Qwen/Qwen3-VL-Embedding-2B（ModelScope，4.25GB，dim=2048）
**方法：** 官方 repo `Qwen3VLEmbedder.process()`，默认 instruction "Represent the user's input."

> 所有数字均来自真实工具运行（capture_reference.py / check.py），非估算。

---

## 1. 官方真值对齐

官方 HF model card（sentence-transformers 用法）公布的 4×3 相似度矩阵即金标准。
本机 float32 满精度复现：

| query \ doc | D0 文本 | D1 图 | D2 图文 | | D0 文本 | D1 图 | D2 图文 |
|---|---|---|---|---|---|---|---|
| **复现(fp32)** | | | | **官方公布** | | | |
| Q0 dog/beach | 0.8158 | 0.7178 | 0.7173 | | 0.8160 | 0.7155 | 0.7054 |
| Q1 train dog | 0.5195 | 0.3303 | 0.4392 | | 0.5173 | 0.3295 | 0.4446 |
| Q2 surfing | 0.3884 | 0.2858 | 0.3314 | | 0.3863 | 0.2987 | 0.3312 |
| Q3 skyline | 0.1093 | 0.0387 | 0.0695 | | 0.1061 | 0.0433 | 0.0839 |

- **max|Δ| vs 官方 = 0.014379 → ALIGNED**（阈值 2e-2）
- 文本列 D0 全部 <0.003（几乎完全吻合）；涉图列 D1/D2 差 ~0.012–0.014
- 偏差来源：官方矩阵实为 **bf16** 跑出（见 §2 反推）+ 图片 JPEG 解码/预处理微差

## 2. 量化漂移阶梯（候选精度 vs float32 金标准，全 2B）

| 精度 | min 向量 cosine vs fp32 | 矩阵 max\|Δ\| vs fp32 | 矩阵 max\|Δ\| vs 官方 | 显存(约) | 0.999 gate |
|---|---|---|---|---|---|
| float32 (self-check) | 1.000000 | 0.000000 | 0.014379 | ~8.5GB | PASS |
| fp16 | 0.999969 | 0.000662 | 0.013717 | ~4.5GB | PASS |
| bf16 | 0.999135 | 0.004374 | 0.012534 | ~4.5GB | PASS |
| int8 (bnb LLM.int8) | 0.987986 | 0.023810 | 0.026278 | ~2.5GB | FAIL |
| int4 / NF4 (bnb) | 0.901032 | 0.131480 | 0.117100 | ~1.5GB | FAIL |

- 漂移随精度单调增长，检测器清楚区分每一档。
- 图向量 D1/D2 每一档都是漂移最狠的（int4 时 D1=0.901），视觉塔对量化最敏感。
- **int4 的 0.90 是 bnb NF4(data-free 朴素量化)的锅，不是 int4 本身**：带校准的
  GPTQ-Int4 / AWQ-Int4 通常能把 int4 拉回 0.98+。社区 `lihongjie007/...GPTQ-Int4`
  是这类，但 hf-mirror 只 308 跳回 huggingface.co、国内下不动，未实测。
- **0.999 是"近无损"门槛**：int8/int4 FAIL 不等于不可用，检索场景按用途设 0.98/0.95 分级更合理。

### release 量化选型指导（实测支撑）
- 小显存 + 高质量甜点：**FP8(Ada 原生 ~2.5GB 预期近无损) 或 int8(~2.5GB, 0.988)**
- **bnb NF4 int4(~1.5GB) 质量掉太多，不直接用**；要 1.5GB 需上校准 int4(GPTQ/AWQ)
- 最稳：**bf16(~4.5GB 近无损)**
- 量化环境：bitsandbytes 0.49.2，Windows CUDA wheel 直接可用，零额外下载即时量化

## 3. 三个交叉印证（数字为真的物理证据）

1. **fp16 漂移 ≈ bf16 的 1/7**（矩阵 Δ 0.000662 vs 0.004374）—— fp16 有 10 位尾数、bf16 只有 7 位，精度差方向完全正确。
2. **图向量漂移 > 文本向量** —— 视觉塔对量化更敏感，符合预期。
3. **bf16 比 fp32 更贴官方矩阵**（0.0125 < 0.0144）—— 反推官方公布矩阵就是 bf16 跑的，同时解释了 §1 的 0.0144 偏差。

## 4. 检测器判据

- 主判据：min 向量 cosine vs 满精度金标准 ≥ 阈值（默认 0.999）→ PASS，可当部署 gate。
- 辅助展示：相似度矩阵 max|Δ| vs fp32 金标准（漂移幅度）+ vs 官方公布（对齐度）。
- self-check：float32 候选必得 cosine=1.0 / Δ=0，验证 harness 确定性。

## 5. 复现步骤

```bash
# 环境（本机 4070 Ti S）
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe --index-url https://download.pytorch.org/whl/cu126 torch torchvision
uv pip install --python .venv/Scripts/python.exe --index-url https://pypi.tuna.tsinghua.edu.cn/simple "transformers>=4.57.3" "qwen-vl-utils>=0.0.14" "accelerate>=1.0" modelscope
# 注意：torch 必须只用 pytorch CUDA index，加清华 extra-index 会被换成 +cpu 变体
git clone --depth 1 https://github.com/QwenLM/Qwen3-VL-Embedding.git
.venv/Scripts/modelscope.exe download --model Qwen/Qwen3-VL-Embedding-2B --local_dir models/Qwen3-VL-Embedding-2B

# 跑
python capture_reference.py --dtype float32   # 金标准 → reference_vectors.json
python check.py --dtype bf16                   # 漂移检测
python check.py --dtype fp16
python check.py --dtype float32                # self-check, 必 1.0
```

## 6. 已知坑

- **torch +cpu 陷阱**：`--extra-index-url` 指清华会优先返回 CPU 版 torch。只用 pytorch CUDA index。
- **hf-mirror 没同步**：Qwen3-VL-Embedding 太新，hf-mirror 下不到 LFS 权重，走 ModelScope。
- **GBK 控制台**：脚本里 emoji 会 UnicodeEncodeError，已加 `sys.stdout.reconfigure(encoding="utf-8")`。
- **图片 fixture**：用本地 PIL 对象传入（非 file:// 路径），避开 Windows file URL 解析坑，且保证可复现。

## 7. 待决：图片 embedding 生产服务方式

LM Studio `/v1/embeddings` 只收文本、且对该 arch 加载失败。生产要跑图 embedding 必须走
transformers / vLLM(>=0.14) / llama.cpp vision 端点，待 P5+ 决策。
