"""Derive every computed constant, KPI token, and ribbon token from raw data."""

import json
import math

from config import (
    FINISH_COLORS, COLL_COLORS, STATUS_ABBREV,
    fmt_gbp, fmt_pct, badge_class, arrow_color, fmt_inv,
)

# Source spreadsheet stores cover as inventory_units / monthly_sales_units (months).
# Multiply by 52/12 to convert to weeks everywhere before display.
_MONTHS_TO_WEEKS = 52 / 12

# ── Period model ──────────────────────────────────────────────────────────────

def compute_periods(current, lm, ly, pm):
    def _r(v, dp=2):
        return round(v, dp) if v is not None else 0

    def _i(v):
        return int(v) if v is not None else 0

    return {
        'q1_25': {
            'label': pm['ly']['label'],
            'short': pm['ly']['short'],
            'total':   _r(ly['total']),
            'd2c':     _r(ly['d2c']),
            'b2b':     _r(ly['b2b']),
            'uk':      _r(ly['uk']),
            'us':      _r(ly['us']),
            'row':     _r(ly['row']),
            'total_u': _i(ly['total_u']),
            'd2c_u':   _i(ly['d2c_u']),
            'b2b_u':   _i(ly['b2b_u']),
            'uk_u':    _i(ly['uk_u']),
            'us_u':    _i(ly['us_u']),
            'row_u':   _i(ly['row_u']),
        },
        'q4_25': {
            'label': pm['lm']['label'],
            'short': pm['lm']['short'],
            'total':   _r(lm['total']),
            'd2c':     _r(lm['d2c']),  # LM D2C includes ROW per spec
            'b2b':     _r(lm['b2b']),  # LM B2B includes ROW per spec
            'uk':      _r(lm['uk']),
            'us':      _r(lm['us']),
            'row':     _r(lm['row']),
            'total_u': _i(lm['total_u']),
            'd2c_u':   _i(lm['d2c_u']),
            'b2b_u':   _i(lm['b2b_u']),
            'uk_u':    _i(lm['uk_u']),
            'us_u':    _i(lm['us_u']),
            'row_u':   _i(lm['row_u']),
        },
        'q1_26': {
            'label': pm['cm']['label'],
            'short': pm['cm']['short'],
            'total':   _r(current['total_sales']),
            'd2c':     _r(current['d2c_gbp']),   # current D2C excludes ROW per spec
            'b2b':     _r(current['b2b_gbp']),   # current B2B excludes ROW per spec
            'uk':      _r(current['uk_gbp']),
            'us':      _r(current['us_gbp']),
            'row':     _r(current['row_gbp']),
            'total_u': _i(current['units']),
            'd2c_u':   _i(current['d2c_units']),
            'b2b_u':   _i(current['b2b_units']),
            'uk_u':    _i(current['uk_units']),
            'us_u':    _i(current['us_units']),
            'row_u':   _i(current['row_units']),
        },
    }


# ── TOTAL_SALES ───────────────────────────────────────────────────────────────

def compute_total_sales(current):
    return round(current['total_sales'])


# ── STATUSES ──────────────────────────────────────────────────────────────────

def compute_statuses(statuses_raw):
    out = []
    for s in statuses_raw:
        out.append({
            's':     s['s'],
            'sales': round(s['sales'], 2) if s['sales'] else 0,
            'units': int(s['units']) if s['units'] else 0,
            'vs_lq': _r4(s.get('vs_lq')),
            'vs_ly': _r4(s.get('vs_ly')),
            'gm':    _r4(s.get('gm')),
            'st':    _r4(s.get('st')),
            'wc':    round(s['wc'] * _MONTHS_TO_WEEKS, 3) if s.get('wc') else 0,
            'inv':   int(s['inv']) if s.get('inv') else 0,
        })
    return out


def _r4(v):
    if v is None or v == '':
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


# ── PROD_TYPES ────────────────────────────────────────────────────────────────

def compute_prod_types(types_raw):
    out = []
    for t in types_raw:
        out.append({
            't':      t['t'],
            'sales':  round(t['sales']) if t.get('sales') else 0,
            'units':  int(t['units']) if t.get('units') else 0,
            'vs_lq':  _r4(t.get('vs_lq')),
            'gm':     _r4(t.get('gm')),
        })
    return out


