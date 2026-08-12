# CLAUDE.md

Guidance for Claude Code when working in this repository.

**`ROADMAP.md` is the source of truth** for architecture, locked decisions, and
phasing. This file only tracks conventions and pointers actually encoded in the
repo — if the two ever disagree, `ROADMAP.md` wins.

## Conventions

- Deterministic; fail loud; never hand-patch an output — fix the mapping in
  code and regenerate.
- Commit every run so each report is reproducible and diffable — but see the
  correction below before committing anything under `trading/source/`.
- Never commit real secrets. `.env` files are gitignored; only `.env.example`
  (placeholders) is tracked, if one exists.
- **`source/` (at any depth — repo root, `trading/source/`, etc.) is dropped
  raw feeds and is gitignored — NEVER committed, no exceptions.** `/output` is
  generated dashboards and **is** committed (needed for diffability).

  **Correction, 12 Aug 2026:** this file used to carve out `trading/source/*.csv`
  (raw Matrixify order exports) as a deliberate exception — "a frozen,
  immutable monthly snapshot, the auditable record a report was built from."
  That reasoning is retired. Those exports are full Shopify order-level data:
  customer name, email, phone, billing/shipping address, browser IP, and
  card-adjacent fields (CC BIN, AVS/CVV result codes). They were committed for
  ~6 months before anyone caught it (see `docs/2026-08-12_pii_remediation.md`),
  and this repo is now public and cloned fresh by every colleague's dashboard
  request — there is no framing under which that data belongs in git here.
  **If you need an audit trail of what a report was built from, commit a
  manifest instead** (period, row count, sha256 of the file, Matrixify job ID,
  pull timestamp) — not the file. `trading/source/` stays gitignored like
  every other `source/` directory, full stop.
- `snake_case` for any SQL written in this repo.
- Load workbooks with `data_only=False` when you need to see formulas rather
  than resolved values (useful for debugging a `SUMIF`/`XLOOKUP` mismatch
  against the hand-built sheet).
- Keep `/common`, `/returns`, `/trading`, `/reviews` as separate concerns —
  shared logic (SKU/status/finish enrichment, the reconciliation gate,
  spreadsheet IO) belongs in `/common`, not duplicated per-report.

## The reconciliation gate

Runs on every build; aborts and writes no output on failure. Full detail in
`ROADMAP.md` §5. In short:

- **Trading:** `uk + us + row == total` within 0.1%; a ROW bucket must exist
  even if zero; returns are never netted into revenue; the VAT toggle applied
  matches config.
- **Returns:** Total == sum of status/category blocks for **additive measures
  only** (units, cash) — order counts are distinct per grouping and do **not**
  sum to Total, never assert additivity on them. Every label must match a
  whitespace-normalised label in the data. Headline must be orders-based and
  every row order-month bucketed.
- **All:** regression-check against the committed fixture for any period
  already captured.

A failed gate prints the offending figures and the gap — never bypass it to
force output through; fix the source or the mapping.

## Data connections

`common/sources.py` is the single source-of-truth for every external connection — pinned
IDs, tab names, expected columns, and where a fetched snapshot lands on disk. **When a
source moves:** get the new Drive share link → copy its file ID out of the URL
(`.../d/<FILE_ID>/edit...`) → replace the one line in `common/sources.py`'s `SOURCES`
dict → re-run `python common/sources.py preflight`.

| Source | Connector | ID / tab | Lands at |
|---|---|---|---|
| Line Detail | Google Drive | `1r5D03e3Df_Qyinrps0wqLlqsp3YGATi6Ob3cvjv7Dok`, tab `Line Detail` | `trading/source/line_detail.xlsx` |
| On-hand inventory | Google Drive | `1wb7Xj1ionrL3eoZKBZ5JZTxo6jAiIqFaBQ74IJprcjo`, tab `IN Shopify Product Data` | `trading/source/shopify_inventory.csv` |
| Yotpo reviews | Google Drive | `1wb7Xj1ionrL3eoZKBZ5JZTxo6jAiIqFaBQ74IJprcjo`, tab `API Yotpo Reviews` | `source/yotpo_reviews.csv` |
| ReturnZap | Google Drive | `1tyinVS7suxKIdaY9Y3R6geaOFkYgeaeVWcDU2VHaGzs`, tab `API: Returns` | `source/returns_zap.csv` |
| Ship sheet (inbound POs — **not** on-hand) | Google Drive | `1wErCQzW2pi8-OnzN2bbuu5bNkTFt1PGhmBumEFzQMaM`, tab `IP Import` | not fetched by any builder |
| Matrixify orders | MCP | `Matrixify-PlankUK` / `Matrixify-PlankUS` | `trading/source/orders_<period>_<STORE>.csv` |

The Drive/Matrixify MCP tools are only reachable from an interactive Claude Code session
(or the shared Claude Project) — never from a standalone `python` process, so
`common/sources.py` itself never calls a connector. It owns the pinned locations, the
normalizers that shape a raw download to match what the existing parsers already expect,
and `preflight()`, which validates whatever's already landed (shape/columns/non-empty) —
it does not itself check live connector reachability. Whoever is driving a build through
Claude fetches the raw bytes via the connector and hands them to the matching
`normalize_*`/`load_*` function here.

