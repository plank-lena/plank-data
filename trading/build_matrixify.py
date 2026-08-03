"""Monthly Trading builder -- Matrixify frozen-snapshot source.

Supersedes build.py's live Shopify GraphQL source (shopify_feed.py) for the
reasons in ROADMAP.md Phase B / the 2026-08-03 Matrixify migration brief:
live queries gave timezone-boundary leakage, mid-run drift against an
actively-changing store, and non-reproducibility. This reuses the SAME
AB/country/channel/reconciliation-gate logic (revenue.py, common/) against
rows parsed from a committed Matrixify export CSV instead.

STATUS (2026-08-03): only Matrixify-PlankUS is connected this session --
Matrixify-PlankUK was not available, so UK exports/reconciliation are
blocked until that connector is added. US-only figures are reported below;
do not treat a US-only "PASS" as the full uk+us+row gate passing.

Run:  python trading/build_matrixify.py trading/source/orders_2026-05_US.csv us 2026-05
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from matrixify_source import load_rows, build_lines, order_month_london
from revenue import country_bucket, line_ab

from common.fx import ensure_month, seed_confirmed, lookup_month

JULY_FX_RATE = 1.3250
JULY_FX_SOURCE = "googlefinance (sheet AA, month rate)"


def channel_from_company(company):
    """B2B if a Company is present, else D2C -- see brief Step 3. Channel
    never partitions the reconciled total (ROADMAP.md §4).
    """
    return "B2B" if company else "D2C"


def compute_store(csv_path, store_label, month_str):
    rows = load_rows(csv_path)
    lines, orders_meta, order_shipping = build_lines(rows, store_label)

    if month_str == "2026-07":
        fx_rows = seed_confirmed(month_str, JULY_FX_RATE, JULY_FX_SOURCE)
    else:
        fx_rows = ensure_month(month_str)
    fx_rate = 1.0 if store_label == "uk" else lookup_month(month_str, fx_rows)

    country_totals = {"UK": 0.0, "US": 0.0, "ROW": 0.0}
    units_totals = {"UK": 0, "US": 0, "ROW": 0}
    channel_totals = {"D2C": 0.0, "B2B": 0.0}
    tax_total = 0.0
    returns_total = 0.0
    order_names_in_month = set()

    kept, dropped_other_month = 0, 0
    for line in lines:
        if line["order_month"] != month_str:
            dropped_other_month += 1
            continue
        kept += 1

        bucket = country_bucket(line["ship_country_code"], store_label)
        chan = channel_from_company(line["company"])
        ab = line_ab(line["net_of_discount"], line["tax"], line["returns_inc_vat"],
                     line["tax_returned"], fx_rate)

        country_totals[bucket] += ab
        units_totals[bucket] += line["units"]
        channel_totals[chan] += ab
        tax_total += line["tax"] / fx_rate
        returns_total += line["returns_inc_vat"] / fx_rate
        order_names_in_month.add(line["order_name"])

    shipping_total = sum(
        v for name, v in order_shipping.items()
        if orders_meta.get(name, {}).get("created_at")
        and order_month_london(orders_meta[name]["created_at"]) == month_str
    ) / fx_rate

    print(f"{store_label}: {len(rows)} raw CSV rows, {kept} lines kept for {month_str} "
          f"({dropped_other_month} lines belonged to a different month), "
          f"{len(order_names_in_month)} distinct orders", file=sys.stderr)

    return {
        "country_totals": country_totals,
        "units_totals": units_totals,
        "channel_totals": channel_totals,
        "tax_total": tax_total,
        "returns_total": returns_total,
        "shipping_total": shipping_total,
        "grand_total": sum(country_totals.values()),
        "order_count": len(order_names_in_month),
    }


if __name__ == "__main__":
    csv_path = sys.argv[1]
    store_label = sys.argv[2]
    month_str = sys.argv[3]

    result = compute_store(csv_path, store_label, month_str)
    print(f"\n=== {store_label} {month_str} (Matrixify source) ===")
    for label in ("UK", "US", "ROW"):
        print(f"  {label:6s} £{result['country_totals'][label]:,.2f}")
    print(f"  Total  £{result['grand_total']:,.2f}")
    print(f"  Units  {sum(result['units_totals'].values()):,}")
    print(f"  Orders {result['order_count']:,}")
    print(f"  Tax    £{result['tax_total']:,.2f}")
    print(f"  Returns £{-result['returns_total']:,.2f}")
    print(f"  Shipping £{result['shipping_total']:,.2f}")
