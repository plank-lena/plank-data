"""Derive every computed constant, KPI token, and ribbon token from raw data."""

import json
import math

from config import (
    assign_finish_colors, assign_dept_colors, COLL_COLORS, STATUS_ABBREV,
    fmt_gbp, fmt_pct, badge_class, arrow_color,
)

# BRIEF #4 step 4 item 4: a SKU counts as "live" for movers regardless of
# which of the two status vocabularies its source used -- see contract.py's
# LIVE_STATUS_VALUES docstring for why both "Live" (Line Detail's raw enum)
# and "Continuity"/"Newness" (the oracle's coarse bucket) mean the same thing.
LIVE_STATUS_VALUES = {"Live", "Continuity", "Newness"}

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
    """Per-category bars data (BRIEF #4 step 4 §2/§6): current + LY value
    (both absolute, for the bar label) plus yoy_dir (direction-only colour
    cue via badge_class -- never the YoY % itself, so a new/near-zero-LY
    category like Taps doesn't read as a spurious "+900%"), and the
    subcategory breakdown for the drill toggle. Categories/subcategories are
    whatever the data has -- no fixed list or count (§5/§10).
    """
    out = []
    colors = assign_dept_colors(t['t'] for t in types_raw)
    for t in types_raw:
        vs_ly = _r4(t.get('vs_ly'))
        sales = round(t['sales']) if t.get('sales') else 0
        denom = 1 + vs_ly if vs_ly is not None else None
        ly_sales = round(sales / denom) if denom and abs(denom) > 1e-9 else None
        color, text_color = colors[t['t']]
        out.append({
            't':          t['t'],
            'sales':      sales,
            'ly_sales':   ly_sales,
            # T2b: LQ ghost marker + Channel/Country toggle views. lq_sales
            # is real on the Matrixify path (oracle-bootstrapped) and on the
            # oracle path itself (reconstructed from its own real vs_lq);
            # d2c/b2b/uk/us are real on the Matrixify path only -- the oracle
            # sheet has no such column at department grain, so they're 0.0
            # there (see contract.py's emit_contract_from_oracle docstring
            # note), and Channel/Country views will show flat/empty bars for
            # an oracle-sourced dashboard. Not fabricated either way.
            'lq_sales':   round(t['lq_sales']) if t.get('lq_sales') is not None else None,
            'd2c':        round(t.get('d2c') or 0),
            'b2b':        round(t.get('b2b') or 0),
            'uk':         round(t.get('uk') or 0),
            'us':         round(t.get('us') or 0),
            'units':      int(t['units']) if t.get('units') else 0,
            'vs_lq':      _r4(t.get('vs_lq')),
            'vs_ly':      vs_ly,
            'yoy_dir':    badge_class(vs_ly),
            'gm':         _r4(t.get('gm')),
            # CA2 (round-3 review): per-market vs-LM -- real once a prior
            # month's own contract exists to chain from (contract.py's
            # pmc_dept_uk_us), None otherwise, never fabricated.
            'uk_vs_lq':   _r4(t.get('uk_vs_lq')),
            'us_vs_lq':   _r4(t.get('us_vs_lq')),
            'color':      color,
            'textColor':  text_color,
            'subcats': [{
                'name':  sc['name'],
                'sales': round(sc['sales']) if sc.get('sales') else 0,
                'units': int(sc['units']) if sc.get('units') else 0,
                'vs_ly': _r4(sc.get('vs_ly')),
            } for sc in t.get('subcats', [])],
        })
    return out


# ── Category top collections (BRIEF #4 step 4 §2/§6) ─────────────────────────
# Derived from COLLECTIONS at compute time, same convention this codebase
# already uses for CAT_ANALYSIS/TYPE_DATA (HANDOFF.md: "derived at runtime
# from these -- do not generate those by hand"). Every toggle state
# (cash/units x UK/US/Total) is pre-computed here so the template's JS does
# no arithmetic, only show/hide.

