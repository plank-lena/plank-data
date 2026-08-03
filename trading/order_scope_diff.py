"""One-off diagnostic (2026-08-03): set-diff the pipeline's July order names
against the sheet's own 3,355 distinct order names (Cowork extract,
trading/tests/fixtures/2026-07_order_names_sheet.csv), to find the exact
order-scope rule -- the sheet counts refunded orders (SUM col L = distinct
names), so refunded-status is NOT the exclusion rule; the 28 pipeline-extra
orders are absent from the sheet's extract entirely, i.e. an upstream scope
difference.

Run:  python trading/order_scope_diff.py
"""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from build import fetch
from shopify_feed import STORES, get_access_token, _graphql

SHEET_CSV = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "2026-07_order_names_sheet.csv")


def normalize(name, strip_ex_suffix=False):
    n = str(name).strip().lstrip("#").upper()
    if strip_ex_suffix and n.endswith("-EX1"):
        n = n[: -len("-EX1")]
    return n


def load_sheet_names():
    with open(SHEET_CSV, newline="") as fh:
        return [row["order_name"] for row in csv.DictReader(fh)]


ORDER_DETAIL_QUERY = """
query($q: String) {
  orders(first: 5, query: $q) {
    edges {
      node {
        name
        createdAt
        displayFinancialStatus
        cancelledAt
        test
        sourceName
        tags
        purchasingEntity { __typename }
        currentTotalPriceSet { shopMoney { amount } }
        customer { email }
      }
    }
  }
}
"""


def lookup_orders(store, names):
    """Fetch full detail for a list of order names on one store."""
    domain = os.environ.get(store["domain_env"])
    client_id = os.environ.get(store["client_id_env"])
    client_secret = os.environ.get(store["client_secret_env"])
    token = get_access_token(domain, client_id, client_secret)

    out = []
    for name in names:
        bare = name.lstrip("#")
        query = f'name:{bare} OR name:#{bare}'
        data = _graphql(domain, token, ORDER_DETAIL_QUERY, {"q": query})
        for edge in data["orders"]["edges"]:
            node = edge["node"]
            if normalize(node["name"]) == normalize(name):
                out.append(node)
    return out


def main():
    print("Fetching pipeline July orders (both stores)...", file=sys.stderr)
    fetched = fetch("2026-07")
    pipeline_names_raw = [o["name"] for o in fetched["orders_meta"]]

    sheet_names_raw = load_sheet_names()

    print(f"\npipeline order count: {len(pipeline_names_raw)}")
    print(f"sheet order count:    {len(sheet_names_raw)}")

    for label, strip_ex in [("WITH -EX1 suffix kept", False), ("WITH -EX1 suffix stripped", True)]:
        pipeline_norm = {normalize(n, strip_ex) for n in pipeline_names_raw}
        sheet_norm = {normalize(n, strip_ex) for n in sheet_names_raw}

        pipeline_not_sheet = sorted(pipeline_norm - sheet_norm)
        sheet_not_pipeline = sorted(sheet_norm - pipeline_norm)

        print(f"\n=== Diff -- {label} ===")
        print(f"  pipeline-not-sheet: {len(pipeline_not_sheet)}")
        for n in pipeline_not_sheet:
            print(f"    {n}")
        print(f"  sheet-not-pipeline: {len(sheet_not_pipeline)}")
        for n in sheet_not_pipeline:
            print(f"    {n}")

    # Use the EX1-stripped diff for the actual lookup (matches pipeline's
    # convention of listing exchange orders as their own line items under
    # the parent order name where applicable -- confirm via the printed
    # diff above whether -EX1 orders appear as extras either way).
    pipeline_norm = {normalize(n) for n in pipeline_names_raw}
    sheet_norm = {normalize(n) for n in sheet_names_raw}
    extra_normalized = sorted(pipeline_norm - sheet_norm)

    # Map back to original (unnormalized) names for the Shopify lookup.
    norm_to_raw = {normalize(n): n for n in pipeline_names_raw}
    extra_raw = [norm_to_raw[n] for n in extra_normalized if n in norm_to_raw]

    print(f"\nLooking up {len(extra_raw)} pipeline-not-sheet orders in Shopify...", file=sys.stderr)
    details = []
    for store in STORES:
        details.extend(lookup_orders(store, extra_raw))

    print(f"\n=== Detail for {len(details)} pipeline-not-sheet orders ===")
    print(f"{'name':16s} {'created_utc':20s} {'fin_status':12s} {'cancelled_at':22s} "
          f"{'test':5s} {'source_name':14s} {'entity':10s} {'total':>10s} {'email':24s}")
    for o in details:
        print(f"{o['name']:16s} {o['createdAt']:20s} {o['displayFinancialStatus']:12s} "
              f"{str(o['cancelledAt']):22s} {str(o['test']):5s} {o['sourceName']:14s} "
              f"{o['purchasingEntity']['__typename'] if o['purchasingEntity'] else '-':10s} "
              f"{o['currentTotalPriceSet']['shopMoney']['amount']:>10s} "
              f"{(o['customer']['email'] if o['customer'] else '-') or '-':24s}")

    cancelled_count = sum(1 for o in details if o["cancelledAt"] is not None)
    print(f"\ncancelled_at not null: {cancelled_count} / {len(details)}")


if __name__ == "__main__":
    main()