# ── SKUS (top 25 overall) ─────────────────────────────────────────────────────

def compute_skus(skus_all):
    top = sorted(skus_all, key=lambda s: -s['gross'])[:25]
    out = []
    for i, s in enumerate(top):
        vs_ly_val = None
        if s.get('ly') and s['ly'] > 0:
            vs_ly_val = round((s['gross'] - s['ly']) / s['ly'], 3)
        out.append({
            'r':           i,
            'sku':         s['sku'],
            'd':           s['desc'],
            'c':           s['coll'],
            't':           s['type_'],
            'uk_s':        STATUS_ABBREV.get(s['uk_status'], s['uk_status']),
            'us_s':        STATUS_ABBREV.get(s['us_status'], s['us_status']),
            'total_sales': round(s['gross'], 2),
            'total_units': s['units'],
            'vs_lq':       _r4(s.get('vslq')),
            'vs_ly':       vs_ly_val,
            'gm':          _r4(s.get('gm')),
            'st':          _r4(s.get('st')),
            'wc':          round(s['wc'] * _MONTHS_TO_WEEKS, 3) if s.get('wc') else 0,
            'inv':         s['inv'],
            'uk_s2':       round(s['uk'], 2),
            'uk_u':        s['uk_u'],
            'us_s2':       round(s['us'], 2),
            'us_u':        s['us_u'],
            'd2c':         round(s['d2c'], 2),
            'b2b':         round(s['b2b'], 2),
            'lq':          round(s['lq'], 2) if s.get('lq') else 0,
            'ly':          round(s['ly'], 2) if s.get('ly') else 0,
        })
    return out


# ── NEWNESS_SKUS (top 25 Newness SKUs) ───────────────────────────────────────

def compute_newness_skus(skus_all):
    newness = [
        s for s in skus_all
        if s['uk_status'] == 'Newness' or s['us_status'] == 'Newness'
    ]
    top = sorted(newness, key=lambda s: -s['gross'])[:25]
    out = []
    for s in top:
        out.append({
            'sku':   s['sku'],
            'desc':  s['desc'],
            'coll':  s['coll'],
            'ptype': s['type_'],
            'finish': s['finish'],
            'sales': round(s['gross']),
            'units': s['units'],
            'gm':    _r4(s.get('gm')),
            'st':    _r4(s.get('st')),
            'wc':    round(s['wc'] * _MONTHS_TO_WEEKS, 2) if s.get('wc') else 0,
            'd2c':   round(s['d2c']),
            'b2b':   round(s['b2b']),
            'uk':    round(s['uk']),
            'us':    round(s['us']),
        })
    return out


# ── CAT_SKUS (top 8 per category from full By SKU) ───────────────────────────

def compute_cat_skus(skus_all):
    cats = ['Cabinetry', 'Electric', 'Accessories', 'Lighting']
    result = {}
    for cat in cats:
        cat_rows = [s for s in skus_all if s['type_'] == cat]
        top8 = sorted(cat_rows, key=lambda s: -s['gross'])[:8]
        result[cat] = [{
            'sku':   s['sku'],
            'd':     s['desc'],
            'c':     s['coll'],
            'sales': round(s['gross']),
            'vs_lq': _r4(s.get('vslq')),
            'gm':    _r4(s.get('gm')),
            'uk':    round(s['uk']),
            'us':    round(s['us']),
        } for s in top8]
    return result


# ── COLLECTIONS (js array) ────────────────────────────────────────────────────

