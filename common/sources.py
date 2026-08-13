"""Single source-of-truth for every external data connection the trading and
returns builders read from -- Drive-hosted sheets, Matrixify order exports,
and the local snapshot paths those connectors land at. One place to update
when a source moves: get the new Drive share link -> copy its file ID out of
the URL (.../d/<FILE_ID>/edit...) -> replace the one line in SOURCES below ->
re-run `python common/sources.py preflight`.

IMPORTANT -- who actually calls the connectors: the Google Drive and
Matrixify MCP tools are only reachable from an interactive Claude Code
session (or the shared Claude Project), never from a plain `python` process
-- there is no local API key this module could use to call them itself, by
design (see CLAUDE.md's "no .env" rule for these sources). So this module
does NOT perform any network fetch. What it owns:
  - the pinned IDs/tabs/expected shape (SOURCES below) -- the thing that
    changes when a source moves;
  - where a fetched snapshot lands on disk (the *_SNAPSHOT paths below) --
    every builder reads from these paths unchanged, whether the snapshot was
    just refreshed live or is a few days stale;
  - normalizing a raw connector download into the exact shape the EXISTING
    parsers already expect, so no parser gains source-branching logic (the
    pattern trading/line_detail.py's own docstring already describes:
    "dropbox... just needs to land a file shaped like the committed local
    snapshot... so no parsing logic ever branches on source");
  - the returns-export exact-duplicate-row fix (dedupe_returns_export) --
    see its own docstring for why this exists and isn't optional.

Refresh procedure (performed by whoever/whatever is driving the builder
through Claude -- ROADMAP.md §9 "Roles"): for each entry in SOURCES, fetch
the raw bytes via the named connector, pass them to that source's normalize_*
function here, which writes the matching *_SNAPSHOT path. Then run each
builder exactly as it runs today -- no builder code branches on where its
input file came from.
"""
import csv
import json
import os

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

# ---------------------------------------------------------------------------
# Pinned sources -- the only place any of these IDs/tabs should live.
# ---------------------------------------------------------------------------

