# Returns companion: SKU grain, and a headline that was blending Retail and Trade

_13 Aug 2026. Decision + correction. Triggered by: "we need to do this for the returns excel as
well" (i.e. the same By-SKU depth the trading companion got earlier the same day), then "build
with your recs"._

## The correction, which matters more than the feature

`build_returns_companion` built its Overview KPI block from
`by_market.loc["Total"]` — Retail **and** Trade blended. `run()`, which feeds the HTML dashboard,
headlines `by_month(s_retail, ret_retail)`. So for the same period the two deliverables reported
**different headline return rates**, and the Excel one was the side contradicting a locked
decision:

> Trade un-blended (§5.3, LOCKED): headline defaults to RETAIL; trade is computed and reported
> separately; the two are never combined into one blended rate/value.

Fixed: the companion now headlines Retail, matching the dashboard and §5.3. Trade and blended are
still computed and still reported — on the Reconciliation tab, both bases side by side with their
order counts, and an explicit line saying companions issued before today headlined the blended
figure in error. The size of the correction is visible on the face of the document rather than
being a silent restatement.

**Not reissuing prior returns companions** (Lena's call — flag to Daisy, don't reissue unless she
asks). Ken and Georgia have read the earlier files.

## The feature

`returns/sku_grain.py` — per-SKU metrics by segment and market, parallel to trading's
`common/sku_cuts.py` in vocabulary and deliberately *not* in metrics. Cuts: `retail`, `trade`,
`uk`, `us`, `row`, the four market × segment crosses, and `blended`. Metrics per cut: returns
cash, units returned, returned orders, orders, return rate, returns as % of sales.

Three rules the trading version doesn't need:

1. **Retail leads, Trade sits beside it, blended trails and is labelled.** The By-SKU tab's last
   block reads "Retail + Trade — transparency only, not the headline", on the same footing
   `run()` already gives `by_month_blended`.
2. **ROW returns columns are omitted, not zeroed.** `prep()` documents that ReturnZap's Country
   field holds only GB or US across all 54,831 raw rows — it cannot represent a ROW return. ROW
   *sales* are real (market has come from ship-to country since this morning). So a naive ROW cut
   would show real orders against zero returns and print a 0.0% return rate, reading as "ROW never
   returns anything". The ROW block is sales-only, banded "ROW (sales only)", with the reason in
   the tab note. `row_returns_recordable()` decides this **from the data**, so the columns appear
   on their own if the source ever gains ROW returns — no code change.
3. **The rate is withheld below the order floor, the counts never are.** The headline rate is
   orders-based (locked), which doesn't survive being cut thin — at SKU × market × segment grain
   most cells are single-digit order counts where one return reads as 25%. Rate suppressed below
   `build.MIN_TRACKER_ORDERS` (20, the floor the ranked tracker already uses — not a new
   threshold); `orders` and `returned_orders` are always written either side of it, and
   `below_floor` flags the row.

Exchange convention carried through unchanged per cut, not just at total: order and unit
aggregates **include** exchanges (a fit problem is a fit problem), cash aggregates **exclude**
exchange-attributable value (an exchange retains revenue).

Ranking is by **all** returns cash (Retail + Trade), not Retail alone: the question the tab
answers is "what is coming back", and a Trade-heavy SKU ranked to the bottom of a 700-row sheet is
a miss. The lock governs headline metrics, not sort order, and every metric on the row is still
segment-separated.

## Movement columns

Returns has **no committed contract**, so unlike trading there is no version bump, no back-fill,
and nothing to chain to — every figure is recomputed from source each run. A comparative therefore
means re-running `prep()` on the prior window against the same loaded frames, which only works if
the rolling snapshot covers it. `_prior_window()` in `build_dashboard.py` picks a like-for-like
window (prior month for a monthly build, prior quarter for a quarterly one, handling the year
boundary), and when that window has no orders in the loaded sources the columns are omitted with
the reason printed. It never shows a −100% drop off a period that was never loaded.

## Layout is computed, not hardcoded

`_by_sku_layout()` returns the block structure, and headers, formats, bands and values all derive
from it. The column set genuinely varies — the ROW block narrows, the movement block disappears —
so parallel hardcoded lists would drift. 69 columns in the ROW-omitted, prior-loaded case.

An un-updated caller that passes only `sku_agg` still gets the original 8-column sheet rather than
an error.

## Verification

`returns/tests/test_sku_grain.py` — 8 test functions, all passing: Retail/Trade separation, ROW
omitted when unrecordable *and* appearing when the source has it, the rate floor (including that
it's a parameter), the exchange convention, uplift's five None cases, the tab writing with the
ROW band checked positionally for absent returns columns, and the narrow fallback. Both existing
returns regression tests skip on missing maintainer-local sources, unchanged.

Sign convention worth recording, since it bit the test fixture: `refund_val` is
`-sum(Line: Total)` over Refund Line rows whose Total is already negative, so returns value
arrives **positive**. `build._sku_return_value` applies `.abs()` defensively; the companion's
`_sku_aggregate` does not and relies on this.