def compute_category_top_collections(collections_computed, skus_all=None, top_n=8, sku_top_n=10):
    """{category: [{c, cash:{uk,us,total}, units:{uk,us,total}, b2b_share,
    skus}]} top_n collections per category by total cash, from the
    already-computed COLLECTIONS array (compute_collections' output).

    skus_all (C2, round-2 review): when given, each collection row also
    carries its own top sku_top_n SKUs by revenue -- same click-to-expand
    drill compute_finish_data's top_collections already has (T3a), filtered
    to SKUs matching both this category (s['type_']) and this collection
    (s['coll']). None (the default) keeps the prior shape exactly -- every
    existing caller that doesn't pass skus_all sees skus: [] on every row,
    never a KeyError.
    """
    by_cat = {}
    for c in collections_computed:
        by_cat.setdefault(c['t'], []).append(c)

    skus_all = skus_all or []
    result = {}
    for cat, rows in by_cat.items():
        top = sorted(rows, key=lambda c: -c['ts'])[:top_n]
        result[cat] = []
        for c in top:
            channel_total = c['d2c'] + c['b2b']
            coll_skus = sorted(
                (s for s in skus_all if s['type_'] == cat and s['coll'] == c['c'] and (s.get('gross') or 0) > 0),
                key=lambda s: -(s.get('gross') or 0),
            )[:sku_top_n]
            result[cat].append({
                'c': c['c'],
                'cash': {'uk': c['uk_s'], 'us': c['us_s'], 'total': c['ts']},
                'units': {'uk': None, 'us': None, 'total': c['tu']},  # per-country units not tracked at collection grain
                'b2b_share': round(c['b2b'] / channel_total, 4) if channel_total else None,
                'skus': [
                    {
                        'sku':   s['sku'],
                        'desc':  s.get('desc') or s['sku'],
                        'sales': round(s.get('gross') or 0),
                        'units': int(s.get('units') or 0),
                        'cash':  {'uk': round(s.get('uk') or 0), 'us': round(s.get('us') or 0), 'total': round(s.get('gross') or 0)},
                        'unitsByGeo': {'uk': int(s.get('uk_u') or 0), 'us': int(s.get('us_u') or 0), 'total': int(s.get('units') or 0)},
                    } for s in coll_skus
                ],
            })
    return result


# ── SKUS (top 25 overall) ─────────────────────────────────────────────────────

def compute_skus(skus_all, top_n=50):
    # T6 (trading review round 1): 25 -> 50.
    top = sorted(skus_all, key=lambda s: -s['gross'])[:top_n]
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
            'is_el_component': bool(s.get('is_el_component')),
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


# ── NEWNESS_SKUS (top 50 Newness SKUs) ───────────────────────────────────────

def compute_newness_skus(skus_all, top_n=50):
    """T6: 25 -> 50. Also fixes a real, pre-existing cross-vocabulary bug
    found alongside T4b's movers split: this filter only ever matched the
    oracle path's own coarse uk_status/us_status values ('Newness' literally),
    silently returning empty on the Matrixify path, where those same fields
    are Line Detail's finer enum ('Live', never 'Newness') -- confirmed
    empirically empty against real May 2026 Matrixify data before this fix.
    _sku_newness() (introduced for T4b) already bridges both vocabularies.
    """
    newness = [s for s in skus_all if _sku_newness(s) == 'Newness']
    top = sorted(newness, key=lambda s: -s['gross'])[:top_n]
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
            'd2c':   round(s['d2c']),
            'b2b':   round(s['b2b']),
            'uk':    round(s['uk']),
            'us':    round(s['us']),
        })
    return out


# NOTE: the previous "top 8 SKUs per category" block (compute_cat_skus /
# CAT_SKUS, keyed by a hardcoded 4-category list) is retired -- BRIEF #4
# step 4 item 9 replaces category analysis's top-SKUs view with top-
# COLLECTIONS (compute_category_top_collections / CAT_TOP_COLLECTIONS
# above), which is also fully dynamic (§5/§10: no fixed category list).


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

