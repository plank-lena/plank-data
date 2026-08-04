# Trading reconciliation handoff (resume here)

**Updated:** 2026-08-05 — reconciles the 2026-08-04 flip below against `trading_logic_spec.md`
(see the new section immediately below; supersedes only the *conclusion* of 2026-08-04's section,
not its underlying numbers, which are still accurate and now reinterpreted). Governing docs:
`ROADMAP.md` (§5's revenue definition was flipped 2026-08-04 and REVERTED 2026-08-05 — see there),
`trading_logic_spec.md` (primary source for the live sheet's actual `AB` formula).

---

## ✔ Returns-basis reconciliation (2026-08-05) — the 2026-08-04 flip was wrong; same-day revert

**The contradiction:** 2026-08-04's diagnostic (below) concluded returns are never netted, based on
column labels ("Gross Sales") plus an arithmetic fit (UK ≈ oracle at gross − order-discounts, no
returns subtracted). `trading_logic_spec.md` — written by reading the **live sheet's actual
formulas**, not inferring from labels — states as a locked decision that the headline **is** net of
returns at line level, citing a real "Returns (inc VAT)" column subtracted in a real formula. That's
stronger provenance; it wins. `ROADMAP.md` §5 has been reverted accordingly.

**The reconciliation (both pieces of evidence are true at once):** `trading_logic_spec.md` also
notes Supermetrics "adjusts returns to the week the order was placed" — an **order-cohort** basis.
A given month's Returns column holds only returns *of orders placed that month*; at the time the
report is pulled, only a handful of that month's own orders have had time to come back. So the
column is genuinely netted, it's just **small** most months. The builder, by contrast, subtracts the
full **processing-window** refund-line total from the Matrixify export (≈£26K UK / ≈£23K US in
May) — refunds of *any* sale, processed in that window, regardless of when the sale happened. That
mismatch — wrong returns basis, not "netted vs. not" — is the believed actual bug:
- **UK:** true cohort-returns ≈ £0–500 against a ≈£250K headline — negligible, which is exactly why
  2026-08-04's "no returns at all" computation fit UK so well. It looked like confirmation of "not
  netted"; it was really "netted, but the true figure for this basis is tiny."
- **US:** true cohort-returns theorised ≈ £3.1K for May — plausibly *is* the +1.45%/-ish residual
  that 2026-08-04's `B` (gross minus order-discounts, no returns) left on the table. The blanket
  cancelled-order exclusion (`C`) never fit this because cancelled orders were never the mechanism.

**Confirmed, unaffected by which way the returns question resolves:**
- UK value = gross ex-VAT minus order-level discount codes, ±0.2% across all three months — this
  finding stands regardless, since UK's true cohort-returns term is negligible either way.
- Cancelled orders are included in the oracle, not excluded — the blanket-exclusion test made both
  legs worse across all three months. The 28-order scope question is not a cancelled-order problem.

**Decisive test — now genuinely primary-source, not more Matrixify arithmetic (I cannot run this
myself; no live-sheet access from this session):** read the live sheet's actual "Returns (inc VAT)"
values, per month per country, and compare to (a) the export's processing-window refund-line totals
(≈£26K UK / ≈£23K US) and (b) the theorised cohort residuals (≈£0–500 UK / ≈£3.1K US). Small and
close to (b) → confirmed, fix is "subtract the sheet's cohort-returns figure instead of raw refund
lines," not "stop netting returns." Large and close to (a) → this reconciliation is wrong in a
different way; re-open from scratch rather than re-guessing.

**Units — separate, still open, treat the builder as correct.** Unaffected by the returns-basis
question. See 2026-08-04's section below for the numbers; the same "read the live sheet's formula,
don't guess from Matrixify arithmetic" approach applies once the value question is settled.

**Do not ship any of this to `revenue.py` yet** — the returns-basis confirmation above is still
pending a primary-source answer.

---

## ✔ Returns-netting basis diagnostic (2026-08-04) — SUPERSEDED

**See the 2026-08-05 section above for the correct interpretation of these same numbers** — the
diagnostic data below (the A/B/C table, the units table) is still accurate and still the source for
the 2026-08-05 reconciliation; only its "returns are never netted" conclusion was wrong.

**Trigger:** the UK −5.16% / US −6.47% shortfall below had been sidelined as "order-scope
under-capture." New evidence (the oracle's monthly workbooks label every measure "Gross Sales £" /
"Gross Unit Sold", with no returns/net concept anywhere) suggested a cheaper hypothesis: the
builder's AB formula nets returns out of a target that was never net of returns in the first place.

**Step 1 — floor isolation (May only):** computed GROSS (`(net_of_discount − tax) / fx`, returns
term dropped entirely) alongside NET (today's shipped AB) against the May oracle. Neither matched
cleanly — GROSS *overshot* the oracle by about the same margin NET undershot it (UK: NET −5.16%,
GROSS +5.31%; US: NET −6.58%, GROSS +4.26%), i.e. the oracle sits roughly *between* the two, not at
either end. This falsified the naive "oracle = GROSS" hypothesis, but the shape (a large, roughly
±5% GROSS/NET straddle) pointed at a second component sitting alongside the returns question, not
against it.

**Step 2 — recomposition diagnostic (April+May+June, all three months, `trading/build_matrixify.py
recomposition`):** three revenue variants per bucket per month, vs. each month's own oracle:
- **A = GROSS** (line-discounts applied via `Line: Total`, no order-level discounts, no returns).
- **B = A − order-level `Discount` line-type rows** (standalone discount-code rows, currently
  dropped entirely by `build_lines()` — never allocated into any line).
- **C = B − orders carrying a non-blank `Cancelled At`** (blanket exclusion, the same "29-order
  scope" question flagged for July below).

Result (gap vs. oracle; UK cancelled-order counts were 18–30/month, not negligible):

| month | B (UK) | C (US) | C (UK) |
|---|---|---|---|
| 2026-04 | +0.087% | −2.149% | −0.965% |
| 2026-05 | −0.201% | −1.154% | −1.379% |
| 2026-06 | +0.116% | −3.153% | −1.135% |

**Confirmed, non-coincidentally (3 independent months, not fit-by-luck):** for **UK**, `B` (gross,
ex-VAT, order-level discounts netted, returns never touched) reproduces the oracle within ±0.2%
every month. Since this holds *without* subtracting returns at all, and holds this tightly across
three separate months, the returns-never-netted hypothesis is confirmed for UK — if returns still
needed netting, this same computation would be short by the return rate (~10%), and it isn't.
**Revenue definition flipped in `ROADMAP.md` §5 as a direct result — then REVERTED 2026-08-05,
see above: the "returns never netted" reading was wrong, the real explanation is the order-cohort
basis.**

**NOT confirmed — stop here, this part is genuinely murkier:** the blanket cancelled-order
exclusion (`C`) does not resolve US (worsens the US gap in all three months vs. `B`) and, applied
to UK, pushes UK *negative* in all three months despite UK having its own 18–30 cancelled
orders/month — meaning cancelled orders are not simply excludable order-wide, and the US residual's
real driver is still unknown (likely needs `Payment: Status` combined with `Cancelled At`, per the
"mixed/inconclusive" cancelled-order finding from 2026-08-03 below — this new evidence sharpens
that finding, it doesn't resolve it). **Do not apply the discount-only fix to `revenue.py`'s
`line_ab` yet** — a partial fix would silently restate UK's committed history while leaving US
wrong; both sides need a confirmed component before `line_ab` changes.

**Units — separate thread, also not fully resolved.** Same diagnostic independently recomputed
`Σ Line: Quantity` over `Line Item` rows only, from scratch, per bucket per month: it exactly
matches the shipped builder's own unit count every time — the builder is not undercounting its own
source data. A "the oracle double-counts a returned unit (once at sale, once at return)" hypothesis
was tested by comparing `oracle − builder` against `Σ|Refund Line quantity|`: order-of-magnitude
plausible for UK/US (ratios ~0.6–1.5×, not a tight match) but fails outright for ROW (gap is
30–50× the actual returned-unit count some months). **Do not pad the builder's units to chase the
oracle's Gross Unit Sold** — treat the oracle's unit figure as the suspect one (it already doesn't
foot internally: UK+US+ROW vs. its own Total column disagrees by several hundred units, with the
sign flipping month to month — April oversums by 850, May undersums by 822, June undersums by 955).

**Tooling added (inert, report-only, does not change any shipped behaviour):**
`compute_combined()` in `build_matrixify.py` gained an `include_returns` flag (default `True` —
unchanged behaviour); `floor_isolation_test_matrixify()` / `floor_isolation` CLI action reproduces
step 1; `recomposition_diagnostic()` + `run_recomposition_diagnostic()` / `recomposition` CLI
action reproduces step 2 and the cross-month table above; `_read_oracle_row7()` reads any
committed monthly oracle's row-7 ground truth directly (generalises the old hardcoded
`MAY_THREE_WAY`). None of these are called from the gate path.

**Next steps, in order (superseded by 2026-08-05 above — kept for history):** ~~(1) find the real
US-side component...; (2) update `line_ab` to drop the returns term...~~ — see the 2026-08-05
section's own next steps instead: get the live sheet's actual Returns figure first, then fix
`line_ab` to use the sheet's returns basis (not drop it), plus the confirmed discount term.
(3) still stands: separately decide whether the oracle's Gross Unit Sold is worth chasing at all
given it doesn't even foot against itself.

---

## ⚠ Correction (BRIEF #5, commit `70362cd`, same day) — the "UK +0.62%" figure below is wrong

Section 4 below reports "UK May total: computed £249,296.19 vs £247,772.02, **+0.62%**." That
number is a **store grand total** (the UK-store export's own UK+US+ROW buckets summed together),
compared against the oracle's **UK-only** country bucket — an apples-to-oranges comparison that
happened to land close by coincidence. BRIEF #5 added a proper three-way reconcile
(`compute_combined()` in `trading/build_matrixify.py`, unions both stores and buckets by ship-to
country) and the **real** UK ship-to bucket is **£234,981.78 vs £247,772.02 — a −5.16% gap**, the
same order of magnitude as US's known shortfall, not a near-miss. ROW itself is close and clean
(£14,556.43 vs £14,456.95, 0.688%, and `uk+us+row` ties to an independently-computed grand total
exactly, residual 0). **Section 39's "Hypotheses tested" (discount-row, cancelled-orders) were
tested against the wrong UK number too** — they may still be directionally right (both were
US-anchored anyway) but re-run them against £234,981.78 before trusting the exact percentages
quoted there. Everything else in this doc (the refund-export fix, the US figures, the recommended
next steps) is unaffected by this correction.

---

## What changed this session (the delta)

1. **Root cause confirmed and fixed:** both committed May 2026 source CSVs
   (`trading/source/orders_2026-05_UK.csv`, `orders_2026-05_US.csv`) had **zero** `Refund
   Line`/`Refund Shipping`/`Transaction` rows. The export recipe only pulled
   `base+customers+line_type+line_items` — Matrixify gates refunds/transactions behind separate
   groups. This was a **silent regression on the US file specifically**: commit `2ed0e7e` had a
   correct US export (with order `#US29002`'s 3 `Refund Line` rows, cited in
   `matrixify_source.py`'s docstring); commit `ed1a665` then re-exported and silently overwrote
   it with a version missing that data — a violation of this repo's own "never overwrite a
   committed month's export" rule, done without noting it.
2. **Both stores re-exported** via Matrixify with `refunds`+`transactions` groups added (same
   filter: `created_at` date range 2026-04-30→2026-06-02, CSV, one-off). Verified before
   committing: UK now has 889 `Refund Line` + 20 `Refund Shipping` rows; US has 261 `Refund Line`
   + 13 `Refund Shipping`; `#US29002` shows exactly the 3 refund rows the docstring describes.
   Scoping re-checked (London-month bucketing clean, buffer edges only catch late-Apr/early-Jun).
3. **Committed** as `162e8c8`, replacing the two source files in place (same filenames — this is
   the one deliberate exception to "each month gets its own file," since it's correcting a
   regression, not adding a new snapshot).
4. **Reconciliation improved substantially but is not yet within the 0.1% gate:**

   | | computed | expected (sheet) | gap |
   |---|---|---|---|
   | UK May total | £249,296.19 | £247,772.02 | **+0.62%** (was +11.2% before the fix) |
   | US May total | £200,222.76 | £214,063.73 | **−6.47%** (was ~−8.7%) |
   | US May units | 9,143 | 9,436 | **−293 (−3.1%)** (was −4.9%) |

---

## Hypotheses tested this session (with results — don't re-test these)

### UK value residual (+0.62%, +£1,524.17)

- **Ruled out: un-netted order-level discount codes.** Matrixify's standalone `Discount`
  line-type rows (order-level codes, e.g. `#1782149564`'s −£35.82 on a 10%-off code) are **not**
  currently allocated back into `net_of_discount` by `build_lines()`/`line_ab()`. Confirmed
  `Line: Total` for ordinary `Line Item` rows already nets *per-line* `Line: Discount`
  (`Total == Price×Qty + Discount`, verified on 8 sampled UK lines) — so only the order-level
  *code* discount is unaccounted for, not per-line discounts.
  **Tested the fix:** subtracting the full May-bucketed `Discount`-row total (−£14,028.54 for UK)
  from the grand total swings UK to **£235,267.65, a −5.05% gap — far worse than the current
  +0.62%.** This means the sheet's own Supermetrics-fed `N` ("Discounts") column, per
  `trading_logic_spec.md` §2, most likely **also doesn't capture order-level discount codes**
  (Supermetrics appears to pull only line-level discount allocations, the same thing Matrixify's
  `Line: Discount` already gives us) — so the current un-netted behaviour is probably *already
  correct* and matches the sheet's own methodology. **Do not "fix" this without sheet-side
  evidence to the contrary** — it would make the reconciliation worse, not better.
- **Not yet tested:** any other source of the remaining +0.62% / £1,524. At this size (£0.74/order
  average across 2,054 orders) it doesn't look like a single-order data error; more likely a small
  systematic edge case (zero-net branch, a specific line-type miscount, or a genuine small
  divergence between the sheet and Shopify's own current-state data — the sheet was built at some
  point in time and Shopify data can drift after the fact, e.g. late edits to an order).

### US units gap (−293, −3.1%) and US value gap (−6.47%)

- **Ruled out:** duplicate `Line: ID` rows (0 found), gift-card lines (0 in May), non-shipping-
  required lines (0 in May), missing `Line: ID` (0). None of these explain the shortfall.
- **Cancelled orders — mixed/inconclusive signal, do not treat as a clean fix:**
  29 orders in US May (283 units) and 18 orders in UK May carry a `Cancelled At` value and are
  currently **included** in both revenue and units (the builder doesn't filter on it at all).
  Tested **excluding** them entirely:
  - UK value: gap **widens** (+0.62% → +1.05%) — excluding cancelled orders makes UK worse.
  - US value: gap **narrows** (−6.47% → −3.22%) — excluding cancelled orders makes US better.
  - US units: gap **widens** (−293 → −576) — excluding cancelled orders makes units worse.

  This is a genuinely mixed signal — cancelled orders help US value but hurt US units and hurt UK
  value. A blanket include/exclude toggle can't satisfy all three at once, which means either (a)
  cancelled orders need different treatment for revenue vs. units, (b) `Cancelled At` alone is too
  blunt a signal and needs to be combined with `Payment: Status` (e.g. only exclude if *also*
  voided/unpaid, not if refunded), or (c) the true fix is unrelated to cancellation and this
  29-order/283-unit coincidence with the gap size (293) is exactly that — a coincidence.
  **This is very likely the same "28-order scope" question the original handoff flagged for
  July** (29 ≈ 28) — worth resolving both months together with the same rule, once found.

---

## Recommended next steps (in order)

1. **Get sheet-side ground truth for a handful of specific orders**, not just totals. The value
   residuals are now small enough (0.6%–6.5%, down from double digits) that further progress by
   guessing at the aggregate level has diminishing returns — pulling 5-10 cancelled/refunded May
   US orders' `Payment: Status` + `Cancelled At` and comparing directly against how the hand-built
   sheet treats those *same* order numbers would likely resolve the units/cancellation question
   definitively, rather than more hypothesis-and-recompute cycles against the total alone.
2. Once the cancellation rule is confirmed, re-measure **UK first** (it's the FX=1 clean leg) —
   if UK lands within 0.1% with the confirmed rule, apply the same rule to US and re-measure the
   value residual before touching FX/tax at all (per the original handoff's ordering: don't chase
   the value residual while units are still short).
3. **US FX/tax residual** (still open, untouched this session): verify the May US FX rate
   actually used (`common/fx.py`'s `ensure_month('2026-05')` — this session's run used `1.3509`)
   against the sheet's own basis; the original handoff notes May's `GOOGLEFINANCE` rate may be
   unrecoverable (only July's 1.3250 is confirmed), so US-May value may be inherently
   approximate — UK remains the real fixture for the gate.
4. **July**: export both stores with the same (now-corrected) `refunds+transactions` recipe,
   reconcile at component level, and resolve the 28-order scope question — very likely the same
   rule as #1 above, so solving May's version first should directly answer July's.
5. Only after both months reconcile within 0.1%: retire the ShopifyQL live-query path
   (`trading/shopify_feed.py`, `trading/build.py`'s live branch, `trading/order_scope_diff.py`).

---

## Definition of done (unchanged from the original handoff)

- [x] UK connected; export recipe fixed (refunds/transactions groups added).
- [x] ROW bucketed as a first-class three-way component (BRIEF #5, `70362cd`) — ROW is close
      (0.688%) and `uk+us+row` ties to grand total exactly (residual 0).
- [ ] UK May reconciles within 0.1% (currently **−5.16%**, corrected — see the callout above;
      was misreported as +0.62% before the ship-to three-way reconcile existed).
- [ ] Units gap closed on both legs; `uk + us + row == total` within 0.1%, ROW present (structural
      tie already holds; oracle-value parity does not yet).
- [ ] US value residual explained (FX and/or tax) and within tolerance, or flagged approximate.
- [ ] July components reconcile; 28-order scope question resolved and documented — **ruled out
      2026-08-05:** not a cancelled-order-exclusion rule (tested, made both legs worse); likely the
      same order-cohort returns-basis question now pending sheet-owner confirmation instead.
- [ ] ShopifyQL trading path removed; frozen monthly FX in use for US.

## Commands to reproduce this session's numbers

```
python trading/build_matrixify.py trading/source/orders_2026-05_UK.csv uk 2026-05
python trading/build_matrixify.py trading/source/orders_2026-05_US.csv us 2026-05
```