SOURCES = {
    "line_detail": {
        "connector": "google_drive",
        "file_id": "1r5D03e3Df_Qyinrps0wqLlqsp3YGATi6Ob3cvjv7Dok",
        "tab": "Line Detail",
        "note": (
            "Product reference: taxonomy/status/cost. CONFIRMED 2026-08-11: the "
            "live sheet has 2 leading admin rows above the real header (a 'COPY "
            "THIS LINE' row + a blank row) and an extra Barcode column the "
            "committed snapshot doesn't carry -- normalize_line_detail_xlsx "
            "strips the leading rows so the header lands on row 1, matching "
            "what trading/line_detail.py's load_line_detail() assumes."
        ),
    },
    "shopify_product_data": {
        "connector": "google_drive",
        "file_id": "1wb7Xj1ionrL3eoZKBZ5JZTxo6jAiIqFaBQ74IJprcjo",
        "tab": "IN Shopify Product Data",
        "note": (
            "On-hand `Inventory` by `SKU`, for weeks-cover. Lives as a tab "
            "inside the same Yotpo-bound sheet, not its own file (confirmed "
            "2026-08-11 by downloading the sheet and listing tab names -- the "
            "colon in 'IN: Shopify Product Data' is stripped on xlsx export, "
            "so the literal tab name is 'IN Shopify Product Data'). NEVER the "
            "ship_sheet entry below -- that's inbound POs, not on-hand stock."
        ),
    },
    "yotpo_reviews": {
        "connector": "google_drive",
        "file_id": "1wb7Xj1ionrL3eoZKBZ5JZTxo6jAiIqFaBQ74IJprcjo",
        "tab": "API Yotpo Reviews",
        "note": (
            "Pulled with deleted=true -- deleted/escalated rows are in this "
            "tab by design, the whole criticism signal review_feedback.py "
            "needs. Same sheet as shopify_product_data above, different tab."
        ),
    },
    "matrixify_orders_uk": {
        "connector": "google_drive",
        "file_id": "10XoD6qOSr3fwiRE4cGfGrotcRd0YjU60vGvhNJmtE-E",
        "tab": "Matrixify Orders UK",
        "expected_columns": (
            "Name", "Created At", "Cancelled At", "Payment: Status", "Source",
            "Top Row", "Company: Name", "Billing: Company", "Shipping: Company",
            "Shipping: Country Code", "Line: Type", "Line: ID", "Line: SKU",
            "Line: Quantity", "Line: Total", "Line: Tax Total",
        ),
        "note": (
            "Rolling ~400-day Matrixify order snapshot (2026-08-12, PII incident "
            "follow-up -- see docs/2026-08-12_pii_remediation.md and "
            "docs/2026-08-12_matrixify_sheet_bridge.md). A recurring Matrixify "
            "scheduled export (fixed filename, no job ID, so the download URL "
            "never changes) is fetched daily by a small Apps Script and landed "
            "in this sheet -- Drive is the hand-off, same as every other source "
            "here, so a sandboxed session never touches app.matrixify.app "
            "directly (that domain isn't in its network allowlist). Columns are "
            "the minimal set trading/matrixify_source.py actually reads -- no "
            "customer PII, no payment fields, unlike the old per-month exports "
            "this replaces. One rolling file serves ANY period within its "
            "window: emit_contract_from_matrixify and _matrixify_headline_totals "
            "both filter by order_month AFTER parsing, so this file never needs "
            "to be re-scoped per period."
        ),
    },
    "matrixify_orders_us": {
        "connector": "google_drive",
        "file_id": "10XoD6qOSr3fwiRE4cGfGrotcRd0YjU60vGvhNJmtE-E",
        "tab": "Matrixify Orders US",
        "expected_columns": (
            "Name", "Created At", "Cancelled At", "Payment: Status", "Source",
            "Top Row", "Company: Name", "Billing: Company", "Shipping: Company",
            "Shipping: Country Code", "Line: Type", "Line: ID", "Line: SKU",
            "Line: Quantity", "Line: Total", "Line: Tax Total",
        ),
        "note": "US twin of matrixify_orders_uk above -- same sheet, different tab.",
    },
    "returns_zap": {
        "connector": "google_drive",
        "file_id": "1tyinVS7suxKIdaY9Y3R6geaOFkYgeaeVWcDU2VHaGzs",
        "tab": "API: Returns",
        "expected_columns": (
            "Country", "Order Id", "Order Number", "Order Date", "RMA Number",
            "Return Date", "Stage", "Status", "SKU", "Shopify Variant Id",
            "Product", "Variant", "Quantity", "Value", "Return Type", "Return Reason",
        ),
        "note": (
            "ReturnZap pull via the getReturns Apps Script function, key in "
            "Script Properties (Yotpo-style secret boundary). ONE tab holds "
            "BOTH stores (US + UK) -- Country is ship-to (UK/US/ROW), Value "
            "is the line refund value. Tab is named 'API: Returns' in the "
            "Sheets UI, but CONFIRMED 2026-08-12: the colon is stripped on "
            "xlsx export (same quirk as shopify_product_data below) -- the "
            "literal tab name after download is 'API Returns', which is "
            "what normalize_returns_zap_xlsx actually looks for. WHEN THIS "
            "SOURCE MOVES: only the "
            "file_id line above changes -- tab/expected_columns stay pinned "
            "here too, so a rename elsewhere is caught by preflight, not "
            "silently followed. KNOWN BUGS, not fixed here (no Apps Script "
            "connector available to this build -- flag at the source):\n"
            "  (1) 2026-08-11 initial pull: exact full-row duplicates "
            "(1,755 raw / 450 distinct, one row x139) -- almost certainly "
            "appending instead of clearing-then-writing. dedupe_returns_"
            "export() fixes this on read, every time, regardless of scale.\n"
            "  (2) 2026-08-11 re-check, after the archive/resolved-state fix "
            "widened the pull to 16,468 raw rows: US now has real data "
            "through 2026-08-01, but GB/UK stops dead at Return Date "
            "2025-03-03 and never resumes -- a store-specific outage (UK "
            "auth/connection/date-filter), not the general archive/"
            "pagination gap the fix already addressed for US. This is why "
            "assert_returns_overlap_sales legitimately still fails for any "
            "period after Feb 2025 that includes UK orders."
        ),
    },
    "ship_sheet": {
        "connector": "google_drive",
        "file_id": "1wErCQzW2pi8-OnzN2bbuu5bNkTFt1PGhmBumEFzQMaM",
        "tab": "IP Import",
        "note": (
            "INBOUND PURCHASE ORDERS ONLY (Ordered/Received/Remaining per PO, "
            "vendor, cost price) -- stock on order/arriving, never on-hand. "
            "Not read by any fetch function here; kept in the registry only "
            "so nobody re-discovers it and wires it into weeks-cover by "
            "mistake. Use shopify_product_data for on-hand inventory."
        ),
    },
    "revenue_tracker": {
        "connector": "google_drive",
        "file_id": "1gFdzyNvA1IBN1Iy0hwjpf0sXeNHNA-PCudFTXLhkpQ0",
        "tab": "ACT DAY",
        "kind": "reference",
        "note": (
            "REFERENCE, NOT A FEED (item 8, 2026-08-12) -- read-only, used only "
            "to compute an on-dashboard reconciliation LINE tying dashboard "
            "Total Revenue to this tracker's own Gross Revenue figure "
            "(tracker_gross - tracker_shipping ~= dashboard_total, shipping is "
            "the only allowed difference). Never a source any builder computes "
            "its own headline FROM -- the internal country reconciliation "
            "(uk+us+row==total) stays the real integrity check.\n"
            "STILL OPEN, do not wire the actual tie-out until confirmed with "
            "Annie (she offered a walkthrough -- lock the precise cell/"
            "definition with her, don't infer it):\n"
            "  - Exact source figure: candidate found 2026-08-12 by reading the "
            "sheet directly -- 'ACT DAY' has a 'Gross Revenue' block (D2C: "
            "rows 139-177, its own UK/US/Other sub-rows + a 'TOTAL' row at "
            "167; B2B: rows 297+, analogous shape) with each region's own "
            "'Shipping' sub-row already broken out -- but which exact row/sum "
            "Annie means by 'Gross Revenue' hasn't been confirmed with her.\n"
            "  - VAT basis: the sheet has a SEPARATE 'Incl VAT' row (ACT DAY "
            "row 510) used only for the ROAS efficiency metric, distinct from "
            "the 'Gross Revenue' block above -- circumstantial evidence Gross "
            "Revenue itself is ex-VAT (matching this dashboard's own basis), "
            "but not confirmed with Annie.\n"
            "  - Region mapping: tracker rows are literally 'UK'/'US'/'Other' "
            "-- Other -> ROW mapping is very likely direct but unconfirmed."
        ),
    },
    "matrixify_uk": {
        "connector": "mcp",
        "server": "Matrixify-PlankUK",
        "note": "UK store order exports (Orders entity, filtered per period).",
    },
    "matrixify_us": {
        "connector": "mcp",
        "server": "Matrixify-PlankUS",
        "note": "US store order exports (Orders entity, filtered per period).",
    },
}

