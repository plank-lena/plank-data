"""
returns/render.py — flattens build.py's enriched frames into the row-array JSON
payloads returns/template.html's client-side JS aggregates, fills the template,
and writes a self-contained static HTML file (Option A: no external calls at
render time, no live/daily-refreshing tracker -- see ROADMAP.md / brief §6).

Client-side aggregation (not trading's server-baked {{TOKEN}}/js_block_* approach):
the 3-level drill x retail/trade x region x month combinatorics would be unwieldy
to pre-bake, and the brief tags the drill [X] (client-side interactivity). Trading
and returns share visual language (see common/embedded_fonts.css), not rendering
mechanism.

Period-agnostic (2026-08-05): takes already-loaded sales_df/ld_std/returns_df (see
build.py's loaders) plus month_nums/year, so the same renderer produces Q1, Q2, or
any other period's dashboard -- see returns/build_q1.py / build_q2.py for the two
periods currently wired up.
"""
import sys
import os
import json
import calendar

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from returns import build

TEMPLATE = os.path.join(os.path.dirname(__file__), "template.html")
FONTS_CSS = os.path.join(os.path.dirname(__file__), "..", "common", "embedded_fonts.css")
DEFAULT_REVIEWS_JSON = os.path.join(os.path.dirname(__file__), "..", "reviews", "reviews.json")

QUARTER_LABELS = {(1, 2, 3): "Q1", (4, 5, 6): "Q2", (7, 8, 9): "Q3", (10, 11, 12): "Q4"}


def _default_period_label(month_nums, year, months):
    key = tuple(sorted(month_nums))
    if key in QUARTER_LABELS:
        return f"{QUARTER_LABELS[key]} {year}"
    return f"{months[month_nums[0]]}–{months[month_nums[-1]]} {year}"


def _names(s):
    """sku -> display name (product title), for the SKU-row label."""
    d = s.dropna(subset=["name"]).drop_duplicates("sku")
    return dict(zip(d["sku"], d["name"]))


def build_cube(s, ret, shopv):
    """One row per (month, country, seg, category, subcategory, family, sku):
    units_sold, gross_sales, orders (sales side, from s) joined to units_returned /
    returned_orders (from the single-count zap join, ret) and value_returned
    (stock-linked, exchange-value netted out -- ruling 5 / §5.1, from shopv).
    """
    keys = ["m", "mkt", "seg", "category", "subcategory", "family", "sku"]
    sales = s.groupby(keys).agg(
        units_sold=("units", "sum"), gross_sales=("cash", "sum"), orders=("order", "nunique")
    )
    rkeys = ["m", "mkt", "seg", "sku"]
    returned = ret.groupby(rkeys).agg(
        units_returned=("qty", "sum"), returned_orders=("order", "nunique")
    )

    d = shopv[shopv.qty != 0]
    exch = d[d.is_exch_line].groupby(rkeys)["val"].sum()
    stock = d.groupby(rkeys)["val"].sum()
    value_returned = (stock - exch.reindex(stock.index).fillna(0)).abs()

    rows = sales.join(returned, on=rkeys).join(
        value_returned.rename("value_returned"), on=rkeys
    ).fillna({"units_returned": 0, "returned_orders": 0, "value_returned": 0})
    rows = rows.reset_index()
    cols = keys + ["units_sold", "gross_sales", "orders", "units_returned", "returned_orders", "value_returned"]
    return rows[cols].values.tolist()


def build_orders(s, ret):
    """One row per DISTINCT ORDER: [m, mkt, seg, returned(0/1)]. The headline/trend
    orders-based rate needs a true distinct-order count under any filter combo --
    summing the cube's per-SKU "orders" column would double-count an order that
    spans multiple SKUs. Per-category/subcategory/SKU order counts in the cube stay
    as they are: those are legitimately distinct-per-group and, per the existing
    convention, never summed across groups.
    """
    o = s.drop_duplicates("order")[["m", "mkt", "seg", "order"]].copy()
    returned_orders = set(ret["order"])
    o["returned"] = o["order"].isin(returned_orders).astype(int)
    return o[["m", "mkt", "seg", "returned"]].values.tolist()


