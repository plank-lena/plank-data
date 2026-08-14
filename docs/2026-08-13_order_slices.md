


# Order slices replace the rolling Drive snapshot — 13 Aug 2026

Supersedes `docs/2026-08-12_matrixify_sheet_bridge.md`. That design was live for about
24 hours before it stopped working.

## What was broken

Colleagues couldn't generate reports. Three separate faults wearing one symptom.

**1. The orders sheet couldn't be downloaded at all.** `download_file_content` returned
`File too large for export.` The sheet was 3.78MB. The ceiling is not on file size — it
is on Google's native-to-xlsx **conversion**, which fails before any bytes move. A 7.76MB
plain CSV downloaded fine the same day. That is the whole reason the storage format
changed.

Splitting the workbook into separate UK and US sheets (11:32 on 13 Aug) treated the
symptom. Both halves were still Google-native, so both still went through the failing
conversion.

**2. A 400-day window cannot serve a year-on-year comparison.** As of 13 Aug 2026, 400
days back reached only 9 Jul 2025. So:

| Report | Last-year period | Days back (start → end) | In window? |
|---|---|---|---|
| Aug 2026 | Aug 2025 | 377 → 347 | fully |
| Jul 2026 | Jul 2025 | 408 → 378 | first 8 days missing |
| Jun 2026 | Jun 2025 | 438 → 409 | no |
| Q2 2026 | Q2 2025 | 499 → 409 | no |
| Q1 2026 | Q1 2025 | 589 → 500 | no |

August 2026 was the only period whose comparison still fit, and it would have fallen out
within five weeks. A month needs roughly 13 months plus days-elapsed of history; a
quarter needs up to 550 days. The 400-day figure was set 35 days above a 365-day floor
that was never the real requirement.

**3. The guards passed on partial and missing data.** `matrixify_orders_snapshot_covers()`
returned true on **one** row in the period, and was joined with **`or`** across stores —
so a report could be built from UK alone. The country reconciliation gate cannot catch
that: `uk + us + row` and the total all derive from the same rows, so a missing store
gives `us = 0` and still ties to the penny. With the Revenue Tracker tie-out parked,
nothing was checking. The last-year-unavailable branch printed to stderr and published
with the comparison zeroed.

`returns/build_dashboard.py` had **no coverage guard at all** — worse than trading, which
at least had a flawed one.

## What replaced it

**Plain CSV files in Drive, one per store per month.** Folder
`11RudAVLppGyg5nvlzAIbtwHKjmS49kUz`, manifest `1gB7QmexvcQgKghXWgMRD2czN-C1UYPZD`. All
`text/csv` — **do not let Drive convert these to Sheets**, that is the broken path. Local
path convention is `trading/source/orders_<period>_<STORE>.csv`, restoring the per-period
convention `contract.py` never stopped passing a period argument for.

Sizes: UK months 1.2–2.3MB, US months ~0.4MB. Monthly granularity was the right call —
quarterly slices would have put a single dashboard run near 20MB.

**History: Jan 2025 onward**, from 14 one-shot Matrixify window exports (7 quarters × 2
stores), sliced by `trading/tools/backfill_slice.py`. 42 slices, Dec 2024 spillover
through Aug 2026 partial.

**Daily refresh:** Apps Script `runDaily` fetches a 60-day window per store
(`plank_orders_current_uk` / `_us`, daily 03:30), slices by order month, merges into the
Drive files, updates the manifest. First clean run 13 Aug 2026.

## Two rules that are load-bearing

**Slice by ORDER, never by row.** `Created At` appears only on an order's Top Row. Every
other row of that order — line items, discounts, shipping, and refund lines — inherits its
month. Refund lines carry the **parent order's** date, so a refund raised in August against
a June order belongs in June's file.

Split row-by-row instead and cross-month refunds land in the wrong file, where
`build_lines()` finds no matching line item and drops them via `skipped_orphan_refunds` —
silently. Returns go to zero, revenue goes up, nothing fails.

