# Monthly Trading Dashboard — Build Handoff / Spec

This document is the single source of truth for turning a one-off monthly dashboard
rebuild into a repeatable **template-fill** pipeline. A Claude Code session should be
able to build the whole pipeline from this file plus the two attached inputs.

## Goal

Given a monthly trading-report `.xlsx`, produce the styled HTML dashboard
deterministically. Move away from "edit last month's HTML" (diff-based) to
"fill a tokenised template" (template-fill).

## Inputs you will find in this folder

- `template/dashboard_current.html` — the latest working dashboard (May 2026). This
  becomes the template after tokenisation. It already contains the finish pie-chart
  view and the corrected category-SKU logic.
- `source/<month>_Monthly_Trading_Report.xlsx` — one or more monthly source files.
  Use the most recent as the live input and any older ones as regression fixtures.

## Target repo structure

```
trading-dashboard/
  template/dashboard.template.html   # tokenised version of the current HTML
  src/
    config.py        # ALL cell/column mappings + period model + abbrev maps
    extract.py       # xlsx -> structured dict for every section
    compute.py       # derive KPI/ribbon/static values, arrows, badge classes
    render.py        # fill template tokens + inject the 10 data blocks
    validate.py      # input schema asserts + output checks
    pipeline.py      # orchestrate extract -> compute -> render -> validate
  tests/fixtures/    # past months' xlsx + known-good HTML
  HANDOFF.md         # this file
  README.md
```

## Build order

1. Tokenise `dashboard_current.html` -> `dashboard.template.html` (replace every
   static value AND every data block listed below with `{{TOKENS}}`).
2. `config.py` — lift every mapping in this doc into one place. Derive the period
   model (current / last-month / last-year) from the sheet's own blocks; never
   hardcode a month name.
3. `extract.py` — one clean pass over the three sheets producing a structured object.
4. `compute.py` — the only substantial net-new logic: derive the static KPI/ribbon
   values, arrow directions and badge classes from extracted data.
5. `render.py` — fill tokens, inject the 10 data blocks.
6. `validate.py` — input asserts FIRST (fail loudly), then output checks.
7. `pipeline.py` + add 2–3 past months to `tests/fixtures` as regression cases.
8. Run past months through; confirm output is stable; then wire into the shared Project.

---

## Source spreadsheet schema (3 sheets)

> FIRST STEP of every run: assert this structure. The previous source file had errors,
> so `validate.py` must check sheet names, that row 7 of "Monthly Summary" is the
> TOTAL row, and that expected columns sit where this doc says. Fail loudly if not.

### Sheet "Monthly Summary"
Current-month figures live in cols B–EU, organised by Product Status / Channel /
Country. **Row 7 = TOTAL.** All column letters below are for row 7 unless a row is given.

Headline (row 7):
- `F` total sales · `J` units · `S` GM% · `Q` sell-through · `R` weeks-cover · `P` inventory
- `V` D2C £ · `AH` B2B £ · `AT` UK £ · `CD` US £ · `DN` ROW £
- `Z` D2C units · `AL` B2B units · `AX` UK units · `CH` US units · `DR` ROW units
- `AF` D2C-GM% · `AR` B2B-GM%
- `G` vs LM-1 · `I` vs LY · `K` units vs LM · `M` units vs LY
- `AU` UK vs LM · `AW` UK vs LY · `CE` US vs LM · `CG` US vs LY

Last-month (LM-1) block, cols EX–FU (row 7): `EX` total, `EY` D2C, `EZ` B2B, `FA` UK,
`FD` US, `FG` ROW, `FJ` total units, `FK` D2C units, `FL` B2B units, `FM` UK units, `FP` US units, `FS` ROW units.

Last-year (LY) block, cols GP–HM (row 7): `GP` total, `GQ` D2C, `GR` B2B, `GS` UK,
`GV` US, `GY` ROW, `HB` total units.

Product-Status rows (cols: sales `F`, units `J`, vs_lq `G`, vs_ly `I`, gm `S`,
st `Q`, wc `R`, inv `P`): row 8 Continuity, 9 Newness, 10 Discontinued, 11 Dead.

Product-Type rows (sales `F`, units `J`, vs_lq `G`, gm `S`): row 16 Cabinetry,
33 Electric, 24 Accessories, 39 Lighting, 44 Components. NOTE: Components GM can be
negative — that is real, do not clamp it.

Finish rows (total `F`, units `J`, vsLQ `G`, vsLY `I`, d2c `V`, b2b `AH`, uk `AT`,
us `CD`): 47 Antique Brass, 48 Brass, 49 Aged Brass, 52 Unlacquered Brass,
55 Polished Nickel, 56 Black, 59 Stainless Steel, 69 Burgundy.

