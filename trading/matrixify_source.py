"""Matrixify CSV -> per-line records for the trading revenue engine.

Frozen-snapshot ingestion (2026-08-03 migration): replaces the live Shopify
GraphQL source (shopify_feed.py) for trading. The AB/country/channel/gate
logic in revenue.py and build.py is UNCHANGED -- only how raw lines are
sourced is new. Reason: a monthly report needs a reproducible, frozen
snapshot; live queries gave timezone-boundary leakage, mid-run drift, and
non-reproducibility (see ROADMAP.md Phase B). A Matrixify export is a
point-in-time snapshot, immutable once downloaded and committed.

Row structure, confirmed against a real export (trading/source/
orders_2026-05_US.csv, order #US29002, 2026-08-03): a refund shares the
SAME `Line: ID` as its original `Line Item` row, as one or more `Refund
Line` rows with NEGATIVE Quantity/Total/Tax Total (one row per unit
refunded, in that example). Summing them recovers exactly the same O/S
(returns/tax-returned) the GraphQL engine computed from refundLineItems.
"""
import csv
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def _num(v):
    if v is None or v == "":
        return 0.0
    return float(v)


def _parse_dt(s):
    """Matrixify time_format '%Y-%m-%d %H:%M:%S %z', e.g.
    "2026-05-01 04:37:15 -0400" -- carries an explicit UTC offset
    regardless of which store/timezone it came from.
    """
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")


def order_month_london(created_at_str):
    """Bucket by order-created month in Europe/London -- fixes the UTC vs.
    ambiguous-remote-timezone boundary leakage the live GraphQL source hit.
    Returns 'YYYY-MM' or None if created_at_str is blank.
    """
    dt = _parse_dt(created_at_str)
    if dt is None:
        return None
    return dt.astimezone(LONDON).strftime("%Y-%m")


def load_rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def build_lines(rows, store_label):
    """Group a store's exported rows into per-line records.

    Returns (lines, orders_meta, order_shipping):
      lines: [{sku, order_name, units, net_of_discount, tax,
               returns_inc_vat, tax_returned, order_month, ship_country_code,
               company, cancelled_at, financial_status, store_label}]
      orders_meta: {order_name: {created_at, cancelled_at, financial_status,
                                  ship_country_code, company, source}}
      order_shipping: {order_name: shipping_line_total} (inc-VAT, summed
        across any Shipping Line rows for that order)
    """
    by_line = defaultdict(list)
    orders_meta = {}
    order_shipping = defaultdict(float)

    for row in rows:
        name = row["Name"]
        line_type = row["Line: Type"]

        if row.get("Top Row", "").lower() == "true":
            company = row.get("Company: Name") or row.get("Billing: Company") or row.get("Shipping: Company") or None
            orders_meta[name] = {
                "created_at": row["Created At"],
                "cancelled_at": row.get("Cancelled At") or None,
                "financial_status": row.get("Payment: Status") or None,
                "ship_country_code": row.get("Shipping: Country Code") or None,
                "company": company,
                "source": row.get("Source") or None,
            }

        if line_type == "Shipping Line":
            order_shipping[name] += _num(row.get("Line: Total"))
        elif line_type in ("Line Item", "Refund Line"):
            line_id = row.get("Line: ID")
            if line_id:
                by_line[(name, line_id)].append(row)

    lines = []
    skipped_orphan_refunds = 0
    for (name, line_id), line_rows in by_line.items():
        original = next((r for r in line_rows if r["Line: Type"] == "Line Item"), None)
        if original is None:
            # A refund whose original Line Item row isn't in this export
            # window (e.g. the sale happened in a prior month). Can't
            # compute AB for a line with no base sale row -- skip and count.
            skipped_orphan_refunds += 1
            continue

        refund_rows = [r for r in line_rows if r["Line: Type"] == "Refund Line"]
        meta = orders_meta.get(name, {})

        # Feedback row (Tom, SKU Performance, 2026-08-10 -- "Bottom 20 SKU
        # performance units and sales don't look correct"): found 2026-08-12
        # doing the root-cause investigation -- cash was already netted for
        # returns (returns_inc_vat below), but units never were, an
        # asymmetry invisible on high-revenue SKUs but glaring on the
        # bottom-20 (return-dominated SKUs show near-zero net revenue
        # alongside their full PRE-return unit count, e.g. "sold 12 units"
        # for a SKU that netted to ~£0 because 10 of those 12 came back).
        # Refund Line rows carry NEGATIVE Quantity (this module's own
        # docstring, confirmed against a real export) -- same sign
        # convention already used for returns_inc_vat/tax_returned below,
        # so net units the identical way: negate the already-negative sum
        # to get a positive "units returned" magnitude, then subtract it.
        units_returned = -sum(_num(r.get("Line: Quantity")) for r in refund_rows)

        lines.append({
            "sku": original.get("Line: SKU") or None,
            "order_name": name,
            "units": _num(original.get("Line: Quantity")) - units_returned,
            "net_of_discount": _num(original.get("Line: Total")),
            "tax": _num(original.get("Line: Tax Total")),
            "returns_inc_vat": -sum(_num(r.get("Line: Total")) for r in refund_rows),
            "tax_returned": -sum(_num(r.get("Line: Tax Total")) for r in refund_rows),
            "order_month": order_month_london(meta.get("created_at")),
            "ship_country_code": meta.get("ship_country_code"),
            "company": meta.get("company"),
            "cancelled_at": meta.get("cancelled_at"),
            "financial_status": meta.get("financial_status"),
            "store_label": store_label,
        })

    if skipped_orphan_refunds:
        import sys
        print(f"matrixify_source: skipped {skipped_orphan_refunds} refund row(s) whose "
              f"original Line Item isn't in this export window", file=sys.stderr)

    return lines, orders_meta, dict(order_shipping)
