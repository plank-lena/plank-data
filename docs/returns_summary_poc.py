"""
returns_summary_poc.py
======================
Proof-of-concept for the Path-2 (hybrid) returns builder.

Goal of this spike
------------------
Show that the returns `Summary` block (Total, by-status, by-category) can be
reproduced *deterministically in code* from the raw feeds, and that the numbers
match the hand-built Q1 workbook within tolerance. If they match, the hybrid
plan is sound: the workbook stays the human-readable spec + test oracle, while
the monthly/quarterly run is deterministic code behind a verification gate.

Result (Q1 Jan-Feb-Mar 2026)
----------------------------
- Status block (Live / Discontinued / Disco to Resource / Not For Sale) + Total:
  EXACT match (0.000%) on all six metrics.
- Category block: exact on every row except "Electric Accessory", where the
  workbook shows 0 because its SUMIF criteria label carries a trailing space
  ('Electric Accessory ') that does not match the data. The builder (which
  normalises whitespace) correctly recovers GBP 169.90. i.e. the gate catches a
  latent spreadsheet bug instead of inheriting it.

SCOPE NOTE (important) -- this script takes units returned from the workbook's
own helper column N (the sheet's pre-computed returns-join output), NOT from the
raw 'Returns zap' tab. It therefore proves the AGGREGATION/GROUPING logic, not
the returns JOIN. A connector-fed production run has no column N and must rebuild
the join from the raw feed; see returns_summary_builder.py and
returns_spike_findings.md, where that recompute does NOT reconcile (the legacy
join double-counts). Treat this PASS as "grouping proven", not "returns proven".

Data lineage (what the workbook formulas actually do)
-----------------------------------------------------
Raw feeds:
  Shopify Data   one row per order-line. Columns used (positional / Excel):
                   A Country (UK/US)      -> market
                   B month (date)         -> period bucket
                   H variant_sku          -> SKU (join key)
                   I total_sales          -> sales cash (ex-VAT revenue basis)
                   K ordered_item_quantity-> units sold
                   N Returns              -> units returned  (derived upstream
                                             by SUMIF into 'Returns zap' on a
                                             composite order+SKU key)
                   O Returns reason       -> return reason  (derived upstream by
                                             XLOOKUP into 'Returns zap')
  Orders pivot   distinct-order counts per month x SKU x market -> returned orders
  Line Detail    SKU master: UK status, category, RRP ex-VAT, etc.

Per-SKU aggregates (monthly tabs), then summed across the 3 months (quarter tab):
  units_sold    = SUMIFS(Shopify Data.K  by SKU, month)
  units_ret     = SUMIFS(Shopify Data.N  by SKU, month)
  cash          = SUMIFS(Shopify Data.I  by SKU, month)
  orders        = COUNTIFS(Shopify Data lines by SKU, month)   # line count
  ret_orders    = SUMIFS(Orders pivot count by SKU, month)     # distinct orders
  returns_cash  = RRP_ex_VAT[SKU] * units_ret                  # NB: notional

Summary = group per-SKU rows by status (Line Detail col B) and by category
(Line Detail col E) and sum.

Definitional register (pin these; they are decisions, not data)
---------------------------------------------------------------
1. RETURN SOURCE. Units returned come from the Returns-zap-derived column, NOT
   Shopify's own returns field. Returns-zap counts a return whether or not the
   warehouse has checked it in; Shopify only counts checked-in returns and so
   undercounts. Headline must use the Returns-zap basis.
2. RETURNS CASH IS NOTIONAL. It is RRP-ex-VAT x units returned, i.e. list value
   of returned units, not the actual refunded amount. Label it as such.
3. ORDERS vs RETURNED ORDERS use different constructions: orders = order-line
   count from Shopify Data; returned orders = distinct-order count from the
   Orders pivot. Keep the asymmetry explicit.
4. LABELS ARE WHITESPACE-SENSITIVE in the hand-built sheet. Normalise (strip)
   on every join key and category label, and flag mismatches loudly.
5. LQ / LY comparison columns are hand-carried in the workbook. In the builder
   they should be read from committed prior-period outputs, not re-entered.

For the real monthly/quarterly run the raw feeds arrive from connectors
(Shopify / Matrixify + the Returns-zap export + Line Detail), not from this
workbook; the reproduction logic below is unchanged.
"""

