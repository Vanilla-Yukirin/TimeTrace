"""量化漂移检测：候选精度跑官方标准输入，对金标准(float32)逐向量 cosine + 矩阵 max|Δ|，
同时报对官方公布矩阵的偏差。

判据：min 向量 cosine >= 阈值 → PASS（可当部署 gate）。
self-check：--dtype float32 应得 cosine≈1.0 / 矩阵 Δ≈0。

用法:
  python check.py --dtype bf16
  python check.py --dtype fp16 --threshold 0.999
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _lab import DEFAULT_MODEL, LAB_DIR, embed_standard, max_abs_matrix_diff, similarity_matrix


def cosine(a, b) -> float:
    ta, tb = torch.tensor(a), torch.tensor(b)
    denom = ta.norm() * tb.norm()
    return 0.0 if denom == 0 else float((ta @ tb) / denom)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dtype", required=True, help="候选精度：bf16 / fp16 / float32(self-check)")
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--ref", default=str(LAB_DIR / "reference_vectors.json"))
    ap.add_argument("--threshold", type=float, default=0.999)
    args = ap.parse_args()

    ref = json.loads(Path(args.ref).read_text(encoding="utf-8"))
    print(f"[check] reference: model={ref['model']} dtype={ref['dtype']} dim={ref['dim']}")
    print(f"[check] candidate dtype={args.dtype}")

    qv, dv, dim = embed_standard(args.model, args.dtype)
    if dim != ref["dim"]:
        raise SystemExit(f"dim 不符：ref={ref['dim']} candidate={dim}")

    ref_vecs = ref["query_vectors"] + ref["doc_vectors"]
    cand_vecs = qv + dv
    labels = [f"Q{i}" for i in range(len(qv))] + [f"D{i}" for i in range(len(dv))]

    cosines = []
    print(f"\n{'vec':>4} | {'cosine vs ref':>14}")
    print("-" * 22)
    for lab, rv, cv in zip(labels, ref_vecs, cand_vecs):
        c = cosine(rv, cv)
        cosines.append(c)
        print(f"{lab:>4} | {c:>14.6f}")

    sim = similarity_matrix(qv, dv)
    diff_ref = max_abs_matrix_diff(sim, ref["sim_matrix"])
    diff_official = max_abs_matrix_diff(sim, ref["official_sim_2b"])

    min_cos = min(cosines)
    verdict = "PASS" if min_cos >= args.threshold else "FAIL"
    print("-" * 22)
    print(f"\nmin 向量 cosine vs ref = {min_cos:.6f}")
    print(f"相似度矩阵 max|Δ| vs ref(float32) = {diff_ref:.6f}")
    print(f"相似度矩阵 max|Δ| vs 官方公布     = {diff_official:.6f}")
    print(f"threshold = {args.threshold}  →  {verdict}")
    raise SystemExit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
