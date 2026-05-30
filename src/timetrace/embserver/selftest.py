"""Built-in drift self-test: run the official standard inputs through the
currently-loaded model and compare against the bundled fp32 golden master.

Same logic as the standalone drift detector (tools/embedding_check), but driven
through the live engine so it doubles as the backend for the frontend "detect"
button and a deployment gate (`timetrace-embserver selftest`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent / "selftest_data"
_REF = _DATA / "reference_vectors.json"
_IMG = _DATA / "demo.jpeg"

# Official model-card standard inputs (all default instruction).
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


def _cosine(a: list[float], b: list[float]) -> float:
    import torch  # noqa: PLC0415

    ta, tb = torch.tensor(a), torch.tensor(b)
    denom = ta.norm() * tb.norm()
    return 0.0 if denom == 0 else float((ta @ tb) / denom)


def _max_abs_matrix_diff(m1, m2) -> float:
    import torch  # noqa: PLC0415

    return float((torch.tensor(m1) - torch.tensor(m2)).abs().max())


def _sim_matrix(qv, dv) -> list[list[float]]:
    import torch  # noqa: PLC0415

    return (torch.tensor(qv) @ torch.tensor(dv).T).tolist()


def standard_items() -> tuple[list[dict], list[dict]]:
    """(query_items, doc_items) in the engine's normalized {text,image} shape."""
    from PIL import Image  # noqa: PLC0415

    img = Image.open(_IMG).convert("RGB")
    queries = [{"text": q} for q in STANDARD_QUERIES]
    docs = [
        {"text": _DOC_CAPTION},
        {"image": img},
        {"text": _DOC_CAPTION, "image": img},
    ]
    return queries, docs


async def run_selftest(engine: Any, threshold: float = 0.999) -> dict:
    """Embed standard inputs at the loaded dtype, score vs golden master."""
    ref = json.loads(_REF.read_text(encoding="utf-8"))
    queries, docs = standard_items()

    qv = await engine.embed(queries)
    dv = await engine.embed(docs)

    ref_vecs = ref["query_vectors"] + ref["doc_vectors"]
    cand_vecs = qv + dv
    labels = [f"Q{i}" for i in range(len(qv))] + [f"D{i}" for i in range(len(dv))]
    per_vec = [
        {"label": lab, "cosine": _cosine(rv, cv)}
        for lab, rv, cv in zip(labels, ref_vecs, cand_vecs)
    ]
    cosines = [p["cosine"] for p in per_vec]
    sim = _sim_matrix(qv, dv)
    min_cos = min(cosines)

    return {
        "dtype": engine.dtype,
        "model": engine.model_path.name,
        "dim": len(qv[0]),
        "ref_dtype": ref.get("dtype"),
        "per_vector": per_vec,
        "min_cosine": min_cos,
        "matrix_max_abs_diff_vs_ref": _max_abs_matrix_diff(sim, ref["sim_matrix"]),
        "matrix_max_abs_diff_vs_official": _max_abs_matrix_diff(sim, ref["official_sim_2b"]),
        "threshold": threshold,
        "verdict": "PASS" if min_cos >= threshold else "FAIL",
    }
