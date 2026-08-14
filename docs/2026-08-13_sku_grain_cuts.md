# SKU-grain cuts, and why the By-SKU tab can't be copied from the hand-built report

_13 Aug 2026. Decision + finding. Triggered by: "change the SKU list part of the companion
Excel to match this trade report's By SKU tab", then "back fill, don't leave things blank"._

## The finding that shaped the design

The hand-built Monthly Trading Report's `By SKU` tab carries 114 columns — a full channel ×
country cross, realised margins per cut, and per-SKU LM-1 / LY LM blocks. The obvious way to
"match" it was to read those columns out of the report. **That would have been wrong, and it
took a tie-check to see why.**

Comparing each committed contract against its own hand-built report, per SKU, on revenue:

| Month | Pipeline total | Report total | Gap | SKUs tying within 0.5% |
|---|---|---|---|---|
| Apr 2026 | £455,525 | £491,189 | −7.4% | 158 / 695 |
| May 2026 | — | — | −7.3% (overlap sum) | 205 / 702 |
| Jun 2026 | £484,390 | £504,721 | −4.1% | 206 / 715 |

Units diverge too (Jun: 25,360 vs 26,548). Two separate things are going on:

1. **A systematic basis difference.** The aggregate gaps (7.4 / 7.3 / 4.1) track the in-window
   returns share of gross almost exactly — the same figures recorded for the parked Revenue
   Tracker question (Apr 7.9%, May 7.3%, Jun 5.3%). The report is gross; this pipeline is net
   (locked, `trading_logic_spec.md`). **This does not resolve which source is authoritative** —
   that's Lena's, and still parked — it just shows the gross/net split reaches SKU grain.
2. **Bidirectional per-SKU divergence.** Some SKUs are *higher* in the pipeline than the report
   (e.g. Apr `CDB-BOBB-175-1BR`: 14,748 vs 14,405). Returns netting alone can only move one way,
   so per-line discount attribution differs as well. Median absolute divergence: 2.2% (Jun),
   5.6% (Apr).

Consequence: importing the report's splits onto our rows would put a UK/US/D2C/B2B breakdown
next to a revenue figure computed a different way. Every share and margin on that row would be
false, and per-SKU `uk + us + row` would not sum to the SKU's own total. **Not done.**

## What was built instead

Everything at SKU grain is derived from the same enriched order lines and the same `line_ab` as
the committed figures.

- **`common/sku_cuts.py`** — one definition of a SKU's cuts: `total`, three countries, two
  channels, six crosses. Enforces three rules: ROW is an explicit cut (the report has none, so
  its per-SKU UK + US silently misses ROW — July 2026: £9,359, 1.75%); GM per cut is *realised*
  (`1 − units × supplier_cost / revenue`), not the catalogue `gm_pct`, which would print the same
  number in every cut; ratios with an empty denominator are `None`, never `0.0`.
- **`trading/contract.py`** — accumulates and emits those cuts, plus `row`/`row_u` (previously
  dropped at SKU grain entirely) and the catalogue attributes the tab needs
  (`item_type`/`material`/`style`/`is_kit`/`supplier_cost`, all already carried per line by
  `enrich_lines`). Contract version **1.0 → 1.1**. `assert_cuts_reconcile` is the SKU-grain twin
  of the headline country gate: country, channel and cross cuts must each sum to the SKU's total,
  or the build fails.
- **`trading/excel_companion.py`** — the 70-column `By SKU` tab, block-banded in the report's own
  order and wording. LM-1 and LY LM come from the prior periods' **committed contracts** (same
  net basis, no re-derivation) — chaining, exactly as `lq`/`ly` already worked.
- **`trading/backfill_sku_grain.py`** — the additive 1.0 → 1.1 back-fill. Re-derives a committed
  period from its own order lines, gates every SKU on matching the committed revenue and units,
  merges **only** the new keys, and asserts no pre-existing key or block changed. It cannot alter
  a published figure: a mismatch aborts the run instead.

## Two deliberate departures from the report's layout

- **A ROW block.** Additive; required by the reconciliation contract.
- **"Net Sales £" where the report says "Gross Sales £".** Same number, honest header — the
  report's label names a figure that has returns netted out. Carrying the wrong word into a new
  deliverable propagates it.

## Not matched, and why

- **cc Size (mm), Available?, Screw?, IMG, US Supplier Cost incl Tariffs, Product Size Leading
  Dimension.** Not read from the Line Detail. `line_detail.OPTIONAL_COLUMN_MAP` now holds the
  header strings **as they appear in the report, unverified against the master** — the loader
  prints which it couldn't find, so the first run over the live sheet tells us the real names.
  Do not guess-fix a name there.
- **LM-1 / LY LM by channel and country for periods whose prior contract is still 1.0.** Falls
  back to the contract's own `lq`/`ly` totals until those months are back-filled.

## Back-fill status

Blocked on data, not code: `Apr`/`May`/`Jun 2026` are outside the rolling order snapshot's
window, so they cannot be re-derived until their archive exports are staged into the snapshot
location. The script fails loud with that instruction rather than falling back to the report.

Until a month is back-filled, its companion renders the **narrow 12-column** By-SKU tab — every
cell populated — with a note naming the back-fill command. Deliberately not an exception:
rebuilding a published month's companion has to keep working.

## Verification

`trading/tests/test_sku_cuts.py` — partition, realised-GM-differs-by-channel, unknown-cost →
`None`, negative/zero denominators, serialize round-trip, the reconcile gate actually firing on a
10% leak, the full tab writing and tying (`uk + us + row` = total on a real row), and the narrow
fallback. All pass. `test_quarterly.py` passes unchanged. `test_contract.py`,
`test_line_detail_enrichment.py` and `test_three_way_regression.py` fail on missing gitignored
`source/` feeds — confirmed identical failures on a clean checkout, unrelated to this change.