### Sheet "By Collection"
Data starts at **sheet row 5** (rank 1); `sheet_row = rank + 4`.
`A` rank · `B` type · `C` collection · `F` gross sales (current) · `G` units ·
`H` vs LM-1 · `Q` GM% · `O` sell-through · `P` weeks-cover · `S` D2C £ · `Z` B2B £ ·
`AG` UK £ · `AR` US £ · `BC` ROW £ · `BM` last-month total (lq_total) ·
`BP` last-month UK (lq_uk) · `BQ` last-month US (lq_us).

### Sheet "By SKU"
Data starts at **row 5**.
`A` rank · `O` sku · `P` desc · `J` collection · `C` type · `E` finish ·
`H` UK status · `I` US status · `R` gross sales · `S` units · `T` vs LM-1 · `Y` GM% ·
`W` sell-through · `X` weeks-cover · `V` inventory · `AQ` UK £ · `AR` UK units ·
`BJ` US £ · `BK` US units · `AC` D2C £ · `AJ` B2B £ · `CE` last-month sales (lq) ·
`CT` last-year sales (ly).

Status abbreviation map (for display): Continuity→Cont, Newness→New,
Not For Sale→N/S, Discontinued→Disc, Dead→Dead, Pre-Launch→Pre.

---

## The 10 data constants to regenerate each month

These are injected into the template as blocks. Everything else in the JS
(`TYPE_DATA`, `CAT_ANALYSIS`, `CAT_NAMES`, `COLL_NAMES`, `FINISH_NAMES`) is **derived
at runtime from these** — do not generate those by hand.

1. `PERIODS` — three periods `q1_25` (LY), `q4_25` (LM), `q1_26` (current). Each:
   total, d2c, b2b, uk, us, row, total_u, label (e.g. "Apr 2026" for LM), short
   (e.g. "Apr '26"). See channel rule below.
2. `COLLECTIONS` — all collections with ts>0 from By Collection (~68). Fields used:
   c, t, ts, tu, vs_lq, gm, st, wc, d2c, b2b, uk_s, us_s, row_s, lq_total, lq_uk, lq_us.
3. `STATUSES` — 4 rows (Continuity/Newness/Discontinued/Dead) from Monthly Summary
   status rows: sales, units, vs_lq, vs_ly, gm, st, wc, inv.
4. `PROD_TYPES` — 5 rows from Monthly Summary type rows: sales, units, vs_lq, gm.
5. `SKUS` — top 25 overall by gross from By SKU.
6. `FINISH_DATA` — 8 finishes; each total, lq, ly(null), units, vsLQ, vsLY(null),
   d2c, b2b, uk, us, lq_uk(0), lq_us(0), collSplit{...}, skus[8], color, textColor.
7. `TOTAL_SALES` — scalar = round(F7). Appears as the const AND inside two helper
   functions; replace all occurrences.
8. `COLL_ANALYSIS` — 10 keyed collections (deep-dive) with sales, lq, lq_uk, lq_us,
   units, gm, st, wc, d2c, b2b, uk, us, row, skus[6].
9. `NEWNESS_SKUS` — top 25 Newness-status SKUs.
10. `CAT_SKUS` — **top 8 by gross PER category, drawn from the FULL By SKU sheet**,
    for Cabinetry / Electric / Accessories / Lighting. Do NOT source these from the
    top-25 `SKUS` array — categories without a top-25 SKU would come back empty, and
    the previous hand-typed lists were stale. Fields: sku, d, c, sales, vs_lq, gm,
    uk, us.

---

## Static template tokens (computed, not data blocks)

All derived from Monthly Summary row 7 + the LM/LY blocks. Currency formatted as £K
(value/1000, 1 dp under 1M). Percentages use forceSign and 1 dp.

- Header: title, `<h1>` month, sub-label, and three badges — all use the
  current / LM / LY month names from the period model.
- KPI Total Revenue: value `F7`; LM badge `G7`; LY badge `I7`.
- KPI Units: `J7`; LM `K7`; LY `M7`.
- KPI Gross Margin: `S7`; sub "D2C `AF7`", "B2B `AR7`".
- KPI D2C Share: `V7/F7`; LM badge = percentage-point delta `(V7/F7 − EY7/EX7) × 100`, shown as e.g. `−5.4pp`, sign-coloured up/dn/flat.
- KPI UK Revenue: `AT7`; LM `AU7`; LY `AW7`.
- KPI US Revenue: `CD7`; LM `CE7`; LY `CG7`.
- KPI Sell-Through: `Q7`; sub WC `R7`, inventory `P7`.
- MoM ribbon — Total trajectory: LY `GP7` → LM `EX7` → CM `F7`.
- MoM ribbon — UK trajectory: LY `GS7` → LM `FA7` → CM `AT7`; badges UK vs LM `AU7`,
  UK vs LY `AW7`.
