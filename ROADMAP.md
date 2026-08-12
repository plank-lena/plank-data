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
- **Revenue definition (trading, locked):** **net of returns**, ex-VAT, per-line discounts
  netted, per `trading_logic_spec.md`'s documented `AB` logic — `revenue.py`'s `line_ab`
  is unchanged throughout this whole investigation and was never proven wrong. **Matching
  the hand-built oracle to 0.1% is RELEASED as a publishing requirement (Lena, 2026-08-05)**
  — the oracle's own returns figure is an early, ~9–15-day-post-close snapshot that keeps
  maturing for weeks, a structurally moving target a deterministic rebuild can't hit by
  design. Full decision + evidence trail in §5. Country (UK + US + ROW) is now the ONLY
  hard reconciliation key — `uk + us + row == total` within 0.1% (a structural leak check,
  not oracle-matching), a ROW bucket always present even if zero. **Cross-dashboard
  consistency** (a month's figure must not change once committed, e.g. when it becomes LM
  in a later month's dashboard) is a SEPARATE guarantee, delivered by always chaining
  LM/LY from the previously-committed contract — never recomputing an old month fresh.
  See §5's DECISION callout.

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
    from the contract at emission, not merely left unread downstream). **REVERSED
    2026-08-11 (Lena)** — see the connections rebuild note right below §2's list: a real
    on-hand inventory source now exists, so `st`/`wc`/`inv` are reinstated, computed for
    real rather than stripped.
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
- [x] **Order-scope reconciliation (both stores) — CLOSED BY DECISION, 2026-08-05, not by
      a formula fix.** Investigation trail: neither "oracle is net" nor "oracle is gross"
      was the clean answer — `trading_logic_spec.md` (primary source, read from the live
      sheet's own formulas) confirms the oracle IS net of returns, on a real,
      empirically-confirmed Supermetrics order-cohort-week basis — but a June 2026
      decisive test (real committed export + real committed oracle) showed June's actual
      cohort-returns are **not** small (≈£16.6K UK), and subtracting them on top of the
      order-discount term overshoots badly; a report-generation-timing (maturity)
      hypothesis fit May/June well but not April as cleanly. **Ruled out entirely:**
      symmetric UK+US cancelled-order scope forensics — a blanket `Cancelled At`
      exclusion was tested across April/May/June 2026 and made both legs *worse*; the
      28-order July scope question was never a cancelled-order-exclusion problem.
      **Decision (see §5):** rather than keep chasing which exact formula reproduces a
      structurally-moving-target oracle, matching it is released as a publishing
      requirement — the builder computes per the documented `AB` logic and gates only on
      the structural uk+us+row leak check. Dashboards now build AND publish normally (no
      permanent provisional banner) once that structural check passes.
      `test_line_detail_enrichment.py`'s coverage/vocabulary/grouping checks (a related
      but separate axis — Line Detail's own enrichment quality vs. the oracle's By
      Collection/Status breakdowns) remain in their existing disclosed-failing state,
      unchanged by this decision and not touched today — same underlying reasoning
      applies if revisited.
- [ ] **D2 — Returns dashboard.** Separate template. Tweak list now received from the Q1
      review: five ruled definitions locked; the watchlist dissolves into the
      category→subcategory→SKU tracker; three decisions remain open (exchange
      definition, family axis, whether trade appears in the headline) — owed by
      Lena/Daisy, §4. Spec: `BRIEF_returns_dashboard_v2.md`. (The returns *join* itself —
      single-count, order-month basis, orders-based rate — is already locked; see §5.)
- [ ] **Step 7 — Yotpo / reviews.** Independent, export-based scanner
      (`reviews/review_feedback.py`), runs on the full export, no live API calls, no
      per-review cost. Blocks nothing else and isn't gated on anything above.
