"""Monthly Trading builder -- revenue + country/channel engine.

Sources order-lines directly from the Shopify Admin GraphQL API (see
shopify_feed.py) -- not ShopifyQL, not Matrixify, not Supermetrics. Real
orders give a stable line_item.id, so the order+SKU double-count trap
TRADING_logic_spec.md warns about (Q7, gap #4) doesn't apply here: every
line pulled is already atomic and uniquely identified.

STATUS (2026-08-03): revenue engine built; NOT yet passing the reconciliation
gate at 0.1%. `fetch()` hits Shopify once per month; `compute()` is a pure
aggregation over the cached raw lines, so diagnostics (e.g. the returns-term
floor-isolation test) don't need a second network pull. See report_may(),
report_july(), and floor_isolation_test() for the three checks currently
being used to close the gap -- ROADMAP.md Phase B has the full writeup.

Run:  python trading/build.py may | july | floor
"""
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shopify_feed import STORES, fetch_store_orders
from revenue import country_bucket, channel, line_ab, refund_events_by_line_id, sum_refund_events

from common.fx import ensure_month, seed_confirmed, lookup_month
from common.reconciliation_gate import assert_country_reconciles

# July 2026 FX: read directly off the live sheet (2026-08-03), seeded rather
# than fetched -- more authoritative than an independent source for a month
# we've actually checked against the sheet itself.
JULY_FX_RATE = 1.3250
JULY_FX_SOURCE = "googlefinance (sheet AA, month rate)"

# July 2026 component ground truth, read directly off the live sheet
# (2026-08-03). No committed July fixture .xlsx exists yet -- these are the
# regression oracle for July until one is added.
JULY_COMPONENTS = {
    "ab_product_sales_ex_vat": 534551.39,
    "ab_incl_shipping": 547452.53,
    "net_sales_tax": 56170.41,
    "returns_inc_vat": -14967.46,
    "shipping_charges": 12901.14,
    "units": 29141,
    "orders": 3355,
    "order_lines": 7965,  # "~7,965" -- reported as a wide-tolerance sanity count, not gated
}

# May 2026 totals-only ground truth -- matches trading/tests/fixtures/
# 2026-05_Monthly_Trading_Report.xlsx cell-by-cell (Monthly Summary F7/AT7/
# CD7/DN7/J7/AX7/CH7/DR7/V7/AH7), restated here so these checks don't need a
# bespoke xlsx reader for every cell.
MAY_TOTALS = {
    "total": 476292.70, "uk": 247772.02, "us": 214063.73, "row": 14456.95,
    "units_total": 27453, "units_uk": 16373, "units_us": 9436, "units_row": 822,
    "d2c": 331370.37, "b2b": 144922.33,
}

# June 2026 totals-only ground truth -- read directly from the committed
# fixture trading/tests/fixtures/2026-06_Monthly_Trading_Report.xlsx
# (Monthly Summary F7/AT7/CD7/DN7/J7/AX7/CH7/DR7/V7/AH7). Used instead of
# July for exact reconciliation (2026-08-03): July is mid-warehouse-move and
# its live order data is still churning (confirmed: two pulls of the same
# query minutes apart differed by ~5% of orders) -- June should be settled.
JUNE_TOTALS = {
    "total": 504721.28, "uk": 237366.24, "us": 251633.05, "row": 15721.99,
    "units_total": 29322, "units_uk": 16342, "units_us": 11070, "units_row": 955,
    "d2c": 323526.46, "b2b": 181194.83,
}

TOL = 0.001  # 0.1% relative, per ROADMAP.md


def _month_bounds(month_str):
    """'2026-05' -> (start, end) ISO date strings, end exclusive."""
    year, month = (int(x) for x in month_str.split("-"))
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    return start, end


