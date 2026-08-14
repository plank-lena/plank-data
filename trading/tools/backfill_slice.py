"""Local backfill slicer -- Python port of Appendix A's Code.gs slicing logic
(Matrixify Slice Architecture brief, 2026-08-13, sections 2.2/3), run against
raw Matrixify export CSVs already on disk instead of inside Apps Script. This
is "Start here" step 3 of that brief: once the 14 one-shot backfill exports
(one per store per quarter, Jan 2025 -> Jul 2026) have been created from a
Project chat and pulled to this machine (the sandbox can't reach
app.matrixify.app itself), run this over each raw file to populate
trading/source/orders_<period>_<STORE>.csv and
trading/source/orders_manifest.csv -- common/sources.py's
matrixify_orders_snapshot()/assert_orders_coverage() read those, not the raw
exports directly.

Local filename convention: orders_<period>_<STORE>.csv (e.g.
"orders_2025-07_UK.csv", period first, STORE uppercase, landed directly in
trading/source/ -- see common.sources.matrixify_orders_snapshot, the single
place that convention is owned). This is a DIFFERENT naming layer from the
brief's own section 2.1 Drive filename (orders_<store>_<YYYY-MM>.csv,
lowercase store) -- don't conflate the two.

Merge, not replace, across backfill windows: each of the 14 quarterly
backfill windows is pulled with a day of padding either side ON PURPOSE --
Matrixify filters `created_at` on the raw timestamp, but the pipeline buckets
by Europe/London (a 2025-03-31 23:45 -0400 order is genuinely April once
bucketed), so adjacent windows deliberately overlap at the edges. Writing a
month that already has a slice on disk UNIONS the new rows into the existing
file, keyed on order Name -- an order seen again replaces its own full
row-set (freshest state wins), but an order only ever captured by an earlier
window is kept, never dropped by a later window's write. (Appendix A's own
writeSlice_, for the STANDING daily pipeline, stays a true whole-slice
replace -- its current-window export always contains the complete month by
construction, so there's no boundary-overlap case to merge across there;
this merge behavior is specific to the one-time backfill's overlapping
quarterly windows.)

Schema note: the brief's own section 3 prose describes a 10-column manifest
including a `settled` column, but the paste-ready Code.gs in the brief's
Appendix A -- the thing that actually runs the STANDING pipeline -- only ever
writes the 9 columns in MANIFEST_COLS below. This slicer matches Code.gs, not
the prose, so the offline backfill manifest and the eventual Drive-produced
standing manifest share one schema rather than silently diverging.

Usage:
  python trading/tools/backfill_slice.py <store> <raw_csv_path> [<raw_csv_path> ...]
  python trading/tools/backfill_slice.py uk trading/source/backfill/plank_orders_archive_uk_2025Q1.csv
"""
import csv
import hashlib
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_TRADING_DIR = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(_TRADING_DIR)
for _p in (_REPO_ROOT, _TRADING_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common.sources import matrixify_orders_snapshot, ORDERS_MANIFEST_PATH as MANIFEST_PATH
from common.sources import ORDERS_MANIFEST_COLS as MANIFEST_COLS
# Imported, not reimplemented: a second copy of this parsing/bucketing logic
# would drift from trading/matrixify_source.py's own and quietly misfile
# orders at month boundaries. order_month_london raises ValueError on
# anything that doesn't match Matrixify's own "%Y-%m-%d %H:%M:%S %z" -- that
# propagates uncaught below, on purpose (see slice_by_order_month).
from matrixify_source import order_month_london


def slice_by_order_month(header, rows):
    """Slice by ORDER, never by row (brief 2.2): every row of an order goes
    into the slice for that order's own Created At, refund rows included --
    cutting by row would orphan any refund whose sale happened in a prior
    month (matrixify_source.py's build_lines() already counts and drops
    exactly this case as skipped_orphan_refunds if it happens).

    A row's own month comes from whichever row for that order has
    Top Row == "true", never the row's own Created At cell -- mirrors
    Code.gs's sliceByOrderMonth_ and matrixify_source.py's build_lines(),
    both of which only ever read Created At off the Top Row for this reason
    (a Refund Line row repeats the order's Name but not reliably its own
    Created At).

    Every non-blank Created At in the file -- not just Top Row ones -- is
    run through order_month_london() before anything else, purely to
    validate its format; several jobs in the live Matrixify queue export
    Created At as %d-%m-%Y instead of the expected %Y-%m-%d %H:%M:%S %z, and
    a wrong-format value must ABORT the whole run rather than parse to a
    plausible-looking but wrong month and misfile every row silently. This
    is a hard failure, not a per-row skip -- an export that's the wrong
    format throughout is not something to partially salvage.
    """
    i_name = header.index("Name")
    i_created = header.index("Created At")
    i_top = header.index("Top Row")

    month_of = {}
    for row in rows:
        created = row[i_created]
        if not created:
            continue
        try:
            period = order_month_london(created)
        except ValueError as e:
            raise ValueError(
                f"slice_by_order_month: Created At {created!r} (order {row[i_name]!r}) doesn't "
                f"parse as Matrixify's expected \"%Y-%m-%d %H:%M:%S %z\" format -- {e}. Several "
                f"jobs in the Matrixify queue export a different date format (e.g. %d-%m-%Y); "
                f"aborting rather than risk silently misfiling every row in this export into the "
                f"wrong month."
            ) from e
        if str(row[i_top]).strip().lower() == "true":
            month_of[row[i_name]] = period

    out = {}
    orphans = 0
    for row in rows:
        period = month_of.get(row[i_name])
        if not period:
            orphans += 1
            continue
        out.setdefault(period, []).append(row)
    if orphans:
        print(f"slice_by_order_month: {orphans} row(s) had no Top Row for their order -- excluded",
              file=sys.stderr)
    return out


def _upsert_manifest_row(rec):
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    existing_rows = []
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, newline="", encoding="utf-8") as f:
            existing_rows = list(csv.DictReader(f))
    kept = [r for r in existing_rows if not (r["store"] == rec["store"] and r["period"] == rec["period"])]
    kept.append(rec)
    kept.sort(key=lambda r: (r["store"], r["period"]))
    with open(MANIFEST_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLS)
        w.writeheader()
        w.writerows(kept)


