"""
Returns builder — LOCKED decisions (Lena + Daisy, Aug 2026) + Q1-2026-review rulings
=====================================================================================
LOCKED (unchanged since the original spike):
1. SINGLE-COUNT : each return counted once (de-duplicated sku+order join).
2. ORDER-MONTH  : every row bucketed by the order's SALE month (earliest sale line).
3. ORDERS-BASED : headline rate = distinct returned orders / distinct orders (both sides).
Units returned (single-counted) and returns cash are secondary detail.

RULED (Q1 2026 review meeting, see BRIEF_returns_dashboard_v2.md §1-§7):
4. Return timing is order/sale-month everywhere (ruling 1) -- already true above;
   January is flagged YoY-only (structurally a Christmas-returns month), never MoM.
5. Return rate is orders-based everywhere (ruling 2) -- already true above.
6. SKU ranking = combined rank of (% orders returned) x (return value), 20-order floor
   (ruling 3 / §2), not unit count alone.
7. Return initiation = when the customer flags it (label created) -- no number change,
   all four Stage buckets already counted (ruling 4); recorded as a decision, not a calc.
8. Refund value = STOCK VALUE ONLY (ruling 5): split from Shopify's own per-line
   qty/value columns (never returned_item_quantity as a totals metric -- see the
   "unreliable" note below -- only its per-row zero/nonzero incidence, which does line
   up with the value column row-for-row). Value-only/no-unit refunds are pulled OUT of
   the headline into an explicit, footnoted bucket (never silently dropped).
9. Exchange = return? DUAL definition (§5.1, LOCKED by Lena): quality/product
   aggregates (order counts, flagged units) INCLUDE exchanges (is_exchange=True rows) --
   a fit/quality signal is still a fit/quality signal. Value aggregates (value_split,
   the tracker's return-value column, the hero) EXCLUDE exchange-attributable value --
   an exchange retains revenue, it isn't lost. Every aggregate below states which
   convention it uses.
10. Trade un-blended (§5.3, LOCKED): headline defaults to RETAIL; trade is computed and
    reported separately; the two are never combined into one blended rate/value.

IMPORTANT -- numbers will NOT match BRIEF_returns_dashboard_v2.md's cited figures
(10.1% / Jan 7.0%-Feb 4.8%-Mar 6.4% / £129,413). Traced and confirmed: those figures
were sanity-checked against the hand-built workbook's own "Orders pivot" tab, which
counts order_id per (month, SKU, country) -- an order with 2 SKUs is counted twice,
inflating the workbook's own order denominator by roughly 2x (a bug already flagged in
docs/returns_spike_findings.md gap #6, "don't rely on an Orders pivot cache"). This
builder counts distinct orders directly from the raw feed, per the LOCKED single-count
decision. The real, structurally-correct numbers for this source file land lower
(quarter order-rate ~7.9%, stock value ~£125k). Per CLAUDE.md's publish gate and the
brief's own §8: match the logic, not the old (or newly-cited) figures.

Blocks produced: by order-month (retail, trade), by status, UK/US split, return-reason
mix + sub-reason detail, by finish, value split (stock/value-only/no-sku), category ->
subcategory -> SKU tracker (combined-rank, 20-order floor), by initiation-stage.
Every block is checked by the reconciliation gate (common/reconciliation_gate.py)
before being returned -- a failed check aborts the run rather than showing a
possibly-wrong number. See ROADMAP.md §5 for the gate contract.

Run:  python returns/build.py [/path/to/Q1_Jan_Feb_Mar_2026.xlsx]

Promoted from returns_builder_v2.py (the proven spike) with the shared sheet-IO
and Line Detail enrichment pulled out to common/, and the reconciliation gate
wired in. Extended for the Q1-2026 review rulings + restructure (D2 rebuild).
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np

from common.io import load_positional_sheets
from common.enrichment import line_detail_lookup
from common.sku_taxonomy import SKUTaxonomy
from common.reconciliation_gate import (
    assert_additive,
    assert_labels_match,
    assert_orders_based_rate,
    assert_bucketed_by,
    assert_no_impossible_rate,
    assert_min_orders_threshold,
    assert_bucket_reported,
)

SRC = sys.argv[1] if len(sys.argv) > 1 else "source/Q1_Jan_Feb_Mar_2026.xlsx"
MONTHS = {1: "Jan", 2: "Feb", 3: "Mar"}
STAT = ["Live", "Discontinued", "Disco to Resource", "Not For Sale"]
ADDITIVE_COLS = ["units_sold", "units_returned"]
MIN_TRACKER_ORDERS = 20  # §2: floor to appear in the ranked SKU tracker

DEFAULT_REASON = "Not right for my project"
FAULT_REASONS = {
    "Disappointed in quality",
    "Finish not as pictured online",
    "Damaged or Defective",
    "Incorrect item sent",
}
FAULT_SUBREASONS = {"Size", "Function"}  # unchanged from the pre-existing quality logic


def prep(src):
    sheets = load_positional_sheets(
        src, {"Shopify Data": "c", "Returns zap": "z", "Line Detail": "l"}
    )
    shop, zap, ld = sheets["Shopify Data"], sheets["Returns zap"], sheets["Line Detail"]

    # SALES lines
    shop["month"] = pd.to_datetime(shop["c1"], errors="coerce")
    s = shop[shop["c7"].notna() & shop["month"].notna()].copy()
    s["sku"] = s["c7"].astype(str).str.strip()
    s["order"] = s["c2"].astype("int64").astype(str)
    s["units"] = pd.to_numeric(s["c10"], errors="coerce").fillna(0)
    s["cash"] = pd.to_numeric(s["c8"], errors="coerce").fillna(0)
    s["mkt"] = s["c0"]
    s["seg"] = np.where(pd.to_numeric(s["c3"], errors="coerce").fillna(0) > 0, "Trade", "Retail")

    # ORDER MONTH = earliest sale line (units>0); fallback earliest line
    om = s[s.units > 0].groupby("order")["month"].min().combine_first(
        s.groupby("order")["month"].min()
    )
    s["omonth"] = s["order"].map(om)
    s = s[(s.omonth.dt.year == 2026) & (s.omonth.dt.month.isin(MONTHS))]
    s["m"] = s.omonth.dt.month

    # RETURNS single-count, attach order-month via the order's home
    zap = zap[zap["z10"].notna() & zap["z1"].notna()].copy()
    zap["sku"] = zap["z10"].astype(str).str.strip()
    zap["order"] = zap["z1"].astype("int64").astype(str)
    zap["qty"] = pd.to_numeric(zap["z14"], errors="coerce").fillna(0)
    zap["reason"] = zap["z16"].astype(str).str.strip()
    zap["subreason"] = zap["z32"].fillna("(none)").astype(str).str.strip().replace({"": "(none)"})
    zap["stage"] = zap["z9"].astype(str).str.strip()
    zap["is_exchange"] = zap["z15"].astype(str).str.strip().str.upper() == "EXCHANGE"
    zap["omonth"] = zap["order"].map(om)
    zap = zap[(zap.omonth.dt.year == 2026) & (zap.omonth.dt.month.isin(MONTHS))]

    # single-count join: one row per (sku, order); is_exchange/stage carried through as
    # "did ANY zap row for this sku+order say so" (a return event doesn't split types)
    ret = zap.groupby(["sku", "order"], as_index=False).agg(
        qty=("qty", "sum"),
        is_exchange=("is_exchange", "any"),
        reason=("reason", "first"),
        subreason=("subreason", "first"),
        stage=("stage", "first"),
    )
    home = s.drop_duplicates("order").set_index("order")[["mkt", "m", "seg"]]
    ret = ret.join(home, on="order", how="inner")
    # zap (raw, pre-single-count) also gets mkt/seg -- reason_mix/reason_detail's
    # counting basis is unchanged (still raw zap, matching the pre-existing
    # behaviour), but the render payload needs it filterable by region/segment too
    zap = zap.join(home, on="order", how="inner")

    # enrich (status + finish, from Line Detail, via the shared lookup)
    lookup = line_detail_lookup(ld, "l0", {"status": "l1", "finish": "l8"})
    for col in ("status", "finish"):
        s[col] = s["sku"].map(lookup[col])
        ret[col] = ret["sku"].map(lookup[col])

    # category/subcategory/family via the shared taxonomy (glossary §5's canonical
    # tree, RECOMMENDED_DRILL = item_type/style; l4/l5 confirmed by inspection to be
    # Product Category/Sub Category, i.e. item_type/style-grain -- see module docstring
    # for the label-reversal gotcha this handles)
    ld_map = {}
    for _, row in ld.iterrows():
        sku = str(row["l0"]).strip()
        cat, sub = str(row.get("l4", "") or "").strip(), str(row.get("l5", "") or "").strip()
        if sku and (cat or sub):
            ld_map[sku] = (cat, sub)
    tax = SKUTaxonomy(line_detail_map=ld_map, seed=_seed())
    for df in (s, ret, zap):
        classified = df["sku"].map(tax.classify)
        df["category"] = classified.map(lambda t: t.item_type)
        df["subcategory"] = classified.map(lambda t: t.style)
        df["family"] = df["sku"].map(tax.family_of)

    # STOCK-VALUE-ONLY refund split (ruling 5): from Shopify's own per-line qty/value
    # columns, never from a cross-source (sku,order) match against the zap join --
    # ROADMAP already documents c11 (returned_item_quantity) as unreliable as a TOTALS
    # metric (sums to a nonsense negative); but its per-row zero/nonzero incidence lines
    # up with the value column row-for-row (confirmed empirically: 1772/1786 agree), so
    # it's used here only as a row classifier, never as a units headline.
    shopv = shop.copy()
    shopv["sku"] = shopv["c7"].astype(str).str.strip()
    shopv["order"] = shopv["c2"].astype("Int64").astype(str)
    shopv["qty"] = pd.to_numeric(shopv["c11"], errors="coerce").fillna(0)
    shopv["val"] = pd.to_numeric(shopv["c9"], errors="coerce").fillna(0)
    shopv = shopv.join(home, on="order", how="inner")
    shopv["has_sku"] = shopv["c7"].notna()

    # exchange-value estimate to net out of stock value (§5.1): matched zap EXCHANGE
    # (sku,order) pairs against Shopify's own refund-$ lines. Match rate isn't perfect
    # (a swap can land on a different variant line) -- this is an approximation, noted
    # as such, not a hard assert.
    exch_keys = set(zip(ret.loc[ret.is_exchange, "sku"], ret.loc[ret.is_exchange, "order"]))
    shopv["is_exch_line"] = list(zip(shopv["sku"], shopv["order"]))
    shopv["is_exch_line"] = shopv["is_exch_line"].isin(exch_keys)

    return s, ret, zap, shopv


def _seed():
    from common.sku_taxonomy import DEFAULT_SEED
    with open(DEFAULT_SEED, encoding="utf-8") as fh:
        return json.load(fh)


def _rate(sdf, rdf):
    o = sdf["order"].nunique()
    r = rdf["order"].nunique()
    return pd.Series(
        {
            "orders": o,
            "returned_orders": r,
            "return_rate": (r / o if o else 0),
            "units_sold": sdf["units"].sum(),
            "units_returned": rdf["qty"].sum(),
        }
    )


def by_month(s, ret):
    rows = {MONTHS[m]: _rate(s[s.m == m], ret[ret.m == m]) for m in MONTHS}
    rows["Quarter"] = _rate(s, ret)
    block = pd.DataFrame(rows).T

    assert_bucketed_by(block.index, list(MONTHS.values()) + ["Quarter"])
    assert_additive(block, ADDITIVE_COLS, list(MONTHS.values()), "Quarter")
    assert_orders_based_rate(block)
    assert_no_impossible_rate(block)
    return block


def by_group(s, ret, col, keys):
    rows = {g: _rate(s[s[col] == g], ret[ret[col] == g]) for g in keys}
    rows["Total"] = _rate(s[s[col].isin(keys)], ret[ret[col].isin(keys)])
    block = pd.DataFrame(rows).T

    assert_labels_match(keys, s[col].dropna().unique())
    assert_additive(block, ADDITIVE_COLS, keys, "Total")
    assert_orders_based_rate(block)
    assert_no_impossible_rate(block)
    return block


def reason_mix(zap):
    r = zap.groupby("reason")["qty"].sum().sort_values(ascending=False)
    return pd.DataFrame({"units_returned": r, "share": r / r.sum()})


def reason_detail(zap):
    """Sub-reason (free-text follow-up) coverage -- collected on ONE dropdown option
    (the default reason) only. §3/§4: only 9.8% of all returns name a fault outright;
    of the default-reason rows, 63% left a follow-up -- that follow-up is the real
    signal the top-level reason mix hides.
    """
    default_mask = zap["reason"] == DEFAULT_REASON
    sub = zap.loc[default_mask & (zap["subreason"] != "(none)"), "subreason"]
    by_sub = sub.value_counts()
    def_total = int(default_mask.sum())
    def_with_sub = int(len(sub))
    fault_outright = int(zap["reason"].isin(FAULT_REASONS).sum())
    return {
        "by_subreason": by_sub.to_dict(),
        "default_total": def_total,
        "default_with_subreason": def_with_sub,
        "default_coverage": (def_with_sub / def_total if def_total else 0),
        "fault_outright": fault_outright,
        "fault_outright_share": fault_outright / len(zap) if len(zap) else 0,
        "total_units": int(len(zap)),
    }


def by_stage(zap):
    """Return-initiation stage mix (ruling 4): a recorded decision (initiation = when
    the customer flags it / label created), not a metric that changes any number --
    all four stages were already counted. Reported here so the notes can cite real
    figures instead of hardcoded prose.
    """
    r = zap["stage"].value_counts()
    return pd.DataFrame({"count": r, "share": r / r.sum()})


def value_split(shopv, seg=None):
    """Stock-value-only refund vs. value-only(no-unit) refund (ruling 5), exchange-
    value netted out of the stock figure (§5.1 -- value aggregates exclude exchanges).
    No-SKU sub-bucket surfaced explicitly, never absorbed (§3).

    seg: None (all), "Retail", or "Trade" -- headline is Retail-only, never blended
    with Trade (§5.3); this function computes whichever slice is asked for.
    """
    d = shopv if seg is None else shopv[shopv["seg"] == seg]
    stock_all = d.loc[d.qty != 0, "val"].sum()
    exch_val = d.loc[d.is_exch_line & (d.qty != 0), "val"].sum()
    stock = stock_all - exch_val  # exchanges netted out: revenue retained, not lost
    value_only = d.loc[(d.qty == 0) & (d.val != 0), "val"].sum()

    nosku = ~d["has_sku"]
    nosku_stock = d.loc[nosku & (d.qty != 0), "val"].sum()
    nosku_value_only = d.loc[nosku & (d.qty == 0) & (d.val != 0), "val"].sum()

    gross = pd.to_numeric(d["c8"], errors="coerce").fillna(0).sum()
    out = {
        "stock_value": abs(round(stock, 2)),
        "value_only": abs(round(value_only, 2)),
        "exchange_value_netted": abs(round(exch_val, 2)),
        "no_sku_stock": abs(round(nosku_stock, 2)),
        "no_sku_value_only": abs(round(nosku_value_only, 2)),
        "no_sku_total": abs(round(nosku_stock + nosku_value_only, 2)),
        "gross_sales": round(gross, 2),
        "stock_value_rate": (abs(stock) / gross if gross else 0),
    }
    for label in ("stock_value", "value_only", "no_sku_total"):
        assert_bucket_reported(out[label], label)
    return out


def _sku_return_value(shopv):
    """Per-SKU stock-linked refund value, exchange-attributable value netted out --
    same methodology as value_split(), just grouped by sku instead of totalled.
    """
    d = shopv[shopv.qty != 0]
    exch = d[d.is_exch_line].groupby("sku")["val"].sum()
    stock = d.groupby("sku")["val"].sum()
    net = (stock - exch.reindex(stock.index).fillna(0)).abs()
    return net


def tracker(s, ret, shopv, min_orders=MIN_TRACKER_ORDERS):
    """Category -> subcategory -> SKU investigation surface (§2 restructure --
    replaces the old separate quality watchlist). Combined rank = sum of dense ranks
    of (% orders returned) and (return value), among SKUs clearing the order floor.

    Order counts (quality convention) INCLUDE exchanges, since ret is the unfiltered
    single-count join. Return value (value convention) EXCLUDES exchange-attributable
    value, via the same per-SKU exchange match value_split() uses at the aggregate level.
    """
    sku_sales = s.groupby(["category", "subcategory", "family", "sku"]).agg(
        units_sold=("units", "sum"),
        gross_sales=("cash", "sum"),
        orders=("order", "nunique"),
    )
    ret_orders = ret.groupby("sku").agg(
        returned_orders=("order", "nunique"),
        units_returned=("qty", "sum"),
    )
    ret_top_order = ret.groupby("sku").apply(
        lambda g: g.groupby("order")["qty"].sum().max() if len(g) else 0
    )
    dominant_reason = ret.groupby("sku")["reason"].agg(
        lambda x: x.value_counts().index[0] if len(x) else ""
    )
    return_value = _sku_return_value(shopv)

    rows = sku_sales.join(ret_orders, on="sku").fillna({"returned_orders": 0, "units_returned": 0})
    rows["dominant_reason"] = rows.index.get_level_values("sku").map(dominant_reason).fillna("")
    rows["top_order_units"] = rows.index.get_level_values("sku").map(ret_top_order).fillna(0)
    rows["return_value"] = rows.index.get_level_values("sku").map(return_value).fillna(0)
    rows["pct_orders_returned"] = np.where(
        rows["orders"] > 0, rows["returned_orders"] / rows["orders"], 0
    )

    eligible = rows[rows["orders"] >= min_orders].copy()
    assert_min_orders_threshold(
        {sku: o for sku, o in zip(eligible.index.get_level_values("sku"), eligible["orders"])},
        "orders",
        min_orders,
    )
    assert_no_impossible_rate(
        eligible.rename(columns={"pct_orders_returned": "return_rate"}), rate_cols=("return_rate",)
    )

    eligible["rank_pct"] = eligible["pct_orders_returned"].rank(method="dense", ascending=False)
    eligible["rank_value"] = eligible["return_value"].rank(method="dense", ascending=False)
    eligible["combined_rank"] = eligible["rank_pct"] + eligible["rank_value"]
    eligible = eligible.sort_values("combined_rank")
    return eligible


def run(src):
    """Build every block and pass it through the reconciliation gate.

    Raw (unformatted) DataFrames/dicts -- this is the entry point both the CLI
    display below and the regression fixture/test use, so the fixture
    reflects exactly the numbers the gate has already checked.
    """
    s, ret, zap, shopv = prep(src)
    fins = s["finish"].value_counts().index[:10].tolist()

    s_retail, ret_retail = s[s.seg == "Retail"], ret[ret.seg == "Retail"]
    s_trade, ret_trade = s[s.seg == "Trade"], ret[ret.seg == "Trade"]

    return {
        "by_month": by_month(s_retail, ret_retail),   # HEADLINE -- retail-only, never blended (§5.3)
        "by_month_trade": by_month(s_trade, ret_trade),
        "by_month_blended": by_month(s, ret),          # reported for transparency only, never headlined
        "by_status": by_group(s, ret, "status", STAT),
        "by_market": by_group(s, ret, "mkt", ["UK", "US"]),
        "reason_mix": reason_mix(zap),
        "reason_detail": reason_detail(zap),
        "by_stage": by_stage(zap),
        "by_finish": by_group(s, ret, "finish", fins),
        "value_split": value_split(shopv),             # retail+trade combined value-only view -- see note below
        "value_split_retail": value_split(shopv, "Retail"),
        "value_split_trade": value_split(shopv, "Trade"),
        "tracker": tracker(s, ret, shopv),
    }


def _show(df, pct=("return_rate",)):
    d = df.copy()
    for c in d.columns:
        if c in pct or c == "share":
            d[c] = (d[c] * 100).map(lambda v: f"{v:.2f}%")
        else:
            d[c] = d[c].map(lambda v: f"{v:,.0f}")
    print(d.to_string())


if __name__ == "__main__":
    blocks = run(SRC)

    print("=== By ORDER MONTH -- RETAIL headline (orders-based rate, single-count) ===")
    _show(blocks["by_month"])

    print("\n=== By ORDER MONTH -- Trade (reported separately, never blended) ===")
    _show(blocks["by_month_trade"])

    print("\n=== By product STATUS ===")
    _show(blocks["by_status"])

    print("\n=== UK / US split ===")
    _show(blocks["by_market"])

    print("\n=== Return REASON mix (share of returned units) ===")
    _show(blocks["reason_mix"], pct=("share",))

    rd = blocks["reason_detail"]
    print(f"\nFault named outright: {rd['fault_outright']} / {rd['total_units']} "
          f"({rd['fault_outright_share']:.1%}) -- \"~90%\" claim is really "
          f"\"Not right for my project\" alone at {rd['default_total']}/{rd['total_units']} "
          f"({rd['default_total']/rd['total_units']:.1%})")
    print(f"Default-reason free-text follow-up: {rd['default_with_subreason']}/{rd['default_total']} "
          f"({rd['default_coverage']:.1%}) filled it in -- {rd['by_subreason']}")

    print("\n=== Return initiation stage (recorded decision, no calc change) ===")
    _show(blocks["by_stage"], pct=("share",))

    print("\n=== By FINISH (top 10 by orders) ===")
    _show(blocks["by_finish"])

    vs = blocks["value_split"]
    print(f"\n=== Value split (stock-only headline, ruling 5; exchanges netted out) ===")
    print(f"Stock value: £{vs['stock_value']:,.0f} ({vs['stock_value_rate']:.1%} of gross) "
          f"| value-only (no unit): £{vs['value_only']:,.0f} (footnote, out of headline) "
          f"| no-SKU bucket: £{vs['no_sku_total']:,.0f}")

    tr = blocks["tracker"]
    print(f"\n=== Category/subcategory/SKU tracker: top 10 by combined rank ({MIN_TRACKER_ORDERS}-order floor) ===")
    print(tr.head(10)[["units_sold", "orders", "returned_orders", "pct_orders_returned",
                        "return_value", "dominant_reason", "combined_rank"]].to_string())

    print(
        "\nreconciliation gate: PASS (additive measures reconcile; labels matched; "
        "headline is orders-based; order-month bucketed; no impossible rates; "
        "20-order floor enforced; no-SKU bucket reported)"
    )
    print(
        "Basis: order-month · orders-based · single-count · retail-default (trade separate, "
        "never blended) · stock-value-only headline · exchanges counted in quality, excluded "
        "from value. Status/finish ORDER counts are distinct and do not sum to Total (an order "
        "can span groups). Recent months still maturing; January is YoY-only, never MoM."
    )