def compute_finish_data(finishes_raw, skus_all, top_n=8):
    """Finish analysis now shows top COLLECTIONS per finish, not top SKUs
    (BRIEF #4 step 4 §2/§9) -- whatever finishes exist in the data render
    (§5/§10). Colour comes from config.assign_finish_colors: the curated
    brand palette for the 8 recurring names, evenly-spaced distinct hues
    for every other one -- step-4-follow-up §1 retires the fixed-length
    palette cycle, which silently duplicated colours once finishes ran
    past its length (the ~29-finish list is well past 8).
    """
    ranked = sorted(finishes_raw.items(), key=lambda kv: -(kv[1].get('total') or 0))
    colors = assign_finish_colors(name for name, _ in ranked)
    result = {}
    for rank, (name, raw) in enumerate(ranked):
        total = raw.get('total') or 0
        vsLQ  = raw.get('vsLQ') or 0
        denom = 1 + vsLQ
        lq    = total / denom if abs(denom) > 1e-9 else 0

        finish_skus = [
            s for s in skus_all
            if s['finish'] == name and s['gross'] > 0
        ]

        coll_totals = {}
        for s in finish_skus:
            ct = coll_totals.setdefault(s['coll'], {
                'sales': 0.0, 'units': 0, 'd2c': 0.0, 'b2b': 0.0,
                'uk': 0.0, 'us': 0.0, 'uk_u': 0, 'us_u': 0,
            })
            ct['sales'] += s.get('gross') or 0
            ct['units'] += s.get('units') or 0
            ct['d2c']   += s.get('d2c') or 0
            ct['b2b']   += s.get('b2b') or 0
            ct['uk']    += s.get('uk') or 0
            ct['us']    += s.get('us') or 0
            ct['uk_u']  += s.get('uk_u') or 0
            ct['us_u']  += s.get('us_u') or 0

        top_colls = sorted(coll_totals.items(), key=lambda kv: -kv[1]['sales'])[:top_n]
        top_collections = []
        for cname, ct in top_colls:
            channel_total = ct['d2c'] + ct['b2b']
            # T3a: click-to-expand drill (finish -> collection -> top SKUs).
            # Scoped to SKUs matching BOTH this finish and this collection --
            # finish_skus is already finish-filtered, so this is just the
            # collection filter on top of it, same source data, no new join.
            coll_skus = sorted(
                (s for s in finish_skus if s['coll'] == cname),
                key=lambda s: -(s.get('gross') or 0),
            )[:top_n]
            top_collections.append({
                'c':         cname,
                'sales':     round(ct['sales']),
                'units':     ct['units'],
                'b2b_share': round(ct['b2b'] / channel_total, 4) if channel_total else None,
                # T3b: section-level Sales/Units x UK/US toggle -- {uk,us,
                # total} shape matches the existing toggleVal() helper
                # Collection Performance's own toggle already reads.
                'cash':  {'uk': round(ct['uk']), 'us': round(ct['us']), 'total': round(ct['sales'])},
                'unitsByGeo': {'uk': ct['uk_u'], 'us': ct['us_u'], 'total': ct['units']},
                'skus': [
                    {
                        'sku':   s['sku'],
                        'desc':  s.get('desc') or s['sku'],
                        'sales': round(s.get('gross') or 0),
                        'units': int(s.get('units') or 0),
                        'cash':  {'uk': round(s.get('uk') or 0), 'us': round(s.get('us') or 0), 'total': round(s.get('gross') or 0)},
                        'unitsByGeo': {'uk': int(s.get('uk_u') or 0), 'us': int(s.get('us_u') or 0), 'total': int(s.get('units') or 0)},
                    } for s in coll_skus
                ],
            })

        color, text_color = colors[name]
        channel_total = (raw.get('d2c') or 0) + (raw.get('b2b') or 0)
        result[name] = {
            'color':           color,
            'textColor':       text_color,
            'total':           round(total),
            'lq':              round(lq),
            'ly':              0,
            'units':           int(raw.get('units') or 0),
            'vsLQ':            _r4(vsLQ),
            'vsLY':            None,
            'd2c':             round(raw.get('d2c') or 0),
            'b2b':             round(raw.get('b2b') or 0),
            'b2b_share':       round((raw.get('b2b') or 0) / channel_total, 4) if channel_total else None,
            'uk':              round(raw.get('uk') or 0),
            'us':              round(raw.get('us') or 0),
            # T3b: units-by-country -- 0 on the oracle path (no such column
            # in the sheet's Finish table), real on the Matrixify path.
            'uk_u':            int(raw.get('uk_u') or 0),
            'us_u':            int(raw.get('us_u') or 0),
            'lq_uk':           0,
            'lq_us':           0,
            'top_collections': top_collections,
        }
    return result


# ── COLL_ANALYSIS -- merged Collection Performance + Analysis drill-down
#    (BRIEF #4 step 4 §3/§6: top 10 collections, each exposing every
#    toggle-state value + movement, and its own top-10 SKUs with
#    cash/units x UK/US, % share of the collection's take, and movement).
#    The drill-down responds to the same cash/units + UK/US/Total toggles
#    as its parent bar chart -- all pre-computed here, no client-side math.

