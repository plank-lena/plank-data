# Plank Product Data Platform — Roadmap

**Status:** Path 2 (deterministic, connector-sourced builders) running end-to-end for
Monthly **and Quarterly** Trading, both through the same redesigned dashboard template.
Returns join is next; the returns *dashboard* has a fresh spec.
**Last updated:** 2026-08-05
**Deliverables:** Monthly Trading dashboard · Quarterly Trading dashboard · Returns
dashboard · Yotpo/reviews scanner

---

## 1. Architecture (pin this)

- **Path-2 deterministic builder.** No database, no live sync, no always-on CI. Each
  report is produced by code that pulls raw feeds from connectors already in place
  (Matrixify exports for trading, Line Detail for the product reference, Returns-zap and
  the Yotpo export for returns/reviews), computes the numbers, and writes the outputs.
  *(This retires the earlier Supabase + Shopify-sync + Evidence + always-on-CI design —
  superseded, not revisited; if an older doc still describes that plan, this file wins.)*
- **Oracle.** The existing hand-built Excel workbooks stay on as the human-readable spec
  and the regression-test oracle — the builder must reproduce their numbers within
  tolerance before anything ships.
- **Gate.** A reconciliation gate runs on every build and aborts, writing no output, on
  failure. See §6.
- **Hosting = Option A.** Build a single self-contained HTML file (fonts/assets embedded,
  no external calls at render time) and hand it off to a Plank-scoped Slack/Drive
  location. Access control is workspace membership. There is **no GitHub Pages, no
  Cloudflare Access, no DNS/subdomain** — that hosting workstream does not exist for this
  project. "Publish" means handing off a file, not a deploy; nothing in this repo runs a
  server.
- **Revenue definition (trading, locked):** **net of returns**, ex-VAT — on Supermetrics'
  **order-cohort-week basis** (a month's Returns column holds only returns of orders
  *placed* that month, growing in size as that cohort matures — NOT small/negligible,
  confirmed via real June 2026 data: ≈£16.6K UK), not the full processing-window refund
  lines the builder currently subtracts. Reverted 2026-08-05, refined same day after a
  decisive June test ruled out the simplest fix — see §5, still **pending confirmation**
  (report-generation timing) from whoever's closest to the live sheet. Country (UK + US +
  ROW) is the reconciliation key — `uk + us + row == total` within 0.1%, a ROW bucket is
  always present even if zero. Full detail in §5.

---

## 2. Status — done and committed (dependency order)

- [x] **#5 — ROW bucket + three-way country reconcile** (`70362cd`). `compute_combined()`
      unions UK+US lines and buckets every one by ship-to country (GB→UK, US→US,
      else→ROW; store-fallback only when ship-to is blank). The grand total is
      accumulated independently of the country buckets, so
      `common/reconciliation_gate.assert_country_reconciles` is a real leak check, not
      vacuous. ROW is first-class and near-exact; `uk+us+row` ties to the independent
      grand total with residual 0. Frozen fixture + regression test lock the bucketing
      logic. (Each bucket vs. the oracle is not yet within 0.1% — see the order-scope
      item in §3; the gate correctly refuses to call that reconciled.)
- [x] **#2 — Line Detail enrichment** (`7263ad2`). `trading/line_detail.py` parses the
      real workbook, de-dupes, validates the status enum, derives `is_live_uk/us`,
      `newness_bucket` (as-of report-month end, not wall-clock), and GM%, then left-joins
      onto every order line without ever dropping one. `is_live_*` / `is_el_component`
      are flags a consumer applies, never row-drops. Coverage 98.37% of revenue
      (unmatched SKUs land in `Unknown`, never dropped).