def compute_collections(collections_raw):
    out = []
    for c in collections_raw:
        out.append({
            'r':        c['r'],
            't':        c['t'],
            'c':        c['c'],
            'ts':       round(c['ts'], 2),
            'tu':       c['tu'],
            'vs_lq':    _r4(c.get('vs_lq')),
            'gm':       _r4(c.get('gm')),
            'st':       _r4(c.get('st')),
            'wc':       round(c['wc'] * _MONTHS_TO_WEEKS, 3) if c.get('wc') else 0,
            'd2c':      round(c['d2c'], 2),
            'b2b':      round(c['b2b'], 2),
            'uk_s':     round(c['uk_s'], 2),
            'us_s':     round(c['us_s'], 2),
            'row_s':    round(c.get('row_s', 0), 2),
            'lq_total': round(c['lq_total'], 2),
            'lq_uk':    round(c['lq_uk'], 2),
            'lq_us':    round(c['lq_us'], 2),
            'uk_vs':    _r4(c.get('uk_vs')),
            'us_vs':    _r4(c.get('us_vs')),
            'skus':     c.get('skus', 0),
        })
    return out


# ── FINISH_DATA ───────────────────────────────────────────────────────────────

def compute_finish_data(finishes_raw, skus_all):
    result = {}
    for name, raw in finishes_raw.items():
        total = raw.get('total') or 0
        vsLQ  = raw.get('vsLQ') or 0
        denom = 1 + vsLQ
        lq    = total / denom if abs(denom) > 1e-9 else 0

        finish_skus = [
            s for s in skus_all
            if s['finish'] == name and s['gross'] > 0
        ]

        top8 = sorted(finish_skus, key=lambda s: -s['gross'])[:8]

        def _coll_split(field):
            sums = {}
            for s in finish_skus:
                sums[s['coll']] = sums.get(s['coll'], 0) + (s.get(field) or 0)
            sorted_c = sorted(sums.items(), key=lambda x: -x[1])
            split, other = {}, 0.0
            for i, (cn, v) in enumerate(sorted_c):
                if i < 7: split[cn] = round(v)
                else: other += v
            if other > 0: split['OTHER'] = round(other)
            return split

        color, text_color = FINISH_COLORS[name]
        result[name] = {
            'color':        color,
            'textColor':    text_color,
            'total':        round(total),
            'lq':           round(lq),
            'ly':           0,
            'units':        int(raw.get('units') or 0),
            'vsLQ':         _r4(vsLQ),
            'vsLY':         None,
            'd2c':          round(raw.get('d2c') or 0),
            'b2b':          round(raw.get('b2b') or 0),
            'uk':           round(raw.get('uk') or 0),
            'us':           round(raw.get('us') or 0),
            'lq_uk':        0,
            'lq_us':        0,
            'collSplit':    _coll_split('gross'),
            'collSplitUK':  _coll_split('uk'),
            'collSplitUS':  _coll_split('us'),
            'skus': [{
                'sku':  s['sku'],
                'coll': s['coll'],
                'sales': round(s['gross']),
                'vsLQ': _r4(s.get('vslq')),
                'gm':   _r4(s.get('gm')),
                'uk':   round(s.get('uk') or 0),
                'us':   round(s.get('us') or 0),
            } for s in top8],
        }
    return result


# ── COLL_ANALYSIS (top 10 deep-dive) ─────────────────────────────────────────

def compute_coll_analysis(collections_raw, skus_all):
    top10 = sorted(collections_raw, key=lambda c: -c['ts'])[:10]

    # Build (type, coll) → top 6 sku objects from full By SKU sheet
    sku_by_coll = {}
    for s in sorted(skus_all, key=lambda s: -s['gross']):
        key = (s['type_'], s['coll'])
        if key not in sku_by_coll:
            sku_by_coll[key] = []
        if len(sku_by_coll[key]) < 6:
            sku_by_coll[key].append({
                'sku':   s['sku'],
                'd':     s['desc'],
                'sales': round(s['gross']),
                'vs_lq': _r4(s.get('vslq')),
                'gm':    _r4(s.get('gm')),
            })

    result = {}
    seen_names = {}
    for i, c in enumerate(top10):
        t, name = c['t'], c['c']
        # Unique key: if name collides with a different type, append type initial
        key = name
        if name in seen_names and seen_names[name] != t:
            key = f'{name}_{t[0]}'
        seen_names[name] = t

        color = COLL_COLORS[i % len(COLL_COLORS)]
        skus_list = sku_by_coll.get((t, name), [])[:6]
        result[key] = {
            'color':    color,
            'sales':    round(c['ts']),
            'lq':       round(c['lq_total']),
            'lq_uk':    round(c['lq_uk']),
            'lq_us':    round(c['lq_us']),
            'units':    c['tu'],
            'gm':       _r4(c.get('gm')),
            'st':       _r4(c.get('st')),
            'wc':       round(c['wc'] * _MONTHS_TO_WEEKS, 3) if c.get('wc') else 0,
            'd2c':      round(c['d2c']),
            'b2b':      round(c['b2b']),
            'uk':       round(c['uk_s']),
            'us':       round(c['us_s']),
            'row':      round(c.get('row_s', 0)),
            'skus':     skus_list,
        }
    return result


