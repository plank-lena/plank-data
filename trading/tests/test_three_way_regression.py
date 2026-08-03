"""Regression test: re-running compute_combined() against the committed May
2026 source CSVs must reproduce the frozen trading/tests/fixtures/
2026-05_three_way.csv within tolerance.

This is a DRIFT check against this module's own prior output, not an oracle
check -- the fixture is intentionally the computed ship-to-country result,
which does NOT yet match the May Monthly Summary oracle (BRIEF #5's known,
deferred US/UK residual; see trading/RECONCILE_HANDOFF.md). Oracle parity is
asserted separately by gate_check_combined()/assert_matches_oracle(), which
is expected to fail until that residual closes -- this test only confirms
the bucketing logic itself hasn't silently changed.

trading/source/*.csv is a committed, tracked fixture (not gitignored, per
CLAUDE.md), so this test does not skip on a missing source file the way
returns/tests/test_regression.py does.

Run:  python trading/tests/test_three_way_regression.py
"""
import csv
import os
import sys

HERE = os.path.dirname(__file__)
TRADING_DIR = os.path.join(HERE, "..")
# build_matrixify.py imports its siblings (matrixify_source, revenue) with
# flat `from x import y` -- it expects trading/ itself on sys.path, not the
# repo root, so it only works via direct script execution (`python
# trading/build_matrixify.py ...`, which Python auto-adds trading/ for) or
# by inserting trading/ here explicitly, as done below.
sys.path.insert(0, TRADING_DIR)

from build_matrixify import compute_combined

FIXTURE = os.path.join(HERE, "fixtures", "2026-05_three_way.csv")
UK_CSV = os.path.join(HERE, "..", "source", "orders_2026-05_UK.csv")
US_CSV = os.path.join(HERE, "..", "source", "orders_2026-05_US.csv")
TOL = 0.001  # 0.1% relative, matches the reconciliation gate's tolerance


def _rel_diff(a, b):
    return abs(a - b) / abs(b) if b else (0.0 if abs(a) < 1e-9 else float("inf"))


def _load_fixture():
    with open(FIXTURE, newline="") as f:
        return {row["bucket"]: row for row in csv.DictReader(f)}


def main():
    result = compute_combined(UK_CSV, US_CSV, "2026-05")
    ct, ut = result["country_totals"], result["units_totals"]
    actual = {
        "UK": {"value_gbp": ct["UK"], "units": ut["UK"]},
        "US": {"value_gbp": ct["US"], "units": ut["US"]},
        "ROW": {"value_gbp": ct["ROW"], "units": ut["ROW"]},
        "Total": {"value_gbp": result["grand_total"], "units": sum(ut.values())},
    }
    expected = _load_fixture()

    failures = []
    for bucket, actual_row in actual.items():
        expected_row = expected[bucket]
        for col in ("value_gbp", "units"):
            a = actual_row[col]
            e = float(expected_row[col])
            rel = _rel_diff(a, e)
            if rel > TOL:
                failures.append(f"{bucket}.{col}: got {a}, fixture has {e} (gap {rel:.4%})")

    if failures:
        print("REGRESSION FAILURES:")
        for f in failures:
            print(f"  {f}")
        return 1

    print("PASS -- reproduces trading/tests/fixtures/2026-05_three_way.csv within 0.1% tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
