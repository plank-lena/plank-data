"""Quarterly Trading builder (BRIEF step 5).

Aggregates three months into the exact same contract shape Step 4's
template/compute/render/validate layer already consumes unchanged --
`period_type`/`mode` is the only thing that changes, and it's read off
`payload["mode"]`. No template redesign; no new hosting (still an Option A
self-contained file).

THE ONE CORRECTNESS RULE (BRIEF step 5 §1): additive components sum;
every rate, ratio, %-share, GM, and YoY is recomputed at quarter level from
summed numerators and denominators -- never averaged across the three
monthly rate OUTPUTS. Concretely:
  - revenue, units, £-by-country, £-by-channel, £-by-department/finish/
    collection, cost-weighted-for-GM-numerator -- all summed directly.
  - GM%, D2C-GM%, B2B-GM% -- recomputed as Σ(monthly_rate × monthly_weight)
    / Σ(monthly_weight), i.e. revenue-weighted, never a plain mean of 3 %s.
  - YoY (vs_ly) at any grain that only carries a RATIO (not an absolute
    prior-year column) -- department/subcategory/status -- has its
    implied prior-year base reconstructed per month (current/(1+ratio)),
    SUMS those bases across months, then computes one ratio from the sums.
  - SKU-level LY comparison uses the sheet's own absolute 'ly' column
    (last-year-same-month sales, not a ratio) -- directly summable, no
    reconstruction needed.

Two front-ends, same shape as BRIEF #3's monthly split:
  emit_contract_from_oracle_quarter(month_xlsx_paths)   -- aggregates 3
    monthly ORACLE workbooks. Correct now; this is what Step 5 is built
    and validated against (see §2/§6 of the brief).
  emit_contract_from_matrixify_quarter(month_specs)      -- aggregates 3
    monthly MATRIXIFY-sourced contracts. Exists and is wired the same way,
    but cannot actually run yet: only May 2026's UK/US Matrixify exports
    are committed (trading/source/orders_2026-05_*.csv) -- April and June
    exports don't exist in this repo. Even once they do, this will stay
    PROVISIONAL until the deferred order-scope reconciliation (ROADMAP.md
    §3) closes, same as every monthly Matrixify contract today -- expected,
    per BRIEF step 5 §5, not a bug to chase here.

LQ (previous quarter) is genuinely unavailable for THIS first quarterly
build -- there is no committed prior quarterly contract, and the 3
monthly oracle files (Apr/May/Jun 2026) carry no Jan/Feb/Mar 2026 data to
reconstruct Q1 2026 from. Stamped as zero throughout (current['vs_lm'] etc.
come out None via _vs's own zero-guard) -- never fabricated -- while the
LABEL is still correct ("Q1 2026", BRIEF step 5 §3: only the label is
required, the values are honestly disclosed as unavailable). One real
consequence: QoQ movers are empty this quarter (no per-SKU LQ figure to
rank rising/falling by) -- the template already renders "No data" for an
empty MOVERS list, so this degrades honestly rather than crashing or
fabricating a ranking. Once a prior quarterly contract exists (Q3 vs Q2),
QoQ movers populate normally with no code change needed here.
"""
import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_DIR = os.path.join(_HERE, "dashboard")
for _p in (_HERE, _DASHBOARD_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from extract import extract_all  # trading/dashboard/extract.py
from contract import (
    PAYLOAD_KEYS, _wrap_contract, _add_headline_kpis, _is_el_component,
    _convert_oracle_weeks_cover, _exclude_dead_categories, _git_commit, _vs, _MONTH_NAMES, _current_to_lm_shape,
    emit_contract_from_matrixify, load_contract, can_publish,
)
from common.reconciliation_gate import assert_country_reconciles

_LY_LM_FIELDS = ('total', 'd2c', 'b2b', 'uk', 'us', 'row',
                  'total_u', 'd2c_u', 'b2b_u', 'uk_u', 'us_u', 'row_u')
_ADDITIVE_CURRENT_FIELDS = (
    'total_sales', 'units', 'd2c_gbp', 'b2b_gbp', 'uk_gbp', 'us_gbp', 'row_gbp',
    'd2c_units', 'b2b_units', 'uk_units', 'us_units', 'row_units',
)
_MONTH_ABBR_TO_NUM = {name[:3]: i + 1 for i, name in enumerate(_MONTH_NAMES)}


# ── Generic recomposition helpers (the §1 rule, applied wherever it's needed) ─

def _weighted_avg(pairs):
    """[(rate, weight), ...] -> Σ(rate·weight)/Σweight, or None. The
    revenue(or sales)-weighted average IS "recompute from summed
    components" for a ratio whose numerator is rate×weight -- never a
    plain mean of the input rates.
    """
    num = den = 0.0
    for rate, weight in pairs:
        if not isinstance(rate, (int, float)) or not weight:
            continue
        num += rate * weight
        den += weight
    return (num / den) if den else None


def _recompute_yoy(pairs):
    """[(current_value, vs_ly_ratio), ...], one pair per month -- used
    wherever the sheet gives a per-month YoY RATIO but no absolute prior-
    year column (department/subcategory/status tables). Each month's
    implied prior-year base is reconstructed (current/(1+ratio)) and the
    BASES are summed across months (not the ratios averaged); the
    quarter's own vs_ly is computed once from those summed components.
    """
    cur_total = base_total = 0.0
    any_base = False
    for cur, vs_ly in pairs:
        cur_total += cur or 0
        if not isinstance(vs_ly, (int, float)):
            continue
        denom = 1 + vs_ly
        if abs(denom) < 1e-9:
            continue
        base_total += (cur or 0) / denom
        any_base = True
    if not any_base or abs(base_total) < 1e-9:
        return None
    return (cur_total - base_total) / base_total


def _find(rows, key, value):
    return next((r for r in rows if r[key] == value), None)


def _union_ordered(items_per_month, key):
    """Preserve first-seen order across months, de-duplicated -- a stable,
    deterministic group order without inventing a sort key.
    """
    seen, ordered = set(), []
    for items in items_per_month:
        for item in items:
            k = item[key]
            if k not in seen:
                seen.add(k)
                ordered.append(k)
    return ordered


# ── Quarter period model ──────────────────────────────────────────────────────

def _quarter_of(month_num):
    return (month_num - 1) // 3 + 1


def _q_label(qtr, year):
    return {'label': f'Q{qtr} {year}', 'short': f"Q{qtr} '{str(year)[2:]}"}


def quarter_period_model(month_period_models):
    """3 monthly period_model['cm'] labels (e.g. 'Apr 2026') -> the
    quarter's own period_model. cm = this quarter; ly = same quarter last
    year (a real label -- the LY figures behind it are reconstructed from
    each month's own LY_BLOCK, see _aggregate_ly); lm = previous quarter
    (label only -- BRIEF step 5 §3 requires the label even though no data
    backs it yet, see module docstring).
    """
    parsed = []
    for pm in month_period_models:
        month_abbr, year = pm['cm']['label'].split()
        if month_abbr[:3] not in _MONTH_ABBR_TO_NUM:
            raise ValueError(f"quarterly: expected a monthly period label, got {pm['cm']['label']!r}")
        parsed.append((_MONTH_ABBR_TO_NUM[month_abbr[:3]], int(year)))
    parsed.sort()
    months_seen = [m for m, _ in parsed]
    years_seen = {y for _, y in parsed}
    if len(years_seen) != 1:
        raise ValueError(f"quarterly: the 3 months must share one year, got {parsed}")
    year = years_seen.pop()
    if months_seen != list(range(months_seen[0], months_seen[0] + 3)) or _quarter_of(months_seen[0]) != _quarter_of(months_seen[-1]):
        raise ValueError(f"quarterly: the 3 months must be one consecutive real quarter, got {parsed}")
    q = _quarter_of(months_seen[0])
    lq_q, lq_y = (4, year - 1) if q == 1 else (q - 1, year)
    return {'cm': _q_label(q, year), 'lm': _q_label(lq_q, lq_y), 'ly': _q_label(q, year - 1)}


# ── Per-block aggregation ─────────────────────────────────────────────────────

def _aggregate_current(months, lq=None):
    current = {f: sum((m['current'].get(f) or 0) for m in months) for f in _ADDITIVE_CURRENT_FIELDS}
    current['gm_pct'] = _weighted_avg([(m['current'].get('gm_pct'), m['current'].get('total_sales')) for m in months])
    current['d2c_gm'] = _weighted_avg([(m['current'].get('d2c_gm'), m['current'].get('d2c_gbp')) for m in months])
    current['b2b_gm'] = _weighted_avg([(m['current'].get('b2b_gm'), m['current'].get('b2b_gbp')) for m in months])
    return current


def _aggregate_ly(months):
    """Real Q(-1y) aggregate, reconstructed from each month's own LY_BLOCK
    -- no separate prior-year source files needed (see module docstring).
    """
    return {f: sum((m['ly'].get(f) or 0) for m in months) for f in _LY_LM_FIELDS}


def _zero_lm():
    """LQ (previous quarter) has no data source this run -- see module
    docstring. Zeros, never a fabricated figure; _vs()'s own zero-guard
    turns every vs_lm/vs_lq field into None downstream. Superseded by a
    real _current_to_lm_shape(lq['current']) once a prior quarterly
    contract is passed in as `lq_contract` -- see emit_contract_from_
    oracle_quarter.
    """
    return {f: 0 for f in _LY_LM_FIELDS}


def _aggregate_statuses(months, lq=None):
    """lq: a previously-committed quarter's own payload (PAYLOAD_KEYS-
    shaped), or None. When given, vs_lq is REAL -- a direct comparison
    against that quarter's own absolute sales, not a reconstruction --
    since lq carries genuine absolute figures at this grain, unlike the
    vs_ly reconstruction above (which exists only because the *monthly*
    sheet gives no absolute prior-year value at department/status grain).
    """
    lq_by_name = {s['s']: s for s in lq['statuses']} if lq else {}
    names = _union_ordered([m['statuses'] for m in months], 's')
    out = []
    for name in names:
        rows = [_find(m['statuses'], 's', name) for m in months]
        sales = sum((r['sales'] if r else 0) or 0 for r in rows)
        units = sum((r['units'] if r else 0) or 0 for r in rows)
        gm = _weighted_avg([(r['gm'], r['sales']) for r in rows if r])
        vs_ly = _recompute_yoy([(r['sales'], r.get('vs_ly')) for r in rows if r])
        prior = lq_by_name.get(name)
        vs_lq = _vs(sales, prior['sales']) if prior else None
        out.append({'s': name, 'sales': sales, 'units': units, 'vs_lq': vs_lq, 'vs_ly': vs_ly, 'gm': gm})
    return out


def _aggregate_prod_types(months, lq=None):
    lq_by_name = {t['t']: t for t in lq['prod_types']} if lq else {}
    dept_names = _union_ordered([m['prod_types'] for m in months], 't')
    out = []
    for name in dept_names:
        rows = [_find(m['prod_types'], 't', name) for m in months]
        sales = sum((r['sales'] if r else 0) or 0 for r in rows)
        units = sum((r['units'] if r else 0) or 0 for r in rows)
        # T2b: additive, same convention as _aggregate_current's channel/
        # country totals -- sum directly, never averaged.
        d2c = sum((r.get('d2c') if r else 0) or 0 for r in rows)
        b2b = sum((r.get('b2b') if r else 0) or 0 for r in rows)
        uk = sum((r.get('uk') if r else 0) or 0 for r in rows)
        us = sum((r.get('us') if r else 0) or 0 for r in rows)
        gm = _weighted_avg([(r['gm'], r['sales']) for r in rows if r])
        vs_ly = _recompute_yoy([(r['sales'], r.get('vs_ly')) for r in rows if r])
        prior = lq_by_name.get(name)
        vs_lq = _vs(sales, prior['sales']) if prior else None
        # T2b: real once a prior quarter's own contract is supplied (lq),
        # same "None until a real prior period exists" convention as
        # _aggregate_collections' lq_total -- never reconstructed here.
        lq_sales = prior['sales'] if prior else None
        # CA2 (round-3 review): per-market vs-LQ, same "real once a prior
        # quarter's own contract exists" convention as vs_lq above -- prior
        # quarter's own uk/us are already real absolute sums (aggregated the
        # same way this quarter's own uk/us are, a few lines up).
        uk_vs_lq = _vs(uk, prior['uk']) if prior and prior.get('uk') is not None else None
        us_vs_lq = _vs(us, prior['us']) if prior and prior.get('us') is not None else None

        lq_subcats = {sc['name']: sc for sc in prior.get('subcats', [])} if prior else {}
        subcat_names = _union_ordered([r.get('subcats', []) if r else [] for r in rows], 'name')
        subcats = []
        for sc_name in subcat_names:
            sc_rows = [_find(r['subcats'], 'name', sc_name) if r else None for r in rows]
            sc_sales = sum((sc['sales'] if sc else 0) or 0 for sc in sc_rows)
            sc_units = sum((sc['units'] if sc else 0) or 0 for sc in sc_rows)
            sc_vs_ly = _recompute_yoy([(sc['sales'], sc.get('vs_ly')) for sc in sc_rows if sc])
            sc_prior = lq_subcats.get(sc_name)
            sc_vs_lq = _vs(sc_sales, sc_prior['sales']) if sc_prior else None
            subcats.append({'name': sc_name, 'sales': sc_sales, 'units': sc_units,
                            'vs_lq': sc_vs_lq, 'vs_ly': sc_vs_ly})

        out.append({'t': name, 'sales': sales, 'units': units, 'vs_lq': vs_lq,
                    'vs_ly': vs_ly, 'gm': gm, 'subcats': subcats,
                    'd2c': d2c, 'b2b': b2b, 'uk': uk, 'us': us, 'lq_sales': lq_sales,
                    'uk_vs_lq': uk_vs_lq, 'us_vs_lq': us_vs_lq})
    return out


def _aggregate_finishes(months, lq=None):
    lq_finishes = lq['finishes'] if lq else {}
    names = []
    seen = set()
    for m in months:
        for name in m['finishes']:
            if name not in seen:
                seen.add(name)
                names.append(name)
    out = {}
    for name in names:
        rows = [m['finishes'].get(name) for m in months]
        total = sum((r['total'] if r else 0) or 0 for r in rows)
        prior = lq_finishes.get(name)
        # vsLY (audit finding, 2026-08-12, item 9): hardcoded None
        # unconditionally before this fix, even though each month's own
        # finish dict already carries a real vsLY ratio (contract.py) --
        # same _recompute_yoy reconstruction technique _aggregate_statuses/
        # _aggregate_prod_types already use, just never applied here. Note
        # the monthly key is 'vsLY' (contract.py's finishes dict), not the
        # 'vs_ly' snake_case key statuses/prod_types use.
        vs_ly = _recompute_yoy([(r['total'], r.get('vsLY')) for r in rows if r])
        out[name] = {
            'total': total,
            'units': sum((r['units'] if r else 0) or 0 for r in rows),
            'vsLQ': _vs(total, prior.get('total')) if prior else None, 'vsLY': vs_ly,
            'd2c': sum((r['d2c'] if r else 0) or 0 for r in rows),
            'b2b': sum((r['b2b'] if r else 0) or 0 for r in rows),
            'uk': sum((r['uk'] if r else 0) or 0 for r in rows),
            'us': sum((r['us'] if r else 0) or 0 for r in rows),
            # Round-2 review: never summed here even after T3b added these
            # to the monthly finish_totals/finishes shape in contract.py --
            # left this quarterly aggregator not knowing about them at all,
            # so compute_finish_data's raw.get('uk_u')/('us_u') fell back to
            # 0 for every finish on a quarterly build. Confirmed reproduction:
            # toggling Finish Analysis to UK or US on the Q2 2026 Matrixify
            # dashboard showed 0 units for every finish before this fix.
            'uk_u': sum((r.get('uk_u') if r else 0) or 0 for r in rows),
            'us_u': sum((r.get('us_u') if r else 0) or 0 for r in rows),
        }
    return out


def _aggregate_skus(months, lq=None):
    """Union of every SKU that sold in any of the 3 months (BRIEF step 5
    §4). Cash/units sum directly. gm is revenue-weighted across the
    months the SKU appeared in. 'ly' (last-YEAR-same-month sales) is the
    sheet's own ABSOLUTE prior-year column -- not a ratio -- so it sums
    directly across months into a real Q(-1y) per-SKU figure, no
    reconstruction needed. 'lq'/'vslq' (last quarter / QoQ movement) are
    real once `lq` (a previously-committed quarter's own payload) is
    given -- a direct lookup of that SKU's own prior-quarter gross, the
    same absolute-value comparison as vs_ly above, just one quarter back
    instead of one year. This is what lets QoQ movers populate for real
    once a prior quarterly contract exists; None (never fabricated) the
    first time a quarter is built, same as every other "no prior period
    committed yet" case in this codebase.
    Static attributes (desc/coll/type_/finish/status) take the LATEST
    month's value where present -- "as of quarter end", same convention
    the Matrixify path's newness_bucket already uses (BRIEF step 5 §4).
    """
    lq_skus = {s['sku']: s for s in lq['skus_all']} if lq else {}
    totals = {}
    for m in months:  # months must already be in chronological order
        for s in m['skus_all']:
            t = totals.setdefault(s['sku'], {
                'gross': 0.0, 'units': 0, 'd2c': 0.0, 'b2b': 0.0,
                'uk': 0.0, 'uk_u': 0, 'us': 0.0, 'us_u': 0,
                'ly': 0.0, '_ly_seen': False, 'gm_num': 0.0, 'gm_den': 0.0,
            })
            t['gross'] += s.get('gross') or 0
            t['units'] += s.get('units') or 0
            t['d2c'] += s.get('d2c') or 0
            t['b2b'] += s.get('b2b') or 0
            t['uk'] += s.get('uk') or 0
            t['uk_u'] += s.get('uk_u') or 0
            t['us'] += s.get('us') or 0
            t['us_u'] += s.get('us_u') or 0
            if s.get('ly'):
                t['ly'] += s['ly']
                t['_ly_seen'] = True
            if isinstance(s.get('gm'), (int, float)) and s.get('gross'):
                t['gm_num'] += s['gross'] * s['gm']
                t['gm_den'] += s['gross']
            # T4b: newness_bucket carried through "as of quarter end" too --
            # same static-attribute convention as desc/coll/type_/finish/
            # status (latest month's value wins), needed for the Matrixify
            # quarterly path's own Newness/Continuity movers split.
            for field in ('desc', 'coll', 'type_', 'finish', 'uk_status', 'us_status', 'newness_bucket'):
                if s.get(field):
                    t[field] = s[field]

    skus_all = []
    for sku, t in totals.items():
        prior = lq_skus.get(sku)
        prior_gross = prior['gross'] if prior else None
        skus_all.append({
            'rank': None, 'sku': sku, 'desc': t.get('desc') or sku,
            'coll': t.get('coll') or '', 'type_': t.get('type_') or 'Unknown',
            'finish': t.get('finish') or '', 'uk_status': t.get('uk_status') or '',
            'us_status': t.get('us_status') or '',
            'gross': t['gross'], 'units': t['units'],
            'vslq': _vs(t['gross'], prior_gross) if prior_gross is not None else None,
            'gm': (t['gm_num'] / t['gm_den']) if t['gm_den'] else None,
            'd2c': t['d2c'], 'b2b': t['b2b'], 'uk': t['uk'], 'uk_u': t['uk_u'],
            'us': t['us'], 'us_u': t['us_u'],
            'lq': prior_gross, 'ly': (t['ly'] if t['_ly_seen'] else None),
            'newness_bucket': t.get('newness_bucket'),
        })
    return skus_all


def _aggregate_collections(months, skus_all, lq=None):
    """Cash/units summed directly from each month's own By Collection
    figures (accurate at source -- not reconstructed from SKU residuals).
    'skus' (distinct-SKU count) is recomputed from the aggregated quarter
    SKU set, since a COUNT of distinct things needs the union, unlike cash.
    vs_lq is real once `lq` (a previously-committed quarter's own payload)
    is given -- direct comparison against that quarter's own ts for the
    same (department, collection) key.

    Keyed on (department, collection name) jointly, not name alone: several
    real collection names collide across different (mostly blank/'Unknown')
    departments (e.g. a Cabinetry BECKER and an Unknown-department BECKER
    are two different collections that happen to share a name) -- a
    name-only lookup would silently merge or drop one of them.
    """
    keys = _union_ordered([[{'k': (c['t'], c['c'])} for c in m['collections']] for m in months], 'k')

    sku_count = {}
    for s in skus_all:
        if s['coll']:
            k = (s['type_'], s['coll'])
            sku_count[k] = sku_count.get(k, 0) + 1

    def _find_coll(coll_rows, dept, coll):
        return next((r for r in coll_rows if r['t'] == dept and r['c'] == coll), None)

    lq_collections = lq['collections'] if lq else []

    agg = {}
    for dept, coll in keys:
        rows = [_find_coll(m['collections'], dept, coll) for m in months]
        ts = sum((r['ts'] if r else 0) or 0 for r in rows)
        gm = _weighted_avg([(r['gm'], r['ts']) for r in rows if r])
        prior = _find_coll(lq_collections, dept, coll)
        uk_s = sum((r['uk_s'] if r else 0) or 0 for r in rows)
        us_s = sum((r['us_s'] if r else 0) or 0 for r in rows)
        agg[(dept, coll)] = {
            'ts': ts,
            'tu': sum((r['tu'] if r else 0) or 0 for r in rows),
            'd2c': sum((r['d2c'] if r else 0) or 0 for r in rows),
            'b2b': sum((r['b2b'] if r else 0) or 0 for r in rows),
            'uk_s': uk_s,
            'us_s': us_s,
            'row_s': sum((r.get('row_s', 0) if r else 0) or 0 for r in rows),
            'gm': gm, 'skus': sku_count.get((dept, coll), 0),
            'vs_lq': _vs(ts, prior['ts']) if prior else None,
            # Audit finding (2026-08-12, item 9): lq_total/lq_uk/lq_us/uk_vs/
            # us_vs were hardcoded 0.0/None unconditionally EVEN WHEN `prior`
            # (a real previously-committed quarter, looked up two lines
            # above for vs_lq) was available -- the template's LQ ghost
            # marker (renderCollections()) would silently show no marker on
            # a quarter that has real prior-quarter data to draw one from.
            # Same _vs() technique already used for vs_lq, just applied to
            # the uk_s/us_s split too.
            'lq_total': prior['ts'] if prior else 0.0,
            'lq_uk': prior['uk_s'] if prior else 0.0,
            'lq_us': prior['us_s'] if prior else 0.0,
            'uk_vs': _vs(uk_s, prior['uk_s']) if prior else None,
            'us_vs': _vs(us_s, prior['us_s']) if prior else None,
        }

    ordered = sorted(agg.items(), key=lambda kv: -kv[1]['ts'])
    return [{
        'r': i + 1, 't': dept, 'c': coll, 'ts': v['ts'], 'tu': v['tu'],
        'vs_lq': v['vs_lq'], 'gm': v['gm'],
        'd2c': v['d2c'], 'b2b': v['b2b'], 'uk_s': v['uk_s'], 'us_s': v['us_s'],
        'row_s': v['row_s'], 'lq_total': v['lq_total'], 'lq_uk': v['lq_uk'],
        'lq_us': v['lq_us'], 'uk_vs': v['uk_vs'], 'us_vs': v['us_vs'],
        'skus': v['skus'],
    } for i, ((dept, coll), v) in enumerate(ordered)]


def _aggregate_quarter_payload(months, lq=None):
    """months: 3 dicts, chronological order, each shaped like a single-
    period contract payload (PAYLOAD_KEYS) -- whichever front-end produced
    them (extract_all() directly for oracle, load_contract(emit_contract_
    from_matrixify(...)) for Matrixify; both converge on this one shape,
    same principle as BRIEF #3's single-schema, dual-emitter design).
    lq: a previously-committed QUARTERLY contract's own payload (already
    load_contract()-ed), or None. When given, every vs_lq at every grain
    (headline, statuses, prod_types/subcats, finishes, collections,
    skus_all) is a REAL comparison against that quarter's own absolute
    figures -- not a reconstruction, an exact prior-period lookup, since
    a committed prior quarter genuinely has the data other periods don't.
    Returns a payload dict, mode='quarter', current['vs_*'] already
    computed, ready for _add_headline_kpis + _is_el_component + strip +
    _wrap_contract.
    """
    period_model = quarter_period_model([m['period_model'] for m in months])

    current = _aggregate_current(months)
    lm = _current_to_lm_shape(lq['current']) if lq else _zero_lm()
    ly = _aggregate_ly(months)
    current['vs_lm'] = _vs(current['total_sales'], lm['total'])
    current['vs_ly'] = _vs(current['total_sales'], ly['total'])
    current['units_vs_lm'] = _vs(current['units'], lm['total_u'])
    current['units_vs_ly'] = _vs(current['units'], ly['total_u'])
    current['uk_vs_lm'] = _vs(current['uk_gbp'], lm['uk'])
    current['uk_vs_ly'] = _vs(current['uk_gbp'], ly['uk'])
    current['us_vs_lm'] = _vs(current['us_gbp'], lm['us'])
    current['us_vs_ly'] = _vs(current['us_gbp'], ly['us'])

    skus_all = _aggregate_skus(months, lq=lq)
    collections = _aggregate_collections(months, skus_all, lq=lq)

    return {
        'mode': 'quarter',
        'period_model': period_model,
        'current': current, 'lm': lm, 'ly': ly,
        'statuses': _aggregate_statuses(months, lq=lq),
        'prod_types': _aggregate_prod_types(months, lq=lq),
        'finishes': _aggregate_finishes(months, lq=lq),
        'collections': collections,
        'skus_all': skus_all,
    }


# ── Oracle front-end ─────────────────────────────────────────────────────────

def emit_contract_from_oracle_quarter(month_xlsx_paths, lq_contract=None, out_path=None):
    """The oracle-sourced quarterly contract (BRIEF step 5) -- aggregates 3
    monthly oracle workbooks, in chronological order, into the exact shape
    emit_contract_from_oracle produces for a month. Correct now; this is
    what Step 5 is validated against (brief §2/§6).

    lq_contract: a previously-committed quarterly contract (dict or path to
    its .json), or None. Passing the prior quarter's own contract here is
    what makes LQ (vs_lq) real instead of zero/None -- e.g. Q2 2026 passing
    the committed Q1 2026 contract. Self-heals going forward with no code
    change: build and commit each quarter in order, and it becomes the next
    quarter's lq_contract.
    """
    if len(month_xlsx_paths) != 3:
        raise ValueError(f"emit_contract_from_oracle_quarter needs exactly 3 monthly oracle files, got {len(month_xlsx_paths)}")

    months = [extract_all(p) for p in month_xlsx_paths]
    for m, p in zip(months, month_xlsx_paths):
        if m['mode'] != 'month':
            raise ValueError(f"{p}: expected a MONTHLY oracle workbook (got mode={m['mode']!r}) -- "
                              "aggregate 3 monthly files, don't pass an already-quarterly one")

    lq = load_contract(lq_contract) if lq_contract is not None else None
    payload = _aggregate_quarter_payload(months, lq=lq)
    _add_headline_kpis(payload['current'])
    for sku in payload['skus_all']:
        sku['is_el_component'] = _is_el_component(sku.get('coll'))
    _convert_oracle_weeks_cover(payload)
    # T2a: the monthly oracle files feed _aggregate_quarter_payload via raw
    # extract_all(), bypassing emit_contract_from_oracle's own dead-category
    # filter -- apply it here too so Door doesn't leak back in at the quarter
    # grain.
    _exclude_dead_categories(payload)

    provenance = {
        'source': 'oracle_quarter_aggregate',
        'reconciled': True,
        'revenue_basis': 'net_sales_exvat_AB',
        'returns_netted': True,
        'fx_table': None,
        'enrichment_coverage': None,
        'unmatched_sku_revenue_share': None,
        'country_gaps_vs_oracle': None,
        # LY is always real (reconstructed from each month's own LY_BLOCK).
        # LQ is real only once a prior quarterly contract was supplied as
        # lq_contract -- disclosed explicitly here rather than silently
        # merged into one "bootstrap" label that would overstate what's
        # actually backed by data on a quarter's first-ever build.
        'lq_ly_source': ('ly_from_monthly_oracle_blocks__lq_from_committed_quarter' if lq
                          else 'ly_from_monthly_oracle_blocks__lq_unavailable'),
        'lq_source_period': lq['period_model']['cm']['label'] if lq else None,
        'aggregated_from': [os.path.basename(p) for p in month_xlsx_paths],
        'built_at': datetime.now(timezone.utc).isoformat(),
        'commit': _git_commit(),
    }
    contract = _wrap_contract(payload, provenance)
    if out_path:
        with open(out_path, 'w') as f:
            json.dump(contract, f, indent=2, default=str)
    return contract


# ── Matrixify front-end ──────────────────────────────────────────────────────

def emit_contract_from_matrixify_quarter(month_specs, oracle_quarter_gaps=None, lq_contract=None,
                                          oracle_bootstrap_path=None, out_path=None,
                                          ly_month_contracts=None, month_oracle_bootstrap_paths=None,
                                          month_requested_period_models=None):
    """The Matrixify-sourced quarterly contract. month_specs: 3
    (period, uk_csv, us_csv) tuples in chronological order. lq_contract:
    same meaning as emit_contract_from_oracle_quarter's -- a previously-
    committed quarterly contract to chain real vs_lq from.

    Runnable as of 2026-08-04: all three months' UK/US Matrixify exports
    (April/May/June 2026) are committed to trading/source/. Raises
    FileNotFoundError naming the missing export if a future quarter is
    attempted before its exports exist, rather than a cryptic failure deep
    in matrixify_source.py.

    `reconciled` is inherited from each month's own contract, which as of
    2026-08-05 (ROADMAP.md §5) is gated on the STRUCTURAL leak check
    (uk+us+row ties to an independent grand total) only -- matching the
    hand-built oracle to 0.1% is no longer required for publishing. See
    RECONCILE_HANDOFF.md for why: the oracle's returns figure reflects an
    early, still-maturing snapshot (~9-15 days post-close) that a
    deterministic rebuild cannot reproduce exactly by design.

    oracle_bootstrap_path (2026-08-05, B2 -- round 2 review): a real
    QUARTERLY-mode oracle workbook (e.g. 2026-Q2_Quarterly_Trading_Report.
    xlsx), used ONLY to backfill per-SKU vslq/lq when no lq_contract is
    available -- _aggregate_skus's own vslq is None for every SKU without a
    real prior quarter to chain from (confirmed: 0/928 SKUs on the first
    Q2 2026 build, which is what emptied Movers and the SKU Performance
    vs-LM columns on quarterly builds). This quarterly oracle file turns
    out to carry genuine Q1-vs-Q2 SKU-level data in its own sheet (896/924
    SKUs had a real vslq when checked directly) -- the same "the oracle
    file already has this, just extract and match by SKU code" technique
    T4a used for collections and the monthly per-SKU fix used for months,
    one level up. Matched by exact SKU code; only fills SKUs lq_contract-
    chaining left as None (a real lq_contract, when present, is a higher-
    fidelity source -- an actual previously-published quarter -- and wins).
    Does NOT touch any other block (collections/finishes/prod_types stay
    on their existing lq_contract-only mechanism) -- scoped precisely to
    the one field this bug report is about, not a general quarterly-oracle-
    bootstrap facility.

    ly_month_contracts (2026-08-06, B3 -- round-2 review): 3 real prior-year
    same-month Matrixify contracts (dict/path/None), one per month_specs
    entry, in the same chronological order -- forwarded as each constituent
    month's own ly_month_contract (see emit_contract_from_matrixify's
    docstring for what that backfills and its disclosed classification-
    coverage caveat). _aggregate_prod_types already reconstructs a
    quarter-level vs_ly from each month's own vs_ly ratio via
    _recompute_yoy -- no separate quarterly aggregation logic needed here,
    just feeding real monthly vs_ly in. None (the default) reproduces the
    prior all-None behaviour exactly.

    month_requested_period_models (2026-08-12, period-from-prompt): 3
    common.period.PeriodModel/None, one per month_specs entry -- forwarded
    as each constituent month's own requested_period_model, so each inner
    monthly contract can bootstrap its OWN lm/ly from fresh Matrixify pulls
    (emit_contract_from_matrixify's requested_period_model branch) with no
    workbook at all, same shape as month_oracle_bootstrap_paths below. Use
    common.period.months_in_quarter(quarter_num, year) + common.period.
    parse_period(...) to build these three month strings/models from a
    single quarter request ("Q2 2026") -- see tests/test_period.py.
    Deliberately does NOT attempt to bootstrap the QUARTER's own lq (the
    previous-quarter total) from scratch -- that would mean recursively
    building the entire previous quarter's 3-month contract, out of scope
    here. lq stays on the existing lq_contract/oracle_bootstrap_path
    mechanism, honest-zero when neither is available (ROADMAP.md's
    accepted "LQ genuinely unavailable for the first quarterly build"
    behaviour, unchanged).

    month_oracle_bootstrap_paths (2026-08-06, QQ1 -- round-3 review): 3
    real MONTHLY-mode oracle workbooks (path/None), one per month_specs
    entry -- forwarded as each constituent month's own oracle_bootstrap_
    path when building that month's contract internally. This was the
    real gap behind "quarterly has no YoY": each inner monthly contract
    was built with NO oracle_bootstrap_path at all (see the module
    docstring above), so every month's own lm/ly fell to the all-zero
    "none_available" branch, and _aggregate_ly's sum of 3 zeros was
    naturally zero too -- not a quarterly-aggregation bug, a missing
    per-month bootstrap one level down. Once each month has its own real
    oracle-bootstrapped headline ly (the same fixture build_matrixify_
    dashboard.py's standalone monthly build already uses), _aggregate_ly's
    sum is real and current['vs_ly'] follows. Composes with
    ly_month_contracts above unchanged: oracle_bootstrap_path never
    populates per-department ly_dept_sales (the oracle sheet has no
    per-department LY column, only vs_lq -- the whole reason B3 needed a
    separate real-2025-data source), so ly_month_contracts' backfill still
    applies on top. None per month (the default) reproduces the prior
    all-zero quarterly LY exactly.
    """
    if ly_month_contracts is not None and len(ly_month_contracts) != 3:
        raise ValueError(f"emit_contract_from_matrixify_quarter: ly_month_contracts must have "
                          f"3 entries (one per month_specs), got {len(ly_month_contracts)}")
    if month_oracle_bootstrap_paths is not None and len(month_oracle_bootstrap_paths) != 3:
        raise ValueError(f"emit_contract_from_matrixify_quarter: month_oracle_bootstrap_paths must "
                          f"have 3 entries (one per month_specs), got {len(month_oracle_bootstrap_paths)}")
    if len(month_specs) != 3:
        raise ValueError(f"emit_contract_from_matrixify_quarter needs exactly 3 months, got {len(month_specs)}")

    for period, uk_csv, us_csv in month_specs:
        for path in (uk_csv, us_csv):
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"emit_contract_from_matrixify_quarter: {period} Matrixify export not found: {path} "
                    "-- only May 2026's UK/US exports are committed today (see module docstring)."
                )

    ly_month_contracts = ly_month_contracts or [None, None, None]
    month_oracle_bootstrap_paths = month_oracle_bootstrap_paths or [None, None, None]
    month_requested_period_models = month_requested_period_models or [None, None, None]
    month_contracts = [
        emit_contract_from_matrixify(period=period, uk_csv=uk_csv, us_csv=us_csv,
                                      oracle_gaps=oracle_quarter_gaps, ly_month_contract=ly_mc,
                                      oracle_bootstrap_path=month_ob_path,
                                      requested_period_model=month_pm)
        for (period, uk_csv, us_csv), ly_mc, month_ob_path, month_pm
        in zip(month_specs, ly_month_contracts, month_oracle_bootstrap_paths, month_requested_period_models)
    ]
    months = [load_contract(c) for c in month_contracts]
    all_reconciled = all(c['provenance']['reconciled'] for c in month_contracts)

    lq = load_contract(lq_contract) if lq_contract is not None else None
    payload = _aggregate_quarter_payload(months, lq=lq)

    # B2: backfill per-SKU vslq/lq from the quarterly oracle bootstrap where
    # lq_contract-chaining left it None -- see this function's own docstring.
    if oracle_bootstrap_path is not None:
        oracle_q = extract_all(oracle_bootstrap_path)
        oracle_sku_lq = {
            sk['sku']: sk['lq'] for sk in oracle_q.get('skus_all', [])
            if isinstance(sk.get('lq'), (int, float))
        }
        matched = 0
        for s in payload['skus_all']:
            if s.get('vslq') is None and s['sku'] in oracle_sku_lq:
                lq_val = oracle_sku_lq[s['sku']]
                s['vslq'] = _vs(s['gross'], lq_val)
                s['lq'] = round(lq_val, 2)
                matched += 1
        print(f"quarterly: {matched}/{len(payload['skus_all'])} SKUs backfilled with the quarterly "
              f"oracle bootstrap's own per-SKU vs-LQ (exact SKU code match) -- unmatched ones keep "
              f"vslq=None/lq=None (a genuinely new SKU this quarter, or one the oracle sheet doesn't "
              f"carry), never fabricated.", file=sys.stderr)

    _add_headline_kpis(payload['current'])
    for sku in payload['skus_all']:
        sku['is_el_component'] = _is_el_component(sku.get('coll'))
    _convert_oracle_weeks_cover(payload)
    # T2a: harmless no-op in practice -- each month's own contract already
    # excluded dead departments at the line level before aggregation -- but
    # applied here too for the same reason as the oracle quarter aggregator.
    _exclude_dead_categories(payload)

    # Independent grand-total leak check, same convention as the monthly
    # gate (BRIEF #5) -- the aggregated country totals must still tie to
    # the aggregated grand total; this does NOT by itself make the quarter
    # "reconciled" against the oracle (that's all_reconciled below).
    country_totals = {'UK': payload['current']['uk_gbp'], 'US': payload['current']['us_gbp'],
                       'ROW': payload['current']['row_gbp']}
    try:
        assert_country_reconciles(country_totals, payload['current']['total_sales'])
    except AssertionError as e:
        print(f"quarterly: leak check FAILED -- {e}", file=sys.stderr)
        all_reconciled = False

    provenance = {
        'source': 'matrixify_quarter_aggregate',
        'reconciled': all_reconciled,
        'revenue_basis': 'net_sales_exvat_AB',
        'returns_netted': True,
        'fx_table': None,
        'enrichment_coverage': None,
        'unmatched_sku_revenue_share': None,
        'country_gaps_vs_oracle': [c['provenance'].get('country_gaps_vs_oracle') for c in month_contracts],
        # B2: the headline-level LQ this label already describes stays
        # unavailable either way (no real Q1 total_sales chain) -- the
        # oracle bootstrap only ever backfills the FINER-grained per-SKU
        # vslq (Movers/SKU Performance), so that's called out as its own
        # suffix rather than overloading what "lq_unavailable" means here.
        'lq_ly_source': ('ly_from_monthly_matrixify_blocks__lq_unavailable__sku_vslq_from_oracle_bootstrap'
                          if oracle_bootstrap_path
                          else 'ly_from_monthly_matrixify_blocks__lq_unavailable'),
        # B3: worst-case (max) of the 3 constituent months' own
        # ly_dept_unclassified_share -- same disclosure the monthly template
        # caveat reads, carried through so the quarterly Category Analysis
        # view caveats too. None if no constituent month had one.
        'ly_dept_unclassified_share': max(
            (s for c in month_contracts
             if (s := c['provenance'].get('ly_dept_unclassified_share')) is not None),
            default=None,
        ),
        'aggregated_from': [p for p, _, _ in month_specs],
        'built_at': datetime.now(timezone.utc).isoformat(),
        'commit': _git_commit(),
    }
    contract = _wrap_contract(payload, provenance)
    if out_path:
        with open(out_path, 'w') as f:
            json.dump(contract, f, indent=2, default=str)
    return contract
