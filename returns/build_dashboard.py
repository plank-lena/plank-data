"""Build a real returns dashboard, period-from-prompt, connector-sourced end
to end -- the returns twin of trading/build_matrixify_dashboard.py.

Until now, returns/build.py's period-from-prompt entry point
(run_for_period) and connector-sourced ReturnZap loader
(load_returns_export_from_sheet) existed and were tested, but nothing wired
them to real data sources for an arbitrary period the way build_q1.py/
build_q2.py wire ONE hardcoded quarter to the old per-store .numbers files.
This is that missing wiring (2026-08-12, PII-incident/Drive-bridge
follow-up, docs/2026-08-12_matrixify_sheet_bridge.md) -- reads:
  - trading's rolling Matrixify order snapshot (common.sources.
    matrixify_orders_snapshot) for sales, same source trading itself now
    uses, not a period-specific file;
  - the Drive-sourced Line Detail snapshot (common.sources.
    LINE_DETAIL_SNAPSHOT);
  - the Drive-sourced ReturnZap snapshot (common.sources.
    RETURNS_ZAP_SNAPSHOT), via load_returns_export_from_sheet.

render() itself has no overwrite guard (unlike trading/contract.py's
write_committed_file) -- it's a plain `open(path, "w")`. Checked here
instead, so an already-published period isn't silently clobbered by a
re-run -- pass --force to override deliberately.

Run:
  python returns/build_dashboard.py "July 2026"
  python returns/build_dashboard.py "Q2 2026"
  python returns/build_dashboard.py 2026-07 --force
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from common.period import parse_period
from common.sources import matrixify_orders_snapshot, LINE_DETAIL_SNAPSHOT
from returns import build, render
from returns.validate import validate_period

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")


def build_returns(period_arg, force=False, as_of=None, out_path=None):
    pm = parse_period(period_arg, as_of=as_of)
    cm = pm["cm"]
    year = cm["start"].year
    month_nums = ([cm["start"].month, cm["start"].month + 1, cm["start"].month + 2]
                  if cm["kind"] == "quarter" else [cm["start"].month])
    label = cm["label"]

    out_path = out_path or os.path.join(OUTPUT_DIR, f"returns-{label.lower().replace(' ', '-')}.html")
    if os.path.exists(out_path) and not force:
        raise FileExistsError(
            f"build_returns: {out_path} already exists -- refusing to silently overwrite an "
            f"already-published period. Pass force=True (--force on the CLI) if this is a "
            f"deliberate re-run, not an accidental re-derive of committed work."
        )

    # Fail loud on coverage/non-empty/both-markets/freshness BEFORE any
    # aggregation -- the same gate run_for_period applies, run explicitly
    # here since render() re-derives its own month_nums/year from what we
    # pass it directly, not from a period string (it doesn't call
    # run_for_period internally).
    validate_period(pm, as_of=as_of)

    sales_sources = [
        ("UK", matrixify_orders_snapshot("uk")),
        ("US", matrixify_orders_snapshot("us")),
    ]
    sales_df = build.load_matrixify_sales(sales_sources)
    ld_std = build.load_line_detail_file(LINE_DETAIL_SNAPSHOT)
    returns_df = build.load_returns_export_from_sheet()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    written = render.render(
        sales_df, ld_std, returns_df,
        month_nums=month_nums, year=year,
        period_label=label,
        source_label="Matrixify rolling snapshot (UK+US) + ReturnZap Drive sheet",
        out_path=out_path,
    )

    # Values-only Excel companion, automatic alongside the HTML (2026-08-13) --
    # matches trading's build_matrixify_dashboard.py pattern. Real per-SKU/
    # per-department aggregation computed fresh here (returns has no
    # pre-built "contract" artifact like trading's contract.json), not a
    # re-derivation of numbers render() already computed independently --
    # both start from the same sales_df/ld_std/returns_df, same prep().
    from returns.excel_companion import build_returns_companion
    companion_path = os.path.splitext(out_path)[0] + "_companion.xlsx"
    if os.path.exists(companion_path) and not force:
        raise FileExistsError(
            f"build_returns: {companion_path} already exists -- refusing to silently overwrite. "
            f"Pass force=True (--force on the CLI) if this is deliberate."
        )
    build_returns_companion(
        companion_path, label, sales_df, ld_std, returns_df, month_nums, year,
        source_note=f"Source: {label} committed returns build. Values-only (no formulas).",
    )

    print(f"dashboard: {written}")
    print(f"companion: {companion_path}")
    return written


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python returns/build_dashboard.py <period> [--force]\n"
              "  <period>: \"July 2026\", \"Q2 2026\", \"2026-07\"", file=sys.stderr)
        sys.exit(1)
    period_arg = sys.argv[1]
    force = "--force" in sys.argv[2:]
    build_returns(period_arg, force=force)
