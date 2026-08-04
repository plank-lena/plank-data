"""One-off: run the returns builder and commit its output as the numeric
regression oracle (returns/tests/fixtures/2026Q1/*.csv).

Run once whenever the 2026Q1 fixture needs regenerating (e.g. a confirmed
change to the locked decisions in ROADMAP.md §4) -- not part of the normal
build. test_regression.py re-runs the builder and diffs against these files
within tolerance.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pandas as pd

from returns.build import run

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "2026Q1")
SRC = sys.argv[1] if len(sys.argv) > 1 else "source/Q1_Jan_Feb_Mar_2026.xlsx"

if __name__ == "__main__":
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    blocks = run(SRC)
    for name, block in blocks.items():
        if name == "tracker":
            # SKU-level, MultiIndex, legitimately volatile row-by-row (ranking can
            # reorder on any real data update) -- gated by its own build-time
            # asserts (20-order floor, no impossible rate), not a fixture target.
            continue
        path = os.path.join(FIXTURE_DIR, f"{name}.csv")
        if isinstance(block, pd.DataFrame):
            block.to_csv(path)
        else:
            # scalar-valued blocks (value_split*, reason_detail) -> one-row CSV;
            # nested dict fields (by_subreason) get JSON-encoded into their cell
            flat = {k: (v if not isinstance(v, dict) else __import__("json").dumps(v))
                    for k, v in block.items()}
            pd.DataFrame([flat]).to_csv(path, index=False)
        print(f"wrote {path}")