def _month_query(month_str):
    """'2026-05' -> Shopify search query for that calendar month.

    Order scope (2026-08-03, revised): the ONLY sheet-confirmed exclusion is
    Shopify Draft Orders (sourceName == "shopify_draft_order" -- note the
    exact literal; "draft_order" alone does not match). An earlier attempt
    at "source_name:web" was WRONG: it also excluded wholesale/app-channel
    orders (e.g. "VS_Patchworks") and Shopify Exchange orders ("-EX1" name
    suffix) -- a set-diff against the sheet's actual 3,355 July order names
    showed the sheet DOES count those, they just showed up as
    sheet-not-pipeline misses once source_name:web dropped them. test:true
    orders = 0 in this store (nothing to exclude there); status is included
    by default (status:any and the unfiltered query return the same count),
    matching "include cancelled."

    NOTE (2026-08-03): Shopify's search-index date filter (created_at:<...)
    is NOT reliable on its own -- confirmed directly that an order created
    2026-08-01T05:38:23Z still matched created_at:<2026-08-01T00:00:00Z
    (explicit UTC timestamp, not just the bare-date form). fetch() below
    re-filters every order client-side against the real createdAt field;
    never trust this query's date bound alone.

    NOTE (2026-08-03): the negated search term "-source_name:X" is ALSO
    unreliable -- confirmed it excludes ~113 orders when only ~2 literal
    "shopify_draft_order" orders exist, so it's doing something other than
    an exact-match negation. Draft-order exclusion is applied client-side
    in fetch() instead, alongside the date re-filter; this query carries NO
    source_name clause at all.
    """
    start, end = _month_bounds(month_str)
    return f"created_at:>={start} created_at:<{end}"


def fetch(month_str):
    """Network phase: pull every non-test order+line for both stores/month,
    with the FX-independent raw fields extracted but AB NOT yet computed
    (compute() does that, cheaply, from this cached result).

    Returns a dict: {lines: [...], order_shipping: [...], row_count_report: {...}}
    lines: {sku, country, channel, units, net_of_discount, tax, refund_events,
            fx_rate, unit_cost} -- refund_events is the RAW per-refund list
      (see revenue.refund_events_by_line_id), not pre-summed, so compute()
      can apply different return-cutoff-date hypotheses without a re-pull.
    order_shipping: {country, shipping_ex_vat} -- one entry per order (shipping
      is order-level, not per-line; must not be double counted across a
      multi-line order).
    orders_meta: {country, is_fully_refunded} -- one entry per order, used to
      count orders per leg and test the fully-refunded-orders hypothesis
      (2026-08-03) WITHOUT touching revenue (which stays keyed off lines/AB).
    """
    if month_str == "2026-07":
        fx_rows = seed_confirmed(month_str, JULY_FX_RATE, JULY_FX_SOURCE)
    else:
        fx_rows = ensure_month(month_str)

    start, end = _month_bounds(month_str)
    date_query = _month_query(month_str)
    lines = []
    order_shipping = []
    orders_meta = []
    row_count_report = {}

    for store in STORES:
        raw_orders, expected_count = fetch_store_orders(store, date_query)
        # Gate on pagination integrity (both sides use the identical remote
        # query, so this catches a dropped page regardless of the date-filter
        # unreliability noted in _month_query's docstring).
        row_count_report[store["label"]] = (len(raw_orders), expected_count)

        # Re-filter client-side: never trust the remote created_at bound or
        # the -source_name negation (see _month_query docstring). Draft-
        # order exclusion is the ONLY sheet-confirmed scope rule; date
        # bounds are re-checked against the real createdAt.
        orders = [
            o for o in raw_orders
            if start <= o["createdAt"][:10] < end
            and o["sourceName"] != "shopify_draft_order"
        ]
        dropped = len(raw_orders) - len(orders)
        print(f"{store['label']}: pulled {len(raw_orders)} orders (Shopify ordersCount: "
              f"{expected_count}), {len(orders)} after client-side date re-filter "
              f"({dropped} dropped)", file=sys.stderr)

        fx_rate = 1.0 if store["label"] == "uk" else lookup_month(month_str, fx_rows)

        for order in orders:
            ship_to = (order["shippingAddress"] or {}).get("countryCodeV2")
            bucket = country_bucket(ship_to, store["label"])
            chan = channel(order["purchasingEntity"])
            refund_events = refund_events_by_line_id(order)

            orders_meta.append({
                "name": order["name"],
                "country": bucket,
                "is_fully_refunded": order["displayFinancialStatus"] == "REFUNDED",
            })

            shipping_incl_vat = sum(
                float(e["node"]["discountedPriceSet"]["shopMoney"]["amount"])
                for e in order["shippingLines"]["edges"]
            )
            order_shipping.append({"country": bucket, "shipping": shipping_incl_vat / fx_rate})

            for edge in order["lineItems"]["edges"]:
                li = edge["node"]
                net_of_discount = float(li["discountedTotalSet"]["shopMoney"]["amount"])
                tax = sum(float(t["priceSet"]["shopMoney"]["amount"]) for t in li["taxLines"])

                variant = li.get("variant") or {}
                inv_item = (variant or {}).get("inventoryItem") or {}
                unit_cost_raw = (inv_item.get("unitCost") or {}).get("amount")

                lines.append({
                    "sku": li["sku"],
                    "order_name": order["name"],
                    "country": bucket,
                    "channel": chan,
                    "units": li["quantity"],
                    "net_of_discount": net_of_discount,
                    "original_gross": float(li["originalTotalSet"]["shopMoney"]["amount"]),
                    "tax": tax,
                    "refund_events": refund_events.get(li["id"], []),
                    "fx_rate": fx_rate,
                    "unit_cost": float(unit_cost_raw) if unit_cost_raw is not None else None,
                })

    return {
        "lines": lines,
        "order_shipping": order_shipping,
        "orders_meta": orders_meta,
        "row_count_report": row_count_report,
    }


