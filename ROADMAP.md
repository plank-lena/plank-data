# Plank Product Data Platform — Roadmap

**Status:** Path 2 (hybrid) confirmed and proven on returns.
**Last updated:** 2026-08-03
**Deliverables covered:** Monthly Trading dashboard · Quarterly Trading dashboard · Returns dashboard

---

## 1. The decision, in one paragraph

We are not maintaining a live database platform (Supabase + Shopify sync + Evidence
+ always-on CI). For a set of reports produced **once a month / once a quarter**, that
was more moving parts than the team can own. Instead: each report is produced by a
**deterministic builder** that pulls its raw feeds from the connectors we already have,
computes the numbers in code, and writes the outputs — first a values-only spreadsheet,
then the styled HTML dashboard. The existing hand-built workbooks stay on as the
**human-readable specification and the regression test oracle**: the builder must
reproduce their numbers before anything ships. A **reconciliation gate** runs on every
output and fails loudly rather than publishing a wrong headline.

Why "hybrid": a colleague can read and trust the workbook; the monthly run is
deterministic code with a hard correctness check. We keep legibility *and* reliability,
and we stop losing an afternoon a month to pull-and-paste.

---

## 2. What the proof established (returns, Q1 2026)

Two scripts were run against `Q1_Jan_Feb_Mar_2026.xlsx`. Full detail in
`returns_spike_findings.md`. The result has **two layers** — read both, because the second
is the one that shapes Phase A.

**Layer 1 — aggregation & grouping: PROVEN (exact).** `returns_summary_poc.py` rebuilds the
per-SKU aggregates and groups them by status and by category, matching the workbook to
**0.000%** on every row and the Total, across all six metrics. It also caught a latent sheet
bug: **Electric Accessory** shows £0 in the workbook because its `SUMIF` label carries a
trailing space; the builder normalises whitespace and recovers **£169.90** — the from-code
build was *more correct than the sheet*. This is the whole argument for the pivot, on real
data. (Caveat: the POC took units returned from the workbook's own helper column, so Layer 1
proves the grouping, not the returns join.)

**Layer 2 — the returns join from raw: NOT yet reconciled.** `returns_summary_builder.py`
recomputes the returns join from the raw `Returns zap` feed — what a connector-fed run must
actually do. The **sales side still matches exactly**, but **units returned do not**: a
corrected de-duplicated join gives ~4,317, the workbook shows 5,259, and faithfully
replicating the sheet's method gives ~6,021. The sheet's composite-key `SUMIF`
**double-counts** when a `sku+order` spans multiple order lines (672 cases), and its totals
are scoped by a hand-curated 745-SKU list — so the published figure is partly an artifact of
spreadsheet mechanics, not a clean quantity.

**Conclusion.** The revenue engine — the core of the *trading* builders — is proven. The
returns *join* is the one genuine open problem: it must be rebuilt correctly from the raw
feed, which will **restate historical returns down by ~15–20%**. That is a decision to
socialise with George, not a bug to match. Proceed to build, with the returns join as the
first task of Phase A.

---

## 3. The repeatable pattern (every dashboard uses this shape)

```
  connectors ─► raw feeds ─► [BUILDER] ─► values-only .xlsx ─► [DASHBOARD] ─► HTML
   (Shopify /    (orders,      deterministic   (matches the       template-fill   (published)
    Matrixify,    returns,      code +          predecessor        + output
    Drive)        Line Detail)  gate            report layout)     checks
```

1. **Feeds** arrive from connectors, not from manual export/paste.
2. **Builder** normalises keys (strip whitespace — see the Electric Accessory bug),
   joins on SKU, aggregates, and computes the report's metrics.
3. **Gate** asserts the numbers before writing anything (see §5). A failure aborts the run.
4. **Oracle** — for any period we already have a hand-built workbook, the builder must
   reproduce it within tolerance. Those become permanent regression fixtures.
5. **History** lives as committed output files, not a database. Prior-period comparisons
   (LQ / LY) are read from those committed files, never re-keyed by hand.
6. **Dashboard** is the existing template-fill step (Option B): values-only spreadsheet in,
   styled HTML out, with its own render checks.

Everything a colleague runs happens through the shared **Claude Project**: they trigger
the builder, eyeball the output, and publish. Only the maintainer touches builder code,
and only when a feed's layout changes.

---