- [x] **#3 — data-contract emission** (`a23f393`). One schema — the shape of
      `extract_all()` — with two emitters: `emit_contract_from_oracle` (correct today,
      used to build/prove the template) and `emit_contract_from_matrixify` (the real
      builder, gated). `load_contract()` reverses either back into the same shape
      `compute.py`/`render.py` consume; neither knows or cares which produced it. Gate
      FAIL → the contract is still written, stamped `reconciled: false`, with a
      PROVISIONAL banner and `can_publish()` refusing. Under Option A, `can_publish()`
      gates *handing a file off*, not a deploy — there's no infra step left to block.
- [x] **Step 4 — Monthly Trading dashboard redesign** (`7988d85`), all 13 items from
      Lena's spec:
  - Headline recomposed: YoY growth% and B2B share of revenue added; GM% moved out of
    the headline into the revenue × GM matrix only; the Sell-Through/WC KPI and the
    entire inventory-feed dependency removed from trading (`st`/`wc`/`inv` are stripped
    from the contract at emission, not merely left unread downstream).
  - Category bars show current + LY value (both absolute) with a direction-only colour
    cue (`badge_class` on the YoY movement, never the % itself — so a near-zero-LY
    category doesn't read as a spurious "+900%"), plus a subcategory drill toggle.
  - Category and Finish analysis switched from top-SKUs to top-collections + B2B share.
  - Collection Performance and Collection Analysis merged into one
    bar-chart-with-click-to-drill view (top-10 SKUs per collection: cash/units × UK/US,
    % share, movement) — every view responds to the same Cash/Units × UK/US/Total
    toggles, with every permutation pre-baked server-side (the template's JS only
    shows/hides, never computes).
  - Movers switched from collections to SKUs: top-10 rising/falling, Live-status only.
  - Revenue × GM matrix gained a bubble-size legend.
  - Fixed-count validation retired — the old "exactly 8 SKUs / 8 finishes" asserts are
    gone; categories, subcategories, finishes, and collections are discovered
    dynamically from whatever the sheet actually has.
  - Period/comparator labels (MoM vs QoQ, LM vs LQ) are driven by `mode`, not
    hardcoded — the template is quarterly-ready without a fork (see Step 5, §3).
  - **Latent bug found and fixed along the way:** the old fixed row-dicts
    (`TYPE_ROWS`/`FINISH_ROWS`) silently hid whatever wasn't hand-listed — a Taps
    department, a "Door" department, and 21 of the 29 real finishes never rendered.
    Now surfaced by construction; a completeness tripwire was added as a follow-up
    (below) so it can't silently return.
- [x] **Step 4 follow-up fixes.** Colour assignment was a fixed-length palette cycled by
      index — the same class of bug as the row-dicts above, just moved into colours: 29
      finishes duplicated onto an 18-entry palette, and 3 of the (then) 7 departments had
      no colour at all. Replaced with `config.assign_finish_colors`/`assign_dept_colors`:
      a small curated map for the recurring names (brand legibility) plus evenly-spaced,
      genuinely distinct HSL colours generated for anything else, keyed by name so a
      given group tends to keep its colour run to run. Category tabs now enumerate every
      department (Taps/Door included, with an explicit "no collections this period"
      state rather than just not having a tab). Completeness tripwire added
      (`validate._completeness_errors`): every department/collection with a
      revenue-bearing SKU must appear in its analysis block — hard error if not (a
      genuine gap surfaced and fixed along the way: `extract_skus_all` and
      `extract_collections` disagreed on the "Unknown" default for a blank Product
      Type). Finishes get the same check as a diagnostic warning, not a hard gate — the
      sheet's Finish table is a curated top-line view, not a guaranteed enumeration of
      every finish string in By SKU (open-world, unlike the closed department/collection
      sets), so a handful of compound/one-off finish names missing a Finish-table row is
      disclosed, not treated as a regression. Output is now genuinely self-contained
      (fonts embedded as base64 `@font-face`, no Google Fonts `<link>`) per Option A.
      Movers grain confirmed as SKU (not collection, per the earlier D1 outline) —
      deliberate: the "Live-status only" filter is defined on `uk_status`/`us_status`,
      which only exists per SKU, so collection grain can't carry it.
- [x] **Step 5 — Quarterly Trading builder** (`trading/quarterly.py`). Aggregates 3
      monthly oracle workbooks (Apr+May+Jun 2026) into the exact Step-4 contract shape,
      `mode: "quarter"`; the redesigned template is reused **completely unchanged** — only
      `render_contract()` now passes `mode` through to `compute_kpi_tokens`/
      `compute_ribbon_tokens`, which already resolved MoM/LM vs QoQ/LQ correctly. The one
      correctness rule: additive components (revenue, units, £ by country/channel/
      department/finish/collection) are summed directly; every rate/GM/YoY is recomputed
      once from the summed numerators/denominators (revenue-weighted), never averaged
      across the three monthly rate outputs — verified against a naive mean on the real
      data (they differ; the contract matches the correct recomputation, not the mean).
      Reconciles exactly to the aggregated Q2 target (£1,472,202.82; UK/US/ROW/units all
      within 0.1%, most exact). LQ (Q1 2026) has no data source this run (no committed
      prior quarterly contract, no Jan–Mar 2026 files) — stamped zero/honest, never
      fabricated, so this quarter's QoQ movers are empty (template already renders "No
      data"); LY (Q2 2025) *is* real, reconstructed by summing each month's own LY_BLOCK
      columns (no separate 2025 source files needed). Frozen `2026-Q2_contract.json` as
      the quarterly regression fixture (`trading/tests/test_quarterly.py`, 8 checks, all
      passing). Matrixify quarterly front-end exists and is wired the same way but can't
      run yet — only May's UK/US exports are committed, no April/June — and will be
      provisional once it can, same order-scope reason as every monthly Matrixify
      contract; a clear `FileNotFoundError` names the missing export rather than failing
      cryptically.
      **Three real, previously-undiscovered bugs found and fixed building this:**
      (1) `contract.py`'s period-string parser only matched full month names (`%B`); the
      sheets use 3-letter abbreviations, so every month except May (spelled identically
      either way) raised `ValueError` — never caught because May was the only month ever
      exercised. (2) `STATUS_ROWS` was hardcoded to 4 statuses and silently dropped two
      real rows, "Not For Sale" and "Pre-Launch" (in **both** monthly and quarterly
      sheets) — the exact Taps/Door/finish bug class from Step 4, just not yet found
      there; `extract_statuses` is now dynamic like Product Type/Finish, and the
      statuses table is now genuinely additive to `total_sales` (100.00% on both May and
      Q2, verified). (3) April 2026's By Collection sheet is missing the "vs LY LM"
      column entirely — the identical column-shift already documented for the quarterly
      layout (`COLL_COL_Q`) — despite being a genuine monthly report; column layout
      turned out to be a property of the specific export's vintage, not of month vs.
      quarter, and was silently reading UK/US units into the UK/US £ slots for every
      April collection. `extract_collections` now **detects** the layout from the
      sheet's own header row instead of trusting `mode`.
- [ ] **Deferred — order-scope reconciliation (both stores), narrowed 2026-08-05, twice.**
      Neither "oracle is net" nor "oracle is gross" is the clean answer —
      `trading_logic_spec.md` (primary source, read from the live sheet's own formulas)
      confirms the oracle IS net of returns, on a real, empirically-confirmed
      Supermetrics order-cohort-week basis — but a June 2026 decisive test (real
      committed export + real committed oracle) shows June's actual cohort-returns are
      **not** small (≈£16.6K UK), and subtracting them on top of the order-discount term
      overshoots badly. Leading hypothesis is now report-generation timing (the frozen
      oracle's own return figure was smaller, at whatever maturity it had when
      generated) — pending confirmation of when `Monthly_Trading_Report.xlsx` is
      typically generated, from whoever's closest to the live sheet (§5, §7). **Ruled out
      entirely:** symmetric UK+US cancelled-order scope forensics — a blanket
      `Cancelled At` exclusion was tested across April/May/June 2026 and made both legs
      *worse*; the 28-order July scope question is not a cancelled-order-exclusion
      problem. **No longer treated as a confirmed independent component:** order-level
      `Discount` rows (standalone discount-code rows, currently dropped entirely by
      `build_lines()`) empirically fit UK within ±0.2% across three months, but may be a
      structural coincidence with the returns question rather than a real separate
      mechanism — see §5. Under Option A this blocks *distributing* a reconciled
      Matrixify-sourced number, not building — a provisional file still builds and hands
      off behind the banner. `test_line_detail_enrichment.py`'s coverage/vocabulary/
      grouping checks stay failing until this closes — disclosed, not a regression.
- [ ] **D2 — Returns dashboard.** Separate template. Tweak list now received from the Q1
      review: five ruled definitions locked; the watchlist dissolves into the
      category→subcategory→SKU tracker; three decisions remain open (exchange
      definition, family axis, whether trade appears in the headline) — owed by
      Lena/Daisy, §4. Spec: `BRIEF_returns_dashboard_v2.md`. (The returns *join* itself —
      single-count, order-month basis, orders-based rate — is already locked; see §5.)
- [ ] **Step 7 — Yotpo / reviews.** Independent, export-based scanner
      (`reviews/review_feedback.py`), runs on the full export, no live API calls, no
      per-review cost. Blocks nothing else and isn't gated on anything above.

---

## 4. Owed-by

- **Lena / Daisy** — the three open returns decisions (exchange definition, family axis,
  trade-in-headline; D2 above).
- **Whoever's closest to the live sheet** — cohort-attribution mechanism confirmed (July
  then June checks, 2026-08-05); June's decisive test ruled out "discounts + sheet's
  current returns" as the fix. Top ask now: **when is `Monthly_Trading_Report.xlsx`
  typically generated/frozen relative to each month's close?** — tests the leading
  report-generation-timing hypothesis directly. April/May's own per-country "Returns
  (inc VAT)" breakdowns are still useful (confirms June's ~2×-July growth pattern
  generalises) but secondary to the timing question. See §5, §7.