def compute(fetched, exclude_returns=False, return_cutoff_date=None):
    """Pure aggregation phase -- no network. Computes AB per line and rolls
    up every total the regression checks need.

    THE RETURNS RULE (2026-08-03, owner-confirmed): a return is attached to
    its order, and counted if that ORDER falls in the report month --
    regardless of which calendar date the refund itself was processed on.
    This is the default (exclude_returns=False, return_cutoff_date=None) and
    is what `fetch()` already scopes lines to (every line belongs to an
    order inside the month window), so no extra filtering is needed here in
    the normal path. A returns gap on a very recent month (July under-counts
    ~9.5% vs the sheet) is expected and NOT a bug to chase: it's an
    as-of-snapshot artifact -- returns for a barely-closed month are still
    arriving, and the sheet's own snapshot was captured at a slightly later
    moment than this query. Don't tune this past ~1% until order scope
    (source_name:web, see _month_query) is confirmed fixed.

    exclude_returns / return_cutoff_date: DIAGNOSTIC ONLY, used by
    floor_isolation_test() to size how much of a gap is returns-shaped --
    NOT the production rule and not meant to be applied in report_may() /
    report_july(). A per-processing-date cutoff was tried against May and
    numerically narrowed that gap, but does not generalise (confirmed against
    July) -- the real rule is the one above, with no cutoff.
    """
    country_totals = {"UK": 0.0, "US": 0.0, "ROW": 0.0}
    units_totals = {"UK": 0, "US": 0, "ROW": 0}
    channel_totals = {"D2C": 0.0, "B2B": 0.0}
    tax_total = 0.0
    returns_total = 0.0

    for line in fetched["lines"]:
        if exclude_returns:
            returns_inc_vat, tax_returned = 0.0, 0.0
        else:
            returns_inc_vat, tax_returned = sum_refund_events(
                line["refund_events"], cutoff_date=return_cutoff_date)

        ab = line_ab(line["net_of_discount"], line["tax"], returns_inc_vat, tax_returned, line["fx_rate"])
        country_totals[line["country"]] += ab
        units_totals[line["country"]] += line["units"]
        channel_totals[line["channel"]] += ab
        tax_total += line["tax"] / line["fx_rate"]
        returns_total += returns_inc_vat / line["fx_rate"]

    shipping_total = sum(o["shipping"] for o in fetched["order_shipping"])
    grand_total = sum(country_totals.values())
    order_count = len(fetched["orders_meta"])  # post client-side date re-filter

    # Orders-count diagnostic (2026-08-03): fully-refunded orders excluded
    # from the COUNT only -- their revenue still flows through AB's
    # zero-net branch above, untouched. Purely a count-side experiment.
    orders_count_by_country = {"UK": 0, "US": 0, "ROW": 0}
    orders_count_excl_refunded_by_country = {"UK": 0, "US": 0, "ROW": 0}
    fully_refunded_count = 0
    for o in fetched["orders_meta"]:
        orders_count_by_country[o["country"]] += 1
        if o["is_fully_refunded"]:
            fully_refunded_count += 1
        else:
            orders_count_excl_refunded_by_country[o["country"]] += 1

    return {
        "country_totals": country_totals,
        "units_totals": units_totals,
        "channel_totals": channel_totals,
        "tax_total": tax_total,
        "returns_total": returns_total,
        "shipping_total": shipping_total,
        "grand_total": grand_total,
        "order_count": order_count,
        "orders_count_by_country": orders_count_by_country,
        "orders_count_excl_refunded_by_country": orders_count_excl_refunded_by_country,
        "fully_refunded_count": fully_refunded_count,
        "line_count": len(fetched["lines"]),
        "row_count_report": fetched["row_count_report"],
    }