# ── Static KPI tokens ─────────────────────────────────────────────────────────

def compute_kpi_tokens(current, lm, pm, mode='month'):
    c = current
    total   = c['total_sales']
    lm_d2c  = lm['d2c']
    lm_tot  = lm['total']
    d2c_share     = c['d2c_gbp'] / total if total else 0
    lm_d2c_share  = lm_d2c / lm_tot if lm_tot else 0
    cm_lbl = pm['cm']['label']
    lm_lbl = pm['lm']['label']
    ly_lbl = pm['ly']['label']
    lm_short = pm['lm']['short']
    ly_short = pm['ly']['short']
    dc_bar = cm_lbl.split()[0][:3]  # e.g. "May" (monthly) or "Q2" (quarterly)

    # Mode-driven label vocabulary — same formulas/values throughout, only
    # the words describing "current vs previous period" change.
    is_q = (mode == 'quarter')
    report_type       = 'Quarterly' if is_q else 'Monthly'
    period_noun        = 'Quarter' if is_q else 'Month'
    prev_period_abbr   = 'LQ' if is_q else 'LM'
    period_comp_label  = 'QoQ' if is_q else 'MoM'
    period_trend_label = 'Quarter-on-Quarter' if is_q else 'Month-on-Month'

    inv_units = c.get('inventory', 0) or 0

    # period labels JS array
    period_labels = (
        f'["{pm["ly"]["short"]}","{pm["lm"]["short"]}","{pm["cm"]["short"]}"]'
    )

    toks = {
        # Page identity
        'PAGE_TITLE':   f'{cm_lbl} — {report_type} Trading Report',
        'REPORT_TYPE':         report_type,
        'CM_PERIOD_NOUN':      period_noun,
        'PREV_PERIOD_ABBR':    prev_period_abbr,
        'PERIOD_COMP_LABEL':   period_comp_label,
        'PERIOD_TREND_LABEL':  period_trend_label,
        'CM_LABEL':     cm_lbl,
        'LM_LABEL':     lm_lbl,
        'LY_LABEL':     ly_lbl,
        'CM_SHORT':     pm['cm']['short'],
        'LM_SHORT':     lm_short,
        'LY_SHORT':     ly_short,
        'DC_BAR_LBL':   dc_bar,
        'VS_LM_LABEL':  f'vs {lm_lbl}',
        'VS_LY_LABEL':  f'vs {ly_lbl}',
        'PERIOD_LABELS': period_labels,

        # KPI — Total Revenue
        'KPI_REV_VAL':     fmt_gbp(total),
        'KPI_REV_LM_CLS':  badge_class(c.get('vs_lm')),
        'KPI_REV_LM':      fmt_pct(c.get('vs_lm')),
        'KPI_REV_LY_CLS':  badge_class(c.get('vs_ly')),
        'KPI_REV_LY':      fmt_pct(c.get('vs_ly')),

        # KPI — Units
        'KPI_UNITS_VAL':     f"{int(c['units']):,}",
        'KPI_UNITS_LM_CLS':  badge_class(c.get('units_vs_lm')),
        'KPI_UNITS_LM':      fmt_pct(c.get('units_vs_lm')),
        'KPI_UNITS_LY_CLS':  badge_class(c.get('units_vs_ly')),
        'KPI_UNITS_LY':      fmt_pct(c.get('units_vs_ly')),

        # KPI — Gross Margin
        'KPI_GM_VAL': f"{c['gm_pct']*100:.1f}%" if c.get('gm_pct') else '—',
        'KPI_GM_D2C': f"{c['d2c_gm']*100:.1f}%" if c.get('d2c_gm') else '—',
        'KPI_GM_B2B': f"{c['b2b_gm']*100:.1f}%" if c.get('b2b_gm') else '—',

        # KPI — D2C Share
        'KPI_D2C_SHARE':    f'{d2c_share*100:.1f}%',
        'KPI_D2C_LM_CLS':   badge_class(d2c_share - lm_d2c_share),
        'KPI_D2C_LM':       ('+' if d2c_share >= lm_d2c_share else '') + f'{(d2c_share - lm_d2c_share)*100:.1f}pp',

        # KPI — UK
        'KPI_UK_VAL':    fmt_gbp(c.get('uk_gbp')),
        'KPI_UK_LM_CLS': badge_class(c.get('uk_vs_lm')),
        'KPI_UK_LM':     fmt_pct(c.get('uk_vs_lm')),
        'KPI_UK_LY_CLS': badge_class(c.get('uk_vs_ly')),
        'KPI_UK_LY':     fmt_pct(c.get('uk_vs_ly')),

        # KPI — US
        'KPI_US_VAL':    fmt_gbp(c.get('us_gbp')),
        'KPI_US_LM_CLS': badge_class(c.get('us_vs_lm')),
        'KPI_US_LM':     fmt_pct(c.get('us_vs_lm')),
        'KPI_US_LY_CLS': badge_class(c.get('us_vs_ly')),
        'KPI_US_LY':     fmt_pct(c.get('us_vs_ly')),

        # KPI — Sell-Through
        'KPI_ST_VAL':  f"{c['sell_through']*100:.1f}%" if c.get('sell_through') else '—',
        'KPI_WC_VAL':  f"WC {c['weeks_cover'] * _MONTHS_TO_WEEKS:.1f} wks" if c.get('weeks_cover') else '—',
        'KPI_INV_VAL': fmt_inv(inv_units),
    }

    # MoM Ribbon — Total trajectory
    gp  = lm['total'] if lm else 0   # uses ly actually via ribbon spec
    # Re-reading: Total trajectory uses LY(GP7) → LM(EX7) → CM(F7)
    # We need LY total from the ly block (passed separately)
    # This function doesn't have ly block; store for caller to add ribbon tokens
    toks['_period_labels'] = period_labels  # internal use
    return toks


