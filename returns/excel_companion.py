"""Build the returns dashboard's Excel companion -- values-only, matching the
visual format of Plank_Q1_2026_Returns_Companion_REAL.xlsx (measured off that
file; same design system as trading/excel_companion.py's, reused from
common/excel_styling.py).

Unlike trading, there's no pre-built "contract" artifact for returns to read --
this module calls build.prep() itself and aggregates the same way
render.build_cube() does, just collapsed to sku grain (no month/market/seg
breakout) since the companion reports the whole period at once.

Real improvement over the reference file, not just a reformat: the reference
file's ROW figures are all "PENDING" -- Q1 2026's source (the old workbook) had
no ship-to country field at all. Since 2026-08-13, returns/build.py derives mkt
from Shipping: Country Code the same way trading does, so ROW is real data here,
not a placeholder. The Reconciliation & Data Quality tab says so explicitly
rather than silently dropping the caveat.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from openpyxl import Workbook
from returns import build
from common.excel_styling import (
    title_row, subtitle_row, section_header, table_header, data_row, total_row,
    kpi_block, note_row, FMT_CURRENCY, FMT_PERCENT, FMT_INT,
)


def _sku_aggregate(s, ret, shopv):
    """Per-SKU totals for the whole period (no month/market/seg breakout) --
    same fields render.build_cube() computes, collapsed to sku grain, plus
    finish/department/family carried from s's own per-sku taxonomy (assumed
    1:1 with sku, same assumption build_cube() makes)."""
    sales_agg = s.groupby("sku").agg(units_sold=("units", "sum"), gross_sales=("cash", "sum"),
                                       orders=("order", "nunique"))
    ret_agg = ret.groupby("sku").agg(units_returned=("qty", "sum"), returned_orders=("order", "nunique"))
    d = shopv[shopv.qty != 0]
    exch = d[d.is_exch_line].groupby("sku")["val"].sum()
    stock = d.groupby("sku")["val"].sum()
    value_returned = stock - exch.reindex(stock.index).fillna(0)
    taxonomy = s.drop_duplicates("sku").set_index("sku")[["finish", "department", "family", "status"]]

    agg = (sales_agg.join(ret_agg, how="left")
                     .join(value_returned.rename("value_returned"), how="left")
                     .join(taxonomy))
    agg = agg.fillna({"units_returned": 0, "returned_orders": 0, "value_returned": 0})
    agg["return_rate"] = (agg["returned_orders"] / agg["orders"]).where(agg["orders"] > 0, 0)
    return agg.reset_index()


def _build_overview(wb, headline, by_market, period_label, source_note):
    ws = wb.active
    ws.title = "Overview"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 16

    title_row(ws, 1, f"{period_label} - Returns Report (Companion)", 8)
    subtitle_row(ws, 2, "REAL DATA - single-count  |  orders-based rate  |  ex-VAT", 8)
    note_row(ws, 3, source_note, 8)

    section_header(ws, 5, f"{period_label} Headline", 8)
    kpi_block(ws, 6, "Return Rate (orders-based)", f"{headline['order_rate']:.1%}",
               f"{headline['returned_orders']:,.0f} of {headline['orders']:,.0f} orders", 1)
    kpi_block(ws, 6, "Returns Value", f"£{headline['value_returned']:,.0f}", "ex-VAT", 3)
    kpi_block(ws, 6, "Return Rate (units)", f"{headline['unit_rate']:.1%}",
               f"{headline['units_returned']:,.0f} of {headline['units_sold']:,.0f} units", 5)
    kpi_block(ws, 6, "Returned Orders", f"{headline['returned_orders']:,.0f}", "single-count", 7)

    uk, us, row = by_market.loc["UK"], by_market.loc["US"], by_market.loc["ROW"]
    kpi_block(ws, 10, "\U0001F1EC\U0001F1E7 UK Orders Returned", f"{uk['return_rate']:.1%}",
               f"{uk['returned_orders']:,.0f} of {uk['orders']:,.0f}", 1)
    kpi_block(ws, 10, "\U0001F1FA\U0001F1F8 US Orders Returned", f"{us['return_rate']:.1%}",
               f"{us['returned_orders']:,.0f} of {us['orders']:,.0f}", 3)
    kpi_block(ws, 10, "\U0001F30D ROW Orders Returned",
               f"{row['return_rate']:.1%}" if row["orders"] else "n/a",
               f"{row['returned_orders']:,.0f} of {row['orders']:,.0f}" if row["orders"] else "no ROW orders this period", 5)
    kpi_block(ws, 10, "Units Returned", f"{headline['units_returned']:,.0f}", "single-count", 7)

    section_header(ws, 15, "Returns by Country - ORDERS  (reconciliation key)", 4)
    table_header(ws, 16, ["Country", "Orders", "Orders returned", "% returned"])
    r = 17
    names = {"UK": "United Kingdom", "US": "United States", "ROW": "Rest of World (ROW)"}
    for k in ["UK", "US", "ROW"]:
        row_data = by_market.loc[k]
        data_row(ws, r, [names[k], row_data["orders"], row_data["returned_orders"], row_data["return_rate"]],
                  [None, FMT_INT, FMT_INT, FMT_PERCENT])
        r += 1
    tot = by_market.loc["Total"]
    total_row(ws, r, ["TOTAL", tot["orders"], tot["returned_orders"], tot["return_rate"]],
               [None, FMT_INT, FMT_INT, FMT_PERCENT])
    r += 1
    note_row(ws, r, "Country here is ship-to (Shipping: Country Code), the same derivation trading "
                     "uses for ROW -- real data, not a placeholder (see Reconciliation tab for the "
                     "one caveat: ROW's own overlap isn't asserted, since ReturnZap's Country field "
                     "is GB/US only).", 4)


def _style_drill_level(ws, row, level, ncols=6):
    from common.excel_styling import DRILL_FILLS, DRILL_INDENT, DRILL_BOLD
    from openpyxl.styles import Alignment, PatternFill, Font
    fill = PatternFill("solid", fgColor=DRILL_FILLS[level])
    font_color = "FFFFFF" if level == 0 else "1F2933"
    for col in range(1, ncols + 1):
        c = ws.cell(row=row, column=col)
        c.font = Font(name="Arial", size=10, bold=DRILL_BOLD[level], color=font_color)
        c.fill = fill
        if col == 1:
            c.alignment = Alignment(horizontal="left", vertical="center", indent=DRILL_INDENT[level])
        else:
            c.alignment = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[row].outline_level = level
    if level == 3:
        ws.row_dimensions[row].hidden = True


def _build_drill_down(wb, sku_agg, headline, period_label):
    ws = wb.create_sheet("Returns Drill-Down")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.outlinePr.summaryBelow = False
    for col, w in zip("ABCDEF", [34, 20, 15, 14, 14, 12]):
        ws.column_dimensions[col].width = w

    title_row(ws, 1, f"{period_label} - Returns Drill-Down", 6)
    subtitle_row(ws, 2, "Total -> Product Type -> Collection -> SKU (returns cash)  |  click +/- "
                         "to expand  |  ex-VAT", 6)
    note_row(ws, 3, "SKUs collapsed by default - use the outline +/- to drill in. Children sum "
                     "to parents.", 6)

    headers = ["Product / Collection / SKU", "Type / Finish", "Returns cash",
               "Units returned", "Returned orders", "Return rate"]
    table_header(ws, 5, headers)
    fmts = [None, None, FMT_CURRENCY, FMT_INT, FMT_INT, FMT_PERCENT]

    r = 6
    data_row(ws, r, ["TOTAL", "", headline["value_returned"], headline["units_returned"],
                      headline["returned_orders"], headline["order_rate"]], fmts)
    _style_drill_level(ws, r, 0)
    r += 1

    by_dept = sku_agg.groupby("department").agg(
        value_returned=("value_returned", "sum"), units_returned=("units_returned", "sum"),
        returned_orders=("returned_orders", "sum"), orders=("orders", "sum"), n_families=("family", "nunique"))
    for dept, drow in by_dept.sort_values("value_returned", ascending=False).iterrows():
        drate = drow["returned_orders"] / drow["orders"] if drow["orders"] else 0
        data_row(ws, r, [dept or "(uncategorised)", f"{int(drow['n_families'])} collections",
                          drow["value_returned"], drow["units_returned"], drow["returned_orders"], drate], fmts)
        _style_drill_level(ws, r, 1)
        r += 1

        fam_group = sku_agg[sku_agg["department"] == dept].groupby("family").agg(
            value_returned=("value_returned", "sum"), units_returned=("units_returned", "sum"),
            returned_orders=("returned_orders", "sum"), orders=("orders", "sum"))
        for fam, frow in fam_group.sort_values("value_returned", ascending=False).iterrows():
            frate = frow["returned_orders"] / frow["orders"] if frow["orders"] else 0
            data_row(ws, r, [fam or "(uncategorised)", dept, frow["value_returned"], frow["units_returned"],
                              frow["returned_orders"], frate], fmts)
            _style_drill_level(ws, r, 2)
            r += 1

            skus = sku_agg[(sku_agg["department"] == dept) & (sku_agg["family"] == fam)]
            for _, srow in skus.sort_values("value_returned", ascending=False).iterrows():
                data_row(ws, r, [srow["sku"], srow["finish"], srow["value_returned"], srow["units_returned"],
                                  srow["returned_orders"], srow["return_rate"]], fmts)
                _style_drill_level(ws, r, 3)
                r += 1

    ws.freeze_panes = "A6"
    ws.sheet_format.outlineLevelRow = 3


def _build_detail(wb, reason_mix_df, by_status, by_finish, period_label):
    ws = wb.create_sheet("Detail")
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDE", [26, 16, 14, 12, 12]):
        ws.column_dimensions[col].width = w

    title_row(ws, 1, f"{period_label} - Returns Detail", 5)
    subtitle_row(ws, 2, "by reason / by status / by finish  |  ex-VAT", 5)

    r = 4
    section_header(ws, r, "By Return Reason (unit counts)", 3); r += 1
    table_header(ws, r, ["Reason", "Returned units", "Share"]); r += 1
    total_units = reason_mix_df["units_returned"].sum()
    for reason, row_data in reason_mix_df.iterrows():
        data_row(ws, r, [reason, row_data["units_returned"], row_data["share"]],
                  [None, FMT_INT, FMT_PERCENT])
        r += 1
    total_row(ws, r, ["TOTAL", total_units, 1.0], [None, FMT_INT, FMT_PERCENT]); r += 2

    section_header(ws, r, "By Product Status (returns cash & orders)", 5); r += 1
    table_header(ws, r, ["Status", "Returns cash", "Units returned", "Returned orders", "% orders"]); r += 1
    fmts = [None, FMT_CURRENCY, FMT_INT, FMT_INT, FMT_PERCENT]
    for status, row_data in by_status.iterrows():
        if status == "Total":
            continue
        data_row(ws, r, [status, row_data.get("value_returned", 0), row_data["units_returned"],
                          row_data["returned_orders"], row_data["return_rate"]], fmts)
        r += 1
    tot = by_status.loc["Total"]
    total_row(ws, r, ["TOTAL", tot.get("value_returned", 0), tot["units_returned"],
                       tot["returned_orders"], tot["return_rate"]], fmts)
    r += 2

    section_header(ws, r, "By Finish (returns cash & rate)", 5); r += 1
    table_header(ws, r, ["Finish", "Returns cash", "Units returned", "Returned orders", "% orders"]); r += 1
    for finish, row_data in by_finish.iterrows():
        if finish == "Total":
            continue
        data_row(ws, r, [finish, row_data.get("value_returned", 0), row_data["units_returned"],
                          row_data["returned_orders"], row_data["return_rate"]], fmts)
        r += 1


def _build_by_sku(wb, sku_agg, period_label):
    ws = wb.create_sheet("By SKU")
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDEFGH", [8, 20, 15, 18, 15, 14, 14, 12]):
        ws.column_dimensions[col].width = w

    title_row(ws, 1, f"{period_label} - Returns by SKU (full)", 8)
    subtitle_row(ws, 2, "ranked by returns cash  |  ex-VAT  |  filterable", 8)
    table_header(ws, 3, ["Rank", "SKU", "Type", "Collection", "Finish",
                          "Returns cash", "Units returned", "Return rate"])
    fmts = [FMT_INT, None, None, None, None, FMT_CURRENCY, FMT_INT, FMT_PERCENT]

    ranked = sku_agg.sort_values("value_returned", ascending=False)
    r = 4
    for i, (_, row_data) in enumerate(ranked.iterrows(), start=1):
        data_row(ws, r, [i, row_data["sku"], row_data["department"], row_data["family"], row_data["finish"],
                          row_data["value_returned"], row_data["units_returned"], row_data["return_rate"]], fmts)
        r += 1
    ws.freeze_panes = "A4"


def _build_reconciliation(wb, headline, by_market, period_label):
    ws = wb.create_sheet("Reconciliation & Data Quality")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 50

    title_row(ws, 1, f"{period_label} - Returns Reconciliation & Data Quality", 3)
    subtitle_row(ws, 2, "what reconciles, what does not, and why", 3)
    note_row(ws, 3, "This tab is deliberately loud about gaps - the reconciliation contract requires it.", 3)

    r = 5
    section_header(ws, r, "ROW bucket", 3); r += 1
    row_data = by_market.loc["ROW"]
    data_row(ws, r, ["ROW orders", int(row_data["orders"]),
                      "real data (ship-to country) -- resolved 2026-08-13, was PENDING in the old source"])
    r += 1
    data_row(ws, r, ["ROW returned orders", int(row_data["returned_orders"]),
                      "overlap NOT asserted for ROW: ReturnZap's own Country field is GB/US only, "
                      "so a ROW return can never be confirmed against it -- see build.py's prep()"])
    r += 1
    note_row(ws, r, "RESOLVED vs. the old source: country reconciliation now uses real ship-to data "
                     "for sales; the one remaining caveat is the overlap check, not the country split "
                     "itself.", 3)
    r += 2

    section_header(ws, r, "Headline consistency", 3); r += 1
    table_header(ws, r, ["Source", "Returns cash", "Note"]); r += 1
    data_row(ws, r, ["Pipeline headline (this companion)", headline["value_returned"],
                      "single source (ReturnZap sheet, deduped) -- no cross-view drift possible, "
                      "unlike the old multi-workbook source"], [None, FMT_CURRENCY, None])
    r += 2

    section_header(ws, r, "Notes", 3); r += 1
    notes = [
        "Source: the same ReturnZap Drive sheet + rolling Matrixify snapshot the HTML dashboard "
        "itself uses (returns/build.py's prep()) -- not a separate re-derivation.",
        "Single-count and orders-based headline rate, per the locked returns methodology.",
        "Returns reported separately, never netted into revenue.",
        "Values-only (no formulas).",
    ]
    for n in notes:
        note_row(ws, r, f"\u2022  {n}", 3)
        r += 1


def build_returns_companion(out_path, period_label, sales_df, ld_std, returns_df, month_nums, year, source_note=""):
    s, ret, zap, shopv, months = build.prep(sales_df, ld_std, returns_df, month_nums, year)

    by_market = build.by_group(s, ret, "mkt", ["UK", "US", "ROW"])
    fins = s["finish"].value_counts().index.tolist()
    by_finish = build.by_group(s, ret, "finish", fins)
    from returns.build import STAT
    by_status = build.by_group(s, ret, "status", STAT)
    reason = build.reason_mix(zap)
    vsplit = build.value_split(shopv)

    sku_agg = _sku_aggregate(s, ret, shopv)
    # attach value_returned onto the by_group blocks (by_group itself doesn't carry $ value)
    for block, col in [(by_finish, "finish"), (by_status, "status")]:
        vmap = sku_agg.groupby(col)["value_returned"].sum()
        block["value_returned"] = block.index.map(vmap).fillna(0)

    tot = by_market.loc["Total"]
    headline = {
        "orders": tot["orders"], "returned_orders": tot["returned_orders"],
        "order_rate": tot["return_rate"], "units_sold": tot["units_sold"],
        "units_returned": tot["units_returned"],
        "unit_rate": tot["units_returned"] / tot["units_sold"] if tot["units_sold"] else 0,
        "value_returned": vsplit["stock_value"],
    }

    wb = Workbook()
    _build_overview(wb, headline, by_market, period_label,
                     source_note or f"Source: {period_label} committed returns build. Values-only.")
    _build_drill_down(wb, sku_agg, headline, period_label)
    _build_detail(wb, reason, by_status, by_finish, period_label)
    _build_by_sku(wb, sku_agg, period_label)
    _build_reconciliation(wb, headline, by_market, period_label)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    wb.save(out_path)
    return out_path