def _pct_diff(computed, expected):
    return abs(computed - expected) / abs(expected) * 100 if expected else float("nan")


def report_fx_diagnostic(month_str, seeded_rate):
    """DIAGNOSTIC ONLY (2026-08-03) -- report the mean of daily GBP/USD
    across the month (Frankfurter) beside the seeded monthly rate, to see
    whether the owner's month-rate is closer to a spot value (1st-of-month)
    or an end-of-month average. Does NOT change fx_rates.csv or any
    computed total -- purely informational.
    """
    year, month = (int(x) for x in month_str.split("-"))
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year:04d}-{month + 1:02d}-01" if month < 12 else f"{year + 1:04d}-01-01"
    resp = requests.get(f"https://api.frankfurter.app/{start}..{end}?from=GBP&to=USD", timeout=15)
    resp.raise_for_status()
    daily_rates = list(resp.json()["rates"].values())
    daily_usd = [r["USD"] for r in daily_rates]
    mean_rate = sum(daily_usd) / len(daily_usd)
    delta_pct = (seeded_rate - mean_rate) / mean_rate * 100

    print(f"\n=== FX diagnostic ({month_str}, informational only) ===")
    print(f"  Seeded monthly rate (sheet AA, spot on the 1st): {seeded_rate:.4f}")
    print(f"  Mean of {len(daily_usd)} daily rates across the month:  {mean_rate:.4f}")
    print(f"  Delta: {delta_pct:+.3f}%")


def gate_check(result):
    """Row-count-ties-to-Shopify + uk+us+row==independent-total. Raises with
    a clear message on failure (per-component/per-leg diffs should already
    have been printed by the caller's report_*() before this runs, so a
    failure here doesn't hide the numbers that led to it).
    """
    for label, (pulled, expected) in result["row_count_report"].items():
        assert pulled == expected, (
            f"RECONCILE FAIL: {label} pulled {pulled} orders but Shopify ordersCount "
            f"reports {expected} -- pagination dropped rows"
        )
    assert_country_reconciles(result["country_totals"], result["grand_total"])
    print("\ngate: row counts tie to Shopify totals; uk+us+row == independent grand total. PASS",
          file=sys.stderr)


def report_july(result):
    print("\n=== July 2026 component regression ===")
    rows = [
        ("AB product sales ex-VAT", result["grand_total"], JULY_COMPONENTS["ab_product_sales_ex_vat"]),
        ("AB incl. shipping", result["grand_total"] + result["shipping_total"], JULY_COMPONENTS["ab_incl_shipping"]),
        ("Net Sales Tax (R)", result["tax_total"], JULY_COMPONENTS["net_sales_tax"]),
        ("Returns inc VAT (O)", -result["returns_total"], JULY_COMPONENTS["returns_inc_vat"]),
        ("Shipping charges", result["shipping_total"], JULY_COMPONENTS["shipping_charges"]),
        ("Units", sum(result["units_totals"].values()), JULY_COMPONENTS["units"]),
        ("Orders", result["order_count"], JULY_COMPONENTS["orders"]),
        ("Order-lines (~)", result["line_count"], JULY_COMPONENTS["order_lines"]),
    ]
    print(f"{'component':28s} {'computed':>15s} {'expected':>15s} {'gap %':>8s}")
    for label, computed, expected in rows:
        print(f"{label:28s} {computed:15,.2f} {expected:15,.2f} {_pct_diff(computed, expected):7.3f}%")

    total = result["grand_total"]
    print("\n--- per-leg (UK/US/ROW) ---")
    for label, value in result["country_totals"].items():
        share = value / total * 100 if total else 0
        print(f"  {label:6s} £{value:,.2f}  ({share:.1f}% of total)")
    print("  NOTE: no July per-leg (UK/US/ROW) ground truth was given, only the combined "
          "total -- this shows raw computed shares for context, not a per-leg gap. A July "
          "UK/US/ROW split from the sheet (like the one given for May) would let this be a "
          "real localisation check rather than just a shares readout.")
    print("  By-product-status breakdown not possible yet -- no Line Detail data source is "
          "wired into trading/ (returns/ has its own copy via common/enrichment.py, but "
          "trading has no product-status enrichment yet).")