- **Maintainer** — the custom/project instructions that still cite "GitHub Pages behind
  Cloudflare Access" need updating to Option A (self-contained file → Slack/Drive).

---

## 5. Definitional register (decisions, not data — pin these)

These are the choices baked into the reports that must live in code with a test, because
they are exactly where a naive rebuild goes silently wrong.

### Trading (revenue) — the reconciliation contract

> **⚠ Revenue definition — REVERTED to net-of-returns, 2026-08-05 (Lena), after a
> same-day flip-then-revert.** The 2026-08-04 "gross, returns never netted" callout this
> replaces was based on weaker-provenance evidence (column labels + an arithmetic fit
> against the oracle) and directly contradicted `trading_logic_spec.md`, which documents
> the live sheet's actual `AB` formula from reading real formula cells, not a guess —
> `AB` genuinely subtracts a real "Returns (inc VAT)" column. **Primary source wins:
> revenue IS net of returns.** Plank trading revenue = **sales net of returns, ex-VAT
> (net of discounts too)** — but on Supermetrics' **order-cohort-week basis**: a month's
> Returns column holds only returns *of orders placed that month*, which at snapshot time
> is small (few of that month's own orders have been returned yet by the time the report
> is pulled) — this is why the 2026-08-04 diagnostic's "no returns term" computation
> fit UK so well: UK's *true* cohort-returns figure is tiny (≈£0–500) against a ≈£250K
> headline, not because returns are actually excluded.
>
> **The actual bug, per this reconciliation, is a returns-basis mismatch, not a
> netting-vs-not-netting one:** `line_ab()` subtracts the full **processing-window**
> refund-line total from the Matrixify export (≈£26K UK / ≈£23K US in May) — refunds
> *processed* that month regardless of when the original sale happened — instead of the
> sheet's much smaller **order-cohort** figure (returns of *that month's own* orders
> only). This also reframes the US residual: the 2026-08-04 diagnostic's `B` (gross minus
> order-level discounts, no returns subtracted) left US short by +1.45%/−1.35%-ish
> depending on the month — plausibly *because* it wasn't subtracting the small genuine
> cohort-returns figure (theorised ≈£3.1K for May) that US, unlike UK, actually needs.
> Cancelled orders were a red herring for this residual (see below).
>
> **Cohort-attribution mechanism: CONFIRMED, 2026-08-05, empirically** — not just
> asserted. Read directly off the live sheet for July 2026: col O totals −£6,624.46
> (GBP, inc-VAT, 116 lines) UK and −$8,343.00 (USD, 52 lines) US, ROW £0. The whole tab
> is scoped to July-created orders only (no June-or-earlier order leaks in), and at least
> one return sits on an order last-updated **1 Aug** (processed after month-end) yet is
> still attributed to its **July** order — the exact signature of order-cohort
> attribution, not processing-date attribution. This part of the theory is settled.
>
> **Magnitude — decisive test run against June 2026 (real committed export + real
> committed oracle), 2026-08-05: "add the sheet's real returns on top of the confirmed
> discount term" is RULED OUT.** June's real col O: −£16,579.12 UK / −$11,256.40 US
> (≈−£8,495 @1.325) / −£202.43 ROW — about **2× July's** figure, exactly what a maturing
> cohort predicts (June had ~5–9 more weeks to accrue returns than July's 3-day-old
> pull). Subtracting this real figure **in addition to** the confirmed order-discount
> term overshoots badly: UK swings from `B`'s +0.116% to **−6.87%**, US from −1.35% to
> −4.73%. Subtracting it **alone** (no order-discount term) does better but still misses:
> UK −1.69%, US −1.09%, ROW +0.15%. Both undershoot the oracle — the signature of
> subtracting a *more mature* (larger) return figure than whatever the frozen oracle
> itself baked in at its own generation time.
>
> **New leading hypothesis: report-generation timing (maturity), not formula shape.**
> `trading_logic_spec.md`'s cited cells (`AT7`/`CD7`/`DN7`) are confirmed to be the exact
> same columns (46/82/118) `_read_oracle_row7()` reads from the committed oracle — this
> **is** the same artifact the spec describes, not two different reports. Most consistent
> read: the historical oracle files were likely generated close to each month's own
> close (similar cadence to July's 3-day pull), baking in a *small* `O` — which is why
> `B` (discounts only) fit so well. Today's live pull, taken weeks later, sees a *larger,
> more mature* `O` for the same month that's real today but isn't what the frozen number
> reflects. If true, `line_ab` can't just "subtract the sheet's current `O`" — it would
> need to reproduce the oracle's own maturity window, a harder problem than a formula
> tweak. This also reopens the order-discount finding's certainty: it may be a
> structural coincidence (both terms happen to be similarly sized) rather than the real
> mechanism — not disproven, but no longer certain.
>
> **Single most valuable next fact:** when is `Monthly_Trading_Report.xlsx` typically
> generated/frozen relative to each month's close? A few-days-after-month-end cadence
> supports the maturity theory directly; a much-later/repeatedly-revised cadence means
> this doesn't hold either and the residual is something else. April/May's own `O`
> breakdowns are still useful for confirming June's ~2×-July pattern generalises.

- **Empirically holds, but downgraded from "confirmed independent mechanism" to
  "observed fit of uncertain cause"** (2026-08-04/05 diagnostics — `trading/
  build_matrixify.py recomposition`, see `RECONCILE_HANDOFF.md`): **UK value = gross
  ex-VAT minus order-level discount codes**, a component `build_lines()` currently drops
  entirely (standalone `Discount` line-type rows, not the per-line discounts already
  netted into `Line: Total`), reproduces the oracle within ±0.2% across April/May/June
  2026. **No longer believed independent of the returns question** — the June decisive
  test (above) shows real cohort-returns are *not* negligible (≈£16.6K UK), so this fit
  may be a structural coincidence (order-discounts and early-maturity returns happening
  to be similarly sized) rather than the true mechanism. Do not treat this as a
  standalone confirmed component until the maturity-timing question above resolves.
  - **Cancelled orders are included in the oracle, not excluded as a scope question.** A
    blanket `Cancelled At` exclusion was tested against all three months and made the US
    gap *worse* every month, and pushed UK *negative* every month despite UK having its
    own 18–30 cancelled orders/month that the oracle is evidently still counting. This
    closes the "28-order scope" question from a different angle than expected: it isn't
    a scope-exclusion problem at all.
  - **Units are a separate, still-open thread; treat the builder as correct.** The
    builder's own unit count exactly matches a from-scratch recount of `Line: Quantity`
    over `Line Item` rows in the same source CSVs — it is not undercounting its own data.
    The oracle's "Gross Unit Sold" does not foot internally (UK+US+ROW vs. its own Total
    column disagrees by a few hundred units, sign flips month to month) and a "the oracle
    double-counts returned units" hypothesis only partially fits (order-of-magnitude
    match for UK/US, fails outright for ROW). Resolve the same way as the value
    question — read the live sheet's unit formula — rather than padding the builder to
    match a figure that may itself be wrong.