def compute_ribbon_tokens(current, lm, ly, pm):
    """Return ribbon tokens for the MoM ribbon section."""
    cm_total = current.get('total_sales', 0)
    lm_total = lm.get('total', 0)
    ly_total = ly.get('total', 0)
    cm_uk    = current.get('uk_gbp', 0)
    lm_uk    = lm.get('uk', 0)
    ly_uk    = ly.get('uk', 0)
    cm_us    = current.get('us_gbp', 0)
    lm_us    = lm.get('us', 0)
    ly_us    = ly.get('us', 0)

    cm_lbl = pm['cm']['label']
    lm_lbl = pm['lm']['label']
    ly_lbl = pm['ly']['label']

    uk_vs_lm    = current.get('uk_vs_lm', 0) or 0
    uk_vs_ly    = current.get('uk_vs_ly', 0) or 0
    us_vs_lm    = current.get('us_vs_lm', 0) or 0
    us_vs_ly    = current.get('us_vs_ly', 0) or 0
    total_vs_lm = current.get('vs_lm', 0) or 0
    total_vs_ly = current.get('vs_ly', 0) or 0

    def _sign_pct(v):
        return fmt_pct(v) if v is not None else '—'

    return {
        # Total trajectory
        'RIB_TOTAL_LY_PERIOD':  f'{ly_lbl} (LY)',
        'RIB_TOTAL_LY_VAL':     fmt_gbp(ly_total),
        'RIB_TOTAL_ARR1_CLS':   'up' if lm_total >= ly_total else 'dn',
        'RIB_TOTAL_LM_PERIOD':  f'{lm_lbl} (LM)',
        'RIB_TOTAL_LM_VAL':     fmt_gbp(lm_total),
        'RIB_TOTAL_ARR2_CLS':   'up' if cm_total >= lm_total else 'dn',
        'RIB_TOTAL_CM_PERIOD':  f'{cm_lbl} (CM)',
        'RIB_TOTAL_CM_VAL':     fmt_gbp(cm_total),
        'RIB_TOTAL_LM_CLS':     badge_class(total_vs_lm),
        'RIB_TOTAL_LM_BADGE':   f'{_sign_pct(total_vs_lm)} MoM',
        'RIB_TOTAL_LY_CLS':     badge_class(total_vs_ly),
        'RIB_TOTAL_LY_BADGE':   f'{_sign_pct(total_vs_ly)} YoY',

        # UK trajectory
        'RIB_UK_LY_VAL':     fmt_gbp(ly_uk),
        'RIB_UK_ARR1_COLOR': arrow_color(ly_uk, lm_uk),
        'RIB_UK_LM_VAL':     fmt_gbp(lm_uk),
        'RIB_UK_ARR2_COLOR': arrow_color(lm_uk, cm_uk),
        'RIB_UK_CM_VAL':     fmt_gbp(cm_uk),
        'RIB_UK_LM_CLS':     badge_class(uk_vs_lm),
        'RIB_UK_LM_BADGE':   f'{_sign_pct(uk_vs_lm)} MoM',
        'RIB_UK_LY_CLS':     badge_class(uk_vs_ly),
        'RIB_UK_LY_BADGE':   f'{_sign_pct(uk_vs_ly)} YoY',

        # US trajectory
        'RIB_US_LY_VAL':     fmt_gbp(ly_us),
        'RIB_US_ARR1_COLOR': arrow_color(ly_us, lm_us),
        'RIB_US_LM_VAL':     fmt_gbp(lm_us),
        'RIB_US_ARR2_COLOR': arrow_color(lm_us, cm_us),
        'RIB_US_CM_VAL':     fmt_gbp(cm_us),
        'RIB_US_LM_CLS':     badge_class(us_vs_lm),
        'RIB_US_LM_BADGE':   f'{_sign_pct(us_vs_lm)} MoM',
        'RIB_US_LY_CLS':     badge_class(us_vs_ly),
        'RIB_US_LY_BADGE':   f'{_sign_pct(us_vs_ly)} YoY',
    }