def report_line_trace(fetched):
    """DIAGNOSTIC (2026-08-03): pick the single highest-value order (by
    summed net-of-discount across its lines) and print M/T/R/O/S/AB per
    line, end to end -- for manual cross-check against the same order in
    the live sheet, if visible. This does NOT by itself prove or disprove
    anything; it's a concrete worked example for a human (or the sheet
    owner) to verify the formula against.
    """
    by_order = {}
    for line in fetched["lines"]:
        by_order.setdefault(line["order_name"], []).append(line)

    order_name, order_lines = max(
        by_order.items(), key=lambda kv: sum(l["net_of_discount"] for l in kv[1])
    )

    print(f"\n=== Line-level trace: order {order_name} ({len(order_lines)} line(s)) ===")
    print(f"{'sku':22s} {'M (gross)':>12s} {'T (net-disc)':>13s} {'R (tax)':>10s} "
          f"{'O (returns)':>12s} {'S (tax-ret)':>12s} {'AB':>12s}")
    for line in order_lines:
        returns_inc_vat, tax_returned = sum_refund_events(line["refund_events"])
        ab = line_ab(line["net_of_discount"], line["tax"], returns_inc_vat, tax_returned, line["fx_rate"])
        print(f"{(line['sku'] or '(no sku)'):22s} {line['original_gross']:12,.2f} "
              f"{line['net_of_discount']:13,.2f} {line['tax']:10,.2f} "
              f"{returns_inc_vat:12,.2f} {tax_returned:12,.2f} {ab:12,.2f}")
    print("  Cross-check this order's M/T/R/O/S/AB against the same order name in the live "
          "sheet's 'LM Shopify' tab (Order name column) if you have it open -- that's the "
          "fastest way to confirm or rule out a per-line formula issue.")


def report_refunded_orders_test(result):
    """DIAGNOSTIC (2026-08-03): exclude fully-REFUNDED orders from the
    ORDERS COUNT only (revenue is untouched -- still flows through AB's
    zero-net branch). Reports both gaps explicitly; does not assume a pass.
    """
    expected_orders = JULY_COMPONENTS["orders"]
    excl = result["orders_count_excl_refunded_by_country"]
    incl = result["orders_count_by_country"]
    new_total = sum(excl.values())
    old_total = sum(incl.values())

    print("\n=== Fully-refunded-orders count test (revenue untouched) ===")
    print(f"  Fully-refunded orders found: {result['fully_refunded_count']}")
    print(f"  Orders per leg (all):          UK {incl['UK']}  US {incl['US']}  ROW {incl['ROW']}  Total {old_total}")
    print(f"  Orders per leg (excl. refund): UK {excl['UK']}  US {excl['US']}  ROW {excl['ROW']}  Total {new_total}")
    print(f"  Orders vs expected {expected_orders}: was {_pct_diff(old_total, expected_orders):.3f}%, "
          f"now {_pct_diff(new_total, expected_orders):.3f}% "
          f"({'AT/above' if new_total >= expected_orders else 'BELOW'} target)")

    ab_expected = JULY_COMPONENTS["ab_product_sales_ex_vat"]
    ab_actual = result["grand_total"]
    print(f"  AB revenue (should be UNCHANGED, ~542k, not drift toward {ab_expected:,.2f}): "
          f"£{ab_actual:,.2f}  (gap {_pct_diff(ab_actual, ab_expected):.3f}%)")

    orders_ok = _pct_diff(new_total, expected_orders) <= 0.1 and new_total >= expected_orders
    revenue_held = ab_actual > 540000  # did NOT drop toward 534.5k
    if orders_ok and revenue_held:
        print("  PASS: orders land at/above target within 0.1%, revenue held steady.")
    elif not revenue_held:
        print("  FLAG: revenue drifted down toward the expected AB figure -- the zero-net "
              "branch may not be firing correctly for refunded orders. Separate bug, do not "
              "conflate with the orders-count question.")
    else:
        print("  NOT a full explanation: orders still off target after this exclusion.")


