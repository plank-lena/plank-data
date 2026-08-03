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

from returns.build import run

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "2026Q1")
SRC = sys.argv[1] if len(sys.argv) > 1 else "source/Q1_Jan_Feb_Mar_2026.xlsx"

if __name__ == "__main__":
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    blocks = run(SRC)
    for name, block in blocks.items():
        path = os.path.join(FIXTURE_DIR, f"{name}.csv")
        block.to_csv(path)
        print(f"wrote {path}")
