# The 13 Aug migration was half-landed — 14 Aug 2026

Follows `docs/2026-08-13_order_slices.md` and `docs/2026-08-13_sku_grain_cuts.md`. Neither is
superseded; both describe designs that were correct and incompletely applied.

## What was actually wrong

The 13 Aug slice migration changed `common/sources.py` and `trading/contract.py`, and added
*callers* for an API that was never written. The result: **no monthly or returns report could be
generated at all**, from any clone, while the runsheet listed both as live.

| Symptom | Cause |
|---|---|
| `build_matrixify_dashboard.py` → `TypeError` | still called `matrixify_orders_snapshot("uk")` with no period, and passed a *path* to `matrixify_orders_snapshot_covers()`, which now takes a store. Also still OR'd coverage across stores — the exact bug the brief replaced |
| `returns/build_dashboard.py` → `TypeError` | same pre-slice signature; and it had no coverage guard of any kind, so a missing store would have skewed the return-rate denominator silently |
| `backfill_sku_grain.py` → `TypeError` | same. **This is why the SKU-grain back-fill was recorded as "blocked on data, not code."** It was blocked on code. The data was fine |
| `build_matrixify_quarterly_dashboard.py` → `ImportError` | imported `_period_fully_covered` and `_resolve_ly_month_contract` from the monthly builder; neither existed |
| `quarterly.py` → `ImportError` then `TypeError` | imported `contract._settled_at_for`, which didn't exist; then passed `source_slices=` to `emit_contract_from_matrixify()`, which didn't accept it |

Only the quarterly builder's *own* logic had been migrated. Two of the three builders had not.

## The settled tier, now implemented

`code_map.md` documented a two-tier force since 13 Aug: `--force` for an unsettled period,
`--force-settled` once it has settled, and "a contract with no `settled_at` counts as settled."
None of it existed in `contract.py`.

- `_settled_at_for(period_key)` — period end + `SETTLING_DAYS` (30), UTC. Accepts month
  (`2026-06`) and quarter (`2026-Q2`) keys. **Validation: it reproduces the only `settled_at` ever
  stamped by hand — `2026-06` → `2026-07-30T00:00:00+00:00` — exactly.**
- `_existing_is_settled(out_path)` — reads `provenance.settled_at` out of the file being
  overwritten. **A committed file with no readable `settled_at` counts as settled.** That is the
  deliberate default, not a gap: it covers everything written before the field existed, and every
  non-JSON output (an `.xlsx` carries no provenance to read).
- `write_committed_file(..., force_settled=False)` — refuses a settled overwrite on `force` alone.

The monthly path also now stamps `settled_at`, `source_slices` and `pipeline_sha`, none of which
it stamped before. Without `settled_at`, every freshly built month counted as settled the instant
it was written, so the tiers could never tell a provisional month from a closed one.

## Two tables that had gone stale

`_PRIOR_PERIOD` held two entries; `_LY_MONTH_CONTRACTS` held three. **Any month outside them
silently got `prior_month_contract=None` / `ly_month_contract=None`** — so its companion's LM-1 and
LY LM blocks came out empty even when the prior contracts existed on disk. Jan, Feb, Mar and Jul
2026 were all outside. Replaced with `_prior_period()` / `_ly_period()` calendar arithmetic, which
cannot go stale.

## Per-SKU LM/LY: the real cause

Per-SKU `lq`/`ly` are populated **only** from `oracle_sku_lq` / `oracle_sku_ly`, which are filled
**only** on the `oracle_bootstrap_path` branch — retired 13 Aug. On the `matrixify_bootstrap` path
every month took thereafter, `_matrixify_headline_totals()` builds headline grain only, so all
per-SKU LM/LY came out `None`.

**Not fixed in code, and deliberately so.** The companion's LM-1 / LY LM blocks read the *prior
periods' committed contracts* at SKU grain (`_prior_cuts_index`), which is the better source
anyway — same net basis, no re-derivation. Once the prior months exist as v1.1 contracts the blocks
populate through the intended path. Building the chain in order is the fix; the `lq`/`ly` fallback
is now dead weight worth removing separately.

