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

### Phase B — Monthly Trading builder *(sheet reverse-engineered; ready to build — see `TRADING_logic_spec.md`)*
- [x] Investigate the trading Google Sheet (Cowork, 3 Aug) — logic fully reverse-engineered;
      **Path B confirmed** (port to code; retire Supermetrics; sheet stays as spec + oracle)
- [ ] Build the revenue engine from **ShopifyQL** (`run-analytics-query`), reproducing `AB`:
      `(net_sales_incVAT − tax − returns)/FX`, exclude shipping lines, include the zero-net branch
- [ ] Country UK/US/ROW from ship-to (store fallback); channel D2C/B2B from company/flag
- [ ] Add GM (Shopify variant `unit_cost`, Line Detail fallback) and inventory (on-hand, excl.
      flagged group); reproduce sell-through and months-cover
- [ ] Recompute vs-LM / vs-LY live from shifted-window pulls (not hand-carried)
- [ ] **Freeze FX**: committed dated GBP/USD table keyed by order date (the one deviation)
- [ ] De-dupe on **order+SKU** (no line-item id) — same trap as returns — or pull `line_item.id`
      via Admin API / Matrixify
- [ ] Emit the values-only Monthly Trading workbook (Monthly Summary / By Collection / By SKU),
      feed the existing `trading/dashboard/` template-fill step
- [ ] Gate: `uk + us + row` within 0.1%, ROW present, VAT-by-tax, frozen FX, row-count tie
- [ ] Regress against a committed month (Apr/May/Jun 2026) before shipping; **relabel the
      headline honestly** (not "gross") and update the glossary

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
