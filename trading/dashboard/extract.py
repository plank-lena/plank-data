"""Extract all data from the monthly trading xlsx into structured dicts."""

import re
from pathlib import Path

import openpyxl

from config import (
    MS_ROW7, LM_BLOCK, LY_BLOCK, PERIOD_CELLS,
    STATUS_ROWS, STATUS_COLS, TYPE_ROWS, TYPE_COLS,
    FINISH_ROWS, FINISH_COLS, COLL_COL, COLL_COL_Q, SKU_COL,
)


# ── Period model ──────────────────────────────────────────────────────────────

def _parse_period(value):
    """Parse 'May - 2026' → {label:'May 2026', short:"May '26"}."""
    m = re.match(r'(\w+)\s*-\s*(\d{4})', str(value).strip())
    if not m:
        raise ValueError(f'Cannot parse period cell: {value!r}')
    month, year = m.group(1), m.group(2)
    return {'label': f'{month} {year}', 'short': f"{month[:3]} '{year[2:]}"}


def extract_period_model(ws):
    """Return period model dict with keys cm/lm/ly each having label+short."""
    pm = {}
    for key, cell in PERIOD_CELLS.items():
        pm[key] = _parse_period(ws[cell].value)
    return pm


# ── Monthly Summary headline (row 7) ─────────────────────────────────────────

def _cell(ws, col_letter, row=7):
    return ws[f'{col_letter}{row}'].value


def extract_headline(ws):
    """Return dict of all row-7 values from Monthly Summary."""
    h = {k: _cell(ws, col) for k, col in MS_ROW7.items()}
    lm = {k: _cell(ws, col) for k, col in LM_BLOCK.items()}
    ly = {k: _cell(ws, col) for k, col in LY_BLOCK.items()}
    return {'current': h, 'lm': lm, 'ly': ly}


# ── Product Status rows ───────────────────────────────────────────────────────

def extract_statuses(ws):
    rows = []
    for name, row in STATUS_ROWS.items():
        r = {'s': name}
        for field, col in STATUS_COLS.items():
            r[field] = ws[f'{col}{row}'].value
        rows.append(r)
    return rows


# ── Product Type rows ─────────────────────────────────────────────────────────

def extract_product_types(ws):
    rows = []
    for name, row in TYPE_ROWS.items():
        r = {'t': name}
        for field, col in TYPE_COLS.items():
            r[field] = ws[f'{col}{row}'].value
        rows.append(r)
    return rows


# ── Finish rows ───────────────────────────────────────────────────────────────

def extract_finishes(ws):
    finishes = {}
    for name, row in FINISH_ROWS.items():
        f = {}
        for field, col in FINISH_COLS.items():
            f[field] = ws[f'{col}{row}'].value
        finishes[name] = f
    return finishes


# ── By Collection ─────────────────────────────────────────────────────────────

def extract_collections(ws_coll, ws_sku, mode='month'):
    """Return list of collection dicts (all rows with gross > 0).

    mode='quarter' selects the quarterly By-Collection column mapping
    (COLL_COL_Q), which is shifted by one column vs the monthly layout
    from the 'vs LQ-1' column onward — see config.py for the verified offsets.
    """
    # Build SKU count per (type, coll) from By SKU
    sku_counts = {}
    for row in ws_sku.iter_rows(min_row=5, values_only=True):
        if row[SKU_COL['sku']] is None:
            continue
        key = (str(row[SKU_COL['type_']] or '').strip(),
               str(row[SKU_COL['coll']] or '').strip())
        sku_counts[key] = sku_counts.get(key, 0) + 1

    collections = []
    C = COLL_COL_Q if mode == 'quarter' else COLL_COL
    for row in ws_coll.iter_rows(min_row=5, values_only=True):
        if row[C['rank']] is None:
            continue
        gross = row[C['gross']]
        if not gross or gross <= 0:
            continue
        t = str(row[C['type_']] or '').strip()
        c = str(row[C['coll']] or '').strip()
        uk_s = row[C['uk']] or 0
        us_s = row[C['us']] or 0
        lq_total = row[C['lq_total']] or 0
        lq_uk = row[C['lq_uk']] or 0
        lq_us = row[C['lq_us']] or 0
        uk_vs = (uk_s - lq_uk) / lq_uk if lq_uk else None
        us_vs = (us_s - lq_us) / lq_us if lq_us else None
        collections.append({
            'r':        int(row[C['rank']]),
            't':        t,
            'c':        c,
            'ts':       gross,
            'tu':       int(row[C['units']] or 0),
            'vs_lq':    row[C['vs_lq']],
            'gm':       row[C['gm']],
            'st':       row[C['st']],
            'wc':       row[C['wc']],
            'd2c':      row[C['d2c']] or 0,
            'b2b':      row[C['b2b']] or 0,
            'uk_s':     uk_s,
            'us_s':     us_s,
            'row_s':    row[C['row']] or 0,
            'lq_total': lq_total,
            'lq_uk':    lq_uk,
            'lq_us':    lq_us,
            'uk_vs':    uk_vs,
            'us_vs':    us_vs,
            'skus':     sku_counts.get((t, c), 0),
        })
    return collections


