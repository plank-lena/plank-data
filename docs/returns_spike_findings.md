# Returns spike — findings & gap list (Q1 Jan–Feb–Mar 2026)

**Purpose.** Before committing to the Path-2 (hybrid) build, prove that a deterministic
code builder can reproduce the hand-built returns `Summary` from raw feeds — and, just as
important, surface every point where the raw data does *not* fully determine the published
number. This is the gap list that becomes the spine of the trading-builder spec.

Two scripts were run against `Q1_Jan_Feb_Mar_2026.xlsx`:

- `returns_summary_poc.py` — aggregates the workbook's own columns and checks the grouping.
- `returns_summary_builder.py` — recomputes the returns join from the **raw `Returns zap`**
  feed, i.e. what a connector-fed production run must actually do.

---

## Result in one line

The **sales/revenue engine and all grouping logic reproduce exactly**; the **returns join
does not reconcile when rebuilt from the raw feed**, because the hand-built method
double-counts and depends on a manually curated SKU list. The approach is sound; the
returns join is the one real open problem, and it implies a restatement of historical
returns numbers.

---

## Layer 1 — aggregation & grouping: PROVEN (exact)

Rebuilding per-SKU aggregates from the rawest Shopify columns, then grouping by status and
by category, matches the workbook to **0.000%** on every status row and the Total, and on
every category row, for all six metrics (cash, units sold, units returned, orders, returned
orders, returns cash).

One data-quality catch fell out of it: the category **Electric Accessory** shows **£0** in
the workbook because its `SUMIF` criteria label carries a **trailing space**
(`"Electric Accessory "`) that fails to match the data. The builder normalises whitespace
and correctly recovers **£169.90**. The from-code build was *more correct than the sheet*
— exactly the behaviour we want from the gate.

> Caveat on Layer 1: the POC took **units returned from the workbook's helper column N**
> (the sheet's own returns-join output), not from the raw `Returns zap` tab. So Layer 1
> proves the aggregation/enrichment/grouping — not the join. The join is Layer 2.

---

## Layer 2 — the returns join from raw: NOT yet reconciled

Recomputing units returned from the raw `Returns zap` feed (group by `sku + order_id`,
attribute to the sale line's month/market) does **not** land on the workbook's figure.

| Method (quarter, Units Returned) | Value |
|---|---|
| Corrected — each `sku+order` return counted once (recommended) | **≈ 4,317** |
| Workbook `Summary` (published) | **5,259** |
| Sum of the sheet's helper column N over the quarter | 5,620 |
| Faithful replication of the sheet's per-line stamping | ≈ 6,021 |

Sales-side metrics (cash, units sold, orders, UK/US split) match **exactly** in both
scripts; only the returns metrics diverge.

### Why it diverges — the mechanics

1. **Per-line double-count.** The sheet joins returns to orders on a composite
   `sku&order_id` key via `SUMIF`, which stamps the *full* return quantity onto **every**
   matching order line. When a `sku+order` spans more than one line (**672 keys** in Q1),
   the return is counted multiple times. This inflates the sheet.
2. **Curated SKU-list scope.** The `Summary` only sums SKUs on a **hand-copied 745-SKU
   list** (per the workbook's `Manual` tab: *"copy list of SKUs on main tab"*). Returns on
   SKUs outside that list are silently excluded (~361 units), which pulls the sheet back
   down. The published 5,259 is the net of these two opposing artifacts — i.e. it is partly
   a product of spreadsheet mechanics, not a clean quantity.
3. **~7% unmatched.** About 7% of return rows don't match a quarter sales line (order-date
   boundary cases, a handful of SKU-string mismatches). The sheet's composite key drops
   these too, but not identically to a clean join.

### Confirmations that fell out

- **Shopify-native returns columns are unusable.** The native `returned_item_quantity`
  summed to **−6,798** over the quarter. This validates the `Manual` rule to **rely on the
  Returns-zap basis**, never Shopify's own returns fields.
- **Returns cash is notional:** `RRP-ex-VAT × units returned` (list value of returned
  units), not the actual refunded amount. The raw `Returns zap` feed *does* carry a
  `Full Price` column (T) that could give a truer figure if wanted.

---

## Gap list — decisions the raw data does not make for us

Each of these must be pinned in code with a test, and several need a human ruling
(flag to George where noted).

1. **Returns join method — replicate vs correct.** Recommend the **corrected de-duplicated
   join**, not the sheet's per-line stamping. Consequence: historical returns restate down
   by ~15–20%. **→ Confirm with George before publishing**, and note it in the dashboard.
2. **Composite key hygiene.** The sheet's key is separator-less (`sku&order_id`) and
   whitespace-sensitive. Use an explicit delimiter and `strip()` every key/label.
3. **SKU-list scope.** Decide whether the builder includes *all* SKUs with activity in the
   period (recommended, deterministic) or mirrors a maintained list. The hand-copied list is
   a manual step and an error source; automating it removes both.
4. **Return → period attribution.** Returns are currently bucketed to the **sale month** via
   the matched order line (sale-cohort). Reconcile this against the returns-dashboard
   decision on record (return-month view + sale-cohort denominator + recency flag).
5. **Returns cash basis.** Keep the notional `RRP × units` figure *or* switch to actual
   refund value from `Returns zap` `Full Price`. Label clearly either way.
6. **Returned-orders source.** The sheet reads returned-order counts from an `Orders pivot`
   cache. That pivot won't exist in a connector run — the builder must rebuild the
   distinct-returned-order count directly from the raw feed.
7. **LQ / LY comparisons.** Hand-carried in the workbook today. Read them from committed
   prior-period outputs instead of re-keying.
8. **Swatch multipack judgement.** The `Manual` tab has a genuine human step (*"put black &
   stainless swatches on it to make into multipack"*). This is judgement, not computation —
   surface it as a prompt in the runbook, don't try to mechanise it.

---

## Verdict

- **Proven:** the sales/revenue engine and all aggregation/grouping reproduce exactly from
  raw; the gate catches latent sheet errors (Electric Accessory). Green light for the
  trading builders, whose core is exactly this revenue engine.
- **Open:** the returns join must be rebuilt correctly from the raw `Returns zap` feed
  (items 1–3, 6 above) and will restate historical returns. This is the first task of
  Phase A, and a decision to socialise — not a bug to match.
