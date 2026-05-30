"""金标准：float32 满精度跑官方标准输入 → 算 4×3 相似度矩阵，与官方公布矩阵对齐验证，
写 reference_vectors.json（含 query/doc 满精度向量 + 矩阵）。

用法:
  python capture_reference.py
  python capture_reference.py --dtype float32 --model ./models/Qwen3-VL-Embedding-2B
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

import torch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _lab import (
    DEFAULT_MODEL,
    LAB_DIR,
    OFFICIAL_SIM_2B,
    embed_standard,
    max_abs_matrix_diff,
    similarity_matrix,
)


def fmt_matrix(m) -> str:
    return "\n".join("  [" + ", ".join(f"{x:+.4f}" for x in row) + "]" for row in m)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dtype", default="float32")
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--out", default=str(LAB_DIR / "reference_vectors.json"))
    args = ap.parse_args()

    print(f"[capture] model={args.model} dtype={args.dtype}")
    print(
        f"[capture] cuda={torch.cuda.is_available()} "
        f"device={torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'}"
    )

    qv, dv, dim = embed_standard(args.model, args.dtype)
    sim = similarity_matrix(qv, dv)
    print(f"[capture] queries={len(qv)} docs={len(dv)} dim={dim}")

    print("\n[capture] 复现的相似度矩阵 (4×3):")
    print(fmt_matrix(sim))
    print("\n[capture] 官方公布矩阵 (2B):")
    print(fmt_matrix(OFFICIAL_SIM_2B))
    diff = max_abs_matrix_diff(sim, OFFICIAL_SIM_2B)
    # 官方矩阵为默认精度(bf16)+ 其图片预处理跑出, 满精度复现落在 bf16+预处理量级内即算对齐
    aligned = diff < 2e-2
    print(
        f"\n[capture] vs 官方真值 max|delta| = {diff:.6f}  ->  "
        f"{'ALIGNED (harness 匹配官方)' if aligned else 'DEVIATED 需排查'}"
    )

    payload = {
        "model": Path(args.model).name,
        "dtype": args.dtype,
        "dim": dim,
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "platform": platform.platform(),
        "query_vectors": qv,
        "doc_vectors": dv,
        "sim_matrix": sim,
        "official_sim_2b": OFFICIAL_SIM_2B,
        "max_abs_diff_vs_official": diff,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[capture] wrote golden master → {args.out}")


if __name__ == "__main__":
    main()