## 4. Definitional register (decisions, not data — pin these)

These are the choices baked into the reports that must live in code with a test, because
they are exactly where a naive rebuild goes silently wrong.

### Trading (revenue) — the reconciliation contract

> **✔ Revenue definition — CONFIRMED (Lena, Aug 2026).** Plank revenue = **sales net of returns,
> ex-VAT** (net of discounts too, per Shopify "net sales"). This intentionally **redefines** the
> earlier contract wording (*"gross sales; returns never netted"*) — that phrasing is retired for
> trading. It matches the live sheet's `AB` formula `(net_sales_incVAT − tax − returns) / FX`, so
> month-over-month history stays comparable. Returns are still handled separately in the *returns*
> report. **Action:** update the glossary to this definition and never label the headline "gross."

- **Revenue basis = reproduce `AB` exactly:** `(Total Product Sales inc-VAT − Shopify Net Sales
  Tax − Returns) ÷ FX`, per line, excluding shipping lines; includes the zero-net edge branch.
  Net of discounts, net of in-window returns, ex-VAT.
- **VAT = subtract Shopify's per-line tax, NOT `/1.2`.** There is no `/1.2` in the real revenue
  path. **Retire the `UK_SALES_ARE_INC_VAT` toggle for trading.** (Caveat: this trusts Shopify's
  per-line tax config; a `/1.2` assumption would diverge on zero-/mixed-rate lines.)
- **FX must be made deterministic.** The sheet uses **live `GOOGLEFINANCE`** for US→GBP, so the
  same month reprints differently over time. Replace with a **frozen, dated GBP/USD table stored
  in the repo**, keyed by order date. This is the one deliberate deviation from the sheet.
- **Country is the reconciliation key**, not channel. `uk + us + row` must equal the headline
  total within **0.1%** (verified 0.0000% on 2026-04/05/06). D2C/B2B do **not** partition the
  total — never reconcile from the channel split.
- **ROW** is derived from **ship-to country** (GB→UK, US→US, else→ROW) with a **store fallback**
  (PH→UK, P US→US) when country is `N/A`. It reconciles by construction (one country per line).
- **Order+SKU is the only join key** (no stable Shopify line-item id surfaced) — the **same
  double-count trap as returns**: sum/de-dupe on order+SKU, or pull a real `line_item.id` via the
  Admin API / Matrixify.
- **"Weeks Cover" is really months** (`inventory ÷ monthly units`) — reproduce the value; keep the
  dashboard's ×52/12 correction; name it correctly in code.

### Returns — confirmed from the Q1 proof

**LOCKED decisions (Lena + Daisy, Aug 2026) — apply consistently across the whole dashboard:**
- **Single-count.** Each return is counted **once** via a de-duplicated `sku+order` join.
  The legacy per-line `SUMIF` stamping double-counted (~22% on Q1) and is retired. Historical
  returns restate down accordingly; Daisy has signed off on restating the history.
- **Order-month basis.** Every view buckets by **order month (the sale month)**, not the
  return month — a January order returned in March counts in **January**. This is consistent
  across sales, returns, and every table in the document. Chosen for assessing *product*, not
  topline cash. **This supersedes** the earlier return-month / sale-cohort-denominator /
  recency-flag decision.
  - *Maturity caveat (display):* order-month cohorts make the **most recent months look
    artificially low** on returns, because their returns haven't all happened yet. Flag
    recent, still-maturing months on the dashboard so they aren't read as improvements.
- **Orders-based return rate.** The headline rate is **returned orders ÷ orders**
  (distinct orders on both sides), *not* units returned ÷ units sold, everywhere. Units
  returned and returns cash remain as secondary detail.

- **Return source = Returns-zap, not Shopify.** Shopify only counts a return once the
  warehouse checks it in and therefore undercounts; Returns-zap counts it regardless.
  Headline uses the Returns-zap basis. (In the workbook this is `Shopify Data` col N,
  derived by composite-key `SUMIF` into the Returns-zap tab.)
- **Returns cash is notional:** `RRP-ex-VAT × units returned` — list value of returned
  units, *not* the actual refunded amount. Label it as such wherever it appears.
- **Orders vs returned orders use different constructions:** orders = order-line count
  from Shopify Data; returned orders = distinct-order count from the Orders pivot.
