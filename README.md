# Plank Data

Deterministic builders for Plank Hardware's monthly/quarterly trading reports and
returns report. Each report is produced by code that pulls raw feeds from
connectors, computes the numbers, writes a values-only spreadsheet, then fills an
HTML dashboard template. No database, no live sync — see `ROADMAP.md` for the full
architecture decision and current status; **that file is the source of truth**,
not this one.

> **Two audiences use this repo.** **Maintainers** run builders locally / via
> Claude Code (most of this file). **Report generators** (colleagues) don't touch
> the repo at all — they use the shared Claude Project (trigger → eyeball →
> publish); their instructions live in the Project, not here.

---

## Status (see `ROADMAP.md` §6 for the authoritative phase list)

- **Returns** — aggregation/grouping proven from raw feeds; the returns join
  (Layer 2) is being rebuilt correctly (`returns/build.py`, in progress).
- **Trading** — not started. Blocked on `BRIEF_cowork_trading_sheet.md` (the
  trading Google Sheet investigation). Do not invent trading aggregation from
  scratch; wait for that spec.
- **Reviews** — Yotpo-based review summarisation and theming, migrated as-is
  from an earlier spike; not yet wired into the builder/gate pattern.

---

## Repo layout

```
plank-data/
  ROADMAP.md      # source of truth — decisions, reconciliation contract, phasing
  common/         # shared: sku/status/finish enrichment, reconciliation gate, io helpers
  returns/        # returns builder (build.py) + dashboard template + fixtures
  trading/        # trading builder (blocked) + the existing dashboard-fill step
  reviews/        # Yotpo review summarisation / theming scripts
  source/         # dropped feeds — gitignored, never committed
  output/         # generated dashboards — committed, so each run is diffable
  reference/      # domain glossary, Line Detail dictionary, Phase 0 runbook (superseded)
  docs/           # spike artifacts: findings, POC scripts, the original handoff brief
```

---

## Running the returns builder (maintainer)

```
python returns/build.py source/Q1_Jan_Feb_Mar_2026.xlsx
```

This prints the by-order-month, by-status, UK/US, return-reason, and by-finish
blocks. It does not yet emit a values-only workbook or the HTML dashboard —
those are the next steps in `ROADMAP.md` Phase A.

## Running the trading dashboard-fill step (maintainer)

The dashboard *rendering* step (xlsx → HTML) already exists and works; the
trading *data builder* (feeds → values-only xlsx) does not yet exist and is
blocked. Until Phase B lands, this step only runs against the existing
hand-built Monthly/Quarterly Trading Report workbooks:

```
python trading/dashboard/pipeline.py trading/tests/fixtures/2026-06_Monthly_Trading_Report.xlsx
```

Full spec for this step (cell mappings, validation checklist, known data
quirks) is in `trading/dashboard/HANDOFF.md`.

---

## Conventions

- Deterministic; fail loud; never hand-patch an output — fix the mapping and
  regenerate.
- Commit every run so each report is reproducible and diffable.
- Load workbooks with `data_only=False` when you need to see formulas vs.
  resolved values.
- Reconciliation gate runs on every build and aborts (writes no output) rather
  than publish a wrong headline. See `ROADMAP.md` §5 for the exact assertions.
- Keep GitHub Pages behind Cloudflare Access when publishing.

---

## For the shared Project — report-generator instructions

_Seed this section (or an expanded version per report) into the Claude
Project's custom instructions. Colleagues do not need this repo or Claude
Code._

1. Open the shared Claude Project for the report you need.
2. Attach the relevant source file(s) when prompted.
3. Ask Claude to generate the report.
4. Sanity-check the headline numbers, then publish.
5. Never hand-edit output numbers. If Claude reports a schema mismatch or a
   failed reconciliation gate, escalate to the maintainer rather than forcing
   it through.
