"""Build the Q2 2026 (Apr-Jun) returns dashboard.

Sales: trading's own Matrixify order exports (trading/source/orders_2026-0{4,5,6}_
{UK,US}.csv) -- same frozen-snapshot source trading/ already uses for its Monthly/
Quarterly reports, per the same "a report needs a reproducible snapshot" reasoning
(ROADMAP.md). Line Detail: trading/source/line_detail.xlsx, a standalone catalog
(not period-specific). Returns/reasons: two rolling, per-store returns-app exports
-- source/ytd_returns_2.numbers (UK) + source/ytd_returns_us.numbers (US), the
same pair build_q1.py uses. These are separate files, not one combined export:
confirmed 2026-08-05 that the original "single rolling file" only ever covered UK
order ids, silently zeroing every US figure until caught (see build.py's
load_returns_export()/prep() for the per-market guard added as a result).

Run:  python returns/build_q2.py [returns_export[,returns_export2]] [out.html]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from returns import build, render

TRADING_SOURCE = os.path.join(os.path.dirname(__file__), "..", "trading", "source")
SALES_SOURCES = [
    ("UK", os.path.join(TRADING_SOURCE, "orders_2026-04_UK.csv")),
    ("US", os.path.join(TRADING_SOURCE, "orders_2026-04_US.csv")),
    ("UK", os.path.join(TRADING_SOURCE, "orders_2026-05_UK.csv")),
    ("US", os.path.join(TRADING_SOURCE, "orders_2026-05_US.csv")),
    ("UK", os.path.join(TRADING_SOURCE, "orders_2026-06_UK.csv")),
    ("US", os.path.join(TRADING_SOURCE, "orders_2026-06_US.csv")),
]
LINE_DETAIL_SRC = os.path.join(TRADING_SOURCE, "line_detail.xlsx")
RETURNS_SRC = ["source/ytd_returns_2.numbers", "source/ytd_returns_us.numbers"]
OUT = os.path.join(os.path.dirname(__file__), "..", "output", "returns-q2-2026.html")

if __name__ == "__main__":
    returns_src = sys.argv[1].split(",") if len(sys.argv) > 1 else RETURNS_SRC
    out = sys.argv[2] if len(sys.argv) > 2 else OUT

    sales_df = build.load_matrixify_sales(SALES_SOURCES)
    ld_std = build.load_line_detail_file(LINE_DETAIL_SRC)
    returns_df = build.load_returns_export(returns_src)

    returns_label = "+".join(os.path.basename(p) for p in returns_src)
    written = render.render(
        sales_df, ld_std, returns_df,
        month_nums=[4, 5, 6], year=2026,
        period_label="Q2 2026",
        source_label=f"Matrixify Apr–Jun 2026 (UK+US) + {returns_label}",
        out_path=out,
    )
    print(f"wrote {written}")
