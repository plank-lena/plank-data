# Matrixify orders: Drive-bridged, not direct — 12 Aug 2026

## Why this exists

A sandboxed Claude session (this Project's chat, as opposed to Claude Code running locally with
a normal terminal's network access) cannot reach `app.matrixify.app` directly — it's not in that
environment's network allowlist, and the generic web-fetch tool won't fetch a URL that didn't
come from a web search. The Matrixify MCP tools themselves work fine for creating and polling
export jobs; only the final download step breaks. See the incident's own conversation for the
full diagnosis. Every dashboard built before this date used Claude Code on a Mac, where an
unrestricted `curl` made this invisible.

**The fix mirrors how every other source already works here**: Drive is the hand-off. A small
Apps Script does the one thing that needs unrestricted network access (fetching Matrixify's own
export URL), and lands the result in a Sheet, which the Drive connector reads directly — no
different from Line Detail, inventory, Yotpo, or ReturnZap.

## How it's wired

**Matrixify side** (one scheduled export per store, both already running):
- Fixed `file_name` (`plank_orders_uk` / `_us` — renamed 12 Aug 2026 from `plank_trading_orders_uk`/
  `_us` once returns needed the same export; the old jobs are still running daily in Matrixify
  until manually cancelled from its own job list, harmless but worth tidying up), so the download
  URL never changes — no job ID in it, unlike a one-off export.
- `created_at` filter: `relative date`, 400 days, re-evaluated fresh on every scheduled run —
  a rolling window, not a fixed date range.
- 18 columns — the 16 `trading/matrixify_source.py` reads (`Name`, `Created At`, `Cancelled At`,
  `Payment: Status`, `Source`, `Top Row`, `Company: Name`, `Billing: Company`, `Shipping: Company`,
  `Shipping: Country Code`, `Line: Type`, `Line: ID`, `Line: SKU`, `Line: Quantity`, `Line: Total`,
  `Line: Tax Total`), plus `ID` and `Line: Title` that `returns/build.py`'s `load_matrixify_sales`
  also needs (see "Two real bugs" below) — deliberately still narrower than the old per-month
  exports, which pulled full customer/transaction groups (name, email, phone, address, browser
  IP, card BIN/AVS/CVV) that neither dashboard ever used. See `docs/2026-08-12_pii_remediation.md`.
- Repeats daily, unzipped CSV. Requires **Settings → Security → "Allow downloading your files by
  external services"** enabled in each store (done, both stores, 12 Aug 2026).

**Apps Script side** (Sheet: "Product Dashboard-- Matrixify Orders (Auto-Refresh)",
id `10XoD6qOSr3fwiRE4cGfGrotcRd0YjU60vGvhNJmtE-E`): `UrlFetchApp.fetch` on the two fixed URLs,
`Utilities.parseCsv`, written into two tabs (`Matrixify Orders UK` / `Matrixify Orders US`) on a
daily time trigger. Much simpler than the ReturnZap bridge (`claude/returnzap_setup_runbook.md`)
— no API key, no pagination/checkpointing, because Matrixify's own scheduling does the polling
and the fixed-URL feature does the "always latest" part; the script only needs to fetch once.
Its `MATRIXIFY_URLS` were updated in place (12 Aug 2026) when the export was widened to 18
columns — same Sheet, same tabs, just pointed at the new job's fixed URL.

**ReturnZap side, same pattern, separate sheet**: the existing `getReturns` Apps Script
(`claude/returnzap_setup_runbook.md`) already wrote to a Drive sheet — that part didn't change.
What was missing was the pipeline actually reading it for an arbitrary period (see below).

## Pipeline side

- `common/sources.py`: two new `SOURCES` entries (`matrixify_orders_uk`/`_us`), both pointing at
  the one sheet, different tabs. `matrixify_orders_snapshot(store)` now returns ONE rolling file
  per store (`trading/source/orders_ALL_<STORE>.csv`), not a file per period — it accepts a
  `period` arg for backwards compatibility but ignores it.
- New `normalize_matrixify_orders_sheet(raw_xlsx_path, store, out_path)`, same pattern as
  `normalize_line_detail_xlsx` — extracts one tab to a plain CSV, same column names Matrixify
  itself uses, so `trading/matrixify_source.py` needs no changes at all.
- New `normalize_returns_zap_xlsx(raw_xlsx_path, out_path)` — same pattern again, for the
  ReturnZap sheet's own tab. CONFIRMED 2026-08-12: the tab is named `API: Returns` in the Sheets
  UI but the colon is stripped on xlsx export (same quirk already documented for
  `IN: Shopify Product Data` -> `IN Shopify Product Data`) — the literal tab name after download
  is `API Returns`.
- `trading/build_matrixify_dashboard.py` and `trading/build_matrixify_quarterly_dashboard.py`:
  both now call `common.sources.matrixify_orders_snapshot` instead of constructing
  `orders_<period>_<STORE>.csv` paths inline (that per-period convention is retired — see the
  gitignore fix in `docs/2026-08-12_pii_remediation.md`). Both gained the
  `matrixify_orders_snapshot_covers` fail-loud guard, one per period they touch (the quarterly
  builder checks all 3 constituent months plus each month's own LM/LY bootstrap window).
- New `returns/build_dashboard.py` — the returns twin of `build_matrixify_dashboard.py`. Before
  this, `returns/build.py`'s period-from-prompt entry point (`run_for_period`) and
  connector-sourced ReturnZap loader (`load_returns_export_from_sheet`) existed and were tested,
  but nothing wired them to real data for an arbitrary period — only the hardcoded, single-quarter
  `build_q1.py`/`build_q2.py` scripts existed, pointed at the old per-period files and the
  pre-connector `.numbers` exports. This is that missing wiring. Also adds an overwrite guard
  `render()` itself doesn't have (unlike trading's `write_committed_file`) — refuses to clobber an
  already-written period's output unless `--force` is passed.

## Two real bugs this surfaced, not just plumbing

**The returns join needs two more columns than trading alone needed.** `returns/build.py`'s
`load_matrixify_sales` reads Matrixify's own `ID` column (the raw Shopify order ID — the same
numeric ID space ReturnZap's own `Order Id` column uses) to join a return to its sale, and
`Line: Title` for display. Trading's `matrixify_source.py` never needed either (it keys off
`Name`, the human-readable order number). Rather than run a second, differently-shaped export
just for returns, the one shared scheduled export was widened to 18 columns instead — see
"Matrixify side" above.

