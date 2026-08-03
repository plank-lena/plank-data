# CLAUDE.md

Guidance for Claude Code when working in this repository.

**`ROADMAP.md` is the source of truth** for architecture, locked decisions, and
phasing. This file only tracks conventions and pointers actually encoded in the
repo — if the two ever disagree, `ROADMAP.md` wins.

## Conventions

- Deterministic; fail loud; never hand-patch an output — fix the mapping in
  code and regenerate.
- Commit every run so each report is reproducible and diffable.
- Never commit real secrets. `.env` files are gitignored; only `.env.example`
  (placeholders) is tracked, if one exists.
- `/source` is dropped raw feeds and is gitignored — never committed. `/output`
  is generated dashboards and **is** committed (needed for diffability).
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
- **Trading feed shape / VAT basis**: blocked on the trading Google Sheet
  investigation (`BRIEF_cowork_trading_sheet.md`, not yet landed). Don't
  invent trading aggregation ahead of that spec — see `ROADMAP.md` §7.
- **Line Detail source**: `LINE_DETAIL_SOURCE = local | dropbox` switch
  (mirrors the returns/trading builders) — activate the Dropbox path once its
  credentials exist.