- [x] **Connections rebuild (2026-08-11).** `common/sources.py` is now the single
      source-of-truth for every external data connection: Line Detail and on-hand
      inventory (`IN Shopify Product Data`, never the ship sheet) are Drive-sourced via
      the Google Drive connector; ReturnZap's `getReturns` Apps Script pull (already
      built, key in Script Properties) is read the same way. Live weeks-cover/inventory
      reinstatement (above) is part of this — `trading/contract.py`'s
      `emit_contract_from_matrixify` takes a real `inventory_index` now.
      **Two real data-quality findings surfaced doing this, not fixed here:**
      (1) the live ReturnZap sheet had 1,755 raw rows but only 450 distinct — one return
      duplicated 139 times, byte-identical — almost certainly the Apps Script appending
      instead of clearing-then-writing; `common/sources.dedupe_returns_export` fixes this
      on read (reported, never silent), but the script itself should be checked/fixed at
      the source. (2) even after dedupe, the sheet's total return volume (450 rows,
      ~292 orders, spanning mid-2024–2026) is far below the ~2,020 distinct returned
      orders the existing `.numbers` exports show for Q1 2026 ALONE — `assert_returns_
      overlap_sales` correctly refuses to build against it for any historical period
      (0.19% overlap on a Q1 2026 test), so `returns/build_q1.py`/`build_q2.py` still
      default to the proven `.numbers` files; the new sheet-based reader
      (`returns/build.py`'s `load_returns_export_from_sheet`) is wired, tested, and ready
      once the Apps Script's pagination is fixed upstream to pull full history. Matrixify
      MCP connectivity confirmed live (`matrixify_export_get_setup` against
      Matrixify-PlankUK); the full export→poll→download round trip is written and
      documented in `common/sources.py` but wasn't run to create a new committed month
      in this pass — that's a content decision (which period) for whoever's building the
      next report, not a connections question.

---

## 4. Owed-by

- **Lena / Daisy** — the three open returns decisions (exchange definition, family axis,
  trade-in-headline; D2 above).
- ~~Whoever's closest to the live sheet~~ — **nothing owed as of 2026-08-05.** The
  report-generation-timing question this section used to ask about is moot: matching the
  hand-built oracle is released as a publishing requirement (§5's DECISION callout), so
  there's no longer a fact whose answer would change what ships. Thank you for the July
  and June primary-source checks — they're what confirmed the cohort mechanism and made
  this decision an informed one, not a guess.
- **Maintainer** — the custom/project instructions that still cite "GitHub Pages behind
  Cloudflare Access" need updating to Option A (self-contained file → Slack/Drive).

---

## 5. Definitional register (decisions, not data — pin these)

These are the choices baked into the reports that must live in code with a test, because
they are exactly where a naive rebuild goes silently wrong.

### Trading (revenue) — the reconciliation contract

> **✔ DECISION (Lena, 2026-08-05) — the hand-built oracle is RELEASED as a publishing
> requirement.** After the investigation below (kept in full for the evidence trail —
> read it if you want the "why"), the short version: the oracle's returns figure is a
> snapshot taken ~9–15 days after each month's close, and returns keep maturing for weeks
> afterward (proven: the same July cohort's UK return-line count rises 116 → 298 → 448 at
> 3 days / 34 days / 13 months old). A deterministic, reproducible builder — the whole
> point of this project's Path-2 architecture — cannot hit a target whose "correct" value
> depends on the arbitrary date someone happened to press export. Chasing 0.1% parity
> against it is chasing a moving target, not fixing a bug. **We build and release
> according to the documented logic (below), and no longer gate on matching the oracle.**
>
> **What this changes, concretely (2026-08-05):**
> - `trading/contract.py`'s `emit_contract_from_matrixify` — `reconciled` / `can_publish()`
>   now depend ONLY on the structural leak check (`common/reconciliation_gate.
>   assert_country_reconciles`: `uk+us+row` ties to an independently-computed grand
>   total — a property of the bucketing logic being correct, not of matching a historical
>   number). `country_gaps_vs_oracle` is still computed/reported when an oracle target is
>   available, purely as historical context — it must never be read as pass/fail.
> - `trading/quarterly.py`'s `emit_contract_from_matrixify_quarter` inherits this
>   automatically (its `reconciled` flag is derived from the underlying months' own flags
>   plus its own structural leak check — no oracle dependency of its own).
> - `trading/build_matrixify.py`'s oracle-comparison CLI actions (`reconcile`,
>   `floor_isolation`, `recomposition`, `maturity_cutoff`) are kept as **diagnostic
>   tooling only** — useful for historical comparison/investigation, not something a real
>   build needs to pass.
> - `revenue.py`'s `line_ab` formula is **unchanged** — it was never proven wrong per
>   `trading_logic_spec.md`'s documented AB logic (gross ex-VAT, per-line discounts
>   netted via `Line: Total`, minus tax, minus cohort-attributed returns); the entire
>   investigation was about matching a specific historical snapshot, not about the
>   formula's own correctness.
>
> **What "correct" now means:** the builder computes revenue per the documented AB logic
> from whatever Matrixify data is available at build time — not a specific historical
> number. A month built today will show more mature returns than the same month shown
> historically; that's expected and consistent, not an error.
>
> **The actual cross-dashboard consistency guarantee — read this before touching any
> builder call site.** "The numbers must match across all dashboards, and a month must
> keep its own figure as it becomes LM in later dashboards" is satisfied by the EXISTING
> contract-chaining mechanism, not by the oracle-match question above (these are
> orthogonal). `emit_contract_from_matrixify`'s `lm_contract`/`ly_contract` params, when
> supplied, pull LM/LY from a **previously-committed contract's own frozen `current`
> block** — never a fresh Matrixify recompute of that past month. This MUST be used for
> every month after the first: **always pass the prior month's already-committed contract
> as `lm_contract`** (same-month-last-year's as `ly_contract`), **never re-run the builder
> against an old month's export to regenerate its own figures.** Because returns mature
> for weeks after a month closes, a fresh recompute of May in August would show more
> returns than May's own originally-published contract — if June's dashboard re-derived
> "LM" that way instead of chaining from May's committed contract, May's number would
> silently differ depending on which dashboard you're looking at it from, exactly the
> failure this decision must prevent. `lm_contract=None`/`ly_contract=None` is only
> correct for a period with no prior committed contract at all (the very first month
> built) or a deliberate, explicit historical restatement (same standard as the returns
> report's own "Daisy signed off on restating history" precedent — a conscious act, never
> a silent side-effect of building a later month).
>
> Full evidence trail below and in `RECONCILE_HANDOFF.md` — kept for context, not because
> any of it is still an open question blocking release.

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
> **Generation timing: CONFIRMED (Lena, 2026-08-05) — ~9–15 days post-close** (Mar 9d,
> Apr 11d, May 15d, Jun 13d, from the report files' own save timestamps), consistent
> with the sheet's rolling `EOMONTH(TODAY(),-1)` design. A fully-mature LY July 2025
> comparison independently proves the maturity mechanism itself: UK return-line counts
> rise monotonically with cohort age (116 → 298 → 448 at 3 days / 34 days / 13 months) —
> settled beyond reasonable doubt, regardless of exact-day debates.
>
> **But testing returns-with-a-maturity-cutoff as a standalone replacement for the
> discount term (2026-08-05, `maturity_cutoff` diagnostic, data already committed, no
> further live-sheet access needed) does NOT cleanly resolve the formula.** At each
> month's own confirmed/estimated generation cutoff: May lands within +0.083% Total (as
> good as `B`'s discount-only fit), June within −0.034%–+0.553% depending on 13d vs 15d
> (comparable to `B`), but **April is noticeably worse** (+1.094% vs. `B`'s +0.087%) and
> wants a ~20-day cutoff to match `B`'s precision, not its own confirmed ~11-day mark.
> **The two mechanisms are not independently additive** (confirmed again on June: adding
> both terms overshoots) and checking whether discounted/returned orders overlap enough
> to explain that — they don't (~9–10% of discount value sits on also-returned orders,
> too small). **Net state: two plausible single-term explanations, each fits reasonably
> for most months, don't combine, and April fits neither as tightly as May/June — this
> is more open than it looked an hour ago, not resolved.** Do not implement either as
> `line_ab`'s fix yet. See `RECONCILE_HANDOFF.md` for the full table and next options.

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
  columns. *Retired from the dashboard with Step 4* (§2), **reinstated 2026-08-11** (Lena)
  now that a real on-hand inventory source exists (`common/sources.py`'s
  `shopify_product_data` — the `IN Shopify Product Data` tab, **never** the ship sheet,
  which is inbound POs, not on-hand). `trading/contract.py` converts months→weeks once,
  in `_convert_oracle_weeks_cover`/`emit_contract_from_matrixify`'s own `_wc` helper, for
  both emitters — not duplicated in `compute.py`. `sell_through` has no code-defined
  formula anywhere else in this repo (the old oracle path just copied a raw sheet
  column); the standard `units_sold / (units_sold + inventory_on_hand)` is used —
  flagged as an assumption to confirm, not a locked decision like the returns dedupe key.

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

- **Trading:** assert `uk + us + row == total` within 0.1% (relative, an independently-
  computed grand total — the structural leak check, `common/reconciliation_gate.
  assert_country_reconciles`); assert a ROW bucket is present; assert VAT was removed by
  subtracting Shopify tax (no `/1.2`); assert FX came from the frozen dated table, not a
  live source; assert every toggle state (cash/units × UK/US/Total) reconciles to the
  same total (Step 4, §2). **Matching the hand-built oracle to 0.1% is NOT part of this
  gate as of 2026-08-05** — released as a publishing requirement, see §5's DECISION
  callout; `country_gaps_vs_oracle` is still reported when available, informationally
  only.
- **Returns:** assert Total == sum of the status/category block for **additive measures
  only** (units, cash) — order counts are distinct per grouping and never asserted
  additive. Assert every label matches a whitespace-normalised label in the data; assert
  the return source is Returns-zap; assert the headline rate is orders-based; assert
  every row is bucketed by order month.
- **All reports:** regression-check against the committed fixture (a *frozen contract
  JSON*, e.g. `2026-05_contract_redesign.json` — reproducibility of the builder's own
  output) for any period already captured. For **trading specifically**, this is NOT the
  same thing as matching the hand-built oracle workbook (released, see above) — it's a
  determinism check (same input in, same output out), which still applies and always
  should.

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
- **Sales value basis** → `line_ab` reproduces `trading_logic_spec.md`'s documented `AB`
  logic (net of discounts and returns, ex-VAT, cohort-attributed) and is unchanged
  throughout this investigation — never proven wrong. **Matching the hand-built oracle's
  specific historical number is RELEASED as a requirement (Lena, 2026-08-05) — see §5's
  DECISION callout.** The oracle's own returns figure is an early, ~9–15-day-post-close
  snapshot that keeps maturing for weeks (proven via cohort-age comparisons: UK
  return-line counts rise 116 → 298 → 448 at 3 days / 34 days / 13 months old) — a
  structurally moving target, not a formula bug. Order-level discount codes' apparent fit
  is very likely coincidental (see §5's investigation trail), not a real term to add.
- ~~Returns-basis magnitude / code fix~~ → **closed by decision, 2026-08-05, not by
  further forensics.** The decisive June test ruled out "discounts + the sheet's current
  returns" as a formula (overshoots badly); April didn't fit the leading
  report-generation-timing hypothesis as cleanly as May/June did either. Rather than keep
  chasing which exact formula reproduces a moving-target oracle, the oracle-match
  requirement itself is released — see §5. `line_ab` is not being changed.
- ~~Order-scope reconciliation~~ → **closed by decision, 2026-08-05** (was §3). Cancelled
  orders were ruled out as the mechanism (blanket exclusion made both legs worse); the
  remaining UK/US residual is understood to be the returns-maturity mismatch above, which
  is no longer being chased against the oracle. See §5.
- ~~Oracle's own Gross Unit Sold reliability~~ → **not being chased further, 2026-08-05.**
  It doesn't foot internally (UK+US+ROW vs. Total disagrees, sign flips month to month)
  and a returned-units-double-counted hypothesis only partially explains it (fails for
  ROW) — the builder's own unit count is independently verified correct (matches a
  from-scratch recount of its own source data exactly). Not worth further investigation
  now that oracle-parity isn't the bar; the builder's units stand as computed.

Resolved (cont.):
- ~~Line Detail source~~ → **resolved 2026-08-11**, and it's Google Drive, not Dropbox:
  `common/sources.py`'s `normalize_line_detail_xlsx` downloads the live sheet and lands a
  normalized copy at the same path `trading/line_detail.py` always read locally — no
  parser change needed, per that module's own docstring.

Still open:
- **Frozen FX source:** pick the authoritative dated GBP/USD series to commit for every
  month (only July is confirmed today) — close enough to the sheet's historical
  `GOOGLEFINANCE` values that the regression stays in tolerance.
- **Glossary/label fix:** rename the trading headline from "gross sales" to an honest
  label and document the returns-netting — needs the glossary owner's sign-off (revenue
  is net-of-returns, so "gross sales" remains a genuine mislabel, independent of the
  oracle-match decision above).
- **Contract-chaining discipline going forward:** every build after the first committed
  month for a given period MUST pass the prior month's/prior year's already-committed
  contract as `lm_contract`/`ly_contract` — never recompute a historical month fresh.
  This is the actual mechanism that keeps dashboards consistent with each other now that
  oracle-matching is released — see §5's DECISION callout. Worth a lint/test that catches
  a call site that forgets this, if this becomes a real production pipeline rather than
  ad hoc builds.
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
