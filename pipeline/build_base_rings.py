"""Regenerate pipeline/base_rings.json: the small ring subset the API serves without recomputing.

Why: scoring all candidate rings takes ~8 s cold, too slow for a serverless cold start.
The subset is every ring the UI can show: score >= 0.5 (the lowest analyst threshold),
the top rings by exposure, and every ring in demo/data.js.

    python -m pipeline.build_base_rings
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipeline.service import compute_all_rings  # noqa: E402

OUT = os.path.join(ROOT, "pipeline", "base_rings.json")
TOP_BY_EXPOSURE = 60


def select(rings):
    ids = {r["ring_id"] for r in rings if (r.get("aggregate") or 0) >= 0.5}
    ids |= {r["ring_id"] for r in sorted(rings, key=lambda r: -(r.get("expected_loss") or 0))[:TOP_BY_EXPOSURE]}
    demo = os.path.join(ROOT, "demo", "data.js")
    if os.path.exists(demo):
        with open(demo, encoding="utf-8") as f:
            ids |= set(re.findall(r'"ring_id":\s*"([A-Z0-9]+)"', f.read()))
    return [r for r in rings if r["ring_id"] in ids]


def main():
    rings = compute_all_rings()
    payload = {"schema_version": 1, "total_candidate_count": len(rings), "rings": select(rings)}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUT}: {len(payload['rings'])} of {len(rings)} rings")


if __name__ == "__main__":
    main()