# Where a fetched snapshot lands -- every builder reads from these paths,
# whether freshly refreshed or a few days stale. trading/source/*.xlsx here
# is a rolling product-reference snapshot (refreshed every run), NOT a
# frozen monthly export -- CLAUDE.md's "never overwrite a committed month's
# export" exception is about the Matrixify order CSVs, not this file.
SNAPSHOT_DIR = os.path.join(_REPO_ROOT, "source")
TRADING_SNAPSHOT_DIR = os.path.join(_REPO_ROOT, "trading", "source")

LINE_DETAIL_SNAPSHOT = os.path.join(TRADING_SNAPSHOT_DIR, "line_detail.xlsx")
SHOPIFY_INVENTORY_SNAPSHOT = os.path.join(TRADING_SNAPSHOT_DIR, "shopify_inventory.csv")
RETURNS_ZAP_SNAPSHOT = os.path.join(SNAPSHOT_DIR, "returns_zap.csv")
YOTPO_REVIEWS_SNAPSHOT = os.path.join(SNAPSHOT_DIR, "yotpo_reviews.csv")


def matrixify_orders_snapshot(store, period=None):
    """trading/source/orders_ALL_<STORE>.csv -- ONE rolling snapshot per
    store (2026-08-12, PII incident follow-up), superseding the old
    per-period file (orders_<period>_<STORE>.csv, CLAUDE.md's old commit
    exception -- retired, see docs/2026-08-12_pii_remediation.md). `period`
    is accepted so existing call sites (contract.py's LM/LY bootstrap) don't
    need to change, but it's ignored: emit_contract_from_matrixify and
    _matrixify_headline_totals both filter parsed rows by order_month AFTER
    loading, so the same rolling file correctly serves any period inside its
    window -- it never needs to be re-scoped per period. This file is never
    committed either (source/ is gitignored, no exceptions, same as every
    other snapshot here).
    """
    return os.path.join(TRADING_SNAPSHOT_DIR, f"orders_ALL_{store.upper()}.csv")