# ── By SKU ────────────────────────────────────────────────────────────────────

def extract_skus_all(ws_sku):
    """Return list of ALL SKU dicts from By SKU sheet (row 5+)."""
    skus = []
    C = SKU_COL
    for row in ws_sku.iter_rows(min_row=5, values_only=True):
        if row[C['sku']] is None:
            continue
        gross = row[C['gross']]
        if not isinstance(gross, (int, float)) or gross <= 0:
            continue
        skus.append({
            'rank':      row[C['rank']],
            'sku':       str(row[C['sku']]).strip(),
            'desc':      str(row[C['desc']] or '').strip(),
            'coll':      str(row[C['coll']] or '').strip(),
            'type_':     str(row[C['type_']] or '').strip(),
            'finish':    str(row[C['finish']] or '').strip(),
            'uk_status': str(row[C['uk_status']] or '').strip(),
            'us_status': str(row[C['us_status']] or '').strip(),
            'gross':     gross,
            'units':     int(row[C['units']] or 0),
            'vslq':      row[C['vslq']],
            'gm':        row[C['gm']],
            'st':        row[C['st']],
            'wc':        row[C['wc']],
            'inv':       int(row[C['inv']] or 0) if row[C['inv']] is not None else 0,
            'd2c':       row[C['d2c']] or 0,
            'b2b':       row[C['b2b']] or 0,
            'uk':        row[C['uk']] or 0,
            'uk_u':      int(row[C['uk_u']] or 0),
            'us':        row[C['us']] or 0,
            'us_u':      int(row[C['us_u']] or 0),
            'lq':        row[C['lq']],
            'ly':        row[C['ly']],
        })
    return skus


# ── Master entry point ────────────────────────────────────────────────────────

def extract_all(path):
    """Load xlsx and return complete raw data dict.

    Auto-detects monthly vs quarterly source by summary sheet name
    ('Monthly Summary' vs 'Quarterly Summary'). The two layouts are
    otherwise structurally identical except for the By-Collection column
    shift handled in extract_collections/COLL_COL_Q.
    """
    wb = openpyxl.load_workbook(str(path), data_only=True)
    if 'Quarterly Summary' in wb.sheetnames:
        ws_ms = wb['Quarterly Summary']
        mode = 'quarter'
    else:
        ws_ms = wb['Monthly Summary']
        mode = 'month'
    ws_coll = wb['By Collection']
    ws_sku  = wb['By SKU']

    headline = extract_headline(ws_ms)
    return {
        'mode':         mode,
        'period_model': extract_period_model(ws_ms),
        'current':      headline['current'],
        'lm':           headline['lm'],
        'ly':           headline['ly'],
        'statuses':     extract_statuses(ws_ms),
        'prod_types':   extract_product_types(ws_ms),
        'finishes':     extract_finishes(ws_ms),
        'collections':  extract_collections(ws_coll, ws_sku, mode=mode),
        'skus_all':     extract_skus_all(ws_sku),
        '_ws_ms':       ws_ms,
        '_ws_coll':     ws_coll,
        '_ws_sku':      ws_sku,
    }
