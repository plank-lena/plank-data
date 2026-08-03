# Trading reconciliation handoff (resume here)

**Updated:** 2026-08-03 (late evening) — supersedes the "Code session handoff — trading
reconciliation" note from earlier the same day (that note was pasted into conversation, not
committed as a file; this is now the durable copy). Governing docs unchanged: `ROADMAP.md`,
`trading_logic_spec.md`.

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
- [ ] UK May reconciles within 0.1% (currently +0.62%).
- [ ] Units gap closed on both legs; `uk + us + row == total` within 0.1%, ROW present.
- [ ] US value residual explained (FX and/or tax) and within tolerance, or flagged approximate.
- [ ] July components reconcile; 28-order scope rule resolved and documented (likely same rule
      as May's cancelled-order question above).
- [ ] ShopifyQL trading path removed; frozen monthly FX in use for US.

## Commands to reproduce this session's numbers

```
python trading/build_matrixify.py trading/source/orders_2026-05_UK.csv uk 2026-05
python trading/build_matrixify.py trading/source/orders_2026-05_US.csv us 2026-05
```