- **Returns join double-counts in the legacy sheet.** The composite-key `SUMIF` stamps the
  full return quantity onto every matching order line, inflating returns when a `sku+order`
  spans multiple lines (see §2, Layer 2). The builder must use a **de-duplicated join**;
  this is *more correct* but restates historical returns. Decision to socialise with George.
- **Labels are whitespace-sensitive** in the hand-built sheets — normalise on every key.
- **LQ / LY are hand-carried** in the workbook today; the builder must read them from
  committed prior-period outputs instead.

### Product reference
- **Line Detail is the canonical product reference and status model.** Category hierarchy
  is Product Type (department) › Product Category (item) › Sub Category (style). Kit /
  assembled SKUs stand alone (no rollup).

---

## 5. The reconciliation gate (runs on every build; aborts on failure)

- **Trading:** assert `uk + us + row == total` within 0.1% (relative); assert a ROW
  bucket is present; assert revenue reproduces the sheet's `AB` basis (net-of-discount,
  net-of-in-window-returns, ex-VAT) — **do NOT** assert returns are excluded, that was the old
  misreading; assert VAT was removed by **subtracting Shopify tax** (no `/1.2`); assert FX came
  from the **frozen dated table**, not a live source; assert pulled row counts tie to Shopify
  order totals (guards the fixed-height feed-truncation risk).
- **Returns:** assert Total == sum of the status/category block **for additive measures
  only (units, cash)**; order counts are **distinct** and are recomputed at each grouping —
  they legitimately do **not** sum to the Total (one order can span statuses/categories), so
  never assert additivity on them. Assert every label matches a normalised label in the data
  (catches the Electric Accessory whitespace bug); assert the return source is Returns-zap;
  assert the headline rate is **orders-based** (distinct returned orders ÷ distinct orders,
  distinct on both sides); assert every row is bucketed by **order month**.
- **All reports:** regression check against the committed oracle workbook for any period
  we already have, within tolerance.

A failed gate prints the offending figures and the gap, and writes **no output**.

---

## 6. Deliverables and phasing (dependency-ordered)

### Phase A — Returns dashboard *(nearest; aggregation proven, decisions locked)*
- [x] Prove aggregation/grouping reproduces from raw *(done — §2, Layer 1)*
- [x] Decisions locked with Daisy: single-count, order-month basis, orders-based rate *(§4)*
- [~] **Rebuild the returns join from the raw `Returns zap` feed** (Layer 2): de-duplicated
      `sku+order` join (single-count), **order-month** cohorting, **orders-based** headline
      rate with distinct-order counts on both sides, SKU-list scope automated, returned-order
      counts rebuilt without the `Orders pivot` cache. *Restatement on Q1 ≈ 22% fewer units;
      Daisy has signed off on restating the history.* — **in progress (`returns_builder_v2.py`)**
- [ ] Extend the builder to the full workbook: monthly tabs, `Quarter SKU Summary`, the
      return-reason breakdown, the UK/US split, and the by-finish table
- [ ] Wire the three feeds from connectors (Shopify/Matrixify orders, Returns-zap export,
      Line Detail) in place of pasted tabs
- [ ] Read LQ / LY from committed prior outputs instead of hand-carried columns
- [ ] Emit the values-only workbook, then generate `returns-review` HTML via the
      template-fill step (headline = orders-based rate; flag still-maturing recent months)
- [ ] Lock Q1 (and any other closed period) as a regression fixture

### Phase B — Monthly Trading builder (source = Matrixify; ROW + enrichment landed 2026-08)

⚠️ Reconciliation finding — supersedes the earlier "US ~91.3%" framing. Once revenue is bucketed by ship-to country (not by store-of-origin), the picture is not "US is short, UK nearly passes." Both markets are short by the same sign and order of magnitude — UK −5.16%, US −6.5% — and ROW is effectively a match (+0.69%). The old "+0.62% UK overshoot" was a wrong comparison (a store grand-total, not a country bucket) and has been corrected in RECONCILE_HANDOFF.md.

