import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pipeline.prioritise import normalise_context, prioritise  # noqa: E402

ENT = {
    "A": {"industry_class": "trading"},
    "B": {"industry_class": "services"},
    "C": {"industry_class": "manufacturing"},
}
SECT = {"trading", "services", "manufacturing"}


def ring(rid, agg, loss, ents):
    return {"ring_id": rid, "aggregate": agg, "expected_loss": loss, "entities": ents}


RINGS = [
    ring("R1", 0.90, 50_000_000, ["A"]),
    ring("R2", 0.80, 20_000_000, ["B"]),
    ring("R3", 0.75, 5_000_000, ["C"]),
    ring("R4", 0.70, 1_000_000, ["A"]),
    ring("R5", 0.40, 9_000_000, ["A"]),
]


def run(**ctx):
    return prioritise(RINGS, ENT, normalise_context(ctx, SECT))


def buckets(res):
    return {x["ring_id"]: x["bucket"] for x in res["ranked"]}


def test_threshold_boundary_is_inclusive():
    res = run(risk_threshold=0.70)
    assert set(buckets(res)) == {"R1", "R2", "R3", "R4"}
    assert res["cleared_count"] == 1
    assert "R4" not in buckets(run(risk_threshold=0.71))


def test_exposure_limit_escalates_and_ranks_first():
    res = run(exposure_limit_inr=30_000_000)
    assert buckets(res)["R1"] == "escalate"
    assert res["ranked"][0]["ring_id"] == "R1"
    assert any("limit" in r for r in res["ranked"][0]["reasons"])


def test_sector_filter_routes_others_away():
    res = run(sectors=["trading"])
    b = buckets(res)
    assert b["R1"] == "review_today" and b["R4"] == "review_today"
    assert b["R2"] == "other_desk" and b["R3"] == "other_desk"
    assert [x["ring_id"] for x in res["ranked"]][-2:] == ["R2", "R3"]


def test_capacity_splits_today_and_backlog():
    res = run(daily_capacity=2)
    b = buckets(res)
    assert [k for k, v in b.items() if v == "review_today"] == ["R1", "R2"]
    assert [k for k, v in b.items() if v == "backlog"] == ["R3", "R4"]


def test_defaults_rank_by_expected_loss():
    assert [x["ring_id"] for x in run()["ranked"]] == ["R1", "R2", "R3", "R4"]


def test_every_item_has_reasons():
    assert all(x["reasons"] for x in run(exposure_limit_inr=1, sectors=["trading"])["ranked"])


def test_context_is_clamped_and_validated():
    c = normalise_context({"risk_threshold": 5, "daily_capacity": 9999}, SECT)
    assert c["risk_threshold"] == 0.9 and c["daily_capacity"] == 50
    assert normalise_context({"daily_capacity": -3}, SECT)["daily_capacity"] == 1
    for bad in ({"risk_threshold": "x"}, {"sectors": ["mining"]}, {"sectors": "trading"},
                {"daily_capacity": 2.5}, {"exposure_limit_inr": -1}, {"risk_threshold": True}):
        with pytest.raises(ValueError):
            normalise_context(bad, SECT)
    with pytest.raises(ValueError):
        normalise_context([], SECT)
