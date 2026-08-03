"""Shared spreadsheet IO helpers.

Extracted from returns_builder_v2.py's positional sheet-loading pattern so
every builder reads raw workbook tabs the same way, rather than each
reimplementing "read this sheet, rename columns positionally."
"""

import pandas as pd


def load_positional_sheets(path, sheet_prefixes):
    """Read one or more sheets from an .xlsx, renaming columns positionally.

    sheet_prefixes: dict of {sheet_name: column_prefix}, e.g.
        {"Shopify Data": "c", "Returns zap": "z", "Line Detail": "l"}
    Column 0 of a sheet with prefix "c" becomes "c0", column 1 "c1", etc.
    This matches how the source workbooks are actually addressed throughout
    the returns/trading builders (by Excel-column position, not header text,
    since header text is inconsistent/duplicated across tabs).

    Returns a dict of {sheet_name: DataFrame}, in the same key order as
    sheet_prefixes.
    """
    xl = pd.ExcelFile(path, engine="openpyxl")
    out = {}
    for sheet, prefix in sheet_prefixes.items():
        df = xl.parse(sheet, header=0)
        df.columns = [f"{prefix}{i}" for i in range(len(df.columns))]
        out[sheet] = df
    return out
