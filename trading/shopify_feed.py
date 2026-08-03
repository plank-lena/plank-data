"""Shopify Admin GraphQL connector for the trading builder.

Pulls raw order-lines directly from the Shopify Admin API (Admin GraphQL),
NOT via Supermetrics or ShopifyQL -- this gives every line item a real,
stable `line_item.id`, which the live sheet's Supermetrics feed does not
expose (TRADING_logic_spec.md Q7). That sidesteps the "order+SKU" double-
count trap entirely: each line here is already atomic and uniquely
identified, so aggregation never needs a de-dupe step. This is a deliberate,
favourable difference from the sheet's own method, not a gap.

Auth: same OAuth client-credentials grant as the returns side's connectors
(Dev Dashboard apps expose client_id/client_secret, not a static token) --
see get_access_token().
"""

import os
import time

import requests

SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-10")
MAX_RETRIES = 5

STORES = [
    {
        "label": "uk",
        "domain_env": "SHOPIFY_UK_STORE_DOMAIN",
        "client_id_env": "SHOPIFY_UK_CLIENT_ID",
        "client_secret_env": "SHOPIFY_UK_CLIENT_SECRET",
    },
    {
        "label": "us",
        "domain_env": "SHOPIFY_US_STORE_DOMAIN",
        "client_id_env": "SHOPIFY_US_CLIENT_ID",
        "client_secret_env": "SHOPIFY_US_CLIENT_SECRET",
    },
]

ORDERS_QUERY = """
query($query: String, $cursor: String) {
  orders(first: 50, after: $cursor, sortKey: CREATED_AT, query: $query) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        name
        createdAt
        test
        shippingAddress { countryCodeV2 }
        purchasingEntity { __typename }
        refunds {
          refundLineItems(first: 50) {
            edges {
              node {
                lineItem { id }
                subtotalSet { shopMoney { amount } }
                totalTaxSet { shopMoney { amount } }
              }
            }
          }
        }
        lineItems(first: 50) {
          pageInfo { hasNextPage endCursor }
          edges {
            node {
              id
              sku
              quantity
              currentQuantity
              originalTotalSet { shopMoney { amount } }
              discountedTotalSet(withCodeDiscounts: true) { shopMoney { amount } }
              taxLines { priceSet { shopMoney { amount } } }
              variant { inventoryItem { unitCost { amount } id } }
            }
          }
        }
      }
    }
  }
}
"""

ORDERS_COUNT_QUERY = """
query($query: String) {
  ordersCount(query: $query) { count }
}
"""


def get_access_token(domain, client_id, client_secret):
    resp = requests.post(
        f"https://{domain}/admin/oauth/access_token",
        data={"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _graphql(domain, token, query, variables):
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                f"https://{domain}/admin/api/{SHOPIFY_API_VERSION}/graphql.json",
                json={"query": query, "variables": variables},
                headers={"X-Shopify-Access-Token": token},
                timeout=30,
            )
        except requests.exceptions.RequestException:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(2**attempt)
            continue

        if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
            time.sleep(float(resp.headers.get("Retry-After", 2**attempt)))
            continue

        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"Shopify GraphQL error: {body['errors']}")
        return body["data"]


def orders_count(domain, token, date_query):
    data = _graphql(domain, token, ORDERS_COUNT_QUERY, {"query": date_query})
    return data["ordersCount"]["count"]


def fetch_orders(domain, token, date_query):
    """Yield every non-test order matching date_query (a Shopify search
    query string, e.g. "created_at:>=2026-05-01 created_at:<2026-06-01").
    """
    cursor = None
    while True:
        data = _graphql(domain, token, ORDERS_QUERY, {"query": date_query, "cursor": cursor})
        page = data["orders"]
        for edge in page["edges"]:
            node = edge["node"]
            if node["test"]:
                continue
            if node["lineItems"]["pageInfo"]["hasNextPage"]:
                raise RuntimeError(
                    f"Order {node['name']} has >50 line items -- pagination "
                    "within a single order isn't implemented; extend fetch_orders "
                    "before trusting this run's totals."
                )
            yield node
        if not page["pageInfo"]["hasNextPage"]:
            return
        cursor = page["pageInfo"]["endCursor"]


def fetch_store_orders(store, date_query):
    """store: one of STORES. Returns (orders_list, expected_count) for the
    given date window -- expected_count is the independent ordersCount used
    by the row-count-ties-to-Shopify-totals gate check.
    """
    domain = os.environ.get(store["domain_env"])
    client_id = os.environ.get(store["client_id_env"])
    client_secret = os.environ.get(store["client_secret_env"])
    if not domain or not client_id or not client_secret:
        raise RuntimeError(
            f"{store['domain_env']} / {store['client_id_env']} / "
            f"{store['client_secret_env']} must be set"
        )
    token = get_access_token(domain, client_id, client_secret)
    expected_count = orders_count(domain, token, date_query)
    orders = list(fetch_orders(domain, token, date_query))
    return orders, expected_count
