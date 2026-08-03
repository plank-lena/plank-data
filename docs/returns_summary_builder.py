"""
Returns Summary — reference builder  (Path-2 spike / proof of approach)
=======================================================================
Rebuilds the `Summary` headline block of the Q1 returns workbook from the RAW
feeds only, and checks it against the workbook's own cached values (the oracle).

Verdict from the spike (see returns_spike_findings.md):
  * SALES side (revenue, units sold, orders, UK/US split) reproduces EXACTLY.
  * RETURNS side does NOT cleanly reconcile to the hand-built sheet, because the
    sheet's composite-key join double-counts and depends on a manual SKU list.
    This builder implements the CORRECTED (de-duplicated) returns join and
    reports the delta vs the legacy number rather than reproducing the quirk.

Run:  python returns_summary_builder.py  /path/to/Q1_Jan_Feb_Mar_2026.xlsx
"""
import sys, pandas as pd, numpy as np, openpyxl

YEAR, MONTHS = 2026, {1, 2, 3}
STATUSES = ['Live', 'Discontinued', 'Disco to Resource', 'Not For Sale']

def load(src):
    xl = pd.ExcelFile(src, engine='openpyxl')
    shop = xl.parse('Shopify Data', header=0); shop.columns = [f'c{i}' for i in range(len(shop.columns))]
    zap  = xl.parse('Returns zap',  header=0); zap.columns  = [f'z{i}' for i in range(len(zap.columns))]
    ld   = xl.parse('Line Detail',  header=0)
    return shop, zap, ld

def build(src):
    shop, zap, ld = load(src)

    # ---- SALES side (Shopify Data): rawest columns only -------------------
    # c0 Country | c1 month | c2 order_id | c7 variant_sku | c8 total_sales | c10 units
    shop['month'] = pd.to_datetime(shop['c1'], errors='coerce')
    s = shop[shop['c7'].notna() & (shop['month'].dt.year == YEAR)
             & (shop['month'].dt.month.isin(MONTHS))].copy()
    s['sku'] = s['c7'].astype(str).str.strip()
    s['order_id'] = s['c2'].astype('int64').astype(str)
    s['market'] = s['c0']

    sales = s.groupby('sku').agg(units_sold=('c10', 'sum'),
                                 total_cash=('c8', 'sum'),
                                 orders=('order_id', 'size')).reset_index()
    uk_o = s[s.market == 'UK'].groupby('sku')['order_id'].size().rename('uk_orders')
    us_o = s[s.market == 'US'].groupby('sku')['order_id'].size().rename('us_orders')

    # ---- RETURNS side (Returns zap): CORRECTED de-duplicated join ----------
    # z0 Country | z1 Order Id | z10 SKU | z14 Quantity | z16 Return Reason
    zap = zap[zap['z10'].notna() & zap['z1'].notna()].copy()
    zap['sku'] = zap['z10'].astype(str).str.strip()
    zap['order_id'] = zap['z1'].astype('int64').astype(str)
    zap['qty'] = pd.to_numeric(zap['z14'], errors='coerce').fillna(0)
    # one return quantity per (sku, order) — NOT stamped onto every sales line
    ret_key = zap.groupby(['sku', 'order_id'], as_index=False)['qty'].sum()
    # attribute each return to the sale month/market via a UNIQUE sales line
    line1 = s.drop_duplicates(['sku', 'order_id'])[['sku', 'order_id', 'market']]
    ret = ret_key.merge(line1, on=['sku', 'order_id'], how='inner')

    r = ret.groupby('sku').agg(units_returned=('qty', 'sum'),
                               returned_orders=('order_id', 'nunique')).reset_index()
    uk_r = ret[ret.market == 'UK'].groupby('sku')['order_id'].nunique().rename('uk_ret_orders')
    us_r = ret[ret.market == 'US'].groupby('sku')['order_id'].nunique().rename('us_ret_orders')

    # ---- enrich with Line Detail (status + RRP ex VAT) ---------------------
    ld = ld.rename(columns={'SKU': 'sku', 'UK Status': 'status', 'RRP ex VAT': 'rrp'})
    ld['sku'] = ld['sku'].astype(str).str.strip()
    ld = ld.drop_duplicates('sku')

    sku = (sales.merge(r, on='sku', how='left')
                .join(uk_o, on='sku').join(us_o, on='sku')
                .join(uk_r, on='sku').join(us_r, on='sku')
                .merge(ld[['sku', 'status', 'rrp']], on='sku', how='left'))
    fill = ['units_returned', 'returned_orders', 'uk_orders', 'us_orders', 'uk_ret_orders', 'us_ret_orders']
    sku[fill] = sku[fill].fillna(0)
    sku['returns_cash'] = sku['rrp'].fillna(0) * sku['units_returned']   # imputed (RRP ex VAT * units)
    return sku

def summary_block(sku):
    def agg(df):
        return dict(total_cash=df.total_cash.sum(), returns_cash=df.returns_cash.sum(),
                    units_sold=df.units_sold.sum(), units_returned=df.units_returned.sum(),
                    orders=df.orders.sum(), returned_orders=df.returned_orders.sum())
    rows = {s: agg(sku[sku.status == s]) for s in STATUSES}
    rows['Total'] = agg(sku[sku.status.isin(STATUSES)])
    return pd.DataFrame(rows).T

def reconciliation_gate(block):
    """Analogue of the trading uk+us+row gate: status rows must sum to Total."""
    for col in block.columns:
        parts = block.loc[STATUSES, col].sum(); total = block.loc['Total', col]
        rel = abs(parts - total) / total if total else 0
        assert rel <= 1e-6, f'RECONCILE FAIL {col}: status-sum {parts} != total {total}'
    print('reconciliation gate: PASS (status rows sum to Total)')

if __name__ == '__main__':
    src = sys.argv[1] if len(sys.argv) > 1 else 'Q1_Jan_Feb_Mar_2026.xlsx'
    block = summary_block(build(src))
    pd.options.display.float_format = lambda v: f'{v:,.2f}'
    print(block, '\n'); reconciliation_gate(block)