## `pipeline_sha` and `-dirty`

`_git_commit()` now suffixes `-dirty` when the working tree has uncommitted changes. A contract
built from edited-but-uncommitted code previously recorded a sha that does not contain the code
that ran — provenance claiming reproducibility it didn't have.

## FX is the one step a session cannot do

`api.frankfurter.app` is not in a Claude session's network allowlist. Any month whose rate isn't
already frozen in `fx_rates.csv` therefore cannot be built from a chat.

This must stay a hard stop. **A guessed rate would distort every US line, and the country
reconciliation gate cannot catch it** — the headline total and the `uk + us + row` split both
derive from the same rows, so they tie either way. `_fetch_rate()` now raises `FxUnavailable` with
the exact remediation command instead of a bare `403` traceback.

Rates seeded 14 Aug: 2025-01/02/03, 2025-12, 2026-01/02/03. Table now covers 2025-01 … 2025-07,
2025-12, 2026-01 … 2026-07. **2026-08 is not seeded** — needed before any August build.

## A bug introduced and caught during this work

Adding `--force-settled` to the monthly CLI's argument loop stole the `i += 1` from the `--force`
branch, so any `--force` run spun forever in the parser. It presented as a slow build; a
`faulthandler` stack dump located it. Every branch now advances the index. Worth remembering that
this parser is a hand-rolled `while` loop, not `argparse`.

## Findings about the data itself

- **June 2025's committed contract was 1,688 units (8.4%) high** — revenue tied to the penny,
  units did not, monotonically, across 223 of 456 SKUs. Not maturing returns (those move revenue);
  pre-fix unit counting. The rebuild reads 20,044.
- **April 2026's committed contract was £455,524.94; the rebuild is £454,287.94.** April was on
  the retired `oracle_bootstrap` path. The £1,237 gap was the old figure being wrong.
- **Q2 2026 rebuilds to £1,382,546.76 against a committed £1,389,434** — the −0.49% drift logged
  as needing a diff report. It reproduces off a fully rebuilt monthly chain, so the old figure is
  the outlier.
- **One June 2026 order line carries no SKU and no description** (Product Type "Unknown", £204.30,
  32 units). The By-SKU tab excludes it, so that tab's column sums sit 0.04% below the headline.
  Pre-existing; worth cleaning at source.
- **June 2026 is £482,793.86 / 25,351 units across three independent rebuilds** — the
  determinism check that makes the rest of this trustworthy.

## Verification

All 17 contracts (15 monthly, 2 quarterly) reconcile: `uk + us + row` ties to the headline, worst
relative gap 3.7e-15 against a 0.001 tolerance, live ROW bucket in all 17, all at version 1.1.
Both chains checked in both directions: every month's LM equals the prior month's own total to the
penny and the unit, every month's LY equals the prior-year same month's. Each quarter equals the
sum of its three constituent months exactly.

## Still open

- **Quarterly `By SKU` is the narrow 12-column tab.** The aggregator doesn't carry `cuts` through
  when merging three months, so the companion falls back rather than writing 58 empty columns.
- **`lq_ly_source` is hardcoded to `lq_unavailable` on the quarterly matrixify path.** Q2 2026's
  LQ block is genuinely populated (£1,248,267.57, exactly Q1's total; `vs_lm` +10.8%), so the label
  now lies. The "a quarter-on-quarter comparative is not derivable at this grain" note on the By
  Month tab is stale for the same reason.
- **Returns and reviews builders have not been run** since the fix.
- **May 2026 still has three contract files** — `2026-05-matrixify.json` (canonical, rebuilt),
  `2026-05-matrixify-provisional.json`, `2026-05.json`. Which supersedes has never been declared.
- **2026-08 FX rate not seeded.**
