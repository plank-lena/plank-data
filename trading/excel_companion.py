"""Build the trading dashboard's Excel companion -- values-only, matching the
visual format of Plank_Q2_2026_Trade_Companion_REAL.xlsx (measured directly off
that file, see common/excel_styling.py). Called automatically from
build_matrixify_dashboard.py (monthly) and build_matrixify_quarterly_dashboard.py
(quarterly) -- not a separate manual step.

Generalized over period count so the same code serves both:
  - monthly:   constituent_contracts = [(period_label, current_contract)]  (len 1)
  - quarterly: constituent_contracts = [("Apr 2026", c1), ("May 2026", c2), ("Jun 2026", c3)]

current_contract is always the contract for the FULL reported period (the month's
own contract for monthly; the quarterly aggregate contract for quarterly) -- the
source for Summary/Drill-Down/By Collection/By SKU/Cuts. constituent_contracts
drives the By Period and Reconciliation tabs' per-period breakdown; with exactly
one entry, the redundant explicit total row is skipped (it would just repeat the
single period's own numbers).

Fixed 2026-08-13: `finishes` used to carry no per-finish GM or ROW (unlike
Product Type/Collection/SKU, which always did) -- GM was actually being
accumulated internally already (for the Finish Analysis GM% sidebar) but never
exposed; ROW wasn't tracked at all. Both now real, in contract.py's
emit_contract_from_matrixify -- see its finish_totals accumulation.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openpyxl import Workbook
from common.excel_styling import (
    title_row, subtitle_row, section_header, table_header, data_row, total_row,
    kpi_block, note_row, FMT_CURRENCY, FMT_PERCENT, FMT_PERCENT_SIGNED, FMT_INT,
)


def _build_summary(wb, cc, period_label, period_note):
    ws = wb.active
    ws.title = "Summary"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 16

    cur = cc["current"]
    total = cur["total_sales"]
    uk, us, row = cur["uk_gbp"], cur["us_gbp"], cur["row_gbp"]
    d2c, b2b = cur["d2c_gbp"], cur["b2b_gbp"]
    n_collections = sum(1 for c in cc["collections"] if c.get("ts", 0) > 0)

    title_row(ws, 1, f"{period_label} - Trading Report (Companion)", 8)
    subtitle_row(ws, 2, "REAL DATA - pipeline contract  |  ex-VAT  |  drill-down on 'Trade Drill-Down'", 8)
    note_row(ws, 3, f"Source: {period_label} committed trading contract(s). Values-only (no formulas).", 8)

    section_header(ws, 5, f"{period_label} Headline KPIs", 8)
    kpi_block(ws, 6, "Total Revenue", f"£{total:,.0f}", period_note, 1)
    kpi_block(ws, 6, "Units Sold", f"{cur['units']:,.0f}", f"{len(cc['skus_all'])} SKUs", 3)
    kpi_block(ws, 6, "Gross Margin", f"{cur['gm_pct']:.1%}", "revenue-weighted", 5)
    d2c_share = d2c / (d2c + b2b) if (d2c + b2b) else 0
    kpi_block(ws, 6, "D2C Share (of UK+US)", f"{d2c_share:.1%}", "channel excludes ROW", 7)

    kpi_block(ws, 10, "\U0001F1EC\U0001F1E7 UK Revenue", f"£{uk:,.0f}", f"{uk/total:.1%} of total" if total else "-", 1)
    kpi_block(ws, 10, "\U0001F1FA\U0001F1F8 US Revenue", f"£{us:,.0f}", f"{us/total:.1%} of total" if total else "-", 3)
    kpi_block(ws, 10, "\U0001F30D ROW Revenue", f"£{row:,.0f}", f"{row/total:.1%} of total" if total else "-", 5)
    kpi_block(ws, 10, "Collections", str(n_collections), "with sales this period", 7)

    section_header(ws, 15, "Revenue by Country  (reconciliation key)", 3)
    table_header(ws, 16, ["Country", "Revenue (ex-VAT)", "Share"])
    rows = [("United Kingdom", uk), ("United States", us), ("Rest of World (ROW)", row)]
    r = 17
    for name, val in rows:
        data_row(ws, r, [name, val, val / total if total else 0], [None, FMT_CURRENCY, FMT_PERCENT])
        r += 1
    total_row(ws, r, ["TOTAL", total, 1.0], [None, FMT_CURRENCY, FMT_PERCENT])
    r += 1
    note_row(ws, r, "Country is the reconciliation key. UK + US + ROW = Total. "
                     "ROW is never derived from the channel split.", 3)
    r += 2

    section_header(ws, r, "Revenue by Channel  (NOT a reconciliation key - excludes ROW)", 3)
    r += 1
    table_header(ws, r, ["Channel", "Revenue (ex-VAT)", "Share of UK+US"])
    r += 1
    uk_us = uk + us
    data_row(ws, r, ["D2C", d2c, d2c / uk_us if uk_us else 0], [None, FMT_CURRENCY, FMT_PERCENT])
    r += 1
    data_row(ws, r, ["B2B", b2b, b2b / uk_us if uk_us else 0], [None, FMT_CURRENCY, FMT_PERCENT])
    r += 1
    total_row(ws, r, ["UK + US subtotal", uk_us, 1.0], [None, FMT_CURRENCY, FMT_PERCENT])
    r += 1
    note_row(ws, r, f"Channel excludes ROW (£{row:,.0f}), which is carried inconsistently across "
                     f"channels. Never reconcile the headline from the channel split.", 3)


def _build_by_period(wb, constituent_contracts, cc, period_label):
    multi = len(constituent_contracts) > 1
    ws = wb.create_sheet("By Period" if not multi else "By Month")
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDEFGHIJK", [16, 15, 12, 11, 13, 13, 12, 13, 12, 11, 11]):
        ws.column_dimensions[col].width = w

    title_row(ws, 1, f"{period_label} - " + ("Monthly Trajectory" if multi else "Period Detail"), 11)
    subtitle_row(ws, 2, "from the committed trading contract(s)  |  ex-VAT  |  vs LM / vs LY as reported", 11)

    headers = ["Period", "Revenue", "Units", "GM", "UK", "US", "ROW", "D2C", "B2B", "vs LM", "vs LY"]
    table_header(ws, 3, headers)
    fmts = [None, FMT_CURRENCY, FMT_INT, FMT_PERCENT, FMT_CURRENCY, FMT_CURRENCY,
            FMT_CURRENCY, FMT_CURRENCY, FMT_CURRENCY, FMT_PERCENT_SIGNED, FMT_PERCENT_SIGNED]

    r = 4
    for label, c in constituent_contracts:
        cur = c["current"]
        data_row(ws, r, [
            label, cur["total_sales"], cur["units"], cur["gm_pct"],
            cur["uk_gbp"], cur["us_gbp"], cur["row_gbp"], cur["d2c_gbp"], cur["b2b_gbp"],
            cur.get("vs_lm"), cur.get("vs_ly"),
        ], fmts)
        r += 1

    if multi:
        qcur = cc["current"]
        total_row(ws, r, [
            f"{period_label} TOTAL", qcur["total_sales"], qcur["units"], qcur["gm_pct"],
            qcur["uk_gbp"], qcur["us_gbp"], qcur["row_gbp"], qcur["d2c_gbp"], qcur["b2b_gbp"],
            None, None,
        ], fmts)
        r += 1
        note_row(ws, r, "vs LM / vs LY are month-on-month figures from each source contract; a "
                         "quarter-on-quarter comparative is not derivable at this grain, so it is omitted.", 11)


def _style_drill_level(ws, row, level, ncols=10):
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


def _build_drill_down(wb, cc, period_label):
    ws = wb.create_sheet("Trade Drill-Down")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.outlinePr.summaryBelow = False
    for col, w in zip("ABCDEFGHIJ", [34, 20, 15, 12, 10, 13, 13, 12, 13, 13]):
        ws.column_dimensions[col].width = w

    title_row(ws, 1, f"{period_label} - Trade Drill-Down", 10)
    subtitle_row(ws, 2, "Total -> Product Type -> Collection -> SKU  |  click the +/- outline "
                         "controls to expand  |  ex-VAT", 10)
    note_row(ws, 3, "SKUs collapsed by default - use the outline +/- to drill in. Children sum "
                     "to parents; UK+US+ROW = revenue at every level.", 10)

    headers = ["Product / Collection / SKU", "Type / Finish", "Revenue", "Units", "GM",
               "UK", "US", "ROW", "D2C", "B2B"]
    table_header(ws, 5, headers)
    fmts = [None, None, FMT_CURRENCY, FMT_INT, FMT_PERCENT, FMT_CURRENCY, FMT_CURRENCY,
            FMT_CURRENCY, FMT_CURRENCY, FMT_CURRENCY]

    cur = cc["current"]
    r = 6
    data_row(ws, r, ["TOTAL", "", cur["total_sales"], cur["units"], cur["gm_pct"],
                      cur["uk_gbp"], cur["us_gbp"], cur["row_gbp"], cur["d2c_gbp"], cur["b2b_gbp"]], fmts)
    _style_drill_level(ws, r, 0)
    r += 1

    skus_by_coll = {}
    for s in cc["skus_all"]:
        skus_by_coll.setdefault((s["type_"], s["coll"]), []).append(s)
    colls_by_type = {}
    for c in cc["collections"]:
        colls_by_type.setdefault(c["t"], []).append(c)

    for pt in sorted(cc["prod_types"], key=lambda x: -x["sales"]):
        data_row(ws, r, [pt["t"], f"{len(colls_by_type.get(pt['t'], []))} collections",
                          pt["sales"], pt["units"], pt["gm"], None, None, None, None, None], fmts)
        _style_drill_level(ws, r, 1)
        r += 1
        for coll in sorted(colls_by_type.get(pt["t"], []), key=lambda x: -x["ts"]):
            data_row(ws, r, [coll["c"], pt["t"], coll["ts"], coll["tu"], coll.get("gm"),
                              coll["uk_s"], coll["us_s"], coll["row_s"], coll["d2c"], coll["b2b"]], fmts)
            _style_drill_level(ws, r, 2)
            r += 1
            for s in sorted(skus_by_coll.get((pt["t"], coll["c"]), []), key=lambda x: -(x.get("gross") or 0)):
                data_row(ws, r, [s["sku"], s["finish"], s["gross"], s["units"], s["gm"],
                                  s["uk"], s["us"], (s["gross"] or 0) - (s["uk"] or 0) - (s["us"] or 0),
                                  s["d2c"], s["b2b"]], fmts)
                _style_drill_level(ws, r, 3)
                r += 1

    ws.freeze_panes = "A6"
    ws.sheet_format.outlineLevelRow = 3


def _build_by_collection(wb, cc, period_label):
    ws = wb.create_sheet("By Collection")
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDEFGHIJ", [8, 20, 14, 14, 10, 12, 12, 12, 12, 12]):
        ws.column_dimensions[col].width = w

    title_row(ws, 1, f"{period_label} - By Collection (all)", 10)
    subtitle_row(ws, 2, "ranked by revenue  |  ex-VAT  |  filterable", 10)
    table_header(ws, 3, ["Rank", "Collection", "Type", "Revenue", "Units", "GM", "UK", "US", "ROW", "D2C"])
    fmts = [FMT_INT, None, None, FMT_CURRENCY, FMT_INT, FMT_PERCENT,
            FMT_CURRENCY, FMT_CURRENCY, FMT_CURRENCY, FMT_CURRENCY]

    r = 4
    for i, c in enumerate(sorted(cc["collections"], key=lambda c: -c["ts"]), start=1):
        data_row(ws, r, [i, c["c"], c["t"], c["ts"], c["tu"], c.get("gm"),
                          c["uk_s"], c["us_s"], c["row_s"], c["d2c"]], fmts)
        r += 1
    ws.freeze_panes = "A4"


def _build_by_sku(wb, cc, period_label):
    ws = wb.create_sheet("By SKU")
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDEFGHIJKL", [8, 20, 30, 14, 12, 13, 11, 12, 10, 11, 11, 11]):
        ws.column_dimensions[col].width = w

    title_row(ws, 1, f"{period_label} - By SKU (full population)", 12)
    subtitle_row(ws, 2, "ranked by revenue  |  ex-VAT  |  filterable", 12)
    table_header(ws, 3, ["Rank", "SKU", "Description", "Collection", "Type", "Finish",
                          "UK status", "Revenue", "Units", "GM", "UK", "US"])
    fmts = [FMT_INT, None, None, None, None, None, None,
            FMT_CURRENCY, FMT_INT, FMT_PERCENT, FMT_CURRENCY, FMT_CURRENCY]

    r = 4
    for i, s in enumerate(sorted(cc["skus_all"], key=lambda s: -(s.get("gross") or 0)), start=1):
        data_row(ws, r, [i, s["sku"], s["desc"], s["coll"], s["type_"], s["finish"],
                          s.get("uk_status"), s["gross"], s["units"], s["gm"], s["uk"], s["us"]], fmts)
        r += 1
    ws.freeze_panes = "A4"


def _build_cuts(wb, cc, period_label):
    ws = wb.create_sheet("Cuts")
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDEFG", [24, 16, 14, 12, 14, 14, 14]):
        ws.column_dimensions[col].width = w

    title_row(ws, 1, f"{period_label} - Dimensional Cuts", 7)
    subtitle_row(ws, 2, "product status / product type / finish  |  ex-VAT", 7)
    cur = cc["current"]
    fmts = [None, FMT_CURRENCY, FMT_INT, FMT_PERCENT, FMT_CURRENCY, FMT_CURRENCY, FMT_CURRENCY]

    def cut_table(start_row, title, col_label, rows, key_name, sales_key, units_key, gm_key, uk_key, us_key, row_key):
        r = start_row
        section_header(ws, r, title, 7)
        r += 1
        table_header(ws, r, [col_label, "Revenue", "Units", "GM", "UK", "US", "ROW"])
        r += 1
        for item in rows:
            data_row(ws, r, [item[key_name], item[sales_key], item[units_key],
                              item.get(gm_key) if gm_key else None,
                              item.get(uk_key) if uk_key else None,
                              item.get(us_key) if us_key else None,
                              item.get(row_key) if row_key else None], fmts)
            r += 1
        total_row(ws, r, ["TOTAL", cur["total_sales"], cur["units"], cur["gm_pct"],
                           cur["uk_gbp"], cur["us_gbp"], cur["row_gbp"]], fmts)
        return r + 2

    r = 4
    r = cut_table(r, "By Product Status (SKU UK status)", "Status",
                  sorted(cc["statuses"], key=lambda x: -x["sales"]),
                  "s", "sales", "units", "gm", None, None, None)
    r = cut_table(r, "By Product Type", "Product type",
                  sorted(cc["prod_types"], key=lambda x: -x["sales"]),
                  "t", "sales", "units", "gm", None, None, None)
    finishes_rows = [{"name": k, **v} for k, v in
                      sorted(cc["finishes"].items(), key=lambda kv: -kv[1]["total"])]
    r = cut_table(r, "By Finish", "Finish", finishes_rows,
                  "name", "total", "units", "gm", "uk", "us", "row")


def _build_reconciliation(wb, constituent_contracts, cc, period_label):
    multi = len(constituent_contracts) > 1
    ws = wb.create_sheet("Reconciliation")
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDE", [26, 18, 18, 14, 14]):
        ws.column_dimensions[col].width = w

    title_row(ws, 1, f"{period_label} - Trade Reconciliation", 5)
    subtitle_row(ws, 2, "uk + us + row -> total, per period  |  ex-VAT", 5)

    section_header(ws, 4, "Country reconciliation by period", 5)
    table_header(ws, 5, ["Period", "UK", "US", "ROW", "Diff vs total"])
    fmts = [None, FMT_CURRENCY, FMT_CURRENCY, FMT_CURRENCY, FMT_CURRENCY]
    r = 6
    for label, c in constituent_contracts:
        cur = c["current"]
        diff = (cur["uk_gbp"] + cur["us_gbp"] + cur["row_gbp"]) - cur["total_sales"]
        data_row(ws, r, [label, cur["uk_gbp"], cur["us_gbp"], cur["row_gbp"], diff], fmts)
        r += 1
    if multi:
        qcur = cc["current"]
        qdiff = (qcur["uk_gbp"] + qcur["us_gbp"] + qcur["row_gbp"]) - qcur["total_sales"]
        data_row(ws, r, [period_label, qcur["uk_gbp"], qcur["us_gbp"], qcur["row_gbp"], qdiff], fmts)
        r += 1
    r += 1

    qcur = cc["current"]
    qsum = qcur["uk_gbp"] + qcur["us_gbp"] + qcur["row_gbp"]
    rel_diff = abs(qsum - qcur["total_sales"]) / qcur["total_sales"] if qcur["total_sales"] else 0
    passed = rel_diff <= 0.001 and qcur["row_gbp"] != 0
    data_row(ws, r, [f"{period_label} sum (uk+us+row)", qsum], [None, FMT_CURRENCY]); r += 1
    data_row(ws, r, [f"{period_label} headline total", qcur["total_sales"]], [None, FMT_CURRENCY]); r += 1
    data_row(ws, r, ["Relative difference", rel_diff], [None, FMT_PERCENT]); r += 1
    data_row(ws, r, ["Tolerance", 0.001], [None, FMT_PERCENT]); r += 1
    data_row(ws, r, ["ROW bucket present", f"YES (£{qcur['row_gbp']:,.0f})" if qcur["row_gbp"] else "NO"]); r += 1
    note_row(ws, r, ("PASS" if passed else "FAIL") +
             " - uk+us+row reconciles to total within 0.1%; live ROW bucket", 5)
    r += 2

    section_header(ws, r, "Notes", 5); r += 1
    notes = [
        "Source: the committed trading contract(s) (trading/contracts/*.json) -- the pipeline's "
        "own reconciliation-gated output, not re-derived here.",
        f"{period_label} = " + ("sum of its constituent months. " if multi else "a single period. ") +
        "SKU-level ROW = gross - UK - US (ties to the contract's own ROW figure).",
        "Country is the reconciliation key, never channel. All ex-VAT; returns reported separately.",
        "Values-only (no formulas).",
    ]
    for n in notes:
        note_row(ws, r, f"\u2022  {n}", 5)
        r += 1


def build_companion(out_path, period_label, current_contract, constituent_contracts, period_note=""):
    """The one function callers need. See module docstring for the two calling
    shapes (monthly: 1 constituent; quarterly: 3).
    """
    wb = Workbook()
    _build_summary(wb, current_contract, period_label, period_note)
    _build_by_period(wb, constituent_contracts, current_contract, period_label)
    _build_drill_down(wb, current_contract, period_label)
    _build_by_collection(wb, current_contract, period_label)
    _build_by_sku(wb, current_contract, period_label)
    _build_cuts(wb, current_contract, period_label)
    _build_reconciliation(wb, constituent_contracts, current_contract, period_label)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    wb.save(out_path)
    return out_path
