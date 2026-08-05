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
   the January-specific "Christmas returns" framing (§1 of the brief) is Q1-only
   copy, handled in the template, not this builder.
5. Return rate is orders-based everywhere (ruling 2) -- already true above.
6. SKU ranking = combined rank of (% orders returned) x (return value), 20-order floor
   (ruling 3 / §2), not unit count alone.
7. Return initiation = when the customer flags it (label created) -- no number change,
   all four Stage buckets already counted (ruling 4); recorded as a decision, not a calc.
8. Refund value = STOCK VALUE ONLY (ruling 5): split from the sales source's own
   per-line refund qty/value columns (never a returned-quantity TOTAL as a headline --
   see load_workbook_sales()'s note -- only its per-row zero/nonzero incidence).
   Value-only/no-unit refunds are pulled OUT of the headline into an explicit,
   footnoted bucket (never silently dropped).
9. Exchange = return? DUAL definition (§5.1, LOCKED by Lena): quality/product
   aggregates (order counts, flagged units) INCLUDE exchanges (is_exchange=True rows) --
   a fit/quality signal is still a fit/quality signal. Value aggregates (value_split,
   the tracker's return-value column, the hero) EXCLUDE exchange-attributable value --
   an exchange retains revenue, it isn't lost. Every aggregate below states which
   convention it uses.
10. Trade un-blended (§5.3, LOCKED): headline defaults to RETAIL; trade is computed and
    reported separately; the two are never combined into one blended rate/value.

MULTI-SOURCE (2026-08-05): the sales side and the returns side are loaded
independently and joined only by order id, so any period can be built by pairing
whichever sales source covers it with a rolling returns-app export (a .csv or
.numbers, e.g. source/ytd_returns_2.numbers, not scoped to one quarter -- see
load_returns_export()):
  - Q1 2026: load_workbook_sales() -- the single Q1_Jan_Feb_Mar_2026.xlsx workbook
    (Shopify Data + Line Detail sheets).
  - Q2 2026 (and beyond): load_matrixify_sales() + load_line_detail_file() --
    per-month Matrixify order exports (same source trading/ already uses) plus the
    standalone trading/source/line_detail.xlsx catalog.
Whichever pair is used, prep()/run() take the already-loaded (sales_df, ld_std,
returns_df) frames and month_nums/year -- they don't know or care which loader
produced them.

IMPORTANT -- numbers will NOT match BRIEF_returns_dashboard_v2.md's cited Q1 figures
(10.1% / Jan 7.0%-Feb 4.8%-Mar 6.4% / £129,413). Traced and confirmed: those figures
were sanity-checked against the hand-built workbook's own "Orders pivot" tab, which
counts order_id per (month, SKU, country) -- an order with 2 SKUs is counted twice,
inflating the workbook's own order denominator by roughly 2x (a bug already flagged in
docs/returns_spike_findings.md gap #6, "don't rely on an Orders pivot cache"). This
builder counts distinct orders directly from the raw feed, per the LOCKED single-count
decision. Per CLAUDE.md's publish gate and the brief's own §8: match the logic, not
the old (or newly-cited) figures.

Blocks produced: by order-month (retail, trade), by status, UK/US split, return-reason
mix + sub-reason detail, by finish, value split (stock/value-only/no-sku), category ->
subcategory -> SKU tracker (combined-rank, 20-order floor), by initiation-stage.
Every block is checked by the reconciliation gate (common/reconciliation_gate.py)
before being returned -- a failed check aborts the run rather than showing a
possibly-wrong number. See ROADMAP.md §5 for the gate contract.

Run:  python returns/build.py [/path/to/Q1_Jan_Feb_Mar_2026.xlsx] [/path/to/returns_export]

Promoted from returns_builder_v2.py (the proven spike) with the shared sheet-IO
and Line Detail enrichment pulled out to common/, and the reconciliation gate
wired in. Extended for the Q1-2026 review rulings + restructure (D2 rebuild), then
generalized to multiple periods/sources (2026-08-05).
"""
import sys
import os
import json
import calendar

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np

from common.io import load_positional_sheets
from common.sku_taxonomy import SKUTaxonomy, DEFAULT_SEED
from common.reconciliation_gate import (
    assert_additive,
    assert_labels_match,
    assert_orders_based_rate,
    assert_bucketed_by,
    assert_no_impossible_rate,
    assert_min_orders_threshold,
    assert_bucket_reported,
    assert_returns_overlap_sales,
)

DEFAULT_MONTH_NUMS = [1, 2, 3]
DEFAULT_YEAR = 2026
MONTHS = {m: calendar.month_abbr[m] for m in DEFAULT_MONTH_NUMS}  # Q1 default, kept for back-compat
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

SRC = sys.argv[1] if len(sys.argv) > 1 else "source/Q1_Jan_Feb_Mar_2026.xlsx"
RETURNS_SRC = sys.argv[2] if len(sys.argv) > 2 else "source/ytd_returns_2.numbers"


# ---------------------------------------------------------------------------
# Sales loaders -- each returns a standardized DataFrame: order, sku, name
# (product title/display name), mkt, seg, created (order-created timestamp),
# units, cash, refund_qty, refund_val.
# ---------------------------------------------------------------------------

def load_workbook_sales(xlsx_path):
    """Q1-style single workbook: Shopify Data (sales) + Line Detail (taxonomy),
    positional columns (header text is inconsistent/duplicated across tabs).
    Returns (sales_df, ld_std).
    """
    sheets = load_positional_sheets(xlsx_path, {"Shopify Data": "c", "Line Detail": "l"})
    shop, ld = sheets["Shopify Data"], sheets["Line Detail"]

    sales_df = pd.DataFrame({
        "order": shop["c2"].astype("Int64").astype(str),
        "sku": shop["c7"].astype(str).str.strip().replace({"nan": None}),
        "name": shop["c5"],
        "mkt": shop["c0"],
        "seg": np.where(pd.to_numeric(shop["c3"], errors="coerce").fillna(0) > 0, "Trade", "Retail"),
        "created": pd.to_datetime(shop["c1"], errors="coerce"),
        "units": pd.to_numeric(shop["c10"], errors="coerce").fillna(0),
        "cash": pd.to_numeric(shop["c8"], errors="coerce").fillna(0),
        # refund_qty/refund_val: Shopify's own per-line return fields. ROADMAP already
        # documents the QUANTITY side as unreliable as a TOTALS metric (sums to a
        # nonsense negative); but its per-row zero/nonzero incidence lines up with the
        # value column row-for-row (confirmed empirically: 1772/1786 agree), so it's
        # used only as a row classifier in value_split(), never as a units headline.
        "refund_qty": pd.to_numeric(shop["c11"], errors="coerce").fillna(0),
        "refund_val": pd.to_numeric(shop["c9"], errors="coerce").fillna(0),
    })
    sales_df.loc[sales_df["sku"].isna() | (sales_df["sku"] == ""), "sku"] = None

    ld_std = pd.DataFrame({
        "sku": ld["l0"].astype(str).str.strip(),
        "status": ld["l1"],
        "finish": ld["l8"],
        "category": ld["l4"].astype(str).str.strip(),      # Product Category = item_type-grain
        "subcategory": ld["l5"].astype(str).str.strip(),   # Sub Category = style-grain
    })
    return sales_df, ld_std


def load_line_detail_file(xlsx_path):
    """Standalone Line Detail catalog (trading/source/line_detail.xlsx) -- named
    headers already, no positional trickery needed. Not period-specific: the same
    file works for any quarter built against it.
    """
    df = pd.read_excel(xlsx_path)
    return pd.DataFrame({
        "sku": df["SKU"].astype(str).str.strip(),
        "status": df["UK Status"],
        "finish": df["Finish"],
        "category": df["Product Category"].astype(str).str.strip(),
        "subcategory": df["Sub Category"].astype(str).str.strip(),
    })


def load_matrixify_sales(sources):
    """sources: iterable of (country, csv_path) pairs, e.g.
    [("UK","trading/source/orders_2026-04_UK.csv"), ("US","trading/source/orders_2026-04_US.csv"), ...]
    -- one or more months per country, concatenated; prep()'s own order-month cohort
    logic (not each file's own boundary) decides which order lands in which month.

    Row structure confirmed in trading/matrixify_source.py: a refund shares the SAME
    `Line: ID` as its original `Line Item` row, as one or more `Refund Line` rows with
    NEGATIVE Quantity/Total (one row per unit refunded). Trade/retail follows the same
    convention trading/build_matrixify.py uses: B2B if a Company is present, else D2C.
    This is a returns-specific adapter (not a reuse of matrixify_source.build_lines(),
    which doesn't expose refunded quantity) -- it only borrows that module's raw CSV
    reader and datetime parsing.
    """
    trading_dir = os.path.join(os.path.dirname(__file__), "..", "trading")
    if trading_dir not in sys.path:
        sys.path.insert(0, trading_dir)
    from matrixify_source import load_rows, _num, _parse_dt

    records = []
    orphan_refunds = 0
    for country, path in sources:
        rows = load_rows(path)
        by_line, orders_meta = {}, {}
        for row in rows:
            name = row["Name"]
            if row.get("Top Row", "").lower() == "true":
                company = (row.get("Company: Name") or row.get("Billing: Company")
                           or row.get("Shipping: Company") or None)
                orders_meta[name] = {
                    "order_id": row.get("ID"),
                    "created_at": row.get("Created At"),
                    "company": company,
                }
            line_type = row.get("Line: Type")
            if line_type in ("Line Item", "Refund Line"):
                line_id = row.get("Line: ID")
                if line_id:
                    by_line.setdefault((name, line_id), []).append(row)

        for (name, _line_id), line_rows in by_line.items():
            original = next((r for r in line_rows if r["Line: Type"] == "Line Item"), None)
            if original is None:
                orphan_refunds += 1  # refund whose sale line isn't in this export window
                continue
            refunds = [r for r in line_rows if r["Line: Type"] == "Refund Line"]
            meta = orders_meta.get(name, {})
            created_raw = meta.get("created_at")
            created = _parse_dt(created_raw) if created_raw else None
            sku = (original.get("Line: SKU") or "").strip() or None
            records.append({
                "order": str(meta.get("order_id") or ""),
                "sku": sku,
                "name": original.get("Line: Title"),
                "mkt": country,
                "seg": "Trade" if meta.get("company") else "Retail",
                "created": pd.Timestamp(created) if created is not None else pd.NaT,
                "units": _num(original.get("Line: Quantity")),
                "cash": _num(original.get("Line: Total")),
                "refund_qty": -sum(_num(r.get("Line: Quantity")) for r in refunds),
                "refund_val": -sum(_num(r.get("Line: Total")) for r in refunds),
            })

    if orphan_refunds:
        print(f"load_matrixify_sales: skipped {orphan_refunds} refund row(s) whose "
              f"original Line Item isn't in the given export window", file=sys.stderr)

    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    # order-created timestamps carry an explicit UTC offset (Matrixify's own format);
    # drop the offset info for simple year/month comparisons downstream, matching how
    # trading buckets by calendar month rather than chasing a display timezone here.
    df["created"] = pd.to_datetime(df["created"], utc=True).dt.tz_localize(None)
    return df


def _read_returns_table(path):
    """Read a returns-app export as a DataFrame, .csv or .numbers (mirrors
    reviews/review_feedback.py's iter_rows(), which handles the same two formats
    for the Yotpo export).
    """
    if os.path.splitext(path)[1].lower() == ".numbers":
        from numbers_parser import Document
        table = Document(path).sheets[0].tables[0]
        rows = table.rows(values_only=True)
        return pd.DataFrame(rows[1:], columns=rows[0])
    return pd.read_csv(path, encoding="utf-8-sig")


def load_returns_export(path):
    """A rolling returns-app export (Order Id, Stage, SKU, Quantity, Return Type,
    Return Reason, Please tell us why, ...), not scoped to any one quarter -- .csv
    or .numbers. No Country column -- unneeded, since mkt/seg are always sourced
    from the sales side via the order join (see prep()), never from the returns
    side. Same column shape as the Q1 workbook's "Returns zap" tab, confirmed
    against a real export; Order Id is the same Shopify-internal numeric id space
    as both the workbook's Shopify Data sheet and Matrixify's own "ID" column, so
    it joins cleanly against either sales source.
    """
    df = _read_returns_table(path)
    return pd.DataFrame({
        "order": pd.array(df["Order Id"], dtype="Int64").astype(str),
        "sku": df["SKU"].astype(str).str.strip(),
        "qty": pd.to_numeric(df["Quantity"], errors="coerce").fillna(0),
        "reason": df["Return Reason"].astype(str).str.strip(),
        "subreason": df["Please tell us why"].fillna("(none)").astype(str).str.strip().replace({"": "(none)"}),
        "stage": df["Stage"].astype(str).str.strip(),
        "is_exchange": df["Return Type"].astype(str).str.strip().str.upper() == "EXCHANGE",
    })


def _seed():
    with open(DEFAULT_SEED, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Core prep/aggregation -- source-agnostic once sales_df/ld_std/returns_df exist.
# ---------------------------------------------------------------------------

def prep(sales_df, ld_std, returns_df, month_nums=None, year=DEFAULT_YEAR):
    month_nums = list(month_nums or DEFAULT_MONTH_NUMS)
    months = {m: calendar.month_abbr[m] for m in month_nums}

    s = sales_df[sales_df["sku"].notna() & sales_df["created"].notna()].copy()

    # ORDER MONTH = earliest sale line (units>0); fallback earliest line
    om = s[s.units > 0].groupby("order")["created"].min().combine_first(
        s.groupby("order")["created"].min()
    )
    s["omonth"] = s["order"].map(om)
    s = s[(s.omonth.dt.year == year) & (s.omonth.dt.month.isin(month_nums))].copy()
    s["m"] = s["omonth"].dt.month.map(months)

    home = s.drop_duplicates("order").set_index("order")[["mkt", "m", "seg"]]

    # RETURNS single-count, attach order-month via the order's home (inner join also
    # restricts returns to orders actually in this period's cohort)
    zap = returns_df[returns_df["sku"].notna() & returns_df["order"].notna()].copy()
    zap = zap.join(home, on="order", how="inner")

    ret = zap.groupby(["sku", "order"], as_index=False).agg(
        qty=("qty", "sum"),
        is_exchange=("is_exchange", "any"),
        reason=("reason", "first"),
        subreason=("subreason", "first"),
        stage=("stage", "first"),
    )
    ret = ret.join(home, on="order", how="inner")
    assert_returns_overlap_sales(ret["order"].nunique(), s["order"].nunique())

    # enrich (status + finish, from Line Detail)
    status_lookup = dict(zip(ld_std["sku"], ld_std["status"]))
    finish_lookup = dict(zip(ld_std["sku"], ld_std["finish"]))
    for df in (s, ret):
        df["status"] = df["sku"].map(status_lookup)
        df["finish"] = df["sku"].map(finish_lookup)

    # category/subcategory/family via the shared taxonomy (glossary §5's canonical
    # tree, RECOMMENDED_DRILL = item_type/style)
    ld_map = {}
    for row in ld_std.itertuples(index=False):
        if row.sku and (row.category or row.subcategory):
            ld_map[row.sku] = (row.category, row.subcategory)
    tax = SKUTaxonomy(line_detail_map=ld_map, seed=_seed())
    for df in (s, ret, zap):
        classified = df["sku"].map(tax.classify)
        df["category"] = classified.map(lambda t: t.item_type)
        df["subcategory"] = classified.map(lambda t: t.style)
        df["family"] = df["sku"].map(tax.family_of)

    # STOCK-VALUE-ONLY refund split inputs (ruling 5): reuses the sales source's own
    # refund_qty/refund_val columns -- see load_workbook_sales()'s note on why the
    # quantity side is only ever used as a row classifier, never a units headline.
    shopv = sales_df.copy()
    # shopv already carries its own mkt/seg (same sales_df as `s`) -- only pull the
    # order-month label from `home`, which also restricts shopv to in-cohort orders.
    shopv = shopv.join(home[["m"]], on="order", how="inner")
    shopv["has_sku"] = shopv["sku"].notna()
    shopv["qty"] = shopv["refund_qty"]
    shopv["val"] = shopv["refund_val"]

    # exchange-value estimate to net out of stock value (§5.1): matched zap EXCHANGE
    # (sku,order) pairs against the sales source's own refund-$ lines. Match rate
    # isn't perfect (a swap can land on a different variant line) -- an approximation,
    # noted as such, not a hard assert.
    exch_keys = set(zip(ret.loc[ret.is_exchange, "sku"], ret.loc[ret.is_exchange, "order"]))
    shopv["is_exch_line"] = list(zip(shopv["sku"], shopv["order"]))
    shopv["is_exch_line"] = shopv["is_exch_line"].isin(exch_keys)

    return s, ret, zap, shopv, months


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


def by_month(s, ret, months):
    rows = {months[m]: _rate(s[s.m == months[m]], ret[ret.m == months[m]]) for m in months}
    rows["Quarter"] = _rate(s, ret)
    block = pd.DataFrame(rows).T

    assert_bucketed_by(block.index, list(months.values()) + ["Quarter"])
    assert_additive(block, ADDITIVE_COLS, list(months.values()), "Quarter")
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
    (the default reason) only. §3/§4: only a small share of all returns name a fault
    outright; of the default-reason rows, roughly six in ten leave a follow-up --
    that follow-up is the real signal the top-level reason mix hides.
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

    gross = pd.to_numeric(d["cash"], errors="coerce").fillna(0).sum()
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


def run(sales_df, ld_std, returns_df, month_nums=None, year=DEFAULT_YEAR):
    """Build every block and pass it through the reconciliation gate.

    Raw (unformatted) DataFrames/dicts -- this is the entry point both the CLI
    display below and the regression fixture/test use, so the fixture
    reflects exactly the numbers the gate has already checked.
    """
    s, ret, zap, shopv, months = prep(sales_df, ld_std, returns_df, month_nums, year)
    fins = s["finish"].value_counts().index[:10].tolist()

    s_retail, ret_retail = s[s.seg == "Retail"], ret[ret.seg == "Retail"]
    s_trade, ret_trade = s[s.seg == "Trade"], ret[ret.seg == "Trade"]

    return {
        "by_month": by_month(s_retail, ret_retail, months),   # HEADLINE -- retail-only, never blended (§5.3)
        "by_month_trade": by_month(s_trade, ret_trade, months),
        "by_month_blended": by_month(s, ret, months),          # reported for transparency only, never headlined
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
    sales_df, ld_std = load_workbook_sales(SRC)
    returns_df = load_returns_export(RETURNS_SRC)
    blocks = run(sales_df, ld_std, returns_df)

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
        "can span groups). Recent months still maturing."
    )