def matrixify_orders_snapshot_covers(csv_path, period):
    """Fail-loud guard for the rolling snapshot: existence alone no longer
    proves a period's data is actually IN the file (unlike the old one-
    file-per-month convention, where existence was a perfect proxy -- one
    file per month, so "exists" and "covers this month" were the same
    fact). Scans the file's own Created At column (same parse/timezone
    logic as trading/matrixify_source.py's order_month_london -- duplicated
    here in miniature rather than imported, to keep common/ independent of
    trading/, not the other way round) and returns True only if at least
    one row actually falls in `period`. Callers should treat False the same
    as a missing file -- fall back or fail loud, never silently proceed to
    a zero LM/LY.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if not os.path.exists(csv_path):
        return False
    london = ZoneInfo("Europe/London")
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            created = row.get("Created At")
            if not created:
                continue
            dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S %z")
            if dt.astimezone(london).strftime("%Y-%m") == period:
                return True
    return False


# ---------------------------------------------------------------------------
# Normalizers -- turn a raw connector download into the shape the EXISTING
# parsers already expect. Each takes a raw downloaded file/bytes and writes
# the matching *_SNAPSHOT path; none of them change any builder's parser.
# ---------------------------------------------------------------------------

def _clean_cell(v):
    """openpyxl reads whole-number cells back as Python floats (12021886714236.0),
    even though the underlying Shopify/Matrixify value is a true integer (an
    order ID, a line ID, a quantity). Left as-is, that trailing '.0' becomes
    part of the string when written to CSV -- harmless for matrixify_source.py
    (which runs everything through float() anyway), but silently fatal for
    returns/build.py's order-id JOIN: the sales side reads this CSV with plain
    csv.DictReader (no numeric coercion, so "12021886714236.0" stays exactly
    that), while the returns side casts ReturnZap's Order Id through pandas'
    Int64 (which cleans "5654760980759.0" -> "5654760980759") -- two clean-
    looking IDs that never actually match as strings. CONFIRMED 2026-08-12:
    this was found by the returns builder's own overlap gate correctly
    refusing to publish a 0%-overlap join rather than a subtler wrong number.
    """
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def normalize_matrixify_orders_sheet(raw_xlsx_path, store, out_path=None):
    """raw_xlsx_path: the 'Matrixify Orders (Auto-Refresh)' Drive sheet,
    downloaded as-is -- three tabs (Sheet1, 'Matrixify Orders UK',
    'Matrixify Orders US'), populated daily by a small Apps Script that
    fetches Matrixify's own fixed-URL scheduled export (see
    docs/2026-08-12_matrixify_sheet_bridge.md). Extracts the one tab for
    `store` ('uk'/'us') to a plain CSV at out_path, same column names
    Matrixify itself uses (e.g. 'Line: Type') -- trading/matrixify_source.py
    reads this exactly like it read the old per-month exports, no parser
    change needed. Blank cells come back as None from openpyxl; written out
    as empty strings, matching how csv.DictReader expects a missing value.
    Whole-number floats are cleaned to plain integers (_clean_cell) before
    writing -- see its docstring for why this matters well beyond cosmetics.
    """
    import openpyxl

    out_path = out_path or matrixify_orders_snapshot(store)
    tab_name = f"Matrixify Orders {store.upper()}"
    wb = openpyxl.load_workbook(raw_xlsx_path, read_only=True, data_only=True)
    ws = wb[tab_name]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in rows:
            if row[0] is None:  # trailing blank row
                continue
            writer.writerow(["" if v is None else _clean_cell(v) for v in row])
    return out_path


def normalize_returns_zap_xlsx(raw_xlsx_path, out_path=RETURNS_ZAP_SNAPSHOT):
    """raw_xlsx_path: the ReturnZap Apps Script's sheet, downloaded as-is
    (single relevant tab, 'API: Returns' in the Sheets UI -- populated by
    the getReturns Apps Script function, see
    claude/returnzap_setup_runbook.md in project knowledge). CONFIRMED
    2026-08-12: the colon is stripped on xlsx export (same quirk already
    documented for 'IN: Shopify Product Data' -> 'IN Shopify Product Data'
    above) -- the literal tab name in a downloaded xlsx is 'API Returns',
    not 'API: Returns'. Extracts that tab to a plain CSV at out_path in the
    sheet's own native column names (Order Id, SKU, Quantity, Stage, ...)
    -- load_returns_zap_snapshot()/load_returns_export_from_sheet() read
    this directly, no parser change needed. Same blank-cell handling as
    normalize_matrixify_orders_sheet: openpyxl's None becomes an empty
    string, matching what pd.read_csv would see from a real CSV export.
    """
    import openpyxl

    wb = openpyxl.load_workbook(raw_xlsx_path, read_only=True, data_only=True)
    ws = wb["API Returns"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in rows:
            if row[0] is None and all(v is None for v in row):  # trailing blank row
                continue
            writer.writerow(["" if v is None else _clean_cell(v) for v in row])
    return out_path


def normalize_line_detail_xlsx(raw_xlsx_path, out_path=LINE_DETAIL_SNAPSHOT):
    """raw_xlsx_path: the live Drive sheet, downloaded as-is (2 leading admin
    rows above the real header, confirmed 2026-08-11). Writes a new xlsx at
    out_path with the real header on row 1 and every data row below it,
    unchanged otherwise (Barcode column and all) -- trading/line_detail.py's
    load_line_detail() takes row 1 as header unconditionally, so this is the
    only change needed for the live sheet to parse exactly like the
    committed local snapshot always has.
    """
    import openpyxl

    src = openpyxl.load_workbook(raw_xlsx_path, read_only=True, data_only=True)
    ws = src["Line Detail"]
    rows = list(ws.iter_rows(values_only=True))
    header_idx = next(
        i for i, row in enumerate(rows)
        if row and any(str(c).strip() == "SKU" for c in row if c is not None)
    )
    out = openpyxl.Workbook()
    out_ws = out.active
    out_ws.title = "Line Detail"
    for row in rows[header_idx:]:
        out_ws.append(row)
    out.save(out_path)
    return out_path


def normalize_shopify_inventory_xlsx(raw_xlsx_path, out_path=SHOPIFY_INVENTORY_SNAPSHOT):
    """raw_xlsx_path: the Yotpo-bound sheet downloaded as xlsx. Extracts the
    `IN Shopify Product Data` tab's SKU/Inventory columns to a flat CSV --
    trading only needs on-hand units by SKU, not the other ~30 product-feed
    columns in that tab.
    """
    import openpyxl

    wb = openpyxl.load_workbook(raw_xlsx_path, read_only=True, data_only=True)
    ws = wb["IN Shopify Product Data"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    sku_idx = header.index("SKU")
    inv_idx = header.index("Inventory")
    df = pd.DataFrame(
        [(r[sku_idx], r[inv_idx]) for r in rows if r[sku_idx]],
        columns=["SKU", "Inventory"],
    )
    df.to_csv(out_path, index=False)
    return out_path


def normalize_yotpo_reviews_xlsx(raw_xlsx_path, out_path=YOTPO_REVIEWS_SNAPSHOT):
    """raw_xlsx_path: the Yotpo-bound sheet downloaded as xlsx. Extracts the
    `API Yotpo Reviews` tab to a flat CSV.

    Found 2026-08-12 refreshing this snapshot for the first time since the
    committed one: openpyxl reads a whole-number Google Sheets cell (ID,
    Score, Votes Up/Down -- confirmed the affected columns) back as a Python
    float, so a naive dump renders "866558445" as "866558445.0". Confirmed
    by diffing a fresh pull against the committed snapshot: every row
    "changed" until this was stripped, at which point the two were
    byte-identical except for 2 genuinely new reviews. Cast every
    whole-number float to int before writing so this doesn't recur on every
    future refresh.
    """
    import openpyxl

    wb = openpyxl.load_workbook(raw_xlsx_path, read_only=True, data_only=True)
    ws = wb["API Yotpo Reviews"]

    def _clean(v):
        if isinstance(v, float) and v.is_integer():
            return int(v)
        return v

    raw_rows = [[_clean(v) for v in row] for row in ws.iter_rows(values_only=True)]
    # Trailing blank column/rows (found 2026-08-12): ws.max_row/max_column on
    # the live sheet run past the real data by a formatting artifact -- an
    # all-blank trailing header cell and 2 fully-blank trailing rows, neither
    # present in the committed snapshot. Confirmed via diff against it that
    # trimming these (not the real header width) reproduces it exactly bar
    # genuinely new reviews.
    header = raw_rows[0]
    width = max(i + 1 for i, v in enumerate(header) if v not in (None, ""))
    rows = [row[:width] for row in raw_rows if any(v not in (None, "") for v in row[:width])]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerows(rows)
    return out_path


def dedupe_returns_export(df):
    """Drop exact full-row duplicates from a raw ReturnZap pull before
    anything downstream sees it.

    Found 2026-08-11: the live sheet had 1,755 raw rows but only 450
    distinct -- one return duplicated 139 times, with Stage/Status/RMA
    Number/Return Date byte-identical across every copy. This is NOT
    stage-history (genuine multi-RMA returns on the same order+SKU do exist
    and legitimately differ by RMA Number/Return Date -- confirmed
    separately those are untouched by this function; only true full-row
    copies are dropped). Left unfixed, returns/build.py's existing
    sku+order dedupe (`.agg(qty=("qty", "sum"))`) would SUM across the
    duplicate copies too, overstating returned units/cash by ~4x on average
    and up to 139x for specific rows -- almost certainly the getReturns
    Apps Script appending on every run instead of clearing-then-writing
    (the documented Yotpo pattern). Returns (deduped_df, n_dropped) -- the
    caller must report n_dropped, never silently absorb it (see
    common/reconciliation_gate.assert_bucket_reported).
    """
    before = len(df)
    deduped = df.drop_duplicates().reset_index(drop=True)
    dropped_exact = before - len(deduped)

    # Second pass, found 2026-08-12 re-verifying order- vs unit-level
    # counting after the full-history pull (74,218 raw rows): a small
    # residual pattern survives the exact-row pass -- the SAME line (same
    # Order Id + SKU + RMA Number + Shopify Variant Id + Quantity)
    # re-captured moments apart with exactly one other field updated
    # (Return Date bumped a few seconds, or Stage/Status/Value/Return
    # Reason changed) -- a state-snapshot artifact, not a second real
    # return. Confirmed on 10 of 15,366 post-exact-dedupe rows (2026-08-12):
    # every affected order already has >=1 OTHER row for that order, so
    # returns/build.py's order-level count (`ret["order"].nunique()`) is
    # structurally unaffected either way -- nunique() on order already
    # collapses duplicate rows for the SAME order to one, regardless of
    # how many SKU-lines or duplicate-lines exist within it (verified with
    # a synthetic 5x-duplicate case). This pass only prevents units_returned
    # (a secondary, non-headline metric per ROADMAP.md's locked "orders-
    # based rate" decision) from double-counting the same physical unit --
    # keeps the row with the latest Return Date (the most up-to-date
    # snapshot) per (Order Id, SKU, RMA Number, Shopify Variant Id, Quantity).
    key_cols = ["Order Id", "SKU", "RMA Number", "Shopify Variant Id", "Quantity"]
    if all(c in deduped.columns for c in key_cols) and "Return Date" in deduped.columns:
        sort_col = pd.to_datetime(deduped["Return Date"], utc=True, errors="coerce")
        deduped = (
            deduped.assign(_sort=sort_col)
            .sort_values("_sort")
            .drop_duplicates(subset=key_cols, keep="last")
            .drop(columns="_sort")
            .sort_index()
            .reset_index(drop=True)
        )
    dropped_snapshot = before - dropped_exact - len(deduped)
    dropped = before - len(deduped)
    if dropped_snapshot:
        print(f"dedupe_returns_export: also collapsed {dropped_snapshot} same-line "
              f"snapshot-update row(s) (same order+SKU+RMA+variant+quantity, one field "
              f"changed between captures) -- kept the latest Return Date per line",
              file=__import__("sys").stderr)
    return deduped, dropped


def load_shopify_inventory(csv_path=SHOPIFY_INVENTORY_SNAPSHOT):
    """-> {sku: on_hand_units}, from the landed shopify_product_data
    snapshot. Never sourced from ship_sheet -- see its SOURCES note.
    """
    df = pd.read_csv(csv_path)
    df["SKU"] = df["SKU"].astype(str).str.strip()
    df = df[df["SKU"].notna() & (df["SKU"] != "") & (df["SKU"].str.lower() != "nan")]
    inv = pd.to_numeric(df["Inventory"], errors="coerce").fillna(0)
    return dict(zip(df["SKU"], inv))


def write_snapshot_metadata(csv_path, file_id, source_modified_time, fetched_at):
    """Record which Drive file/version a landed snapshot came from --
    file_id + the source's own last-modified time, alongside when this
    fetch happened (caller-supplied, real wall-clock time -- this module
    never calls datetime.now() itself). Lets a future run tell whether the
    live sheet has changed since this snapshot was taken without
    re-downloading to check. Sidecar file, `<csv_path>.meta.json` --
    doesn't change how any reader loads the CSV itself.
    """
    meta = {
        "file_id": file_id,
        "source_modified_time": source_modified_time,
        "fetched_at": fetched_at,
    }
    meta_path = csv_path + ".meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    return meta_path


def check_yotpo_freshness(csv_path=YOTPO_REVIEWS_SNAPSHOT, as_of=None, max_staleness_days=14):
    """Part 4 (period-from-prompt, 2026-08-12): Yotpo reviews follow the
    REGION filter only, per the returns brief -- never the trading/returns
    period. review_feedback.py already applies no period filter (confirmed
    2026-08-12), so there is nothing to slice here; this just confirms the
    snapshot itself is present and recent, the same freshness pattern as
    returns/validate.py's check_freshness. Warns (does not abort) if the
    snapshot's newest `Created At` is older than max_staleness_days.
    """
    from datetime import date
    import sys as _sys

    if as_of is None:
        as_of = date.today()
    df = pd.read_csv(csv_path)
    dates = pd.to_datetime(df["Created At"], format="%d-%b-%Y", errors="coerce").dt.date.dropna()
    newest = dates.max()
    staleness = (as_of - newest).days
    if staleness > max_staleness_days:
        print(f"check_yotpo_freshness: WARNING -- newest review Created At is {newest} "
              f"({staleness} days before {as_of}) -- the Yotpo pull may not have run recently.",
              file=_sys.stderr)
    return newest, staleness


def load_returns_zap_snapshot(csv_path=RETURNS_ZAP_SNAPSHOT):
    """Read the landed ReturnZap snapshot and apply dedupe_returns_export.
    Returns (df, n_dropped) in the sheet's own native column names (Order
    Id, SKU, Quantity, Stage, ...) -- returns/build.py's
    load_returns_export_from_sheet() maps these to the standardized shape
    the rest of the pipeline already consumes.
    """
    df = pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False)
    return dedupe_returns_export(df)


# ---------------------------------------------------------------------------
# Preflight -- validates already-landed snapshots (shape/columns/non-empty).
# Does NOT check live connector reachability itself (see module docstring:
# that requires the Drive/Matrixify MCP tools, only reachable from an
# interactive Claude session) -- run this after a refresh to confirm what
# landed is actually usable, naming the exact source on failure.
# ---------------------------------------------------------------------------

_EXPECTED_COLUMNS = {
    LINE_DETAIL_SNAPSHOT: None,  # checked via load_line_detail() itself (header lookup by name)
    SHOPIFY_INVENTORY_SNAPSHOT: {"SKU", "Inventory"},
    # Derived from SOURCES["returns_zap"] rather than re-listed -- one place
    # to update if the sheet's columns ever change.
    RETURNS_ZAP_SNAPSHOT: set(SOURCES["returns_zap"]["expected_columns"]),
}


def preflight_returns_zap(csv_path=RETURNS_ZAP_SNAPSHOT):
    """Returns-connection-scoped preflight: fails loud naming the exact
    problem, never compares against a prior report (that's what the
    reconciliation gate/regression fixture do, not this). Checks:
      - the snapshot loads with every expected column present, non-empty;
      - the RMA Number + SKU duplicate count is logged (not silently
        absorbed) -- there is no separate "line" id in this feed, so
        RMA+SKU is the finest natural key it actually carries; a remaining
        duplicate there is a genuine multi-line return, not noise (do not
        confuse with the exact-full-row dedupe, a stricter, separate check);
      - BOTH markets are present -- non-zero US and UK/GB row counts,
        catching a missing store key outright.

    Also logs (informational -- a staleness signal, not a shape one) the
    most recent Order Date per market. Found 2026-08-11: the UK pull
    stopped dead at Return Date 2025-03-03 while US continued to
    2026-08-01 -- a bare non-zero-rows check does NOT catch this (UK rows
    exist, just none recent), so this is logged every run to make a future
    market-specific staleness visible without re-deriving it by hand.
    """
    if not os.path.exists(csv_path):
        raise AssertionError(f"PREFLIGHT FAILED: returns_zap -- missing snapshot at {csv_path}")

    df = pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False)
    expected_cols = set(SOURCES["returns_zap"]["expected_columns"])
    missing_cols = expected_cols - set(df.columns)
    problems = []
    if missing_cols:
        problems.append(f"returns_zap: missing column(s) {sorted(missing_cols)} in {csv_path}")
    if df.empty:
        problems.append(f"returns_zap: snapshot at {csv_path} is empty")
    if problems:
        raise AssertionError("PREFLIGHT FAILED:\n  " + "\n  ".join(problems))

    deduped, n_full_dupes = dedupe_returns_export(df)
    n_rma_sku_dupes = len(deduped) - len(deduped.drop_duplicates(subset=["RMA Number", "SKU"]))
    print(f"preflight[returns_zap]: {len(df)} raw rows -> {len(deduped)} after exact-duplicate-row "
          f"dedupe ({n_full_dupes} dropped); {n_rma_sku_dupes} row(s) still share an RMA Number+SKU "
          f"key after that (genuine multi-line returns, not noise -- returns/build.py's own gate is "
          f"sku+order, the ROADMAP.md-locked key, not RMA+SKU+line).")

    counts = deduped["Country"].value_counts()
    us_rows = int(counts.get("US", 0))
    uk_rows = int(counts.get("GB", 0) + counts.get("UK", 0))
    if us_rows == 0:
        problems.append("returns_zap: zero US rows in the snapshot -- a market is silently missing")
    if uk_rows == 0:
        problems.append("returns_zap: zero UK/GB rows in the snapshot -- a market is silently missing")
    if problems:
        raise AssertionError("PREFLIGHT FAILED:\n  " + "\n  ".join(problems))
    print(f"preflight[returns_zap]: both markets present (US={us_rows} rows, UK={uk_rows} rows)")

    order_date = pd.to_datetime(deduped["Order Date"], utc=True, errors="coerce")
    for label in ("US", "GB"):
        sub_dates = order_date[deduped["Country"] == label]
        if len(sub_dates):
            print(f"preflight[returns_zap]: most recent {label} Order Date in the snapshot: "
                  f"{sub_dates.max()} -- a market with only OLD rows passes the non-zero check "
                  f"above but is effectively stale; check this before trusting a build for a "
                  f"recent period.")
    return {"us_rows": us_rows, "uk_rows": uk_rows, "exact_duplicates_dropped": n_full_dupes}


def preflight():
    """Assert every landed snapshot exists, is non-empty, and has the
    columns its reader expects -- fails loud, naming the exact source.
    """
    problems = []

    if not os.path.exists(LINE_DETAIL_SNAPSHOT):
        problems.append(f"line_detail: missing snapshot at {LINE_DETAIL_SNAPSHOT}")
    else:
        import openpyxl
        wb = openpyxl.load_workbook(LINE_DETAIL_SNAPSHOT, read_only=True, data_only=True)
        if "Line Detail" not in wb.sheetnames:
            problems.append(f"line_detail: no 'Line Detail' sheet in {LINE_DETAIL_SNAPSHOT}")
        else:
            header = next(wb["Line Detail"].iter_rows(values_only=True))
            if "SKU" not in header:
                problems.append(
                    f"line_detail: header row has no SKU column ({LINE_DETAIL_SNAPSHOT}) -- "
                    f"was normalize_line_detail_xlsx run on the raw download?"
                )

    for path, expected_cols in ((SHOPIFY_INVENTORY_SNAPSHOT, _EXPECTED_COLUMNS[SHOPIFY_INVENTORY_SNAPSHOT]),
                                (RETURNS_ZAP_SNAPSHOT, _EXPECTED_COLUMNS[RETURNS_ZAP_SNAPSHOT])):
        if not os.path.exists(path):
            problems.append(f"{os.path.basename(path)}: missing snapshot at {path}")
            continue
        df = pd.read_csv(path, encoding="utf-8-sig", nrows=5)
        missing = expected_cols - set(df.columns)
        if missing:
            problems.append(f"{os.path.basename(path)}: missing column(s) {sorted(missing)} in {path}")
        if os.path.getsize(path) < 10:
            problems.append(f"{os.path.basename(path)}: empty file at {path}")

    if os.path.exists(RETURNS_ZAP_SNAPSHOT):
        try:
            preflight_returns_zap()
        except AssertionError as e:
            problems.append(str(e))

    if problems:
        raise AssertionError("PREFLIGHT FAILED:\n  " + "\n  ".join(problems))
    print("preflight: all landed snapshots present and shaped as expected")


if __name__ == "__main__":
    preflight()