def report_totals_only(result, targets, label_prefix):
    print(f"\n=== {label_prefix} totals-only check ===")
    ct, ut, cht = result["country_totals"], result["units_totals"], result["channel_totals"]
    rows = [
        ("Total", result["grand_total"], targets["total"]),
        ("UK", ct["UK"], targets["uk"]),
        ("US", ct["US"], targets["us"]),
        ("ROW", ct["ROW"], targets["row"]),
        ("Units total", sum(ut.values()), targets["units_total"]),
        ("Units UK", ut["UK"], targets["units_uk"]),
        ("Units US", ut["US"], targets["units_us"]),
        ("Units ROW", ut["ROW"], targets["units_row"]),
        ("D2C", cht["D2C"], targets["d2c"]),
        ("B2B", cht["B2B"], targets["b2b"]),
        ("Orders", result["order_count"], None),
    ]
    print(f"{'component':14s} {'computed':>15s} {'expected':>15s} {'gap %':>8s}")
    for label, computed, expected in rows:
        if expected is None:
            print(f"{label:14s} {computed:15,.2f} {'n/a':>15s} {'':>8s}")
        else:
            print(f"{label:14s} {computed:15,.2f} {expected:15,.2f} {_pct_diff(computed, expected):7.3f}%")


def report_may(result):
    report_totals_only(result, MAY_TOTALS, "May 2026")


def report_june(result):
    report_totals_only(result, JUNE_TOTALS, "June 2026")


def floor_isolation_test(fetched_may=None):
    """Diagnostic (2026-08-03): recompute May with the returns term (O)
    zeroed out entirely, to size how much of the ~2% UK-side floor (present
    even where FX=1, so not an FX bug) is attributable to returns handling.
    Reuses an already-fetched May payload if given, so this never re-hits
    the network for a second pull.
    """
    fetched = fetched_may or fetch("2026-05")
    with_returns = compute(fetched, exclude_returns=False)
    without_returns = compute(fetched, exclude_returns=True)

    uk_with = with_returns["country_totals"]["UK"]
    uk_without = without_returns["country_totals"]["UK"]
    expected_uk = MAY_TOTALS["uk"]

    gap_with = _pct_diff(uk_with, expected_uk)
    signed_gap_without = (uk_without - expected_uk) / expected_uk * 100

    print("\n=== Floor isolation: May 2026 UK, AB with returns term removed ===")
    print(f"  UK with returns term:    £{uk_with:,.2f}  (gap {gap_with:.3f}%)")
    print(f"  UK without returns term: £{uk_without:,.2f}  (signed gap {signed_gap_without:+.3f}%)")
    if signed_gap_without > 0:
        print("  -> removing returns FLIPS UK positive: returns timing is (at least part of) the floor.")

        print("\n  Scanning return-cutoff dates (only count a refund if created on/before "
              "cutoff) to find where UK lands closest to the fixture:")
        for cutoff in ["2026-05-31", "2026-06-07", "2026-06-15", "2026-06-30", "2026-07-15"]:
            variant = compute(fetched, return_cutoff_date=cutoff)
            uk_variant = variant["country_totals"]["UK"]
            signed_gap = (uk_variant - expected_uk) / expected_uk * 100
            print(f"    cutoff {cutoff}: UK £{uk_variant:,.2f}  (signed gap {signed_gap:+.3f}%)")
    elif abs(signed_gap_without) < gap_with:
        print("  -> removing returns narrows the gap but UK is still short: a factor, not the whole floor.")
    else:
        print("  -> removing returns does not help: floor is NOT primarily a returns-timing issue.")
    return with_returns, without_returns


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "may"

    if action == "floor":
        floor_isolation_test()
    elif action == "may-full":
        fetched = fetch("2026-05")
        report_may(compute(fetched))
        floor_isolation_test(fetched_may=fetched)
    elif action == "july":
        fetched = fetch("2026-07")
        result = compute(fetched)
        report_july(result)
        report_refunded_orders_test(result)
        report_line_trace(fetched)
        report_fx_diagnostic("2026-07", JULY_FX_RATE)
        gate_check(result)
    elif action == "may":
        result = compute(fetch("2026-05"))
        report_may(result)
    elif action == "june":
        result = compute(fetch("2026-06"))
        report_june(result)
        gate_check(result)
    else:
        result = compute(fetch(action))
        print(f"\n=== Monthly Trading revenue ({action}) ===")
        for label in ("UK", "US", "ROW"):
            print(f"  {label:6s} £{result['country_totals'][label]:,.2f}")
        print(f"  Total  £{result['grand_total']:,.2f}")
