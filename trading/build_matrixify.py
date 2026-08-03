"""Monthly Trading builder -- Matrixify frozen-snapshot source.

Supersedes build.py's live Shopify GraphQL source (shopify_feed.py) for the
reasons in ROADMAP.md Phase B / the 2026-08-03 Matrixify migration brief:
live queries gave timezone-boundary leakage, mid-run drift against an
actively-changing store, and non-reproducibility. This reuses the SAME
AB/country/channel/reconciliation-gate logic (revenue.py, common/) against
rows parsed from a committed Matrixify export CSV instead.

STATUS (2026-08-03): both Matrixify-PlankUK and Matrixify-PlankUS are
connected. UK+US now reconcile via compute_combined() below (BRIEF #5:
ROW bucket + three-way country reconcile) -- ship-to country, NOT store of
origin, is the bucketing key, so a UK-store order shipped to e.g. Ireland
correctly lands in ROW rather than being counted as UK. compute_store()
below (single-store, store-label-only reporting) is kept for the per-store
diagnostic CLI use it already had; it is not the reconciled figure -- its
own "Total" is a store's grand total across all three of ITS buckets, not
that store's UK/US/ROW component alone. Use compute_combined() (or the
`reconcile` CLI action) for anything compared against the oracle.

The US £ residual (~-6.5%, standalone Discount rows / cancelled-order
handling) is explicitly OUT of scope here -- see trading/RECONCILE_HANDOFF.md.
Do not tune country bucketing to chase it.

Run:  python trading/build_matrixify.py trading/source/orders_2026-05_US.csv us 2026-05
      python trading/build_matrixify.py reconcile 2026-05
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from matrixify_source import load_rows, build_lines, order_month_london
from revenue import country_bucket, line_ab

from common.fx import ensure_month, seed_confirmed, lookup_month
from common.reconciliation_gate import assert_country_reconciles, assert_matches_oracle

JULY_FX_RATE = 1.3250
JULY_FX_SOURCE = "googlefinance (sheet AA, month rate)"

# May 2026 Monthly Summary row 7 ground truth (AT7/CD7/DN7/F7/AX7/CH7/DR7/J7),
# restated here rather than imported from build.py's identical MAY_TOTALS --
# build.py is the superseded live-query path scheduled for retirement (see
# ROADMAP.md Phase B / RECONCILE_HANDOFF.md), so this module should not take
# a dependency on it just to share a constant.
MAY_THREE_WAY = {
    "total": 476292.70, "uk": 247772.02, "us": 214063.73, "row": 14456.95,
    "units_total": 27453, "units_uk": 16373, "units_us": 9436, "units_row": 822,
}


def channel_from_company(company):
    """B2B if a Company is present, else D2C -- see brief Step 3. Channel
    never partitions the reconciled total (ROADMAP.md §4).
    """
    return "B2B" if company else "D2C"


def _fx_rate_for(store_label, month_str):
    if month_str == "2026-07":
        fx_rows = seed_confirmed(month_str, JULY_FX_RATE, JULY_FX_SOURCE)
    else:
        fx_rows = ensure_month(month_str)
    return 1.0 if store_label == "uk" else lookup_month(month_str, fx_rows)


def _load_store_month(csv_path, store_label, month_str):
    """Load one store's export and filter to the lines belonging to
    month_str. Returns (kept_lines, fx_rate, shipping_total, order_names).
    Shared by compute_store() (single-store diagnostic) and
    compute_combined() (the reconciled ship-to-country three-way) so the
    loading/filtering logic can't drift between the two.
    """
    rows = load_rows(csv_path)
    lines, orders_meta, order_shipping = build_lines(rows, store_label)
    fx_rate = _fx_rate_for(store_label, month_str)

    kept_lines = [line for line in lines if line["order_month"] == month_str]
    dropped_other_month = len(lines) - len(kept_lines)
    order_names = {line["order_name"] for line in kept_lines}

    shipping_total = sum(
        v for name, v in order_shipping.items()
        if orders_meta.get(name, {}).get("created_at")
        and order_month_london(orders_meta[name]["created_at"]) == month_str
    ) / fx_rate

    print(f"{store_label}: {len(rows)} raw CSV rows, {len(kept_lines)} lines kept for "
          f"{month_str} ({dropped_other_month} lines belonged to a different month), "
          f"{len(order_names)} distinct orders", file=sys.stderr)

    return kept_lines, fx_rate, shipping_total, order_names


def compute_store(csv_path, store_label, month_str):
    """Single-store diagnostic: buckets one store's own lines by ship-to
    country. NOT the reconciled figure -- its "Total" is that store's grand
    total across all three of ITS OWN buckets (e.g. the UK store's own
    ROW-shipped orders are included), not a UK/US/ROW component to compare
    against the oracle on its own. Use compute_combined() for that.
    """
    kept_lines, fx_rate, shipping_total, order_names = _load_store_month(csv_path, store_label, month_str)

    country_totals = {"UK": 0.0, "US": 0.0, "ROW": 0.0}
    units_totals = {"UK": 0, "US": 0, "ROW": 0}
    channel_totals = {"D2C": 0.0, "B2B": 0.0}
    tax_total = 0.0
    returns_total = 0.0

    for line in kept_lines:
        bucket = country_bucket(line["ship_country_code"], store_label)
        chan = channel_from_company(line["company"])
        ab = line_ab(line["net_of_discount"], line["tax"], line["returns_inc_vat"],
                     line["tax_returned"], fx_rate)

        country_totals[bucket] += ab
        units_totals[bucket] += line["units"]
        channel_totals[chan] += ab
        tax_total += line["tax"] / fx_rate
        returns_total += line["returns_inc_vat"] / fx_rate

    return {
        "country_totals": country_totals,
        "units_totals": units_totals,
        "channel_totals": channel_totals,
        "tax_total": tax_total,
        "returns_total": returns_total,
        "shipping_total": shipping_total,
        "grand_total": sum(country_totals.values()),
        "order_count": len(order_names),
    }


def compute_combined(uk_csv, us_csv, month_str):
    """Union UK + US store lines for month_str and bucket EVERY line by
    SHIP-TO country (revenue.country_bucket: GB->UK, US->US, else->ROW,
    falling back to the line's own store only when ship-to is blank/N-A --
    never to ROW). This is the reconciled three-way figure BRIEF #5 wants:
    a UK-store order shipped to Ireland lands in ROW, not UK.

    grand_total is accumulated independently of country_totals (a separate
    running sum over every kept line, not sum(country_totals.values())) so
    assert_country_reconciles's uk+us+row check is a real leak check, not
    vacuous -- see its docstring in common/reconciliation_gate.py.
    """
    country_totals = {"UK": 0.0, "US": 0.0, "ROW": 0.0}
    units_totals = {"UK": 0, "US": 0, "ROW": 0}
    channel_totals = {"D2C": 0.0, "B2B": 0.0}
    tax_total = 0.0
    returns_total = 0.0
    shipping_total = 0.0
    grand_total = 0.0
    order_names = set()

    for csv_path, store_label in ((uk_csv, "uk"), (us_csv, "us")):
        kept_lines, fx_rate, store_shipping, store_order_names = _load_store_month(
            csv_path, store_label, month_str)
        shipping_total += store_shipping
        # prefix so an order name can never collide across the two stores
        order_names |= {f"{store_label}:{name}" for name in store_order_names}

        for line in kept_lines:
            bucket = country_bucket(line["ship_country_code"], store_label)
            chan = channel_from_company(line["company"])
            ab = line_ab(line["net_of_discount"], line["tax"], line["returns_inc_vat"],
                         line["tax_returned"], fx_rate)

            country_totals[bucket] += ab
            units_totals[bucket] += line["units"]
            channel_totals[chan] += ab
            tax_total += line["tax"] / fx_rate
            returns_total += line["returns_inc_vat"] / fx_rate
            grand_total += ab

    return {
        "country_totals": country_totals,
        "units_totals": units_totals,
        "channel_totals": channel_totals,
        "tax_total": tax_total,
        "returns_total": returns_total,
        "shipping_total": shipping_total,
        "grand_total": grand_total,
        "order_count": len(order_names),
    }


def report_combined(result, oracle, label_prefix):
    ct, ut = result["country_totals"], result["units_totals"]
    print(f"\n=== {label_prefix} three-way reconcile (ship-to country) ===")
    print(f"{'bucket':10s} {'computed':>15s} {'oracle':>15s} {'gap %':>8s}")
    for key, label in (("uk", "UK"), ("us", "US"), ("row", "ROW")):
        computed = ct[label]
        expected = oracle[key]
        gap = abs(computed - expected) / abs(expected) * 100 if expected else float("nan")
        print(f"{label:10s} {computed:15,.2f} {expected:15,.2f} {gap:7.3f}%")
    total_gap = abs(result["grand_total"] - oracle["total"]) / abs(oracle["total"]) * 100
    print(f"{'Total':10s} {result['grand_total']:15,.2f} {oracle['total']:15,.2f} {total_gap:7.3f}%")

    print("\n--- units (diagnostic only -- NOT gated; oracle's own UK+US+ROW units "
          "don't foot to its Total units column, a sheet-side quirk, not our bug) ---")
    for key, label in (("units_uk", "UK"), ("units_us", "US"), ("units_row", "ROW")):
        print(f"  {label:6s} {ut[label]:>8,.0f}  (oracle {oracle[key]:,})")
    units_total = sum(ut.values())
    print(f"  {'Total':6s} {units_total:>8,.0f}  (oracle {oracle['units_total']:,})")


def gate_check_combined(result, oracle):
    """ROW present + uk+us+row == independent grand total, then each bucket
    (and the total) vs the committed May oracle within 0.1%. Raises on
    failure -- never called with output already written; the caller must
    gate before writing anything downstream.
    """
    assert_country_reconciles(result["country_totals"], result["grand_total"])
    assert_matches_oracle(
        {
            "uk": result["country_totals"]["UK"],
            "us": result["country_totals"]["US"],
            "row": result["country_totals"]["ROW"],
            "total": result["grand_total"],
        },
        oracle,
    )
    print("\ngate: ROW bucket present; uk+us+row == independent grand total; "
          "each bucket + total within 0.1% of the May oracle. PASS", file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1] == "reconcile":
        # python trading/build_matrixify.py reconcile [month] [uk_csv] [us_csv]
        month_str = sys.argv[2] if len(sys.argv) > 2 else "2026-05"
        uk_csv = sys.argv[3] if len(sys.argv) > 3 else "trading/source/orders_2026-05_UK.csv"
        us_csv = sys.argv[4] if len(sys.argv) > 4 else "trading/source/orders_2026-05_US.csv"

        result = compute_combined(uk_csv, us_csv, month_str)
        if month_str == "2026-05":
            report_combined(result, MAY_THREE_WAY, "May 2026")
            gate_check_combined(result, MAY_THREE_WAY)  # raises + aborts before any output is written on failure
        else:
            print(f"\n=== {month_str} three-way (Matrixify source, ship-to country) ===")
            for label in ("UK", "US", "ROW"):
                print(f"  {label:6s} £{result['country_totals'][label]:,.2f}")
            print(f"  Total  £{result['grand_total']:,.2f}")
            print(f"  (no committed oracle for {month_str} yet -- gate not run)")
    else:
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