# ── JS serialisation helpers ──────────────────────────────────────────────────

def _js_val(v):
    """Serialize a Python value to a JS literal."""
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return '0'
        # Strip trailing zeros but keep at least 2dp for readability
        s = f'{v:.4f}'.rstrip('0').rstrip('.')
        return s
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        escaped = v.replace('\\', '\\\\').replace("'", "\\'")
        return f"'{escaped}'"
    if isinstance(v, dict):
        items = ','.join(f'{_js_key(k)}:{_js_val(vv)}' for k, vv in v.items())
        return '{' + items + '}'
    if isinstance(v, list):
        return '[' + ','.join(_js_val(i) for i in v) + ']'
    return repr(v)


def _js_key(k):
    # Quote keys that look like 'Antique Brass' etc.
    if ' ' in str(k) or '-' in str(k):
        return f"'{k}'"
    return str(k)


def js_const(name, value):
    return f'const {name} = {_js_val(value)};'


def js_block_periods(periods):
    lines = ['const PERIODS = {']
    for key, p in periods.items():
        inner = (
            f"  {key}: {{\n"
            f'    label: "{p["label"]}", short: "{p["short"]}",\n'
            f"    total: {p['total']}, d2c: {p['d2c']}, b2b: {p['b2b']},\n"
            f"    uk: {p['uk']}, us: {p['us']}, row: {p['row']},\n"
            f"    total_u: {p['total_u']}, d2c_u: {p['d2c_u']}, b2b_u: {p['b2b_u']},\n"
            f"    uk_u: {p['uk_u']}, us_u: {p['us_u']}, row_u: {p['row_u']}\n"
            f"  }}"
        )
        lines.append(inner + ',')
    lines[-1] = lines[-1].rstrip(',')
    lines.append('};')
    return '\n'.join(lines)


