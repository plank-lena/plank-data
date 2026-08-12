"""Regression test: re-running the returns builder against the real Q1
workbook + the Drive-sourced ReturnZap feed must reproduce the committed
2026Q1_returnzap fixture within tolerance.

RECONCILIATION NOTE (C4, 2026-08-12): this does NOT reproduce the older
fixtures/2026Q1 numbers (the .numbers-sourced baseline, still gated by
test_regression.py, unchanged and still passing) -- and it does not
reproduce the hand-built Q1 report's cited figures either (10.1% quarter
rate; Jan 7.0%/Feb 4.8%/Mar 6.4%; per returns/build.py's own docstring).
All three disagree because each pull had progressively more complete raw
ReturnZap history, not because any of the LOCKED methodology (single-count
sku+order dedupe, order-month basis, orders-based rate, ROADMAP.md §5)
changed:

  RETAIL headline (by_month), orders-based:
                    Quarter rate   Jan     Feb     Mar    Returned orders (of 8,380 retail orders)
  Hand-built report      10.1%    7.0%    4.8%    6.4%    n/a
  .numbers rebuild      10.26%   10.77%  10.09%   9.99%   860
  ReturnZap rebuild     12.16%   16.90%  10.13%  10.03%   1,019

  ALL-SEGMENT (by_market, blended retail+trade), for a like-for-like check:
                    Total rate   Returned orders (of 9,768 all-segment orders)
  .numbers rebuild     9.65%     943
  ReturnZap rebuild   11.53%     1,126

Confirmed independently (2026-08-12), given the concern that duplication
previously caused discrepancies: this order count is NOT inflated by the
residual row-level duplication in the raw feed. An isolated synthetic test
(5 duplicate raw rows for one order) shows returned_orders via
`ret["order"].nunique()` counts that order exactly once regardless of row
count -- duplication can only ever show up in units_returned (a secondary
metric, ~0.4% effect here after common.sources.dedupe_returns_export's two
passes), never in the orders-based headline, by construction.

The stock-value headline barely moves (£123,466.51 -> £123,341.91, 0.1%)
because it's computed entirely from the SALES side's own refund columns
(ruling 5, in build.py's docstring) -- unaffected by which returns feed is
used. The return-RATE and returned-ORDER counts move materially (+19%
returned orders overall, Jan alone +57% relative) because the ReturnZap
sheet now has genuinely more Q1 2026 returns than either prior source ever
did (raw rows: hand-built sheet's own tab < old .numbers export's 8,114
UK-only rows < this sheet's 74,218 rows spanning back to 2021), not a
join/dedupe bug: the exact-duplicate-row fix
(common.sources.dedupe_returns_export) and the sku+order dedupe are
unchanged and both still active.

Requires source/Q1_Jan_Feb_Mar_2026.xlsx and source/returns_zap.csv locally
-- both gitignored (dropped feeds, per ROADMAP.md), so this test is a
maintainer-local check, not something CI can run without them present.
Skips (does not fail) if either source is missing.

Run:  python returns/tests/test_regression_returnzap.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pandas as pd

from returns import build
from common.sources import RETURNS_ZAP_SNAPSHOT

HERE = os.path.dirname(__file__)
FIXTURE_DIR = os.path.join(HERE, "fixtures", "2026Q1_returnzap")
SRC = os.path.join(HERE, "..", "..", "source", "Q1_Jan_Feb_Mar_2026.xlsx")
TOL = 0.001  # 0.1% relative, matches the reconciliation gate's tolerance


def _compare_frame(name, actual, expected):
    failures = []
    for col in expected.columns:
        if not pd.api.types.is_numeric_dtype(expected[col]):
            if not actual[col].astype(str).equals(expected[col].astype(str)):
                failures.append(f"{name}.{col}: non-numeric mismatch")
            continue
        for label in expected.index:
            a = actual.loc[label, col]
            e = expected.loc[label, col]
            rel = abs(a - e) / abs(e) if e else (0.0 if abs(a) < 1e-9 else float("inf"))
            if rel > TOL:
                failures.append(
                    f"{name}.{col}[{label}]: got {a}, fixture has {e} (gap {rel:.4%})"
                )
    return failures


def _compare_dict(name, actual, expected_row):
    failures = []
    for key, e in expected_row.items():
        if key not in actual:
            continue
        a = actual[key]
        if isinstance(a, dict) or isinstance(e, str) and e.strip().startswith("{"):
            continue
        try:
            e = float(e)
            rel = abs(float(a) - e) / abs(e) if e else (0.0 if abs(float(a)) < 1e-9 else float("inf"))
        except (TypeError, ValueError):
            if str(a) != str(e):
                failures.append(f"{name}.{key}: non-numeric mismatch (got {a}, fixture has {e})")
            continue
        if rel > TOL:
            failures.append(f"{name}.{key}: got {a}, fixture has {e} (gap {rel:.4%})")
    return failures


def main():
    if not os.path.exists(SRC) or not os.path.exists(RETURNS_ZAP_SNAPSHOT):
        print(f"SKIP: source file(s) not found ({SRC}, {RETURNS_ZAP_SNAPSHOT}) -- maintainer-local test")
        return 0

    sales_df, ld_std = build.load_workbook_sales(SRC)
    returns_df = build.load_returns_export_from_sheet()
    blocks = build.run(sales_df, ld_std, returns_df, month_nums=[1, 2, 3], year=2026)
    all_failures = []
    for name, block in blocks.items():
        if name == "tracker":
            continue
        fixture_path = os.path.join(FIXTURE_DIR, f"{name}.csv")
        if isinstance(block, pd.DataFrame):
            expected = pd.read_csv(fixture_path, index_col=0)
            all_failures += _compare_frame(name, block, expected)
        else:
            expected_row = pd.read_csv(fixture_path).iloc[0]
            all_failures += _compare_dict(name, block, expected_row)

    if all_failures:
        print("REGRESSION FAILURES:")
        for f in all_failures:
            print(f"  {f}")
        return 1

    print("PASS -- reproduces the 2026Q1_returnzap fixture within 0.1% tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
