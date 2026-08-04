# Plank Product Data Platform — Roadmap

**Status:** Path 2 (deterministic, connector-sourced builders) running end-to-end for
Monthly Trading, through a redesigned dashboard. Returns join and Quarterly Trading are
next; the returns *dashboard* has a fresh spec.
**Last updated:** 2026-08-04
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
- **Revenue definition (trading, locked):** net of returns, ex-VAT. Country (UK + US +
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
    Now surfaced by construction; a completeness tripwire (assert every group the
    sheet has actually renders) is queued as a Step 4 follow-up (§3).

---

## 3. Status — queued / next (in order)

- [ ] **Step 4 follow-up fixes.** Colour + legend coverage for the newly-surfaced
      departments/finishes; dynamic (not hardcoded) category tabs; the completeness
      tripwire itself; make the output file actually self-contained per Option A (fonts
      currently load from a Google Fonts `<link>`, not embedded); verify the movers
      grain matches the intent of the "B2B share" label. Spec:
      `CLAUDE_CODE_step4_followup_fixes.md`.
- [ ] **Step 5 — Quarterly Trading builder.** Cheap and unblocked. Roll three monthly
      builds into the same contract shape (the returns model already shows the
      month→quarter rollup pattern — reuse it); the Step-4-redesigned template is reused
      **unchanged**, since its period/comparator tokens already resolve to QoQ/LQ in
      quarterly mode. Gate + regression fixture as monthly.
- [ ] **Deferred — order-scope reconciliation (both stores).** UK and US are each short
      ~5–6% against the oracle, same sign and order of magnitude (FX and
      discount-netting have both been ruled out as the cause). Cheap test first: confirm
      whether the oracle's country columns are net or gross of returns — the shortfall
      is suspiciously close to the by-value return rate, and if the columns are gross
      this closes the whole gap with no per-order forensics needed. If not: symmetric
      UK+US cancelled/scope forensics (the same question as the 28-order July scope
      mismatch). Under Option A this blocks *distributing* a reconciled Matrixify-sourced
      number, not building — a provisional file still builds and hands off behind the
      banner. `test_line_detail_enrichment.py`'s coverage/vocabulary/grouping checks stay
      failing until this closes — disclosed, not a regression.
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
- **Lena** — the return-basis net/gross answer for the order-scope deferral (§3), if
  already knowable from the sheet owner, before more per-order forensics; the custom/
  project instructions that still cite "GitHub Pages behind Cloudflare Access" need
  updating to Option A (self-contained file → Slack/Drive).

---

## 5. Definitional register (decisions, not data — pin these)

These are the choices baked into the reports that must live in code with a test, because
they are exactly where a naive rebuild goes silently wrong.

### Trading (revenue) — the reconciliation contract

> **✔ Revenue definition — CONFIRMED (Lena, Aug 2026).** Plank revenue = **sales net of
> returns, ex-VAT** (net of discounts too, per Shopify "net sales"). This matches the
> live sheet's `AB` formula `(net_sales_incVAT − tax − returns) / FX`, so month-over-month
> history stays comparable. Returns are still handled separately in the *returns* report.
> Never label the trading headline "gross" (see §7, still open).

- **Revenue basis = reproduce `AB` exactly:** `(Total Product Sales inc-VAT − Shopify Net
  Sales Tax − Returns) ÷ FX`, per line, excluding shipping lines; includes the zero-net
  edge branch. Net of discounts, net of in-window returns, ex-VAT.
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
- ~~Sales value basis~~ → net of discounts and in-window returns.
- ~~Category/finish/collection coverage~~ → resolved by Step 4's dynamic discovery
  (§2) — no more fixed lists to go stale.

Still open:
- **Line Detail source:** activate the Dropbox fetch path
  (`LINE_DETAIL_SOURCE = local | dropbox`) — gated on credentials.
- **Frozen FX source:** pick the authoritative dated GBP/USD series to commit for every
  month (only July is confirmed today) — close enough to the sheet's historical
  `GOOGLEFINANCE` values that the regression stays in tolerance.
- **Glossary/label fix:** rename the trading headline from "gross sales" to an honest
  label and document the returns-netting — needs the glossary owner's sign-off.
- **Order-scope reconciliation** — see §3.
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
