"""The AB revenue formula + country/channel classification.

Ports TRADING_logic_spec.md §2 (the `LM Shopify` transform columns) and §3
Q1-Q3 into code. Formula appendix (verbatim) is at the spec's end -- this
module is the code equivalent of columns W, Y, Z, AA, AB, AD.
"""
from datetime import datetime

# Z: ship-to country -> UK/US/ROW, with a store fallback when country is
# missing (mirrors PH->UK, P US->US in TRADING_logic_spec.md's Z formula).
STORE_COUNTRY_FALLBACK = {"uk": "UK", "us": "US"}


def country_bucket(ship_to_country, store_label):
    if ship_to_country is None:
        return STORE_COUNTRY_FALLBACK.get(store_label, "ROW")
    upper = ship_to_country.upper()
    if upper == "GB":
        return "UK"
    if upper == "US":
        return "US"
    return "ROW"


def channel(purchasing_entity):
    """Y: B2B if a company is present, else D2C.

    TRADING_logic_spec.md Q3 is "B2B if company else the raw B2B? flag" --
    Supermetrics' separate raw flag isn't available to us; PurchasingCompany
    is the native Shopify B2B signal and is the same choice already made in
    the returns side's connector. Channel is descriptive only -- it never
    partitions the reconciled total (ROADMAP.md §4).
    """
    if purchasing_entity and purchasing_entity.get("__typename") == "PurchasingCompany":
        return "B2B"
    return "D2C"


def order_month(created_at_iso):
    """AD: bucket by order-created month, e.g. "May - 2026"."""
    dt = datetime.strptime(created_at_iso[:10], "%Y-%m-%d")
    return dt.strftime("%b - %Y")


def line_ab(net_of_discount, tax, returns_inc_vat, tax_returned, fx_rate):
    """AB per line: (net-of-discount inc-VAT - tax - returns) / FX, with the
    zero-net edge branch.

    net_of_discount : T -- discountedTotalSet(withCodeDiscounts=true), inc-VAT
    tax             : R -- sum of taxLines, inc-VAT sale's tax component
    returns_inc_vat : O -- sum of matching refundLineItem subtotal+tax
                      (see sum_refund_events -- pass 0.0 to test a
                      "no returns at all" diagnostic, or a cutoff-filtered
                      sum to test a returns-timing hypothesis)
    tax_returned    : S -- sum of matching refundLineItem tax only
    fx_rate         : AA -- GBP/USD; 1.0 for GBP-store lines

    Zero-net branch: the spec's ROUND(M+N+O)=0 uses the sheet's sign
    convention where discounts/returns are negative deltas off gross (M).
    Here net_of_discount/returns_inc_vat are magnitudes, so the equivalent
    condition is round(net_of_discount - returns_inc_vat) == 0 -- i.e. the
    line nets to zero after discount and return.
    """
    if round(net_of_discount - returns_inc_vat, 2) == 0:
        return (-returns_inc_vat + tax_returned) / fx_rate
    return (net_of_discount - tax - returns_inc_vat) / fx_rate


def refund_events_by_line_id(order_node):
    """{line_item_gid: [(refund_created_at, subtotal, tax), ...]} for one
    order, from its refunds[].refundLineItems[]. Kept as raw per-refund
    events (not pre-summed) so callers can filter by the refund's own date
    against different candidate cutoffs without a second network pull --
    see TRADING_logic_spec.md's note that a return is "adjusted to the week
    the order was placed," which live per-order-line APIs can over-apply if
    the return happened long after the report would have been generated
    (2026-08-03 floor-isolation finding: subtracting every return ever
    applied, with no cutoff, overshoots the true figure).
    """
    out = {}
    for refund in order_node["refunds"]:
        created_at = refund["createdAt"]
        for edge in refund["refundLineItems"]["edges"]:
            node = edge["node"]
            line_id = node["lineItem"]["id"]
            subtotal = float(node["subtotalSet"]["shopMoney"]["amount"])
            tax = float(node["totalTaxSet"]["shopMoney"]["amount"])
            out.setdefault(line_id, []).append((created_at, subtotal, tax))
    return out


def sum_refund_events(events, cutoff_date=None):
    """Sum (subtotal, tax) from a list of refund events, optionally only
    counting refunds created on or before cutoff_date (an ISO date string,
    inclusive). cutoff_date=None sums everything (no cutoff).
    """
    subtotal_total = tax_total = 0.0
    for created_at, subtotal, tax in events:
        if cutoff_date is not None and created_at[:10] > cutoff_date:
            continue
        subtotal_total += subtotal
        tax_total += tax
    return subtotal_total, tax_total
