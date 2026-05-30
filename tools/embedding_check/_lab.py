"""共享：接官方 repo、定义官方标准输入、按 dtype 加载 embedder、算相似度矩阵。

对齐官方真值：HF model card 用 sentence-transformers 跑出 4×3 相似度矩阵并印在注释里。
本机没有 ST 集成文件，改用官方 repo 的 Qwen3VLEmbedder.process()（默认 instruction
"Represent the user's input." 与 ST 默认一致，底层同一套 forward+last-pool+L2norm），
复现同一个矩阵 → 证明 harness 匹配官方，再拿量化精度对它测漂移。
"""

from __future__ import annotations

import sys
from pathlib import Path

import torch
from PIL import Image

LAB_DIR = Path(__file__).resolve().parent
REPO_DIR = LAB_DIR / "Qwen3-VL-Embedding"
FIXTURE_IMG = LAB_DIR / "fixtures" / "demo.jpeg"
DEFAULT_MODEL = LAB_DIR / "models" / "Qwen3-VL-Embedding-2B"

if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

DTYPES = {
    "float32": torch.float32,
    "fp32": torch.float32,
    "bfloat16": torch.bfloat16,
    "bf16": torch.bfloat16,
    "float16": torch.float16,
    "fp16": torch.float16,
}

# 官方 model card 标准输入（全部默认 instruction）
STANDARD_QUERIES = [
    "A woman playing with her dog on a beach at sunset.",
    "Pet owner training dog outdoors near water.",
    "Woman surfing on waves during a sunny day.",
    "City skyline view from a high-rise building at night.",
]
_DOC_CAPTION = (
    "A woman shares a joyful moment with her golden retriever on a "
    "sun-drenched beach at sunset, as the dog offers its paw in a "
    "heartwarming display of companionship and trust."
)

# 官方公布的期望相似度矩阵（4 queries × 3 documents），2B
OFFICIAL_SIM_2B = [
    [0.8160, 0.7155, 0.7054],
    [0.5173, 0.3295, 0.4446],
    [0.3863, 0.2987, 0.3312],
    [0.1061, 0.0433, 0.0839],
]


def standard_query_inputs() -> list[dict]:
    return [{"text": q} for q in STANDARD_QUERIES]


def standard_document_inputs() -> list[dict]:
    img = Image.open(FIXTURE_IMG).convert("RGB")
    return [
        {"text": _DOC_CAPTION},  # 纯文本
        {"image": img},  # 纯图
        {"text": _DOC_CAPTION, "image": img},  # 图文
    ]


# bitsandbytes 自量化模式（即时量化现有满精度权重，零额外下载）
QUANT_MODES = {"int8", "int4", "nf4"}


def _make_quant_embedder(model_path: str, mode: str):
    """绕过 Qwen3VLEmbedder.__init__ 末尾的 .to(device)（bnb 量化模型禁止 .to），
    用 device_map 在 from_pretrained 时直接落 GPU，其余逻辑全继承父类。"""
    import src.models.qwen3_vl_embedding as m
    from transformers import BitsAndBytesConfig
    from transformers.models.qwen3_vl.processing_qwen3_vl import Qwen3VLProcessor

    if mode == "int8":
        qcfg = BitsAndBytesConfig(load_in_8bit=True)
    else:  # int4 / nf4
        qcfg = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    emb = m.Qwen3VLEmbedder.__new__(m.Qwen3VLEmbedder)
    emb.max_length = m.MAX_LENGTH
    emb.min_pixels = m.MIN_PIXELS
    emb.max_pixels = m.MAX_PIXELS
    emb.total_pixels = m.MAX_TOTAL_PIXELS
    emb.fps = m.FPS
    emb.max_frames = m.MAX_FRAMES
    emb.default_instruction = "Represent the user's input."
    emb.model = m.Qwen3VLForEmbedding.from_pretrained(
        str(model_path),
        trust_remote_code=True,
        quantization_config=qcfg,
        device_map={"": 0},
    )
    emb.processor = Qwen3VLProcessor.from_pretrained(str(model_path), padding_side="right")
    emb.model.eval()
    return emb


def load_embedder(model_path: str, dtype: str):
    from src.models.qwen3_vl_embedding import Qwen3VLEmbedder

    if dtype in QUANT_MODES:
        return _make_quant_embedder(model_path, dtype)
    if dtype == "auto":  # 加载预量化 checkpoint，尊重其 config 里的 quant 设置
        return Qwen3VLEmbedder(model_name_or_path=str(model_path), torch_dtype="auto")
    return Qwen3VLEmbedder(model_name_or_path=str(model_path), torch_dtype=DTYPES[dtype])


def embed_standard(model_path: str, dtype: str):
    """跑官方标准输入 → (query_vecs[4][dim], doc_vecs[3][dim], dim)。已 L2 normalize。"""
    embedder = load_embedder(model_path, dtype)
    q = embedder.process(standard_query_inputs()).float().cpu()
    d = embedder.process(standard_document_inputs()).float().cpu()
    return q.tolist(), d.tolist(), int(q.shape[1])


def similarity_matrix(query_vecs, doc_vecs) -> list[list[float]]:
    """归一化向量内积 = cosine（与官方 model.similarity 等价）。"""
    q = torch.tensor(query_vecs)
    d = torch.tensor(doc_vecs)
    return (q @ d.T).tolist()


def max_abs_matrix_diff(m1, m2) -> float:
    a, b = torch.tensor(m1), torch.tensor(m2)
    return float((a - b).abs().max())