- **Revenue basis — NOT yet shipped in code either way.** `line_ab()` in `revenue.py`
  still computes the pre-2026-08 formula: full processing-window refund lines subtracted,
  order-level discount codes ignored. Do not change it until (a) the sheet-owner question
  above confirms the order-cohort returns basis, and (b) the resulting per-bucket figures
  are re-checked against all three committed months — a partial fix (e.g. UK's confirmed
  discount term alone) would silently restate UK's committed history while the returns
  basis and US side are still unresolved.
- **VAT = subtract Shopify's per-line tax, NOT `/1.2`.** There is no `/1.2` in the real
  revenue path; the `UK_SALES_ARE_INC_VAT` toggle is retired for trading. (Caveat: this
  trusts Shopify's per-line tax config; a `/1.2` assumption would diverge on
  zero-/mixed-rate lines.)
- **FX must be deterministic.** The sheet uses live `GOOGLEFINANCE` for US→GBP, so the
  same month reprints differently over time. The builder uses a frozen, dated GBP/USD
  table stored in the repo, keyed by order date — the one deliberate deviation from the
  sheet. Which authoritative series to commit for months other than July is still open
  (§7).
- **Country is the reconciliation key**, not channel. `uk + us + row` must equal the
  headline total within **0.1%**. D2C/B2B do **not** partition the total — never
  reconcile from the channel split.
