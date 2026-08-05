"""Build the Q1 2026 (Jan-Mar) returns dashboard.

Sales + Line Detail: the Q1_Jan_Feb_Mar_2026.xlsx workbook.
Returns/reasons: two rolling, per-store returns-app exports, not scoped to any
one quarter -- source/ytd_returns_2.numbers (UK) + source/ytd_returns_us.numbers
(US) -- replacing the workbook's own static "Returns zap" tab per Lena's
2026-08-05 instruction. These are TWO separate files, not one combined export:
confirmed 2026-08-05 that the original "single rolling file" only ever covered
UK order ids, silently zeroing every US figure until caught (see build.py's
load_returns_export()/prep() for the per-market guard added as a result).

Run:  python returns/build_q1.py [xlsx] [returns_export[,returns_export2]] [out.html]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from returns import build, render

SALES_SRC = "source/Q1_Jan_Feb_Mar_2026.xlsx"
RETURNS_SRC = ["source/ytd_returns_2.numbers", "source/ytd_returns_us.numbers"]
OUT = os.path.join(os.path.dirname(__file__), "..", "output", "returns-q1-2026.html")

if __name__ == "__main__":
    sales_src = sys.argv[1] if len(sys.argv) > 1 else SALES_SRC
    returns_src = sys.argv[2].split(",") if len(sys.argv) > 2 else RETURNS_SRC
    out = sys.argv[3] if len(sys.argv) > 3 else OUT

    sales_df, ld_std = build.load_workbook_sales(sales_src)
    returns_df = build.load_returns_export(returns_src)

    returns_label = "+".join(os.path.basename(p) for p in returns_src)
    written = render.render(
        sales_df, ld_std, returns_df,
        month_nums=[1, 2, 3], year=2026,
        period_label="Q1 2026",
        source_label=f"{os.path.basename(sales_src)} + {returns_label}",
        out_path=out,
    )
    print(f"wrote {written}")