def compute_coll_analysis(collections_raw, skus_all, top_n_skus=10):
    top10 = sorted(collections_raw, key=lambda c: -c['ts'])[:10]

    # Build (type, coll) → sorted sku objects from full By SKU sheet
    sku_by_coll = {}
    for s in sorted(skus_all, key=lambda s: -s['gross']):
        key = (s['type_'], s['coll'])
        sku_by_coll.setdefault(key, []).append(s)

    result = {}
    seen_names = {}
    for i, c in enumerate(top10):
        t, name = c['t'], c['c']
        # Unique key: if name collides with a different type, append type initial
        key = name
        if name in seen_names and seen_names[name] != t:
            key = f'{name}_{t[0]}'
        seen_names[name] = t

        coll_total = c['ts'] or 0
        top_skus = sku_by_coll.get((t, name), [])[:top_n_skus]
        skus_list = []
        for s in top_skus:
            skus_list.append({
                'sku':        s['sku'],
                'd':          s['desc'],
                'cash':       {'uk': round(s.get('uk') or 0, 2), 'us': round(s.get('us') or 0, 2), 'total': round(s['gross'], 2)},
                'units':      {'uk': s.get('uk_u') or 0, 'us': s.get('us_u') or 0, 'total': s.get('units') or 0},
                'share':      round(s['gross'] / coll_total, 4) if coll_total else None,
                'vs_lq':      _r4(s.get('vslq')),
                'yoy_dir':    badge_class(s.get('vslq')),
                'gm':         _r4(s.get('gm')),
            })

        color = COLL_COLORS[i % len(COLL_COLORS)]
        vs_lq = _r4(c.get('vs_lq'))
        result[key] = {
            'color':    color,
            'sales':    round(c['ts']),
            'lq':       round(c['lq_total']),
            'lq_uk':    round(c['lq_uk']),
            'lq_us':    round(c['lq_us']),
            'units':    c['tu'],
            'gm':       _r4(c.get('gm')),
            'vs_lq':    vs_lq,
            'yoy_dir':  badge_class(vs_lq),
            'd2c':      round(c['d2c']),
            'b2b':      round(c['b2b']),
            'uk':       round(c['uk_s']),
            'us':       round(c['us_s']),
            'row':      round(c.get('row_s', 0)),
            'skus':     skus_list,
        }
    return result


# ── MOVERS -- top-10 rising + top-10 falling, Live-status only ──────────────
# (BRIEF #4 step 4 item 4). "Live" spans both status vocabularies a SKU's
# uk_status/us_status might carry (see LIVE_STATUS_VALUES above) --
# Discontinued/Dead/Not For Sale/Pre-Launch/Disco to Resource are excluded
# on both sides, same for a SKU with no live status at all.
#
# Grain: SKU, not collection -- step-4-follow-up §5 flagged that the
# earlier D1 contract-schema outline (BRIEF_claude_code_3_dashboard_v2.md)
# described movers at collection grain, while this (and the pre-redesign
# dashboard's own "Month-on-Month Collection Movers" section it replaces)
# ships at SKU grain. This is deliberate, not a drift: item 4's own
# "Live-status only" requirement is defined in terms of uk_status/us_status,
# which only exists per SKU -- a collection has no live/discontinued status
# of its own to filter on (it's a mix of SKUs in every status). SKU grain
# is the only grain the Live-status filter can actually apply to; the D1
# outline's "collection" framing is superseded by item 4's own requirement,
# not overlooked.

def _sku_newness(s):
    """'Newness'/'Continuity'/None for one SKU, across BOTH status
    vocabularies this codebase carries (see LIVE_STATUS_VALUES' own
    docstring in contract.py): the oracle path's uk_status/us_status ARE
    already the coarse bucket strings directly (confirmed against real
    data: extract_skus_all's status columns literally read 'Continuity'/
    'Newness'/'Discontinued'/...); the Matrixify path's uk_status/us_status
    are Line Detail's finer enum ('Live', not 'Newness'), so its bucket
    comes from the separate newness_bucket field instead (line_detail.py's
    own newness_bucket, added to skus_all for exactly this split -- T4b).
    Newness takes priority if the two markets ever disagree, same
    either-market-counts-as-live spirit as the overall Live filter below.
    """
    statuses = {s.get('uk_status'), s.get('us_status')}
    if 'Newness' in statuses:
        return 'Newness'
    if 'Continuity' in statuses:
        return 'Continuity'
    return s.get('newness_bucket')