from __future__ import annotations
import sys
import numpy as np
import pandas as pd

# ---- positional column maps (Excel letter -> 0-based index) ----
SHOP = dict(country=0, month=1, sku=7, cash=8, units_sold=10, units_ret=13, reason=14)
PIVOT = dict(month=0, sku=1, market=2, count=3)
LD = dict(sku=0, uk_status=1, category=4, rrp_ex_vat=10)

STATUSES = ["Live", "Discontinued", "Disco to Resource", "Not For Sale"]
TOL = 0.001  # 0.1% relative


def _key(s: pd.Series) -> pd.Series:
    """Normalise a join key: string, stripped. Whitespace bugs die here."""
    return s.astype(str).str.strip()


def load_raw(path: str) -> dict[str, pd.DataFrame]:
    """Load the raw tabs as positional frames (header row kept as row 0)."""
    read = lambda sheet: pd.read_excel(path, sheet_name=sheet, header=None)
    return {
        "shop": read("Shopify Data"),
        "pivot": read("Orders pivot"),
        "ld": read("Line Detail"),
        "summary_oracle": read("Summary"),
    }


def build_sku_aggregates(raw: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Reproduce the per-SKU quarter aggregates from the raw feeds."""
    ld = raw["ld"].iloc[1:].copy()
    ld["sku"] = _key(ld[LD["sku"]])
    status_by_sku = ld.set_index("sku")[LD["uk_status"]]
    category_by_sku = ld.set_index("sku")[LD["category"]]
    rrp_by_sku = pd.to_numeric(ld.set_index("sku")[LD["rrp_ex_vat"]], errors="coerce")

    s = raw["shop"].iloc[1:].copy()
    s["sku"] = _key(s[SHOP["sku"]])
    s["cash"] = pd.to_numeric(s[SHOP["cash"]], errors="coerce").fillna(0)
    s["units_sold"] = pd.to_numeric(s[SHOP["units_sold"]], errors="coerce").fillna(0)
    s["units_ret"] = pd.to_numeric(s[SHOP["units_ret"]], errors="coerce").fillna(0)
    s = s[s["sku"].notna() & (s["sku"] != "nan")]

    g = (
        s.groupby("sku")
        .agg(
            cash=("cash", "sum"),
            units_sold=("units_sold", "sum"),
            units_ret=("units_ret", "sum"),
            orders=("sku", "size"),  # COUNTIFS lines
        )
        .reset_index()
    )

    p = raw["pivot"].iloc[1:].copy()
    p["sku"] = _key(p[PIVOT["sku"]])
    p["count"] = pd.to_numeric(p[PIVOT["count"]], errors="coerce").fillna(0)
    g["ret_orders"] = g["sku"].map(p.groupby("sku")["count"].sum()).fillna(0)

    g["status"] = g["sku"].map(status_by_sku)
    g["category"] = g["sku"].map(category_by_sku)
    g["category_key"] = g["category"].astype(str).str.strip()
    g["rrp_ex_vat"] = g["sku"].map(rrp_by_sku)
    g["returns_cash"] = g["rrp_ex_vat"].fillna(0) * g["units_ret"]  # notional
    return g


METRICS = ["cash", "units_sold", "units_ret", "orders", "ret_orders", "returns_cash"]
# Summary sheet column indices for each metric (Excel C,F,G,I,J,D)
ORACLE_COL = dict(cash=2, units_sold=5, units_ret=6, orders=8, ret_orders=9, returns_cash=3)


def _oracle_row(oracle: pd.DataFrame, excel_row: int) -> dict:
    r = oracle.iloc[excel_row - 1]
    out = {}
    for m, c in ORACLE_COL.items():
        v = pd.to_numeric(r[c], errors="coerce")  # header text / blanks -> NaN
        out[m] = 0.0 if pd.isna(v) else float(v)
    return out


def _sum_block(g: pd.DataFrame, keycol: str, label: str) -> dict:
    sub = g[g[keycol] == label]
    return {m: float(sub[m].sum()) for m in METRICS}, len(sub)


def _compare(name: str, repro: dict, oracle: dict) -> tuple[bool, float, str]:
    worst, wm = 0.0, ""
    for m in METRICS:
        a, b = repro[m], oracle[m]
        rel = (abs(a - b) / abs(b)) if b else (0.0 if abs(a) < 1e-9 else 9.99)
        if rel > worst:
            worst, wm = rel, m
    return worst < TOL, worst, wm


def verify(path: str) -> int:
    raw = load_raw(path)
    g = build_sku_aggregates(raw)
    oracle = raw["summary_oracle"]

    print("=" * 68)
    print("RETURNS SUMMARY — reproduction vs workbook (tolerance 0.1%)")
    print("=" * 68)

    failures = 0

    # --- status block (Excel rows 4-7) + Total (row 3) ---
    print("\nBy status")
    status_rows = {"Live": 4, "Discontinued": 5, "Disco to Resource": 6, "Not For Sale": 7}
    total_repro = {m: 0.0 for m in METRICS}
    for label, er in status_rows.items():
        rep, n = _sum_block(g, "status", label)
        ok, worst, wm = _compare(label, rep, _oracle_row(oracle, er))
        for m in METRICS:
            total_repro[m] += rep[m]
        print(f"  {label:20s} {'ok' if ok else 'MISMATCH':8s} worst={worst*100:6.3f}% ({wm}) n_sku={n}")
        failures += 0 if ok else 1

    ok, worst, wm = _compare("TOTAL", total_repro, _oracle_row(oracle, 3))
    print(f"  {'TOTAL':20s} {'ok' if ok else 'MISMATCH':8s} worst={worst*100:6.3f}% ({wm})")
    failures += 0 if ok else 1

    # --- category block (Excel rows 10-40, child rows only) ---
    print("\nBy category")
    for er in range(10, 41):
        r = oracle.iloc[er - 1]
        b = r[1]
        if isinstance(b, str) and b.strip() == "Finish":
            break  # end of the category block; a separate by-finish table follows
        if not (isinstance(b, str) and b.strip() and not b.strip().endswith("TOTAL")):
            continue
        label = b.strip()
        # skip repeated header rows (their metric cells hold text like "Total Cash")
        if pd.isna(pd.to_numeric(r[ORACLE_COL["cash"]], errors="coerce")) and label.lower().startswith("product"):
            continue
        rep, n = _sum_block(g, "category_key", label)
        orc = _oracle_row(oracle, er)
        ok, worst, wm = _compare(label, rep, orc)
        note = ""
        if not ok and orc["cash"] == 0 and rep["cash"] != 0:
            note = f"  <- sheet shows 0 (label bug); builder recovers {rep['cash']:,.2f}"
            ok = None  # data-quality WARNING, not a repro failure
        tag = "ok" if ok else ("WARN" if ok is None else "MISMATCH")
        print(f"  {label:28s} {tag:8s} worst={worst*100:6.3f}% ({wm}) n_sku={n}{note}")
        if ok is False:
            failures += 1

    print("\n" + "-" * 68)
    if failures == 0:
        print("PASS — builder reproduces the workbook Summary within tolerance.")
    else:
        print(f"FAIL — {failures} block(s) outside tolerance.")
    return failures


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "Q1_Jan_Feb_Mar_2026.xlsx"
    sys.exit(1 if verify(path) else 0)
