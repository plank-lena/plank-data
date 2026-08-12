"""One-off: run the returns builder against the Drive-sourced ReturnZap feed
and commit its output as the numeric regression oracle
(returns/tests/fixtures/2026Q1_returnzap/*.csv).

Separate from make_fixture.py/fixtures/2026Q1 (the .numbers-sourced baseline,
still the regression target for that path -- untouched here) because the
ReturnZap sheet's numbers genuinely differ now that its Apps Script pull
covers full history (2026-08-12): the old .numbers export undercounted
returns relative to what ReturnZap's API actually has, same as the original
hand-built sheet undercounted relative to the .numbers rebuild. Two real,
sequential improvements in raw coverage, not a regression -- see
test_regression_returnzap.py's own docstring for the reconciliation.

Run once whenever this fixture needs regenerating (e.g. the ReturnZap sheet
is refreshed with a materially different pull) -- not part of the normal
build. test_regression_returnzap.py re-runs the builder and diffs against
these files within tolerance.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pandas as pd

from returns import build

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "2026Q1_returnzap")
SRC = sys.argv[1] if len(sys.argv) > 1 else "source/Q1_Jan_Feb_Mar_2026.xlsx"

if __name__ == "__main__":
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    sales_df, ld_std = build.load_workbook_sales(SRC)
    returns_df = build.load_returns_export_from_sheet()
    blocks = build.run(sales_df, ld_std, returns_df, month_nums=[1, 2, 3], year=2026)
    for name, block in blocks.items():
        if name == "tracker":
            continue  # SKU-level, MultiIndex, legitimately volatile row-by-row -- see make_fixture.py
        path = os.path.join(FIXTURE_DIR, f"{name}.csv")
        if isinstance(block, pd.DataFrame):
            block.to_csv(path)
        else:
            flat = {k: (v if not isinstance(v, dict) else __import__("json").dumps(v))
                    for k, v in block.items()}
            pd.DataFrame([flat]).to_csv(path, index=False)
        print(f"wrote {path}")