- **ROW** is derived from ship-to country (GB→UK, US→US, else→ROW) with a store fallback
  when country is blank. It reconciles by construction (one country per line).
- **Order+SKU is the only join key** (no stable Shopify line-item id surfaced) — the same
  double-count trap as returns: sum/de-dupe on order+SKU, or pull a real `line_item.id`
  via the Admin API / Matrixify.
- **"Weeks Cover" is really months** (`inventory ÷ monthly units`) in the sheet's own raw
  columns. *Retired from the dashboard with Step 4* (§2) — trading dropped the
  inventory-feed dependency and the contract no longer emits `st`/`wc`/`inv` at all. Kept
  here only so the raw-sheet fact isn't lost if an inventory feed is ever reintroduced.

### Returns — locked from the Q1 proof

**LOCKED decisions (Lena + Daisy, Aug 2026) — apply consistently across the whole
dashboard:**
- **Single-count.** Each return is counted **once** via a de-duplicated `sku+order`
  join. The legacy per-line `SUMIF` stamping double-counted (~22% on Q1) and is retired;
  historical returns restate down accordingly — Daisy has signed off on restating the
  history.
- **Order-month basis.** Every view buckets by **order month** (the sale month), not the
  return month — a January order returned in March counts in January. Chosen for
  assessing *product*, not topline cash. *Maturity caveat:* order-month cohorts make the
  most recent months look artificially low on returns, because their returns haven't all
  happened yet — flag still-maturing recent months on the dashboard.