def _merge_by_order_name(header, existing_rows, new_rows):
    """Union existing_rows (already on disk) with new_rows (this call's own
    slice), keyed on order Name -- an order appearing in new_rows replaces
    its ENTIRE row-set from existing_rows outright (never a partial splice
    of the two, which could otherwise mix an old and a new refund state for
    the same order); an order only present in existing_rows is carried
    through unchanged. Deliberately order-granularity, not Line: ID
    granularity -- the brief's own warning against a Line:ID-keyed upsert
    (a multi-row refund sharing one Line: ID would collapse) doesn't apply
    here, since every row for a given Name always moves as one unit.
    """
    i_name = header.index("Name")

    existing_by_name = {}
    for row in existing_rows:
        existing_by_name.setdefault(row[i_name], []).append(row)

    new_by_name = {}
    for row in new_rows:
        new_by_name.setdefault(row[i_name], []).append(row)

    merged = {**existing_by_name, **new_by_name}
    return [row for name in sorted(merged) for row in merged[name]]


def _write_slice(store, period, header, new_rows):
    """Merge-write (see module docstring for why this isn't a flat replace):
    reads whatever's already on disk for this (store, period), unions it
    with new_rows keyed on order Name, and rewrites the file with the
    combined set. Safe to call repeatedly across overlapping backfill
    windows or a re-run of the same window.
    """
    out_path = matrixify_orders_snapshot(store, period)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    existing_rows = []
    if os.path.exists(out_path):
        with open(out_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            existing_header = next(reader, None)
            if existing_header == header:
                existing_rows = [r for r in reader if r and r[0]]
            else:
                print(f"_write_slice: {out_path} exists with a different header -- "
                      f"treating as empty rather than guessing a column mapping", file=sys.stderr)

    all_rows = _merge_by_order_name(header, existing_rows, new_rows)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(all_rows)

    i_name = header.index("Name")
    i_created = header.index("Created At")
    dates = sorted(r[i_created] for r in all_rows if r[i_created])
    orders = {r[i_name] for r in all_rows}
    with open(out_path, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()

    _upsert_manifest_row({
        "store": store.lower(), "period": period, "file_name": os.path.basename(out_path),
        "rows": str(len(all_rows)), "orders": str(len(orders)),
        "min_created_at": dates[0] if dates else "",
        "max_created_at": dates[-1] if dates else "",
        "sha256": sha256,
        "last_written": datetime.now(timezone.utc).isoformat(),
    })
    return out_path


def slice_raw_export(store, raw_csv_path):
    """Slice one raw backfill export (a store's Q1/Q2/.../Jul-2026 window)
    into per-month slices, merging each touched month into whatever's
    already on disk and upserting its manifest row. A single raw export can
    span multiple months (a quarterly window does, by construction) --
    every month it touches gets written.
    """
    with open(raw_csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [r for r in reader if r and r[0]]

    by_period = slice_by_order_month(header, rows)
    written = []
    for period, period_rows in sorted(by_period.items()):
        path = _write_slice(store, period, header, period_rows)
        written.append((period, path, len(period_rows)))
        print(f"slice_raw_export: {store}/{period} -> {path} ({len(period_rows)} rows this window)")
    return written


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python trading/tools/backfill_slice.py <store> <raw_csv_path> [<raw_csv_path> ...]\n"
              "  <store>: \"uk\" or \"us\"", file=sys.stderr)
        sys.exit(1)
    store_arg = sys.argv[1]
    for raw_path in sys.argv[2:]:
        slice_raw_export(store_arg, raw_path)
