"""All cell/column mappings, row indices, colour tables, and abbreviation maps."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from openpyxl.utils import column_index_from_string as col_idx
from common.dashboard_colors import (
    FINISH_CURATED,
    DEPT_CURATED,
    assign_colors,
    assign_finish_colors,
    assign_dept_colors,
    badge_class,
    arrow_color,
)

# ── Monthly Summary row 7 column letters ─────────────────────────────────────
MS_ROW7 = {
    'total_sales':  'F',
    'units':        'J',
    'gm_pct':       'S',
    'sell_through': 'Q',
    'weeks_cover':  'R',
    'inventory':    'P',
    'd2c_gbp':      'V',
    'b2b_gbp':      'AH',
    'uk_gbp':       'AT',
    'us_gbp':       'CD',
    'row_gbp':      'DN',
    'd2c_units':    'Z',
    'b2b_units':    'AL',
    'uk_units':     'AX',
    'us_units':     'CH',
    'row_units':    'DR',
    'd2c_gm':       'AF',
    'b2b_gm':       'AR',
    'vs_lm':        'G',
    'vs_ly':        'I',
    'units_vs_lm':  'K',
    'units_vs_ly':  'M',
    'uk_vs_lm':     'AU',
    'uk_vs_ly':     'AW',
    'us_vs_lm':     'CE',
    'us_vs_ly':     'CG',
}

# ── Last-Month block (row 7) ──────────────────────────────────────────────────
LM_BLOCK = {
    'total':   'EX', 'd2c':   'EY', 'b2b':   'EZ',
    'uk':      'FA', 'us':    'FD', 'row':   'FG',
    'total_u': 'FJ', 'd2c_u': 'FK', 'b2b_u': 'FL',
    'uk_u':    'FM', 'us_u':  'FP', 'row_u': 'FS',
}

# ── Last-Year block (row 7) ───────────────────────────────────────────────────
LY_BLOCK = {
    'total':   'GP', 'd2c':   'GQ', 'b2b':   'GR',
    'uk':      'GS', 'us':    'GV', 'row':   'GY',
    'total_u': 'HB', 'd2c_u': 'HC', 'b2b_u': 'HD',
    'uk_u':    'HE', 'us_u':  'HH', 'row_u': 'HK',
}

# ── Period header cells (row 1) ───────────────────────────────────────────────
PERIOD_CELLS = {'cm': 'C1', 'lm': 'EX1', 'ly': 'GP1'}

# ── Product-Status column mapping (rows discovered dynamically -- see
#    extract.extract_statuses; the sheet has 6 real rows -- Continuity/
#    Newness/Discontinued/Dead/Not For Sale/Pre-Launch -- not the 4
#    previously hand-listed) ───────────────────────────────────────────────────
STATUS_COLS = {
    'sales': 'F', 'units': 'J', 'vs_lq': 'G', 'vs_ly': 'I',
    'gm': 'S', 'st': 'Q', 'wc': 'R', 'inv': 'P',
}

# ── Product-Type column mapping (rows are discovered dynamically -- see
#    extract.extract_product_types; a fixed row dict silently hid whichever
#    departments weren't hand-listed, e.g. Taps/Door) ────────────────────────
TYPE_COLS = {'sales': 'F', 'units': 'J', 'vs_lq': 'G', 'gm': 'S'}

# ── Finish column mapping (rows discovered dynamically -- see
#    extract.extract_finishes; the sheet carries ~29 named finishes, not the
#    8 previously hand-curated) ───────────────────────────────────────────────
FINISH_COLS = {
    'total': 'F', 'units': 'J', 'vsLQ': 'G', 'vsLY': 'I',
    'd2c': 'V', 'b2b': 'AH', 'uk': 'AT', 'us': 'CD',
}

# Deterministic, distinct-for-arbitrary-N colour assignment (FINISH_CURATED,
# DEPT_CURATED, assign_colors/assign_finish_colors/assign_dept_colors) moved to
# common/dashboard_colors.py (2026-08-05) -- returns imports the same functions
# so both dashboards assign colours identically. Imported above.

# ── By SKU column indices (0-based) ──────────────────────────────────────────
SKU_COL = {
    'rank':      0,
    'type_':     2,
    'coll':      9,
    'sku':       14,
    'desc':      15,
    'gross':     17,
    'units':     18,
    'vslq':      19,
    'gm':        24,
    'finish':    4,
    'uk_status': 7,
    'us_status': 8,
    'st':        22,
    'wc':        23,
    'inv':       21,
    'd2c':       28,
    'b2b':       35,
    'uk':        42,
    'uk_u':      43,
    'us':        61,
    'us_u':      62,
    'lq':        82,
    'ly':        97,
}

# ── By Collection column indices (0-based) ───────────────────────────────────
COLL_COL = {
    'rank':     0,
    'type_':    1,
    'coll':     2,
    'gross':    5,
    'units':    6,
    'vs_lq':    7,
    'gm':       16,
    'st':       14,
    'wc':       15,
    'd2c':      18,
    'b2b':      25,
    'uk':       32,
    'us':       43,
    'row':      54,
    'lq_total': 64,
    'lq_uk':    67,
    'lq_us':    68,
}

# ── By Collection column indices — QUARTERLY variant ─────────────────────────
# The quarterly "By Collection" sheet drops the mid-sheet "vs LY LM" column
# (monthly col I) that isn't present in the quarterly layout, which shifts
# every field from that point on (monthly index >= 8) left by exactly one
# column. Columns 0-7 (rank..vs_lq) are unaffected. Verified cell-by-cell
# against 2026-Q2_Quarterly_Trading_Report.xlsx before use.
COLL_COL_Q = {
    'rank':     0,
    'type_':    1,
    'coll':     2,
    'gross':    5,
    'units':    6,
    'vs_lq':    7,
    'gm':       15,
    'st':       13,
    'wc':       14,
    'd2c':      17,
    'b2b':      24,
    'uk':       31,
    'us':       42,
    'row':      53,
    'lq_total': 63,
    'lq_uk':    66,
    'lq_us':    67,
}

# ── Status abbreviation map ───────────────────────────────────────────────────
STATUS_ABBREV = {
    'Continuity':  'Cont',
    'Newness':     'New',
    'Not For Sale': 'N/S',
    'Discontinued': 'Disc',
    'Dead':        'Dead',
    'Pre-Launch':  'Pre',
}

# ── Colour palette for top-10 COLL_ANALYSIS (index-matched) ──────────────────
COLL_COLORS = [
    'rgba(188, 60, 36, 0.9)',   # brick      H=15°
    'rgba(172, 96, 32, 0.9)',   # copper     H=27°
    'rgba(108,156, 20, 0.9)',   # lime       H=92°
    'rgba( 40,124, 44, 0.9)',   # forest     H=121°
    'rgba( 56,124, 80, 0.9)',   # sage       H=141°
    'rgba( 20,112,100, 0.9)',   # dark teal  H=175°
    'rgba( 28, 68,160, 0.9)',   # cobalt     H=232°
    'rgba(116, 32,148, 0.9)',   # violet     H=305°
    'rgba(148, 32,120, 0.9)',   # magenta    H=313°
    'rgba(168, 36, 72, 0.9)',   # wine       H=345°
]

# ── Formatting helpers ────────────────────────────────────────────────────────
def fmt_gbp(n):
    if n is None:
        return '—'
    if abs(n) >= 1_000_000:
        return f'£{n/1_000_000:.2f}M'
    return f'£{n/1000:.1f}K'

def fmt_pct(v, force_sign=True):
    if v is None:
        return '—'
    sign = '+' if (v >= 0 and force_sign) else ''
    return f'{sign}{v*100:.1f}%'

def fmt_inv(n):
    if n is None:
        return '—'
    return f'{round(n/1000)}K inv'