def compute_movers(skus_all, top_n=10):
    """T4b (trading review round 1): movers split into separate Newness and
    Continuity sections -- still SKU-based, Live-status-only (§4, unchanged),
    just partitioned by bucket rather than one blended rising/falling pair.
    """
    live = [
        s for s in skus_all
        if (s.get('uk_status') in LIVE_STATUS_VALUES or s.get('us_status') in LIVE_STATUS_VALUES)
        and s.get('vslq') is not None
    ]

    def _mk(s):
        return {
            'sku': s['sku'], 'desc': s['desc'], 'coll': s['coll'], 'type_': s['type_'],
            'sales': round(s['gross']), 'vs_lq': _r4(s.get('vslq')),
        }

    def _rising_falling(bucket_skus):
        rising = sorted(bucket_skus, key=lambda s: -s['vslq'])[:top_n]
        falling = sorted(bucket_skus, key=lambda s: s['vslq'])[:top_n]
        return {'rising': [_mk(s) for s in rising], 'falling': [_mk(s) for s in falling]}

    newness_skus = [s for s in live if _sku_newness(s) == 'Newness']
    continuity_skus = [s for s in live if _sku_newness(s) == 'Continuity']

    return {'newness': _rising_falling(newness_skus), 'continuity': _rising_falling(continuity_skus)}


# ── MATRIX -- revenue x GM bubble points + size-legend values ───────────────
# (BRIEF #4 step 4 §4/§6). GM lives only here now that the headline KPI
# slot is YoY growth%. Size metric is units (bubble area), matching the
# pre-redesign scatter's own "size = units" convention.

def compute_matrix(collections_raw, top_n=40):
    rows = [c for c in collections_raw if c.get('ts') and c['ts'] > 0 and c.get('gm') is not None]
    top = sorted(rows, key=lambda c: -c['ts'])[:top_n]
    points = [{
        'c': c['c'], 't': c['t'],
        'revenue': round(c['ts'], 2), 'gm': _r4(c.get('gm')), 'size': c.get('tu') or 0,
    } for c in top]
    sizes = [p['size'] for p in points if p['size']]
    size_key = {
        'min': min(sizes) if sizes else 0,
        'max': max(sizes) if sizes else 0,
        'label': 'units',
    }
    return {'points': points, 'size_key': size_key}


# ── Static KPI tokens ─────────────────────────────────────────────────────────

def _kpi_rev_trend_html(trend_3mo):
    """T1: two small coloured arrows summarising a genuine trailing-3-
    consecutive-months revenue series (see contract.py's emit_contract_from_
    matrixify docstring for how trend_3mo is sourced) -- '' (nothing
    rendered) when unavailable, never a fabricated trend.
    """
    if not trend_3mo or len(trend_3mo) != 3:
        return ''
    mm2, mm1, cm = trend_3mo
    glyph1 = '▲' if mm1 >= mm2 else '▼'
    glyph2 = '▲' if cm >= mm1 else '▼'
    tip = f'Trailing 3 months: {fmt_gbp(mm2)} → {fmt_gbp(mm1)} → {fmt_gbp(cm)}'
    return (
        f'<span class="kpi-trend" title="{tip}">'
        f'<span style="color:{arrow_color(mm2, mm1)}">{glyph1}</span>'
        f'<span style="color:{arrow_color(mm1, cm)}">{glyph2}</span>'
        f'</span>'
    )