- **Orders-based return rate.** The headline rate is returned orders ÷ orders (distinct
  orders on both sides), never units returned ÷ units sold. Units returned and returns
  cash remain as secondary detail.
- **Return source = Returns-zap, not Shopify.** Shopify only counts a return once the
  warehouse checks it in and undercounts; Returns-zap counts it regardless. Headline uses
  the Returns-zap basis.
- **Returns cash is notional:** `RRP-ex-VAT × units returned` — list value of returned
  units, *not* the actual refunded amount. Label it as such wherever it appears.
- **Labels are whitespace-sensitive** in the hand-built sheets — normalise on every key
  (the `"Electric Accessory "` trailing-space bug is the canonical example: it hid
  £169.90 in the hand-built sheet; the builder recovers it).
- **LQ / LY are hand-carried** in the workbook today; the builder must read them from
  committed prior-period outputs instead, same as trading's contract chain (§2, #3).

### Product reference

- **Line Detail is the canonical product reference and status model.** Category
  hierarchy is Product Type (department) › Product Category (item) › Sub Category
  (style). Kit/assembled SKUs stand alone, no rollup. The trading and returns/reviews
  sides deliberately use two different classifiers for department (see
  `trading/line_detail.py`'s and `common/sku_taxonomy.py`'s own docstrings) — documented,
  not drift.

