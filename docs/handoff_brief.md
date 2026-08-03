# Handoff brief — Claude Code

**For:** the Claude Code session that will scaffold the repo, reorganise the existing files,
and productionise the builders.
**Read first:** `ROADMAP.md` (the current source of truth) and `returns_spike_findings.md`.
Where any older doc (`HANDOFF.md`, `README.md`, the Phase 0 runbook) conflicts with
`ROADMAP.md`, the roadmap wins — the Supabase / Shopify-sync / Evidence / always-on-CI plan
in those older docs is **superseded**.

---

## 1. What this project is

Plank Hardware is replacing hand-built monthly/quarterly trading reports and a returns report
with **deterministic builders**. The chosen architecture (Path 2, hybrid):

- Each report is produced by code that pulls raw feeds from **connectors** (Shopify /
  Matrixify / Google Drive), computes the numbers, writes a **values-only spreadsheet**, then
  fills an HTML **dashboard template**. No database, no live sync, no Evidence.
- The existing hand-built workbooks stay on as the **human-readable spec and regression
  oracle**. A **reconciliation gate** runs on every output and aborts rather than publishing a
  wrong headline.
- Colleagues run everything through a shared **Claude Project** (trigger → eyeball → publish).
  Only the maintainer touches builder code. Publishing stays **behind Cloudflare Access**;
  monthly cadence means publish-manually is fine (drop the always-on CI).

**State right now:** the returns computation is *proven* and implemented
(`returns_builder_v2.py`). Trading is *not* started — it is blocked on a separate
investigation of the trading Google Sheet (see `BRIEF_cowork_trading_sheet.md`). Do not invent
trading aggregation from scratch.

---

## 2. Locked decisions — must be honoured in code

**Returns (Lena + Daisy, Aug 2026):**
1. **Single-count** — each return counted once via a de-duplicated `sku+order` join. Never the
   legacy per-line `SUMIF` stamping (it double-counted ~22% on Q1).
2. **Order-month basis** — every row buckets by the order's **sale month**, consistently across
   the whole document. (Supersedes the earlier return-month / sale-cohort decision.)
3. **Orders-based headline rate** — `distinct returned orders ÷ distinct orders`, distinct on
   **both** sides. Units returned and returns cash are secondary detail.
4. **Return source = Returns-zap**, never Shopify's own returns fields (they only count
   checked-in returns; native columns even went negative in Q1).
5. **Returns cash is notional** = `RRP ex-VAT × units returned` (list value, not actual refund).
6. **Recent months are immature** on an order-month basis — the dashboard must flag
   still-maturing months so they aren't read as improvements.

**Trading (the reconciliation contract — applies to every trading output):**
- Revenue = **gross sales, ex-VAT**; returns reported separately, never netted into the headline.
- **Country is the reconciliation key**, not channel: `uk + us + row` must equal the total
  within **0.1%**; a **ROW bucket must exist** (even if zero) and must come from **ship-to
  country/store** (the raw order-line country field holds only UK/US). D2C/B2B do **not**
  partition the total — never reconcile from the channel split.
- **VAT asymmetry:** GBP RRP is inc-VAT, USD RRP is ex-tax, revenue is ex-VAT — handle
  explicitly; keep a `UK_SALES_ARE_INC_VAT` toggle + warning until the UK per-line basis is
  confirmed by the Google Sheet investigation.

## 3. The reconciliation gate (runs on every build; writes no output on failure)

- **Returns:** Total == sum of status/category blocks **for additive measures only (units,
  cash)**; **order counts are distinct and recomputed per grouping — they do NOT sum to Total**
  (one order can span statuses/categories), so never assert additivity on them. Assert every
  label matches a whitespace-normalised label in the data (this catches a real Q1 bug:
  `"Electric Accessory "` with a trailing space read as £0 in the sheet). Assert the headline is
  orders-based and every row is order-month bucketed.
- **Trading:** `uk + us + row == total` within 0.1%; ROW present; returns not netted; VAT basis
  applied matches the toggle.
- **All:** regression-check against the committed fixture for any period we already have.

---

## 4. Target repo structure