def compute_kpi_tokens(current, lm, pm, mode='month'):
    c = current
    total   = c['total_sales']
    lm_d2c  = lm['d2c']
    lm_tot  = lm['total']
    d2c_share     = c['d2c_gbp'] / total if total else 0
    lm_d2c_share  = lm_d2c / lm_tot if lm_tot else 0
    lm_b2b_share  = (lm.get('b2b') / lm_tot) if lm_tot else None
    b2b_share     = c.get('b2b_share')
    b2b_share_delta = (b2b_share - lm_b2b_share) if (b2b_share is not None and lm_b2b_share is not None) else None
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
        # T1: trailing-3-consecutive-months trend arrows beside the Total
        # Revenue KPI -- distinct from the MoM ribbon's LY/LM/CM trajectory
        # below. current['trend_3mo'] is [M-2, M-1, M] or None (no prior
        # month's contract available yet, e.g. the earliest Matrixify month
        # this repo has); the template hides the arrows entirely when null.
        'KPI_REV_TREND_HTML': _kpi_rev_trend_html(c.get('trend_3mo')),

        # KPI — Units
        'KPI_UNITS_VAL':     f"{int(c['units']):,}",
        'KPI_UNITS_LM_CLS':  badge_class(c.get('units_vs_lm')),
        'KPI_UNITS_LM':      fmt_pct(c.get('units_vs_lm')),
        'KPI_UNITS_LY_CLS':  badge_class(c.get('units_vs_ly')),
        'KPI_UNITS_LY':      fmt_pct(c.get('units_vs_ly')),

        # KPI — YoY Growth % (replaces the headline GM% slot -- BRIEF #4
        # step 4 §1/§6, item 11. GM itself is not deleted, it moves to the
        # revenue x GM matrix only.)
        'KPI_YOY_VAL':     fmt_pct(c.get('yoy_growth_pct')),
        'KPI_YOY_UK_CLS':  badge_class(c.get('uk_vs_ly')),
        'KPI_YOY_UK':      fmt_pct(c.get('uk_vs_ly')),
        'KPI_YOY_US_CLS':  badge_class(c.get('us_vs_ly')),
        'KPI_YOY_US':      fmt_pct(c.get('us_vs_ly')),

        # KPI — D2C Share
        'KPI_D2C_SHARE':    f'{d2c_share*100:.1f}%',
        'KPI_D2C_LM_CLS':   badge_class(d2c_share - lm_d2c_share),
        'KPI_D2C_LM':       ('+' if d2c_share >= lm_d2c_share else '') + f'{(d2c_share - lm_d2c_share)*100:.1f}pp',

        # KPI — B2B Share of Revenue (new -- BRIEF #4 step 4 §1/§6, item 1.
        # Labelled "share of revenue", not "B2B %", because D2C% + B2B%
        # does not sum to 100 -- channel doesn't partition the total,
        # country does; see contract.py's _add_headline_kpis docstring.)
        'KPI_B2B_SHARE':    fmt_pct(b2b_share, force_sign=False),
        'KPI_B2B_LM_CLS':   badge_class(b2b_share_delta),
        'KPI_B2B_LM':       (('+' if b2b_share_delta >= 0 else '') + f'{b2b_share_delta*100:.1f}pp') if b2b_share_delta is not None else '—',

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
    }

    # MoM Ribbon — Total trajectory
    gp  = lm['total'] if lm else 0   # uses ly actually via ribbon spec
    # Re-reading: Total trajectory uses LY(GP7) → LM(EX7) → CM(F7)
    # We need LY total from the ly block (passed separately)
    # This function doesn't have ly block; store for caller to add ribbon tokens
    toks['_period_labels'] = period_labels  # internal use
    return toks


def compute_ribbon_tokens(current, lm, ly, pm, mode='month'):
    """Return ribbon tokens for the MoM/QoQ ribbon section. BRIEF #4 step 4
    §9: the comparator label must come from `mode`, not be hardcoded, so
    quarterly reuses this unchanged and correctly says "QoQ" instead of
    "MoM" -- vs-LY stays "YoY" in both modes per the same section.
    """
    period_comp_label = 'QoQ' if mode == 'quarter' else 'MoM'
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
        'RIB_TOTAL_LM_BADGE':   f'{_sign_pct(total_vs_lm)} {period_comp_label}',
        'RIB_TOTAL_LY_CLS':     badge_class(total_vs_ly),
        'RIB_TOTAL_LY_BADGE':   f'{_sign_pct(total_vs_ly)} YoY',

        # UK trajectory
        'RIB_UK_LY_VAL':     fmt_gbp(ly_uk),
        'RIB_UK_ARR1_COLOR': arrow_color(ly_uk, lm_uk),
        'RIB_UK_LM_VAL':     fmt_gbp(lm_uk),
        'RIB_UK_ARR2_COLOR': arrow_color(lm_uk, cm_uk),
        'RIB_UK_CM_VAL':     fmt_gbp(cm_uk),
        'RIB_UK_LM_CLS':     badge_class(uk_vs_lm),
        'RIB_UK_LM_BADGE':   f'{_sign_pct(uk_vs_lm)} {period_comp_label}',
        'RIB_UK_LY_CLS':     badge_class(uk_vs_ly),
        'RIB_UK_LY_BADGE':   f'{_sign_pct(uk_vs_ly)} YoY',

        # US trajectory
        'RIB_US_LY_VAL':     fmt_gbp(ly_us),
        'RIB_US_ARR1_COLOR': arrow_color(ly_us, lm_us),
        'RIB_US_LM_VAL':     fmt_gbp(lm_us),
        'RIB_US_ARR2_COLOR': arrow_color(lm_us, cm_us),
        'RIB_US_CM_VAL':     fmt_gbp(cm_us),
        'RIB_US_LM_CLS':     badge_class(us_vs_lm),
        'RIB_US_LM_BADGE':   f'{_sign_pct(us_vs_lm)} {period_comp_label}',
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
        vs_lq = f'{c["vs_lq"]}' if c.get('vs_lq') is not None else '0'
        rows.append(
            f'  {{r:{c["r"]},t:"{c["t"]}",c:"{c["c"]}",'
            f'ts:{c["ts"]},tu:{c["tu"]},vs_lq:{vs_lq},'
            f'gm:{gm},'
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
            f'gm:{_js_val(s["gm"])}}}'
        )
    return 'const STATUSES = [\n' + ',\n'.join(rows) + '\n];'