**Windows overlap on purpose; merge, don't overwrite.** Matrixify filters `created_at` on
the raw timestamp, but the pipeline buckets in Europe/London — an order at
`2025-03-31 23:45 -0400` is April. So each window was pulled with a day of padding either
side, and writes merge keyed on order `Name`. Verified: 2025-03 UK took 9,443 rows from
the Q1 file then 414 more from the Q2 overlap, nothing lost, re-running changes nothing.

The month rule is duplicated in one place only — `monthLondon_()` in the Apps Script,
because Apps Script cannot import the Python. Verified identical to
`order_month_london()` on seven boundary cases including a DST change. **If the Python
rule changes, change the JavaScript too.** The Python slicer imports the function rather
than copying it.

## Also changed

- `_ORACLE_FIXTURES`, `_PRIOR_PERIOD` and `_LY_MONTH_CONTRACTS` deleted — three
  hand-maintained lookup tables that all stopped at June 2026, which is why July onward
  hit a wall. Now derived from the period.
- The hand-built-workbook fallback is gone. No silent zero-fill; it raises.
- Guards rewritten: full-period coverage, **`and`** across stores, staleness check.
  Returns got a coverage guard for the first time. The shared check lives in `common/`.
- Contracts now carry `pipeline_sha`, `built_at`, `source_slices` and `settled_at`. Before
  this, a contract recorded nothing about how it was built — which is how May 2026 ended up
  with three contracts nobody could rank (£476,292.70 / 27,453 units; £449,518.95 / 25,207;
  £449,518.95 / 23,797).
- Overwrite is two-tier: `--force` works on an unsettled period, a settled one needs
  `--force-settled`. **A contract with no `settled_at` counts as settled**, which protects
  every existing one by default.
- `settled_at` is period end + 30 days. It is a **policy boundary, not a claim the data is
  final** — a refund can land at any age. The real safety net is comparing `source_slices`
  hashes against the manifest.
- Cancelled `plank_trading_orders_uk` (725453551) and `plank_trading_orders_us` (725581897)
  — retired in Aug, still firing daily for nothing.

## Verified

Old code and new code, run against the **same** slice files, matched exactly for
2026-04/05/06/07 — grand totals, country and channel splits, units, and line-by-line.

Note the original check was wrong and was corrected mid-build: diffing a rebuild against
the committed contracts cannot pass, because refunds have landed since those were written
and revenue is net of returns. The gate is old-vs-new on fixed inputs.

## Matrixify facts worth not relearning

- **`repeat.times` is a repeat count, not a run count.** `times: 1` means two runs. For a
  genuine one-shot, leave scheduling and recurrence off entirely — both default to off, the
  job runs immediately once and leaves no schedule to cancel.
- **The `count` field is unreliable for scope.** A 3-month US window reported 31,603 — the
  same as a full-year job. It is the store's total order count. Use `file_size` from
  `matrixify_job_results_download`, or actual row counts.
- **`zip: false` is only safe with a single entity.** With CSV and multiple entities and zip
  off, Matrixify keeps the first entity and silently drops the rest.
- **A sandboxed session cannot fetch a Matrixify export.** Tested both routes:
  `web_fetch` refuses any URL that didn't come from a prior search or fetch, including one
  returned by an MCP tool; bash gets `HTTP 403, x-deny-reason: host_not_allowed`. The MCP
  tools work for creating, polling and locating an export — only the fetch is blocked. Drive
  is the hand-off because it has to be. **Do not "simplify" this away.**
- **The 03:30 `plank_orders_uk` (725466228) / `plank_orders_us` (725595422) pair is off
  limits** — believed linked to a consumer outside this pipeline, owner unidentified. Do not
  cancel, refilter, rename or tidy it. Its 400-day window is not a bug to fix.

## Known limits

- **The daily refresh only covers 60 days.** A refund raised today against a six-month-old
  order will not update that month. Closed months drift slightly high over time. To correct a
  specific old month, re-export that window and re-run the backfill slicer.
- **Whether the Matrixify download tokens expire is unknown.** If they do, the daily script
  works now and stops silently later. Unresolved as of 13 Aug.
- **No colleague has generated a report from a fresh chat yet.** The daily path still runs
  through an Apps Script only Lena can paste and a Drive folder in her account.
```

---