def js_block_collections(collections):
    rows = []
    for c in collections:
        uk_vs = f'{c["uk_vs"]}' if c.get('uk_vs') is not None else 'null'
        us_vs = f'{c["us_vs"]}' if c.get('us_vs') is not None else 'null'
        gm    = f'{c["gm"]}' if c.get('gm') is not None else '0'
        st    = f'{c["st"]}' if c.get('st') is not None else '0'
        vs_lq = f'{c["vs_lq"]}' if c.get('vs_lq') is not None else '0'
        rows.append(
            f'  {{r:{c["r"]},t:"{c["t"]}",c:"{c["c"]}",'
            f'ts:{c["ts"]},tu:{c["tu"]},vs_lq:{vs_lq},'
            f'gm:{gm},st:{st},wc:{c["wc"]},'
            f'd2c:{c["d2c"]},b2b:{c["b2b"]},'
            f'uk_s:{c["uk_s"]},us_s:{c["us_s"]},row_s:{c["row_s"]},'
            f'lq_total:{c["lq_total"]},lq_uk:{c["lq_uk"]},lq_us:{c["lq_us"]},'
            f'uk_vs:{uk_vs},us_vs:{us_vs},skus:{c["skus"]}}}'
        )
    return 'const COLLECTIONS = [\n' + ',\n'.join(rows) + '\n];'


def js_block_statuses(statuses):
    rows = []
    for s in statuses:
        vs_lq = _js_val(s.get('vs_lq'))
        vs_ly = _js_val(s.get('vs_ly'))
        rows.append(
            f'  {{s:"{s["s"]}",sales:{s["sales"]},units:{s["units"]},'
            f'vs_lq:{vs_lq},vs_ly:{vs_ly},'
            f'gm:{_js_val(s["gm"])},st:{_js_val(s["st"])},'
            f'wc:{s["wc"]},inv:{s["inv"]}}}'
        )
    return 'const STATUSES = [\n' + ',\n'.join(rows) + '\n];'


def js_block_prod_types(prod_types):
    rows = []
    for t in prod_types:
        rows.append(
            f'  {{t:"{t["t"]}",sales:{t["sales"]},units:{t["units"]},'
            f'vs_lq:{_js_val(t["vs_lq"])},vs_ly:null,'
            f'gm:{_js_val(t["gm"])}}}'
        )
    return 'const PROD_TYPES = [\n' + ',\n'.join(rows) + '\n];'


def js_block_skus(skus):
    rows = []
    for s in skus:
        vs_ly = _js_val(s.get('vs_ly'))
        rows.append(
            f'  {{r:{s["r"]},sku:"{s["sku"]}",d:"{_esc(s["d"])}",c:"{s["c"]}",'
            f't:"{s["t"]}",uk_s:"{s["uk_s"]}",us_s:"{s["us_s"]}",'
            f'total_sales:{s["total_sales"]},total_units:{s["total_units"]},'
            f'vs_lq:{_js_val(s["vs_lq"])},vs_ly:{vs_ly},'
            f'gm:{_js_val(s["gm"])},st:{_js_val(s["st"])},wc:{s["wc"]},'
            f'inv:{s["inv"]},uk_s2:{s["uk_s2"]},uk_u:{s["uk_u"]},'
            f'us_s2:{s["us_s2"]},us_u:{s["us_u"]},'
            f'd2c:{s["d2c"]},b2b:{s["b2b"]},'
            f'lq:{s["lq"]},ly:{s["ly"]}}}'
        )
    return 'const SKUS = [\n' + ',\n'.join(rows) + '\n];'