FX is ruled out as the driver: UK is native GBP with no FX in its path, yet it is short too.
Discount-netting is ruled out as the remedy: un-netted order discounts push computed higher, and subtracting them made UK worse. Wrong direction.
The cause is a common, upstream order-scope under-capture in both stores — the builder is missing value/orders the sheet has. The lead is cancelled/scope handling (excluding cancelled orders hurt US units, i.e. the sheet counts cancelled-order units the builder drops); this is the same 28-order July scope question in a different hat.
Cheap test first: the shortfall ≈ the by-value return rate (~6.58% Q1). Before per-order forensics, confirm whether the oracle's country figures are net or gross of returns (the columns are labelled "Gross Sales" but the pinned basis is net-of-returns/AB). If they're gross while the builder computes net, that alone explains a both-stores shortfall ≈ the return rate — and no forensics would ever close it. Settle via TRADING_logic_spec.md or oracle − May returns ≈ builder?.
 Investigate the trading Google Sheet — logic reverse-engineered; Path B confirmed (as before).
 SUPERSEDED: live Shopify Admin GraphQL as the trading source (kept for the pagination / date-boundary findings; not the source — as before).
 Matrixify migration (US). Frozen point-in-time export; matrixify_source.py + build_matrixify.py; refunds dedup on Line: ID; month-bucket via Created At→Europe/London.
 Matrixify-PlankUK connected. UK export now available; uk + us + row is checkable.
 #5 — ROW bucket + three-way country reconcile. compute_combined() unions UK+US lines and buckets by ship-to (GB→UK, US→US, else→ROW; store-fallback only on blank). Grand total computed independently of the country buckets so the leak check is real. common/reconciliation_gate.     assert_matches_oracle added; the gate aborts loudly (exit 1, no false PASS) on failure. ROW is first-class and near-exact (£14,556.43 vs £14,456.95, +0.69%); uk+us+row ties with residual 0. Frozen fixture + regression test lock the bucketing logic. Each bucket vs oracle is not yet within 0.1% — the gate correctly refuses (see finding above).
 #2 — Line Detail enrichment (commit 7263ad2). trading/line_detail.py parses the real workbook, de-dupes, validates the §4 status enum, derives is_live_uk/us, newness_bucket (as-of report-month end, not wall-clock), GM%, and left-joins without dropping a line. Live-only and EL are carried as flags, not row-drops. Coverage 98.37% of revenue (target ≥99%; unmatched go to an Unknown bucket, never dropped). Uses Line Detail's own resolved department field, not the SKU-code taxonomy (documented, not drift). EL-exclusion scope: Lena said ignore — flag carried, no logic.