---

## 6. The reconciliation gate (runs on every build; aborts on failure)

- **Trading:** assert `uk + us + row == total` within 0.1% (relative); assert a ROW
  bucket is present; assert revenue reproduces the sheet's `AB` basis; assert VAT was
  removed by subtracting Shopify tax (no `/1.2`); assert FX came from the frozen dated
  table, not a live source; assert every toggle state (cash/units × UK/US/Total)
  reconciles to the same total (Step 4, §2).
- **Returns:** assert Total == sum of the status/category block for **additive measures
  only** (units, cash) — order counts are distinct per grouping and never asserted
  additive. Assert every label matches a whitespace-normalised label in the data; assert
  the return source is Returns-zap; assert the headline rate is orders-based; assert
  every row is bucketed by order month.
- **All reports:** regression-check against the committed oracle workbook for any period
  already captured.

A failed gate prints the offending figures and the gap, and writes **no output**. Under
Option A this only ever blocks a handoff — there is no deploy step downstream of the gate
to guard.

---

## 7. Open confirmations

Resolved:
- ~~UK VAT basis~~ → subtract Shopify per-line tax (no `/1.2`); toggle retired.
- ~~ROW derivation~~ → ship-to country (GB→UK/US→US/else→ROW), store fallback.
- ~~Channel~~ → B2B if company present else D2C; does not partition the total.
- ~~Primary key~~ → resolved negatively: no line-item id; join on order+SKU, de-dupe.
- ~~Category/finish/collection coverage~~ → resolved by Step 4's dynamic discovery
  (§2) — no more fixed lists to go stale.
- ~~Cancelled-order scope~~ → **resolved negatively, 2026-08-05.** Cancelled orders are
  included in the oracle, not excluded — a blanket `Cancelled At` exclusion made both
  legs worse across all three of April/May/June 2026. This was the original "28-order
  scope" suspicion; it isn't a scope-exclusion problem.