**Whole-number floats silently broke the ID join.** openpyxl reads a whole-number cell back as a
Python float (`12021886714236.0`), even though the underlying value is a true integer. Trading's
own parser was unaffected (it runs everything through `float()` anyway), but returns' order-ID
join compares IDs as plain strings — one side (Matrixify's `ID`, read via bare `csv.DictReader`,
no numeric coercion) kept the trailing `.0`; the other side (ReturnZap's `Order Id`, cast through
pandas' `Int64`) didn't — so two "the same" IDs never matched as strings, and the join silently
found zero overlap. Caught because the returns builder's own overlap gate
(`common/reconciliation_gate.py`'s `assert_returns_overlap_sales`) correctly refused to publish a
0%-overlap result rather than a subtler wrong number — exactly the "fail loud" design working as
intended. Fixed once, at the source: `common/sources.py`'s `_clean_cell` strips the trailing `.0`
from any whole-number float before either normalizer writes it to CSV, so this can't recur in any
column, not just the one that happened to surface it.

## Confirmed working end to end (12 Aug 2026, cold clone from GitHub — no Mac, no prior context)

- Monthly trading: `python trading/build_matrixify_dashboard.py "July 2026"` — real, reconciled
  (`total_sales` £566,895.18, UK+US+ROW ties to the penny), reproducible across two independent
  clones.
- Quarterly trading: `build_matrixify_quarterly_dashboard.build(["2026-04","2026-05","2026-06"])`
  against a scratch output path (Q2 2026 itself is already committed — never re-derived) —
  reconciled, £1,535,693.97 total, ties to the penny.
- Returns: `python returns/build_dashboard.py "July 2026"` — real payload (1,508 cube rows),
  correct period label, ReturnZap sheet confirmed current through 2026-08-11.

## Why this needed a real code change, not just new files

`emit_contract_from_matrixify` and `_matrixify_headline_totals` (`trading/contract.py`) already
filter parsed rows by `order_month` AFTER loading whatever CSV they're given — they were never
actually coupled to "one file = one month," that was just an artifact of how the files happened
to be produced. So the same two rolling files correctly serve CM extraction for ANY period
inside their window, and LM/LY bootstrapping for any period whose LM/LY falls inside the window
too, with zero changes to `contract.py` itself.

**The one thing that DID need to change**: the old convention made "the file exists" and "the
file covers this exact month" the same fact, because each file held exactly one month. A rolling
file breaks that equivalence — it always exists, but doesn't always contain a given period. Left
unhandled, a request outside the window would silently produce an all-zero contract (empty
filter, no error) instead of failing loud. `matrixify_orders_snapshot_covers(csv_path, period)`
(`common/sources.py`) is the fix: it actually scans the file's `Created At` column for at least
one matching row before either the CM extraction or the LM/LY bootstrap proceeds; `build()` now
raises `FileNotFoundError` with a clear message instead of building on an empty filter.

## What this does and doesn't cover

**Covers, permanently, no manual steps ever again**: any period in ongoing operation — CM is
always "now," so always inside a rolling window by definition; LM is 1 month back, LY is 12
months back, both comfortably inside 400 days. Once a month is built once, `ROADMAP.md` §5's
contract-chaining rule means it's never re-derived from Matrixify again anyway — chaining to the
committed contract takes over, so the rolling window only ever needs to reach as far back as the
newest never-yet-built month's own LM/LY.

**Doesn't cover**: a colleague asking for a month that's both never been built AND falls outside
the ~400-day window — e.g., a genuine backfill request for a period from years ago. That's not
what this was built for (confirmed with Lena, 12 Aug 2026 — ongoing operation was the actual
requirement, not arbitrary backfill). If it ever comes up: a one-off historical Matrixify export,
staged the same way Yotpo's manual export already is, is the answer — not a standing requirement
this bridge needs to solve.

## Known small residual risk

The rolling window is evaluated fresh on each scheduled Matrixify run ("relative date, 400 days"
recalculates from whenever the job fires), not frozen — so its exact start date drifts forward
by ~1 day per day, same direction as calendar time. This is intentional and is what makes it
"roll." Matrixify's own docs note a `relative date` filter with a `days` interval excludes the
current in-progress day — immaterial here since nobody requests "today" as a reporting period.