def js_block_prod_types(prod_types):
    rows = []
    for t in prod_types:
        subcats = '[' + ','.join(
            f'{{name:{_js_val(sc["name"])},sales:{sc["sales"]},units:{sc["units"]},vs_ly:{_js_val(sc["vs_ly"])}}}'
            for sc in t.get('subcats', [])
        ) + ']'
        rows.append(
            f'  {{t:"{t["t"]}",sales:{t["sales"]},ly_sales:{_js_val(t.get("ly_sales"))},'
            f'lq_sales:{_js_val(t.get("lq_sales"))},d2c:{t.get("d2c",0)},b2b:{t.get("b2b",0)},'
            f'uk:{t.get("uk",0)},us:{t.get("us",0)},units:{t["units"]},'
            f'vs_lq:{_js_val(t["vs_lq"])},vs_ly:{_js_val(t.get("vs_ly"))},yoy_dir:{_js_val(t.get("yoy_dir"))},'
            f"gm:{_js_val(t['gm'])},color:{_js_val(t['color'])},textColor:{_js_val(t['textColor'])},"
            f'uk_vs_lq:{_js_val(t.get("uk_vs_lq"))},us_vs_lq:{_js_val(t.get("us_vs_lq"))},'
            f'subcats:{subcats}}}'
        )
    return 'const PROD_TYPES = [\n' + ',\n'.join(rows) + '\n];'


def js_block_cat_top_collections(cat_top_collections):
    def _geo_dict(d):
        return f'{{uk:{_js_val(d["uk"])},us:{_js_val(d["us"])},total:{_js_val(d["total"])}}}'
    parts = ['const CAT_TOP_COLLECTIONS = {']
    for cat, rows in cat_top_collections.items():
        rows_str = '[' + ','.join(
            f'{{c:{_js_val(r["c"])},'
            f'cash:{{uk:{_js_val(r["cash"]["uk"])},us:{_js_val(r["cash"]["us"])},total:{_js_val(r["cash"]["total"])}}},'
            f'units:{{uk:{_js_val(r["units"]["uk"])},us:{_js_val(r["units"]["us"])},total:{_js_val(r["units"]["total"])}}},'
            f'b2b_share:{_js_val(r["b2b_share"])},'
            # C2 (round-2 review): same click-to-expand collection -> top-SKUs
            # drill compute_finish_data's top_collections already carries
            # (T3a) -- see js_block_finish_data's identical skus serialization.
            f'skus:[' + ','.join(
                f'{{sku:{_js_val(s["sku"])},desc:{_js_val(s["desc"])},sales:{s["sales"]},units:{s["units"]},'
                f'cash:{_geo_dict(s["cash"])},unitsByGeo:{_geo_dict(s["unitsByGeo"])}}}'
                for s in r.get('skus', [])
            ) + ']}'
            for r in rows
        ) + ']'
        parts.append(f'  {_js_key(cat)}:{rows_str},')
    parts[-1] = parts[-1].rstrip(',')
    parts.append('};')
    return '\n'.join(parts)


def js_block_movers(movers):
    # T4b: MOVERS is now {newness:{rising,falling}, continuity:{rising,falling}}
    # -- one blended rising/falling pair no longer exists.
    def _row(m):
        return (f'{{sku:{_js_val(m["sku"])},desc:{_js_val(m["desc"])},coll:{_js_val(m["coll"])},'
                f't:{_js_val(m["type_"])},sales:{m["sales"]},vs_lq:{_js_val(m["vs_lq"])}}}')
    def _list(rows):
        return ',\n'.join('    ' + _row(m) for m in rows)
    def _bucket(b):
        return (
            '{\n'
            f'    rising: [\n{_list(b["rising"])}\n    ],\n'
            f'    falling: [\n{_list(b["falling"])}\n    ]\n'
            '  }'
        )
    return (
        'const MOVERS = {\n'
        f'  newness: {_bucket(movers["newness"])},\n'
        f'  continuity: {_bucket(movers["continuity"])}\n'
        '};'
    )


def js_block_matrix(matrix):
    points = ',\n'.join(
        f'  {{c:{_js_val(p["c"])},t:{_js_val(p["t"])},revenue:{p["revenue"]},gm:{_js_val(p["gm"])},size:{p["size"]}}}'
        for p in matrix['points']
    )
    sk = matrix['size_key']
    return (
        'const MATRIX = {\n'
        f'  points: [\n{points}\n  ],\n'
        f'  size_key: {{min:{sk["min"]}, max:{sk["max"]}, label:{_js_val(sk["label"])}}}\n'
        '};'
    )


