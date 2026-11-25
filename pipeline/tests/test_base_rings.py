"""pipeline/base_rings.json must match a fresh computation (regenerate: python -m pipeline.build_base_rings)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from pipeline.build_base_rings import select  # noqa: E402
from pipeline.service import compute_all_rings  # noqa: E402


def test_base_rings_file_is_fresh():
    with open(os.path.join(ROOT, "pipeline", "base_rings.json"), encoding="utf-8") as f:
        stored = json.load(f)
    fresh = compute_all_rings()
    assert stored["total_candidate_count"] == len(fresh), "stale: run python -m pipeline.build_base_rings"
    assert stored["rings"] == select(fresh), "stale: run python -m pipeline.build_base_rings"


def test_every_flaggable_ring_is_in_the_subset():
    with open(os.path.join(ROOT, "pipeline", "base_rings.json"), encoding="utf-8") as f:
        ids = {r["ring_id"] for r in json.load(f)["rings"]}
    assert {r["ring_id"] for r in compute_all_rings() if r["aggregate"] >= 0.5} <= ids
