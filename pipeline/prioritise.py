"""pipeline/prioritise.py: rank scored rings for ONE analyst, from their context.

Pure and deterministic. The LLM never touches this: the analyst's context
(risk threshold, exposure limit, sectors, daily capacity) decides the queue and
every placement carries a plain-language reason.
"""

from scoring.scoring import FLAG_THRESHOLD

BUCKETS = ("escalate", "review_today", "backlog", "other_desk", "cleared")
MAX_RESULTS = 200  # cap the response; "cleared" rings are only counted

DEFAULT_CONTEXT = {
    "risk_threshold": FLAG_THRESHOLD,
    "exposure_limit_inr": None,  # None = no escalation limit
    "sectors": [],               # [] = all sectors
    "daily_capacity": 10,
}


def format_inr(n):
    """1_23_45_678 -> '₹1.23 Cr'; keeps reasons readable for analysts."""
    n = float(n)
    if n >= 1e7:
        return f"₹{n / 1e7:.2f} Cr"
    if n >= 1e5:
        return f"₹{n / 1e5:.2f} L"
    return f"₹{n:,.0f}"


def normalise_context(ctx, valid_sectors):
    """Validate and clamp user context. Raises ValueError with a safe message."""
    if ctx is None:
        ctx = {}
    if not isinstance(ctx, dict):
        raise ValueError("context must be an object")
    out = dict(DEFAULT_CONTEXT)

    if "risk_threshold" in ctx:
        v = ctx["risk_threshold"]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("risk_threshold must be a number")
        out["risk_threshold"] = min(0.9, max(0.5, float(v)))

    if ctx.get("exposure_limit_inr") not in (None, 0):
        v = ctx["exposure_limit_inr"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise ValueError("exposure_limit_inr must be a non-negative number")
        out["exposure_limit_inr"] = min(float(v), 1e12)

    if "sectors" in ctx:
        v = ctx["sectors"]
        if not isinstance(v, list) or any(not isinstance(s, str) for s in v):
            raise ValueError("sectors must be a list of strings")
        unknown = [s for s in v if s not in valid_sectors]
        if unknown:
            raise ValueError("unknown sector")
        out["sectors"] = sorted(set(v))

    if "daily_capacity" in ctx:
        v = ctx["daily_capacity"]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("daily_capacity must be an integer")
        out["daily_capacity"] = min(50, max(1, v))
    return out


def ring_sectors(ring, entities):
    return {entities[e].get("industry_class") for e in ring.get("entities", []) if e in entities} - {None}


def prioritise(rings, entities, ctx, total_count=None):
    """Returns {"context", "ranked": [...], "cleared_count", "flagged_count"}.

    `ctx` must already be normalised. `total_count` is the number of rings scored in
    all (rings below every analyst threshold may be omitted from `rings`). Each ranked item:
    {ring_id, rank, bucket, reasons[], aggregate, expected_loss}.
    """
    thr, limit = ctx["risk_threshold"], ctx["exposure_limit_inr"]
    sectors = set(ctx["sectors"])
    cleared = 0
    groups = {b: [] for b in BUCKETS}

    for r in rings:
        agg, loss = r.get("aggregate") or 0, r.get("expected_loss") or 0
        if agg < thr:
            cleared += 1
            continue
        reasons = [f"Score {agg:.2f} is at or above your threshold {thr:.2f}"]
        if sectors and not (ring_sectors(r, entities) & sectors):
            reasons.append("No entity in your sectors; route to the desk that covers them")
            groups["other_desk"].append((r, reasons))
        elif limit and loss >= limit:
            reasons.append(f"Exposure {format_inr(loss)} is at or above your limit {format_inr(limit)}; escalate")
            groups["escalate"].append((r, reasons))
        else:
            reasons.append(f"At-risk exposure {format_inr(loss)}")
            groups["review_today"].append((r, reasons))

    for b in groups:
        groups[b].sort(key=lambda x: (-(x[0].get("expected_loss") or 0), x[0].get("ring_id", "")))

    # Capacity: first N reviewable rings are for today; the rest wait.
    cap = ctx["daily_capacity"]
    overflow = groups["review_today"][cap:]
    groups["review_today"] = groups["review_today"][:cap]
    for r, reasons in overflow:
        groups["backlog"].append((r, reasons + [f"Beyond your daily capacity of {cap}"]))
    for r, reasons in groups["review_today"]:
        reasons.append(f"Within your daily capacity of {cap}")

    ranked, n = [], 0
    for b in BUCKETS:
        for r, reasons in groups[b]:
            n += 1
            if n <= MAX_RESULTS:
                ranked.append({
                    "ring_id": r.get("ring_id"), "rank": n, "bucket": b, "reasons": reasons,
                    "aggregate": r.get("aggregate"), "expected_loss": r.get("expected_loss"),
                })
    if total_count is not None:
        cleared = max(total_count - n, 0)
    return {"context": ctx, "ranked": ranked, "flagged_count": n, "cleared_count": cleared}