def js_block_skus(skus):
    rows = []
    for s in skus:
        vs_ly = _js_val(s.get('vs_ly'))
        rows.append(
            f'  {{r:{s["r"]},sku:"{s["sku"]}",d:"{_esc(s["d"])}",c:"{s["c"]}",'
            f't:"{s["t"]}",uk_s:"{s["uk_s"]}",us_s:"{s["us_s"]}",'
            f'total_sales:{s["total_sales"]},total_units:{s["total_units"]},'
            f'vs_lq:{_js_val(s["vs_lq"])},vs_ly:{vs_ly},'
            f'gm:{_js_val(s["gm"])},is_el:{_js_val(s["is_el_component"])},'
            f'uk_s2:{s["uk_s2"]},uk_u:{s["uk_u"]},'
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
        def _geo_dict(d):
            return f'{{uk:{_js_val(d["uk"])},us:{_js_val(d["us"])},total:{_js_val(d["total"])}}}'
        top_collections_str = '[' + ','.join(
            f'{{c:{_js_val(tc["c"])},sales:{tc["sales"]},units:{tc["units"]},b2b_share:{_js_val(tc["b2b_share"])},'
            f'cash:{_geo_dict(tc["cash"])},unitsByGeo:{_geo_dict(tc["unitsByGeo"])},'
            f'skus:[' + ','.join(
                f'{{sku:{_js_val(s["sku"])},desc:{_js_val(s["desc"])},sales:{s["sales"]},units:{s["units"]},'
                f'cash:{_geo_dict(s["cash"])},unitsByGeo:{_geo_dict(s["unitsByGeo"])}}}'
                for s in tc.get('skus', [])
            ) + ']}'
            for tc in fd['top_collections']
        ) + ']'
        parts.append(
            f"  '{name}': {{\n"
            f"    color:'{fd['color']}', textColor:'{fd['textColor']}',\n"
            f"    total:{fd['total']}, lq:{fd['lq']}, ly:{ly},"
            f" units:{fd['units']}, vsLQ:{fd['vsLQ']}, vsLY:{vsLY},\n"
            f"    d2c:{fd['d2c']}, b2b:{fd['b2b']}, b2b_share:{_js_val(fd.get('b2b_share'))},"
            f" uk:{fd['uk']}, us:{fd['us']}, uk_u:{fd['uk_u']}, us_u:{fd['us_u']},"
            f" lq_uk:{fd['lq_uk']}, lq_us:{fd['lq_us']},\n"
            f"    top_collections:{top_collections_str}\n"
            f"  }},"
        )
    parts[-1] = parts[-1].rstrip(',')
    parts.append('};')
    return '\n'.join(parts)


def js_block_coll_analysis(coll_analysis):
    parts = ['const COLL_ANALYSIS = {']
    for key, ca in coll_analysis.items():
        skus_str = '[' + ','.join(
            f"{{sku:'{_esc(s['sku'])}',d:'{_esc(s['d'])}',"
            f"cash:{{uk:{s['cash']['uk']},us:{s['cash']['us']},total:{s['cash']['total']}}},"
            f"units:{{uk:{s['units']['uk']},us:{s['units']['us']},total:{s['units']['total']}}},"
            f"share:{_js_val(s['share'])},vs_lq:{_js_val(s['vs_lq'])},yoy_dir:{_js_val(s['yoy_dir'])},"
            f"gm:{_js_val(s['gm'])}}}"
            for s in ca['skus']
        ) + ']'
        parts.append(
            f"  {_js_key(key)}:{{color:'{ca['color']}', sales:{ca['sales']},"
            f" lq:{ca['lq']},lq_uk:{ca['lq_uk']}, lq_us:{ca['lq_us']},"
            f" units:{ca['units']}, gm:{_js_val(ca['gm'])},"
            f" vs_lq:{_js_val(ca['vs_lq'])}, yoy_dir:{_js_val(ca['yoy_dir'])},"
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
            f" gm:{_js_val(s['gm'])},"
            f" d2c:{s['d2c']}, b2b:{s['b2b']}, uk:{s['uk']}, us:{s['us']}}}"
        )
    return 'const NEWNESS_SKUS = [\n' + ',\n'.join(rows) + '\n];'


def _esc(s):
    return str(s).replace('\\', '\\\\').replace('"', '\\"').replace("'", "\\'")
