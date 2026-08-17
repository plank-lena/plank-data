"""Per-SKU returns metrics broken out by segment and market, for the returns
companion's `By SKU` tab.

The trading twin of this is common/sku_cuts.py, and the cut vocabulary is
deliberately parallel so the two documents read alike. The METRICS are not
parallel, and three differences are load-bearing:

1. **Retail and Trade are never blended into a headline (build.py §5.3,
   LOCKED).** Trading's TOTAL column is D2C + B2B and that's fine there. Here
   `retail` is the primary cut, `trade` is its own un-blended cut, and the
   combined figure exists only as `blended` -- which the writer labels
   "transparency only, not the headline". `run()` already emits
   `by_month_blended` on exactly that footing; this follows it.

2. **ROW returns are structurally unrecordable.** prep() documents that
   ReturnZap's Country column holds only GB or US across all 54,831 raw rows
   -- it cannot represent a ROW return, ever, with the current source. But
   ROW *sales* are real (mkt has been derived from Shipping: Country Code
   since 2026-08-13). So a naive ROW cut prints real orders against zero
   returns and a 0.0% return rate, which reads as "ROW never returns
   anything" -- a false, confident number. `row_returns_recordable()` decides
   this from the data rather than from a hardcoded assumption, so the day the
   source gains ROW returns the columns populate on their own.

3. **The headline rate is orders-based (returned orders / orders, LOCKED),
   which does not survive being cut thin.** At SKU x market x segment grain
   most cells have single-digit order counts, where one return reads as 25%.
   Counts are facts and are always written; the RATE is suppressed below
   `floor` (build.MIN_TRACKER_ORDERS, the same 20-order floor the ranked SKU
   tracker already uses -- not a new threshold invented here).

Exchange convention, carried from build.py §9 unchanged: order and unit
aggregates INCLUDE exchanges (a fit problem is a fit problem), value
aggregates EXCLUDE exchange-attributable value (an exchange retains revenue).
Applied per cut, not just at total.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from returns.build import MIN_TRACKER_ORDERS

# (key, label, filters) -- filters are ANDed. Order is the column order in the
# companion, so `retail` leads and `blended` trails.
CUT_SPECS = [
    ("retail",     "Retail",      {"seg": "Retail"}),
    ("trade",      "Trade",       {"seg": "Trade"}),
    ("uk",         "UK",          {"mkt": "UK"}),
    ("us",         "US",          {"mkt": "US"}),
    ("row",        "ROW",         {"mkt": "ROW"}),
    ("uk_retail",  "UK Retail",   {"mkt": "UK", "seg": "Retail"}),
    ("uk_trade",   "UK Trade",    {"mkt": "UK", "seg": "Trade"}),
    ("us_retail",  "US Retail",   {"mkt": "US", "seg": "Retail"}),
    ("us_trade",   "US Trade",    {"mkt": "US", "seg": "Trade"}),
    ("blended",    "Retail + Trade", {}),
]
CUT_KEYS = [k for k, _, _ in CUT_SPECS]
CUT_LABELS = {k: lbl for k, lbl, _ in CUT_SPECS}

# Cuts whose returns side is unavailable when the source can't record ROW
# returns. ROW has no Retail/Trade cross here on purpose: with no recordable
# ROW returns there is nothing to split, and a sales-only cross would be four
# more columns of noise.
ROW_DEPENDENT_CUTS = ("row",)


def row_returns_recordable(ret):
    """True if this period's returns data contains any ROW row at all.

    False means the ROW cut's returns columns must be omitted rather than
    written as zero -- see rule 2 in the module docstring. Deliberately reads
    the frame instead of hardcoding "ReturnZap can't do ROW": that fact is
    true today and documented in prep(), but it's a property of the source,
    not of this pipeline, and it should not need a code change to stop being
    true.
    """
    if ret is None or len(ret) == 0 or "mkt" not in ret.columns:
        return False
    return bool(ret["mkt"].eq("ROW").any())


def _apply(df, filters):
    for col, val in filters.items():
        df = df[df[col] == val]
    return df


def _value_returned_by_sku(shopv_cut):
    """Stock value returned, net of exchange-attributable value, per SKU --
    the same computation _sku_aggregate() does at total grain, applied to one
    cut. Kept in one place so the cut columns and the existing total column
    can't drift apart.
    """
    d = shopv_cut[shopv_cut.qty != 0]
    if len(d) == 0:
        return {}
    stock = d.groupby("sku")["val"].sum()
    exch = d[d.is_exch_line].groupby("sku")["val"].sum()
    value = stock - exch.reindex(stock.index).fillna(0)
    return value.to_dict()


def _metrics_for_cut(s_cut, ret_cut, shopv_cut, floor):
    """{sku: {metric: value}} for one cut. Every SKU with either sales or
    returns in the cut appears; a SKU with sales and no returns gets zeros on
    the returns side (that IS a real zero -- it sold and nothing came back),
    which is the opposite of the ROW case, where the zero is an artefact of
    the source and must not be written.
    """
    orders = s_cut.groupby("sku")["order"].nunique().to_dict()
    units_sold = s_cut.groupby("sku")["units"].sum().to_dict()
    gross_sales = s_cut.groupby("sku")["cash"].sum().to_dict()
    returned_orders = ret_cut.groupby("sku")["order"].nunique().to_dict()
    units_returned = ret_cut.groupby("sku")["qty"].sum().to_dict()
    value_returned = _value_returned_by_sku(shopv_cut)

    out = {}
    for sku in set(orders) | set(returned_orders) | set(value_returned):
        o = orders.get(sku, 0)
        ro = returned_orders.get(sku, 0)
        gs = gross_sales.get(sku, 0.0)
        vr = value_returned.get(sku, 0.0)
        out[sku] = {
            "orders": o,
            "units_sold": units_sold.get(sku, 0),
            "gross_sales": gs,
            "returned_orders": ro,
            "units_returned": units_returned.get(sku, 0),
            "value_returned": vr,
            # Orders-based rate (LOCKED). None below the floor: the counts
            # either side of this column carry the fact, and a rate off six
            # orders would be read as a rate.
            "return_rate": (ro / o) if (o and o >= floor) else None,
            # Returns cash as a share of the cut's own sales cash. Value-based,
            # so the order floor doesn't apply -- but a zero or negative sales
            # base makes it meaningless.
            "value_rate": (vr / gs) if gs and gs > 0 else None,
            "below_floor": bool(o and o < floor),
        }
    return out


def aggregate(s, ret, shopv, floor=MIN_TRACKER_ORDERS):
    """{sku: {cut_key: metrics}} plus a meta dict describing what's available.

    `floor` is the order count below which a return RATE is withheld. Exposed
    as a parameter rather than baked in so a period can be inspected at a
    lower floor deliberately, but it defaults to the same MIN_TRACKER_ORDERS
    the tracker uses -- one threshold across the returns document.
    """
    recordable = row_returns_recordable(ret)
    per_cut = {}
    for key, _, filters in CUT_SPECS:
        per_cut[key] = _metrics_for_cut(
            _apply(s, filters), _apply(ret, filters), _apply(shopv, filters), floor)

    skus = sorted({sku for cut in per_cut.values() for sku in cut})
    out = {}
    for sku in skus:
        out[sku] = {}
        for key in CUT_KEYS:
            m = per_cut[key].get(sku)
            if m is None:
                continue
            if key in ROW_DEPENDENT_CUTS and not recordable:
                # Keep the sales side (real), drop the returns side (artefact).
                m = {k: v for k, v in m.items()
                     if k in ("orders", "units_sold", "gross_sales")}
            out[sku][key] = m

    meta = {
        "row_returns_recordable": recordable,
        "floor": floor,
        "skus": len(skus),
        "row_note": (
            "ROW returns columns are shown: the returns source carried at least one ROW row "
            "this period." if recordable else
            "ROW returns columns are omitted, not zero: the returns source records only GB and "
            "US, so it cannot represent a ROW return. ROW sales figures ARE real (market is "
            "derived from ship-to country). These columns will appear automatically if the "
            "source ever carries ROW returns."),
    }
    return out, meta


def uplift(current_metrics, prior_metrics, field):
    """Movement in `field` between two cuts' metrics, as a ratio.

    None -- never 0.0 -- when there's no prior period loaded, when the SKU
    didn't trade in that cut last period, when the prior figure is zero, or
    when either side withheld the value (a suppressed rate has nothing to
    move from). Same convention as the trading companion's uplift.
    """
    if not current_metrics or not prior_metrics:
        return None
    cur, prev = current_metrics.get(field), prior_metrics.get(field)
    if cur is None or prev is None or not prev or prev <= 0:
        return None
    return (cur - prev) / prev


def prior_period_aggregate(sales_df, ld_std, returns_df, month_nums, year, floor=MIN_TRACKER_ORDERS):
    """Run the same aggregation over a prior window on the SAME loaded frames.

    Returns (aggregate, reason_unavailable). There is no committed returns
    contract to chain to -- unlike trading, every returns figure is recomputed
    from source each run -- so the only way to get a comparative is to re-run
    prep() on the prior window. That needs the prior period's orders to be
    present in `sales_df`; when they aren't, this returns (None, reason) and
    the writer omits the movement columns rather than printing a -100% drop
    off an absent period, which is the failure mode worth guarding.
    """
    from returns import build
    try:
        s, ret, zap, shopv, months = build.prep(sales_df, ld_std, returns_df, month_nums, year)
    except Exception as e:
        return None, (f"prior period {month_nums}/{year} could not be prepared from the loaded "
                      f"sources ({type(e).__name__}: {e}) -- movement columns omitted")
    if len(s) == 0:
        return None, (f"prior period {month_nums}/{year} has no orders in the loaded sources -- "
                      f"movement columns omitted (load that period's order export to get them)")
    agg, _ = aggregate(s, ret, shopv, floor=floor)
    return agg, None