**ReturnZap history, resolved 2026-08-12:** the sheet's `getReturns` Apps Script pull
originally had two bugs — exact full-row duplicates (fixed on read by
`common/sources.dedupe_returns_export`, two passes: exact-row, then same-line
snapshot-updates) and a UK-store-specific outage that stopped pulling UK returns after
2025-03-03 while US kept pulling fine. Both are now fixed upstream (confirmed 2026-08-12:
74,218 raw rows, both markets current through 2026-08-05) — `returns/build.py`'s
`load_returns_export_from_sheet`/`run_for_period` reproduce Q1/Q2 2026 within tolerance
(`returns/tests/test_regression_returnzap.py`). `returns/build_q1.py`/`build_q2.py` still
default to the `.numbers` files (not yet flipped over as the production default) —
switching them is a separate decision, not a data-quality blocker anymore.

## Period-from-prompt

The reporting period is an explicit input from the maintainer's/colleague's prompt
("generate the returns dashboard for Q2 2026") — never inferred from a workbook's
internal header cell. `common/period.py`'s `parse_period("Q2 2026" | "June 2026")` is the
one parser both builders call; it returns a `{cm, lm, ly}` model, each with
`label`/`short`/`start`/`end`/`key`, and fails loud on an unparseable or future period.

- **Matrixify (trading):** the period drives the export date filter — `trading/
  build_matrixify_dashboard.py`/`build_matrixify_quarterly_dashboard.py` accept a period
  string directly (`"June 2026"` or `"2026-06"`; `"Q2 2026"` or 3 `YYYY-MM` args for the
  quarterly script) and pull CM + LM + LY windows. LM/LY preference order: (1) a
  previously-committed contract via `--lm-contract`/`--ly-contract` — LOCKED, ROADMAP.md
  §5, never re-derive an already-published month fresh; (2) fresh Matrixify pulls for the
  LM/LY calendar windows (`trading/contract.py`'s `_matrixify_headline_totals`, wired via
  `requested_period_model`) — the connector-flow default once a period has no committed
  contract yet, no workbook involved; (3) `--oracle-bootstrap` (the old default, retired
  2026-08-12) — kept only for explicit oracle-comparison/regression-parity runs.
  `trading/dashboard/config.py`'s `PERIOD_CELLS`/`extract.py`'s `extract_period_model` are
  unchanged and still required for the oracle-only path (`emit_contract_from_oracle`,
  `pipeline.py`) — that workbook stays the human-readable spec/regression oracle
  (ROADMAP.md §1); it's just no longer the connector flow's period source.
- **ReturnZap (returns):** the sheet holds full history — `returns/build.py`'s
  `run_for_period(sales_df, ld_std, returns_df, "Q2 2026")` parses the period, runs
  `returns/validate.py`'s guardrails (coverage, non-empty, both-markets, freshness — all
  computed from the sheet itself, never a prior report), then filters by return-month.
  `assert_returns_overlap_sales` (unchanged, in `common/reconciliation_gate.py`) still
  runs inside `prep()` on top.
- **Yotpo:** no period filter — region filter only, per the returns brief.
  `common.sources.check_yotpo_freshness` just confirms the snapshot is recent.

## Domain glossary

The canonical glossary, SKU taxonomy, and Line Detail column dictionary live in
`/reference`:

- `reference/plank_domain_glossary_v2.pdf` — finish families, category tree
  (department › item_type › style), SKU naming.
- `reference/line_detail_data_dictionary_v3.pdf` — Line Detail column meanings.
- `common/sku_taxonomy.py` — the single shared module that turns a SKU into a
  category tree (department/item_type/style), per the glossary §5. Multiple
  scripts used to classify SKUs independently and disagreed; this module is
  now the one answer both `returns/build.py` and `reviews/review_feedback.py`
  should call. Handles the Product Type / Product Category label reversal
  between the source sheets — see the module's own docstring.
- `reference/phase_0_setup_runbook_v2.pdf` — superseded (Supabase/Evidence-era
  plan); kept for historical context only, not a live spec.

## Known gotchas (see `ROADMAP.md` §4 and `docs/returns_spike_findings.md` for full detail)

- Source sheets label "Product Type" and "Product Category" **oppositely** —
  `sku_taxonomy.py` normalises this; don't read either column name literally
  elsewhere.
- Labels are whitespace-sensitive in the hand-built sheets (e.g. the
  `"Electric Accessory "` trailing-space bug) — strip every join key/label.
- Returns must be joined from the raw `Returns zap` feed, de-duplicated on
  `sku+order`, never Shopify's own returns fields and never the legacy
  per-line `SUMIF` stamping (double-counts when a `sku+order` spans multiple
  order lines).

## How to change things

- **Returns join / rate / cash-basis decisions**: these are LOCKED (Lena +
  Daisy, Aug 2026) — see `ROADMAP.md` §4. Don't change without re-confirming
  with them; a change restates historical numbers.
- **Trading source**: Matrixify exports (`trading/matrixify_source.py` +
  `trading/build_matrixify.py`), NOT live Shopify GraphQL
  (`trading/shopify_feed.py` + `trading/build.py`'s live path) — the latter is
  kept in the repo (its AB/country/channel logic is reused as-is) but was
  superseded 2026-08-03 because a monthly report needs a frozen, reproducible
  snapshot, which a live query against an actively-mutating store cannot give
  you. See `ROADMAP.md` Phase B for the full writeup and current known gaps
  before trusting any trading figure.
- **Line Detail source**: `LINE_DETAIL_SOURCE = local | dropbox` switch
  (mirrors the returns/trading builders) — activate the Dropbox path once its
  credentials exist.
