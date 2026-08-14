"""One definition of a SKU's revenue/units/cost broken out by channel and
country, used by every consumer so the grain can't drift.

Why this module exists (2026-08-13, Lena): the trading companion's `By SKU`
tab was asked to match the hand-built Monthly Trading Report's own By-SKU
tab, which carries a full channel x country cross (D2C UK, B2B UK, D2C US,
B2B US) plus a realised gross margin per cut. Every one of those figures is
already known per order line -- `chan` and `bucket` are both computed in
contract.py's line loop -- it was simply never kept at SKU grain, so the
companion could only print total/D2C/B2B/UK/US.

Three rules this module enforces:

1. **ROW is a cut, not a remainder.** The hand-built report has no ROW
   column at all, so its per-SKU UK + US silently misses ROW revenue
   (July 2026: GBP 9,359 of GBP 534,551, 1.75%). Reproducing that hole would
   contradict the reconciliation contract, so ROW is carried explicitly and
   `uk + us + row == total` holds at SKU grain, per SKU, not just at
   headline.

2. **GM per cut is realised, not catalogue.** line_detail.py gives a SKU one
   static `gm_pct` off RRP, so a "D2C GM%" computed from it would print the
   same number in every cut and mean nothing. The report's channel GMs
   genuinely differ because they are realised: cost is units x supplier
   cost, revenue is what was actually charged after discounting, so a
   heavily-discounted channel shows a thinner margin. That's the formula
   here: `gm = 1 - cost / revenue`. Cost is None-safe -- a SKU with no
   supplier cost in the Line Detail yields gm=None for every cut rather
   than a fabricated margin.

3. **Zero-revenue cuts yield None, not 0.0, for ratios.** A share or margin
   with an empty denominator is unknown, and printing 0.0% for it reads as a
   real, terrible number.

The cut keys are deliberately the same strings in the contract JSON, the
back-fill and the Excel writer -- CUT_KEYS is the single list.
"""

# Cut keys, coarse -> fine. `total` is the SKU's whole revenue; the country
# cuts partition it; the channel cuts partition it; the four cross cuts
# partition the UK and US country cuts. ROW crosses are carried too (the
# report omits them) so the cross cuts also sum to total.
COUNTRY_CUTS = ("uk", "us", "row")
CHANNEL_CUTS = ("d2c", "b2b")
CROSS_CUTS = tuple(f"{c}_{ch}" for c in COUNTRY_CUTS for ch in CHANNEL_CUTS)
CUT_KEYS = ("total",) + COUNTRY_CUTS + CHANNEL_CUTS + CROSS_CUTS


def new_cuts():
    """A zeroed cut accumulator. `cost` stays None until a line with a known
    supplier cost lands, so "no cost data" and "zero cost" stay different.
    """
    return {k: {"rev": 0.0, "u": 0, "cost": None} for k in CUT_KEYS}


def add_line(cuts, ab, units, chan, bucket, supplier_cost_gbp):
    """Accumulate one order line into every cut it belongs to.

    ab: the line's line_ab revenue contribution (net of returns and
        per-line discount, ex-VAT, already FX-converted) -- this function
        never computes revenue, it only routes it.
    chan: "D2C" | "B2B"          bucket: "UK" | "US" | "ROW"
    """
    ch = chan.lower()
    co = bucket.lower()
    if ch not in CHANNEL_CUTS:
        raise ValueError(f"sku_cuts: unknown channel {chan!r}")
    if co not in COUNTRY_CUTS:
        raise ValueError(f"sku_cuts: unknown country bucket {bucket!r}")

    line_cost = (units * supplier_cost_gbp) if isinstance(supplier_cost_gbp, (int, float)) else None
    for key in ("total", co, ch, f"{co}_{ch}"):
        c = cuts[key]
        c["rev"] += ab
        c["u"] += units
        if line_cost is not None:
            c["cost"] = (c["cost"] or 0.0) + line_cost


def gm_of(cut):
    """Realised gross margin for one cut: 1 - cost / revenue. None when cost
    is unknown or revenue is zero/negative (a net-negative SKU-cut -- all
    returns, no sales -- has no meaningful margin).
    """
    rev, cost = cut["rev"], cut["cost"]
    if cost is None or rev is None or rev <= 0:
        return None
    return 1.0 - (cost / rev)


def share_of(cut, denom_cut):
    """cut's revenue as a share of denom_cut's. None on an empty or negative
    denominator rather than 0.0 (see rule 3 in the module docstring).
    """
    rev, den = cut["rev"], denom_cut["rev"] if denom_cut else None
    if not den or den <= 0:
        return None
    return rev / den


def serialize(cuts):
    """Contract-JSON form: rounded, compact, and stable across runs. Kept
    separate from the accumulator so the in-memory floats stay exact until
    the moment they're written.
    """
    out = {}
    for k in CUT_KEYS:
        c = cuts[k]
        out[k] = {
            "rev": round(c["rev"], 6),
            "u": c["u"],
            "cost": round(c["cost"], 6) if c["cost"] is not None else None,
        }
    return out


def deserialize(payload):
    """Contract JSON -> accumulator shape, tolerating a missing cut (a
    contract written before a cut existed) by zeroing it rather than
    raising -- the caller's reconcile check is what decides whether the
    payload is usable, and it gives a far better error than a KeyError.
    """
    cuts = new_cuts()
    for k in CUT_KEYS:
        c = (payload or {}).get(k)
        if not c:
            continue
        cuts[k] = {"rev": c.get("rev", 0.0), "u": c.get("u", 0), "cost": c.get("cost")}
    return cuts


def assert_cuts_reconcile(sku, cuts, tolerance=0.001):
    """Fail loud if a SKU's cuts don't partition its total, both ways:
    country cuts and cross cuts must each sum to `total` within `tolerance`
    (relative). This is the SKU-grain twin of the headline country gate, and
    it is what makes the back-fill safe to merge into a committed contract.
    """
    total = cuts["total"]["rev"]
    checks = {
        "country": sum(cuts[k]["rev"] for k in COUNTRY_CUTS),
        "cross": sum(cuts[k]["rev"] for k in CROSS_CUTS),
        "channel": sum(cuts[k]["rev"] for k in CHANNEL_CUTS),
    }
    scale = abs(total) if abs(total) > 1e-9 else 1.0
    bad = {name: got for name, got in checks.items() if abs(got - total) / scale > tolerance}
    if bad:
        raise AssertionError(
            f"sku_cuts: {sku} does not reconcile -- total={total:.6f} but "
            + ", ".join(f"{n} sum={g:.6f}" for n, g in bad.items())
        )