- MoM ribbon — US trajectory: LY `GV7` → LM `FD7` → CM `CD7`; badges US vs LM `CE7`,
  US vs LY `CG7`.
- `periodLabels` array = [LY short, LM short, CM short].
- vs-labels throughout: "vs {LM month}", "vs {LY month}", "vs LM ({LM short})",
  "vs LY ({LY short})".
- `dc-bar-lbl` = current-month 3-letter abbrev.

Arrow / badge logic (replicate exactly):
- direction = sign of the value; class `up`/`pos` if > 0.005, `dn`/`neg` if < -0.005,
  else `flat`/`neu`.
- Ribbon trajectory arrow colours come from the sign of each consecutive step
  (point[i+1] - point[i]): green if up, red if down, muted if ~0.

---

## Data relationships & lessons (must preserve)

- **Channel/ROW split asymmetry (intentional, carried from the original design):**
  In the CURRENT block, "Total D2C"/"Total B2B" (`V7`/`AH7`) EXCLUDE ROW; i.e.
  UK + US + ROW = Total, and Total-D2C = UK-D2C + US-D2C only. In the LM-1 block the
  D2C/B2B figures INCLUDE ROW (they sum to the LM total). `PERIODS.q4_25` mirrors the
  LM behaviour. Keep this asymmetry; do not "fix" it.
- FINISH `lq` is computed as `total / (1 + vsLQ)`; `vsLY` is left null by design.
- **Weeks-cover unit conversion (important):** The source spreadsheet stores cover in
  all three places (Monthly Summary `R7`, By Collection `P`, By SKU `X`) as
  `inventory_units / monthly_sales_units` — i.e. **months of cover**, not weeks.
  `compute.py` multiplies every raw cover value by `52/12` (`_MONTHS_TO_WEEKS`) before
  emitting it. Do not remove this conversion; without it displayed figures are ~4× too
  low and labelled incorrectly as weeks.
- `CAT_ANALYSIS` and `TYPE_DATA` derive from `COLLECTIONS` at runtime — never hand-set.
- **Reconciliation — hard input failure:** UK (`AT7`) + US (`CD7`) + ROW (`DN7`) must
  equal Total (`F7`) within 0.1% relative. The pipeline aborts before rendering if this
  fails; the channel sum (`V7` + `AH7`) is printed alongside for visibility. A failed
  check means the source file is inconsistent — fix the spreadsheet, do not bypass.

### Reference: verified By SKU extraction (openpyxl, 0-based indices)
```python
COL = dict(type=2, coll=9, sku=14, desc=15, gross=17, vslq=19, gm=24, uk=42, us=61)
# top 8 per category from the FULL sheet:
rows = [r for r in ws.iter_rows(min_row=5, values_only=True)
        if r[COL['type']] and r[COL['sku']] and isinstance(r[COL['gross']], (int,float)) and r[COL['gross']] > 0]
for cat in ['Cabinetry','Electric','Accessories','Lighting']:
    top8 = sorted([x for x in rows if str(x[COL['type']]).strip()==cat],
                  key=lambda x: -x[COL['gross']])[:8]
```
(Letter→index: A=0 … Z=25, AA=26 … AQ=42, BJ=61.)

---

## Validation checklist

Input (assert before doing anything; fail loudly):
- Sheets present: "Monthly Summary", "By Collection", "By SKU".
- Monthly Summary row 7 is the TOTAL row (label check).
- Spot-check that key columns hold the expected kind of value (e.g. `F7` numeric,
  `S7` a fraction 0–1). If a column moved, stop and report — do not guess.
- Regional reconciliation: UK (`AT7`) + US (`CD7`) + ROW (`DN7`) = Total (`F7`)
  within 0.1% relative. Hard abort if violated.

Output:
- No leftover prior-month tokens anywhere in static text (the only legitimate
  remaining "previous month" strings are the LM references, which equal the new LM).
- Each of the 4 categories shows exactly 8 SKUs.
- JS is syntactically valid (`node --check` on the extracted `<script>`).
- Render test: load the HTML in jsdom with a stubbed canvas 2d context and fire the
  load event; assert no exceptions and that key sections populate (KPI values, ribbon,
  finish pie legend = 8 rows, each category tab = 8 SKU rows).