- **Sales value basis** → **still net of discounts and returns, per `trading_logic_spec.md`
  (reverted 2026-08-05 after a same-day flip based on weaker evidence — see §5).** Cohort
  attribution confirmed empirically (July, then June checks); **NOT small at snapshot
  time as first theorised** — June's real figure is ≈£16.6K UK, ≈2× July's, growing with
  cohort maturity. The decisive June test rules out "subtract order-discounts AND the
  sheet's current returns" (overshoots badly) — leading hypothesis is now
  report-generation-timing (the frozen oracle's own return figure was smaller, at
  whatever maturity it had when generated), not a wrong formula shape. Order-level
  discount codes' fit is no longer treated as an independently-confirmed component — see
  §5.

Still open:
- **Line Detail source:** activate the Dropbox fetch path
  (`LINE_DETAIL_SOURCE = local | dropbox`) — gated on credentials.
- **Frozen FX source:** pick the authoritative dated GBP/USD series to commit for every
  month (only July is confirmed today) — close enough to the sheet's historical
  `GOOGLEFINANCE` values that the regression stays in tolerance.
- **Glossary/label fix:** rename the trading headline from "gross sales" to an honest
  label and document the returns-netting — needs the glossary owner's sign-off. (Reopened
  2026-08-05: briefly marked moot during the same-day flip; revenue is net-of-returns
  again, so the "gross sales" label is a genuine mislabel once more.)
- **Returns-basis magnitude — decisive test run 2026-08-05 against June (real data both
  sides); "discounts + sheet's current returns" RULED OUT** (overshoots by −6.87% UK,
  −4.73% US vs. `B` alone's +0.12%/−1.35%) — see §5. **Owed by whoever's closest to the
  live sheet: when is `Monthly_Trading_Report.xlsx` typically generated/frozen relative
  to each month's close?** This is now the single most valuable open fact — it tests the
  leading report-generation-timing (maturity) hypothesis directly. April/May's own
  per-country `O` breakdowns remain useful (confirms/refutes whether June's ~2×-July
  growth pattern generalises) but are secondary to the timing question now.
- **Returns-basis code fix:** blocked on the timing question above — if confirmed,
  `revenue.py`'s `line_ab` needs to reproduce the oracle's own maturity window (not
  simply subtract a live column), which is a harder change than a formula tweak, plus
  settle whether the order-level-discount term is real or coincidental (see §5), then
  re-gate against all committed months. Do not ship the discount term alone — see §5.
- **Oracle's own Gross Unit Sold reliability:** doesn't foot internally (UK+US+ROW vs.
  Total disagrees, sign flips month to month) and a returned-units-double-counted
  hypothesis only partially explains it (fails for ROW) — treat the oracle's unit
  figure, not the builder's, as suspect until this is resolved the same way (read the
  live sheet's unit formula) rather than guessed from Matrixify arithmetic alone.
- **Order-scope reconciliation** — see §3 (narrower now that cancelled orders are ruled
  out as the mechanism).
- **The three returns decisions** — see §3/§4 (D2, owed by Lena/Daisy).

---

## 8. Risks and how the design answers them

| Risk | Mitigation |
|---|---|
| A wrong headline gets published | Reconciliation gate aborts the run; nothing ships un-reconciled |
| Logic drifts from what colleagues trust | Workbook stays the spec + regression oracle; builder must match it |
| Feed layout changes silently | Schema asserts on each feed; the run stops and names what moved |
| LLM invents numbers | Numbers are computed by deterministic code, never free-generated |
| Colleague can't maintain code | Colleague only triggers/eyeballs via the Project; maintainer owns code |
| A file leaks outside Plank | Option A hosting = handoff to a Plank-scoped Slack/Drive location; access is workspace membership, not a public URL — no Pages/Cloudflare/DNS surface to misconfigure |

---

## 9. Roles

- **Maintainer** — owns the builders, the gate, the mappings, and the Project. Changes
  code only when a feed's layout changes or a definition is revised (and re-runs a
  fixture to confirm nothing broke).
- **Report generators (colleagues)** — run the report through the shared Claude Project,
  sanity-check the headline, hand off the self-contained file per Option A. Never edit
  code or hand-patch output numbers.