[~] #3 — data-contract emission (spec'd: BRIEF #3; in progress). The contract is the shape of extract_all(); the Matrixify builder emits it, load_contract() replaces the oracle scrape, and compute.py/render.py/template are reused unchanged. Dual front-ends on one schema: emit_contract_from_oracle (correct now, for template dev) and emit_contract_from_matrixify (gated). Gate FAIL → contract written reconciled:false → PROVISIONAL banner + publish refused. LQ/LY read from committed prior contracts (oracle bootstrap until they accumulate).
 #4 — dashboard redesign (BRIEF #4 step 4, oracle-sourced contract). Recomposed the headline KPI row (yoy_growth_pct/b2b_share added; GM/ST/WC/inventory-feed dependency removed from trading entirely — st/wc/inv now stripped from the contract at emission, not just left unread). Category bars now show current + LY value with a direction-only colour cue (badge_class on the YoY movement, never the % itself) plus a subcategory drill toggle; category/finish "top SKUs" replaced by "top collections" + B2B share. Collection Performance and Collection Analysis merged into one bar-chart-with-click-to-drill view (top-10 SKUs per collection: cash/units × UK/US, % share, movement), everything responding to the same Cash/Units × UK/US/Total toggles, all permutations pre-baked server-side (no client-side math). MoM movers switched from collections to SKUs, top-10 rising/falling, Live-status only (matches across both the Line Detail raw enum and the oracle's coarse status bucket — see contract.py's LIVE_STATUS_VALUES). Rev × GM matrix gained a bubble-size legend. Retired the fixed "exactly 8 SKUs/finishes" row dicts (TYPE_ROWS/FINISH_ROWS) in favour of dynamically discovering every Product Type/Finish block in the sheet — this was hiding real data (Taps, a "Door" department, and 21 of 29 real finishes never rendered before). Period/comparator labels (MoM vs QoQ, LM vs LQ) are now driven by `mode`, not hardcoded, so quarterly (step 5) can reuse this template unchanged. EL-component exclusion stays parked (flag carried on every SKU, filter off) per Lena.
 Deferred: close the both-stores order-scope under-capture. Run the cancelled/scope diagnostic symmetrically across UK and US for the common excluded slice; resolve the 28-order July scope question the same way. Run the net-vs-gross return-basis check first (above). Only when a Matrixify-sourced contract reaches reconciled:true is it publishable.
 Add inventory feed (Supermetrics or Shopify get-inventory-levels) → real st/wc/inv (months_cover); until then the contract emits null → renders —. (GM already lands via #2.)
 Frozen FX: pick the dated GBP/USD series to commit (needed for deterministic re-runs; not the reconciliation blocker).
 Regress against a committed month before shipping; relabel the headline honestly (not "gross") and update the glossary (owner sign-off).
 Once May + July reconcile via Matrixify, remove the superseded live-query path.
§7 edits
Under "Still open", replace the ROW derivation/Channel lines' implication that trading is unverified with: "ROW / three-way reconcile → built (#5); ROW first-class, gate live. Buckets not yet within 0.1% — see Phase B finding (order-scope under-capture, both stores)."
Add to "Still open": "Return-basis (net vs gross): confirm the oracle country columns' returns treatment — cheap test that may explain the whole both-stores shortfall before any per-order forensics."
### Phase C — Quarterly Trading builder
- [ ] Roll the three monthly builds into the quarter (the returns model already shows the
      month→quarter `SUMIF` rollup pattern — reuse it)
- [ ] Quarterly dashboard via template-fill
- [ ] Gate + fixture as above

### Cross-cutting
- [ ] One colleague-facing runbook per report (trigger → eyeball → publish), seeded into
      the Claude Project instructions
- [ ] Publishing: keep GitHub Pages **behind Cloudflare Access** (Pages is public by URL
      on a free org regardless of repo privacy — this is the one piece of "complexity"
      that is load-bearing and stays). Monthly cadence means we can **publish manually**
      and drop the always-on CI.
- [ ] Commit every run so each report is reproducible and diffable.

---

## 7. Open confirmations

Most trading unknowns were **resolved by the Cowork sheet investigation** (`TRADING_logic_spec.md`):
- ~~UK VAT basis~~ → **resolved:** subtract Shopify per-line tax (no `/1.2`); toggle retired.
- ~~ROW derivation~~ → **resolved:** ship-to country (GB→UK/US→US/else→ROW), store fallback.
- ~~Channel~~ → **resolved:** B2B if company present else the B2B flag; does not partition total.
- ~~Cost + inventory~~ → **resolved:** Cost-for-Trade / Line-Detail cost; Supermetrics inventory
  (replaceable by Shopify `unit_cost` + `get-inventory-levels`).
- ~~Primary key~~ → **resolved (negatively):** no line-item id; join on order+SKU, de-dupe.
- ~~Sales value basis~~ → **resolved:** net of discounts and in-window returns (kept by decision).

Still open:
- **Line Detail source:** activate the Dropbox fetch path (`LINE_DETAIL_SOURCE = local | dropbox`)
  — was gated on credentials (~Mon 3 Aug).
- **Frozen FX source:** pick the authoritative dated GBP/USD series to commit (close to the sheet's
  historical `GOOGLEFINANCE` values so the regression stays in tolerance).
- **Glossary/label fix:** rename the trading headline from "gross sales" to an honest label and
  document the returns-netting — needs the glossary owner's sign-off.

---

## 8. Risks and how the design answers them

| Risk | Mitigation |
|---|---|
| A wrong headline gets published | Reconciliation gate aborts the run; nothing ships un-reconciled |
| Logic drifts from what colleagues trust | Workbook stays the spec + regression oracle; builder must match it |
| Feed layout changes silently | Schema asserts on each feed; the run stops and names what moved |
| LLM invents numbers | Numbers are computed by deterministic code, never free-generated |
| Colleague can't maintain code | Colleague only triggers/eyeballs via the Project; maintainer owns code |
| Published dashboard is world-readable | Cloudflare Access stays in front of GitHub Pages |

---

## 9. Roles

- **Maintainer** — owns the builders, the gate, the mappings, and the Project. Changes
  code only when a feed's layout changes or a definition is revised (and re-runs a fixture
  to confirm nothing broke).
- **Report generators (colleagues)** — run the report through the shared Claude Project,
  sanity-check the headline, publish. Never edit code or hand-patch output numbers.
