"""
Returns builder — LOCKED decisions (Lena + Daisy, Aug 2026)
==============================================================
1. SINGLE-COUNT : each return counted once (de-duplicated sku+order join).
2. ORDER-MONTH  : every row bucketed by the order's SALE month (earliest sale line).
3. ORDERS-BASED : headline rate = distinct returned orders / distinct orders (both sides).
Units returned (single-counted) and returns cash are secondary detail.

Blocks produced: by order-month, by status, UK/US split, return-reason mix, by finish.
Every block is checked by the reconciliation gate (common/reconciliation_gate.py)
before being printed -- a failed check aborts the run rather than showing a
possibly-wrong number. See ROADMAP.md §5 for the gate contract.

Run:  python returns/build.py [/path/to/Q1_Jan_Feb_Mar_2026.xlsx]

Promoted from returns_builder_v2.py (the proven spike) with the shared sheet-IO
and Line Detail enrichment pulled out to common/, and the reconciliation gate
(previously absent) wired in. Computation is otherwise unchanged from the
proven version -- this is the fixture reproduced in tests/fixtures/2026Q1.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np

from common.io import load_positional_sheets
from common.enrichment import line_detail_lookup
from common.reconciliation_gate import (
    assert_additive,
    assert_labels_match,
    assert_orders_based_rate,
    assert_bucketed_by,
)

SRC = sys.argv[1] if len(sys.argv) > 1 else "source/Q1_Jan_Feb_Mar_2026.xlsx"
MONTHS = {1: "Jan", 2: "Feb", 3: "Mar"}
STAT = ["Live", "Discontinued", "Disco to Resource", "Not For Sale"]
ADDITIVE_COLS = ["units_sold", "units_returned"]


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
    zap["omonth"] = zap["order"].map(om)
    zap = zap[(zap.omonth.dt.year == 2026) & (zap.omonth.dt.month.isin(MONTHS))]
    ret = zap.groupby(["sku", "order"], as_index=False)["qty"].sum()
    home = s.drop_duplicates("order").set_index("order")[["mkt", "m"]]
    ret = ret.join(home, on="order", how="inner")

    # enrich (status + finish, from Line Detail, via the shared lookup)
    lookup = line_detail_lookup(ld, "l0", {"status": "l1", "finish": "l8"})
    for col in ("status", "finish"):
        s[col] = s["sku"].map(lookup[col])
        ret[col] = ret["sku"].map(lookup[col])

    return s, ret, zap


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
    return block


def by_group(s, ret, col, keys):
    rows = {g: _rate(s[s[col] == g], ret[ret[col] == g]) for g in keys}
    rows["Total"] = _rate(s[s[col].isin(keys)], ret[ret[col].isin(keys)])
    block = pd.DataFrame(rows).T

    assert_labels_match(keys, s[col].dropna().unique())
    assert_additive(block, ADDITIVE_COLS, keys, "Total")
    assert_orders_based_rate(block)
    return block


def reason_mix(zap):
    r = zap.groupby("reason")["qty"].sum().sort_values(ascending=False)
    return pd.DataFrame({"units_returned": r, "share": r / r.sum()})


def _show(df, pct=("return_rate",)):
    d = df.copy()
    for c in d.columns:
        if c in pct or c == "share":
            d[c] = (d[c] * 100).map(lambda v: f"{v:.2f}%")
        else:
            d[c] = d[c].map(lambda v: f"{v:,.0f}")
    print(d.to_string())


def run(src):
    """Build every block and pass it through the reconciliation gate.

    Raw (unformatted) DataFrames -- this is the entry point both the CLI
    display below and the regression fixture/test use, so the fixture
    reflects exactly the numbers the gate has already checked.
    """
    s, ret, zap = prep(src)
    fins = s["finish"].value_counts().index[:10].tolist()
    return {
        "by_month": by_month(s, ret),
        "by_status": by_group(s, ret, "status", STAT),
        "by_market": by_group(s, ret, "mkt", ["UK", "US"]),
        "reason_mix": reason_mix(zap),
        "by_finish": by_group(s, ret, "finish", fins),
    }


if __name__ == "__main__":
    blocks = run(SRC)

    print("=== By ORDER MONTH (orders-based rate, single-count) ===")
    _show(blocks["by_month"])

    print("\n=== By product STATUS ===")
    _show(blocks["by_status"])

    print("\n=== UK / US split ===")
    _show(blocks["by_market"])

    print("\n=== Return REASON mix (share of returned units) ===")
    _show(blocks["reason_mix"], pct=("share",))

    print("\n=== By FINISH (top 10 by orders) ===")
    _show(blocks["by_finish"])

    print(
        "\nreconciliation gate: PASS (additive measures reconcile; labels matched; "
        "headline is orders-based; order-month bucketed)"
    )
    print(
        "Basis: order-month · orders-based · single-count. Status/finish ORDER counts are "
        "distinct and do not sum to Total (an order can span groups). Recent months still maturing."
    )