def js_block_finish_data(finish_data):
    parts = ['const FINISH_DATA = {']
    for name, fd in finish_data.items():
        vsLY = 'null' if fd['vsLY'] is None else str(fd['vsLY'])
        ly   = 'null' if fd['ly'] == 0 else str(fd['ly'])
        def _cs(d): return '{' + ','.join(f'"{k}":{v}' for k, v in d.items()) + '}'
        skus_str = '[' + ','.join(
            f'{{sku:"{s["sku"]}",coll:"{s["coll"]}",sales:{s["sales"]},'
            f'vsLQ:{_js_val(s["vsLQ"])},gm:{_js_val(s["gm"])},'
            f'uk:{s["uk"]},us:{s["us"]}}}'
            for s in fd['skus']
        ) + ']'
        parts.append(
            f"  '{name}': {{\n"
            f"    color:'{fd['color']}', textColor:'{fd['textColor']}',\n"
            f"    total:{fd['total']}, lq:{fd['lq']}, ly:{ly},"
            f" units:{fd['units']}, vsLQ:{fd['vsLQ']}, vsLY:{vsLY},\n"
            f"    d2c:{fd['d2c']}, b2b:{fd['b2b']},"
            f" uk:{fd['uk']}, us:{fd['us']}, lq_uk:{fd['lq_uk']}, lq_us:{fd['lq_us']},\n"
            f"    collSplit:{_cs(fd['collSplit'])},\n"
            f"    collSplitUK:{_cs(fd['collSplitUK'])},\n"
            f"    collSplitUS:{_cs(fd['collSplitUS'])},\n"
            f"    skus:{skus_str}\n"
            f"  }},"
        )
    parts[-1] = parts[-1].rstrip(',')
    parts.append('};')
    return '\n'.join(parts)


def js_block_coll_analysis(coll_analysis):
    parts = ['const COLL_ANALYSIS = {']
    for key, ca in coll_analysis.items():
        skus_str = '[' + ','.join(
            f"{{sku:'{_esc(s['sku'])}',d:'{_esc(s['d'])}',sales:{s['sales']},"
            f"vs_lq:{_js_val(s['vs_lq'])},gm:{_js_val(s['gm'])}}}"
            for s in ca['skus']
        ) + ']'
        parts.append(
            f"  {_js_key(key)}:{{color:'{ca['color']}', sales:{ca['sales']},"
            f" lq:{ca['lq']},lq_uk:{ca['lq_uk']}, lq_us:{ca['lq_us']},"
            f" units:{ca['units']}, gm:{_js_val(ca['gm'])},"
            f"st:{_js_val(ca['st'])},wc:{ca['wc']},"
            f" d2c:{ca['d2c']}, b2b:{ca['b2b']},"
            f" uk:{ca['uk']}, us:{ca['us']}, row:{ca['row']}, skus:{skus_str}}},"
        )
    parts[-1] = parts[-1].rstrip(',')
    parts.append('};')
    return '\n'.join(parts)


def js_block_newness_skus(newness_skus):
    rows = []
    for s in newness_skus:
        rows.append(
            f"  {{sku:'{s['sku']}', desc:'{_esc(s['desc'])}', coll:'{s['coll']}',"
            f" ptype:'{s['ptype']}', finish:'{_esc(s['finish'])}',"
            f" sales:{s['sales']}, units:{s['units']},"
            f" gm:{_js_val(s['gm'])}, st:{_js_val(s['st'])}, wc:{s['wc']},"
            f" d2c:{s['d2c']}, b2b:{s['b2b']}, uk:{s['uk']}, us:{s['us']}}}"
        )
    return 'const NEWNESS_SKUS = [\n' + ',\n'.join(rows) + '\n];'


def js_block_cat_skus(cat_skus):
    parts = ['const CAT_SKUS = {']
    for cat, skus in cat_skus.items():
        rows = []
        for s in skus:
            rows.append(
                f"    {{sku:\"{s['sku']}\", d:\"{_esc(s['d'])}\","
                f" c:\"{s['c']}\", sales:{s['sales']},"
                f" vs_lq:{_js_val(s['vs_lq'])}, gm:{_js_val(s['gm'])},"
                f" uk:{s['uk']}, us:{s['us']}}}"
            )
        parts.append(f"  {cat}: [\n" + ',\n'.join(rows) + '\n  ],')
    parts[-1] = parts[-1].rstrip(',')
    parts.append('};')
    return '\n'.join(parts)


def _esc(s):
    return str(s).replace('\\', '\\\\').replace('"', '\\"').replace("'", "\\'")
