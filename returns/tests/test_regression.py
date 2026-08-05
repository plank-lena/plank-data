"""Regression test: re-running the returns builder against the real Q1
workbook + returns export must reproduce the committed 2026Q1 fixture within
tolerance.

Requires source/Q1_Jan_Feb_Mar_2026.xlsx and source/ytd_returns_2.numbers
locally -- both are gitignored (dropped feeds, per ROADMAP.md), so this test
is a maintainer-local check, not something CI can run without them present.
Skips (does not fail) if either source is missing, since that's an
environment gap, not a correctness regression.

Run:  python returns/tests/test_regression.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pandas as pd

from returns import build

HERE = os.path.dirname(__file__)
FIXTURE_DIR = os.path.join(HERE, "fixtures", "2026Q1")
SRC = os.path.join(HERE, "..", "..", "source", "Q1_Jan_Feb_Mar_2026.xlsx")
RETURNS_SRC = os.path.join(HERE, "..", "..", "source", "ytd_returns_2.numbers")
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
    """expected_row: the single row of a one-row fixture CSV (a pd.Series)."""
    failures = []
    for key, e in expected_row.items():
        if key not in actual:
            continue
        a = actual[key]
        if isinstance(a, dict) or isinstance(e, str) and e.strip().startswith("{"):
            continue  # nested dict fields (e.g. by_subreason) -- structural only, not gated
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
    if not os.path.exists(SRC) or not os.path.exists(RETURNS_SRC):
        print(f"SKIP: source file(s) not found ({SRC}, {RETURNS_SRC}) -- maintainer-local test")
        return 0

    sales_df, ld_std = build.load_workbook_sales(SRC)
    returns_df = build.load_returns_export(RETURNS_SRC)
    blocks = build.run(sales_df, ld_std, returns_df, month_nums=[1, 2, 3], year=2026)
    all_failures = []
    for name, block in blocks.items():
        if name == "tracker":
            continue  # SKU-level/MultiIndex, gated by its own build-time asserts -- see make_fixture.py
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

    print("PASS -- reproduces the 2026Q1 fixture within 0.1% tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