```
plank-data/
  README.md            # how to run (rewrite from the old README; keep the "two audiences" idea)
  ROADMAP.md           # current source of truth (move from outputs)
  common/              # shared: sku/status/finish enrichment · reconciliation gate · io helpers
  returns/
    build.py           # promote returns_builder_v2.py here (data builder → values-only .xlsx)
    template.html      # returns dashboard template (derive from returns-review-q1-2026.html)
    tests/fixtures/    # first committed v2 output = numeric regression oracle
  trading/
    build.py           # Phase B/C data builder — DO NOT START until the Sheet spec lands
    dashboard/         # existing template-fill step (xlsx → HTML): config/extract/compute/
                       #   render/validate/pipeline/build.py + dashboard_template.html
    tests/fixtures/    # 2026-04/05/06 trading .xlsx + their dashboards
  reviews/             # review_feedback.py, reviews.json, themes_*.csv, Yotpo sample
  source/              # dropped feeds — gitignored
  output/              # generated dashboards
  reference/           # glossary, Line Detail dictionary, Phase 0 runbook
  docs/                # spike artifacts: returns_spike_findings.md, returns_double_count_example.xlsx,
                       #   returns_summary_poc.py + returns_summary_builder.py (archive)
```

### Proposed file mapping (confirm each by reading it — some internals I have not re-verified)

| Existing file | Goes to | Note |
|---|---|---|
| `returns_builder_v2.py` | `returns/build.py` | the proven returns data builder |
| `returns-review-q1-2026.html` | `returns/template.html` (source) | strip to a tokenised template |
| `Q1_Jan_Feb_Mar_2026.xlsx` | `returns/tests/fixtures/` + `source/` | **structural** oracle only now (basis changed) |
| `config/extract/compute/render/validate/pipeline/build.py` | `trading/dashboard/` | the xlsx→HTML step, **not** a data builder |
| `dashboard_template.html` | `trading/dashboard/` | monthly dashboard template |
| `May_2026_dashboard.html`, `Q2_2026_dashboardv5_.html` | `trading/tests/fixtures/` | example outputs |
| `202604/05/06_Monthly_Trading_Report.xlsx` | `trading/tests/fixtures/` | values-dump inputs |
| `HANDOFF.md`, `README.md` | `trading/` (then reconcile to ROADMAP) | describe the dashboard step |
| `review_feedback.py`, `reviews.json`, `review_flags.csv`, `data_quality.csv`, `themes_*.csv`, Yotpo CSV | `reviews/` | |
| glossary / dictionary / runbook PDFs | `reference/` | (they're zip bundles of page text+images) |

---

## 5. First tasks, in order

1. **Scaffold** the structure above; `git init`; add `.gitignore` for `source/` and any creds.
2. **Reorganise** the existing files per the mapping (read each to confirm its role first).
3. **Promote** `returns_builder_v2.py` → `returns/build.py`; factor the shared pieces
   (sku/status/finish enrichment, the reconciliation gate, spreadsheet IO) into `common/`.
4. **Fixture:** run the returns builder once, commit its output as
   `returns/tests/fixtures/2026Q1` — this becomes the numeric regression oracle. Add a test that
   re-running reproduces it within tolerance.
5. **Wire the returns feeds from connectors** to replace the pasted tabs: Shopify / Matrixify
   for orders (UK + US), the Returns-zap export, and Line Detail from Drive/Dropbox. Keep a
   `LINE_DETAIL_SOURCE = local | dropbox` switch (Dropbox creds were being set up ~Mon 3 Aug).
6. **Emit** the values-only returns workbook, then render the `returns-review` HTML via the
   template-fill step. Headline = orders-based rate; flag still-maturing recent months.
7. **Colleague runbook** for returns, seeded into the Claude Project (trigger → eyeball →
   publish; never hand-edit output numbers; escalate schema mismatches to the maintainer).

**Trading (Phase B/C)** waits for `BRIEF_cowork_trading_sheet.md` to come back with the trading
logic spec. When it does, build `trading/build.py` on the returns pattern, feed the existing
`trading/dashboard/` step, and lock 2026-04/05/06 as fixtures.

## 6. Conventions

Deterministic; fail loud; never hand-patch an output (fix the mapping and regenerate); commit
every run so each report is reproducible and diffable; `snake_case` in any SQL; load workbooks
`data_only=False` when you need to see formulas vs values; keep Cloudflare Access in front of
GitHub Pages.