def build_gross(shopv):
    """Gross sales by (month, country, seg), from the FULL sales population
    (shopv) -- matches value_split()'s own gross-sales denominator, which
    includes no-SKU lines. The cube's gross_sales is sku-attributed only
    (s excludes rows with no resolvable SKU), so it's the wrong denominator for
    the hero's "% of gross sales" -- a no-SKU line still generated real revenue.
    """
    g = shopv.groupby(["m", "mkt", "seg"])["cash"].sum().reset_index()
    return g.values.tolist()


def build_reasons(zap):
    """One row per raw returns-app row (reason_mix/reason_detail's existing basis,
    unchanged -- pre-existing behaviour, not part of the single-count join).
    """
    cols = ["m", "mkt", "seg", "category", "subcategory", "family", "sku",
            "order", "qty", "reason", "subreason", "is_exchange", "stage"]
    return zap[cols].values.tolist()


def build_value_rows(shopv):
    """Collapsed sales-side rows for the client-side stock/value-only/no-SKU
    split (ruling 5, §3) -- collapsed to (month, country, seg, sku, qty!=0,
    is_exchange-line) before flattening; line-level detail isn't needed, only the
    zero/nonzero incidence and the $ total. sku is "" for no-SKU rows.
    """
    d = shopv.copy()
    d["sku_or_blank"] = d["sku"].where(d["has_sku"], "")
    d["qty_nonzero"] = (d["qty"] != 0).astype(int)
    d["is_exch"] = d["is_exch_line"].astype(int)
    g = d.groupby(["m", "mkt", "seg", "sku_or_blank", "qty_nonzero", "is_exch"])["val"].sum()
    g = g[g != 0].reset_index()
    return g[["m", "mkt", "seg", "sku_or_blank", "qty_nonzero", "is_exch", "val"]].values.tolist()


def render(sales_df, ld_std, returns_df, month_nums=None, year=build.DEFAULT_YEAR,
           period_label=None, source_label=None, reviews_json=DEFAULT_REVIEWS_JSON,
           out_path=None):
    month_nums = list(month_nums or build.DEFAULT_MONTH_NUMS)
    months = {m: calendar.month_abbr[m] for m in month_nums}
    period_label = period_label or _default_period_label(month_nums, year, months)
    source_label = source_label or "the configured sources"

    s, ret, zap, shopv, months = build.prep(sales_df, ld_std, returns_df, month_nums, year)

    payload = {
        "cube": build_cube(s, ret, shopv),
        "orders": build_orders(s, ret),
        "reasons": build_reasons(zap),
        "valueRows": build_value_rows(shopv),
        "gross": build_gross(shopv),
        "names": _names(s),
        "months": list(months.values()),
        "periodLabel": period_label,
        "year": year,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), default=str)

    if os.path.exists(reviews_json):
        with open(reviews_json, encoding="utf-8") as fh:
            review_json = fh.read().strip()
    else:
        review_json = "null"

    with open(TEMPLATE, encoding="utf-8") as fh:
        html = fh.read()
    with open(FONTS_CSS, encoding="utf-8") as fh:
        fonts_css = fh.read()

    month_span = f"{months[month_nums[0]]}–{months[month_nums[-1]]}" if len(months) > 1 else months[month_nums[0]]
    html = html.replace("/*{{EMBEDDED_FONTS}}*/", fonts_css)
    html = html.replace("{{PAGE_TITLE}}", f"Returns Review — Plank Hardware — {period_label}")
    html = html.replace("{{SCOPE_DEFAULT}}", f"{period_label} · orders placed {month_span}")
    html = html.replace("{{SOURCE_FILENAME}}", source_label)
    html = html.replace("{{PAYLOAD_JSON}}", payload_json)
    html = html.replace("{{REVIEW_JSON}}", review_json)

    out_path = out_path or os.path.join(
        os.path.dirname(__file__), "..", "output", f"returns-{period_label.lower().replace(' ', '-')}.html"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path
