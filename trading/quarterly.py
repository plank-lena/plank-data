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
    _strip_vestigial, _git_commit, _vs, _MONTH_NAMES,
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

def _aggregate_current(months):
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
    turns every vs_lm/vs_lq field into None downstream.
    """
    return {f: 0 for f in _LY_LM_FIELDS}


def _aggregate_statuses(months):
    names = _union_ordered([m['statuses'] for m in months], 's')
    out = []
    for name in names:
        rows = [_find(m['statuses'], 's', name) for m in months]
        sales = sum((r['sales'] if r else 0) or 0 for r in rows)
        units = sum((r['units'] if r else 0) or 0 for r in rows)
        gm = _weighted_avg([(r['gm'], r['sales']) for r in rows if r])
        vs_ly = _recompute_yoy([(r['sales'], r.get('vs_ly')) for r in rows if r])
        out.append({'s': name, 'sales': sales, 'units': units, 'vs_lq': None, 'vs_ly': vs_ly, 'gm': gm})
    return out


def _aggregate_prod_types(months):
    dept_names = _union_ordered([m['prod_types'] for m in months], 't')
    out = []
    for name in dept_names:
        rows = [_find(m['prod_types'], 't', name) for m in months]
        sales = sum((r['sales'] if r else 0) or 0 for r in rows)
        units = sum((r['units'] if r else 0) or 0 for r in rows)
        gm = _weighted_avg([(r['gm'], r['sales']) for r in rows if r])
        vs_ly = _recompute_yoy([(r['sales'], r.get('vs_ly')) for r in rows if r])

        subcat_names = _union_ordered([r.get('subcats', []) if r else [] for r in rows], 'name')
        subcats = []
        for sc_name in subcat_names:
            sc_rows = [_find(r['subcats'], 'name', sc_name) if r else None for r in rows]
            sc_sales = sum((sc['sales'] if sc else 0) or 0 for sc in sc_rows)
            sc_units = sum((sc['units'] if sc else 0) or 0 for sc in sc_rows)
            sc_vs_ly = _recompute_yoy([(sc['sales'], sc.get('vs_ly')) for sc in sc_rows if sc])
            subcats.append({'name': sc_name, 'sales': sc_sales, 'units': sc_units, 'vs_ly': sc_vs_ly})

        out.append({'t': name, 'sales': sales, 'units': units, 'vs_lq': None,
                    'vs_ly': vs_ly, 'gm': gm, 'subcats': subcats})
    return out


def _aggregate_finishes(months):
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
        out[name] = {
            'total': sum((r['total'] if r else 0) or 0 for r in rows),
            'units': sum((r['units'] if r else 0) or 0 for r in rows),
            'vsLQ': None, 'vsLY': None,
            'd2c': sum((r['d2c'] if r else 0) or 0 for r in rows),
            'b2b': sum((r['b2b'] if r else 0) or 0 for r in rows),
            'uk': sum((r['uk'] if r else 0) or 0 for r in rows),
            'us': sum((r['us'] if r else 0) or 0 for r in rows),
        }
    return out


def _aggregate_skus(months):
    """Union of every SKU that sold in any of the 3 months (BRIEF step 5
    §4). Cash/units sum directly. gm is revenue-weighted across the
    months the SKU appeared in. 'ly' (last-YEAR-same-month sales) is the
    sheet's own ABSOLUTE prior-year column -- not a ratio -- so it sums
    directly across months into a real Q(-1y) per-SKU figure, no
    reconstruction needed. 'lq'/'vslq' (last quarter / QoQ movement) are
    None -- no Q1 2026 SKU-level data exists this run (see module
    docstring); this is what makes this quarter's QoQ movers empty.
    Static attributes (desc/coll/type_/finish/status) take the LATEST
    month's value where present -- "as of quarter end", same convention
    the Matrixify path's newness_bucket already uses (BRIEF step 5 §4).
    """
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
            for field in ('desc', 'coll', 'type_', 'finish', 'uk_status', 'us_status'):
                if s.get(field):
                    t[field] = s[field]

    skus_all = []
    for sku, t in totals.items():
        skus_all.append({
            'rank': None, 'sku': sku, 'desc': t.get('desc') or sku,
            'coll': t.get('coll') or '', 'type_': t.get('type_') or 'Unknown',
            'finish': t.get('finish') or '', 'uk_status': t.get('uk_status') or '',
            'us_status': t.get('us_status') or '',
            'gross': t['gross'], 'units': t['units'], 'vslq': None,
            'gm': (t['gm_num'] / t['gm_den']) if t['gm_den'] else None,
            'd2c': t['d2c'], 'b2b': t['b2b'], 'uk': t['uk'], 'uk_u': t['uk_u'],
            'us': t['us'], 'us_u': t['us_u'],
            'lq': None, 'ly': (t['ly'] if t['_ly_seen'] else None),
        })
    return skus_all


def _aggregate_collections(months, skus_all):
    """Cash/units summed directly from each month's own By Collection
    figures (accurate at source -- not reconstructed from SKU residuals).
    'skus' (distinct-SKU count) is recomputed from the aggregated quarter
    SKU set, since a COUNT of distinct things needs the union, unlike cash.

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

    agg = {}
    for dept, coll in keys:
        rows = [_find_coll(m['collections'], dept, coll) for m in months]
        ts = sum((r['ts'] if r else 0) or 0 for r in rows)
        gm = _weighted_avg([(r['gm'], r['ts']) for r in rows if r])
        agg[(dept, coll)] = {
            'ts': ts,
            'tu': sum((r['tu'] if r else 0) or 0 for r in rows),
            'd2c': sum((r['d2c'] if r else 0) or 0 for r in rows),
            'b2b': sum((r['b2b'] if r else 0) or 0 for r in rows),
            'uk_s': sum((r['uk_s'] if r else 0) or 0 for r in rows),
            'us_s': sum((r['us_s'] if r else 0) or 0 for r in rows),
            'row_s': sum((r.get('row_s', 0) if r else 0) or 0 for r in rows),
            'gm': gm, 'skus': sku_count.get((dept, coll), 0),
        }

    ordered = sorted(agg.items(), key=lambda kv: -kv[1]['ts'])
    return [{
        'r': i + 1, 't': dept, 'c': coll, 'ts': v['ts'], 'tu': v['tu'],
        'vs_lq': None, 'gm': v['gm'],
        'd2c': v['d2c'], 'b2b': v['b2b'], 'uk_s': v['uk_s'], 'us_s': v['us_s'],
        'row_s': v['row_s'], 'lq_total': 0.0, 'lq_uk': 0.0, 'lq_us': 0.0,
        'uk_vs': None, 'us_vs': None, 'skus': v['skus'],
    } for i, ((dept, coll), v) in enumerate(ordered)]


def _aggregate_quarter_payload(months):
    """months: 3 dicts, chronological order, each shaped like a single-
    period contract payload (PAYLOAD_KEYS) -- whichever front-end produced
    them (extract_all() directly for oracle, load_contract(emit_contract_
    from_matrixify(...)) for Matrixify; both converge on this one shape,
    same principle as BRIEF #3's single-schema, dual-emitter design).
    Returns a payload dict, mode='quarter', current['vs_*'] already
    computed, ready for _add_headline_kpis + _is_el_component + strip +
    _wrap_contract.
    """
    period_model = quarter_period_model([m['period_model'] for m in months])

    current = _aggregate_current(months)
    lm = _zero_lm()
    ly = _aggregate_ly(months)
    current['vs_lm'] = _vs(current['total_sales'], lm['total'])
    current['vs_ly'] = _vs(current['total_sales'], ly['total'])
    current['units_vs_lm'] = _vs(current['units'], lm['total_u'])
    current['units_vs_ly'] = _vs(current['units'], ly['total_u'])
    current['uk_vs_lm'] = _vs(current['uk_gbp'], lm['uk'])
    current['uk_vs_ly'] = _vs(current['uk_gbp'], ly['uk'])
    current['us_vs_lm'] = _vs(current['us_gbp'], lm['us'])
    current['us_vs_ly'] = _vs(current['us_gbp'], ly['us'])

    skus_all = _aggregate_skus(months)
    collections = _aggregate_collections(months, skus_all)

    return {
        'mode': 'quarter',
        'period_model': period_model,
        'current': current, 'lm': lm, 'ly': ly,
        'statuses': _aggregate_statuses(months),
        'prod_types': _aggregate_prod_types(months),
        'finishes': _aggregate_finishes(months),
        'collections': collections,
        'skus_all': skus_all,
    }


# ── Oracle front-end ─────────────────────────────────────────────────────────

def emit_contract_from_oracle_quarter(month_xlsx_paths, out_path=None):
    """The oracle-sourced quarterly contract (BRIEF step 5) -- aggregates 3
    monthly oracle workbooks, in chronological order, into the exact shape
    emit_contract_from_oracle produces for a month. Correct now; this is
    what Step 5 is validated against (brief §2/§6).
    """
    if len(month_xlsx_paths) != 3:
        raise ValueError(f"emit_contract_from_oracle_quarter needs exactly 3 monthly oracle files, got {len(month_xlsx_paths)}")

    months = [extract_all(p) for p in month_xlsx_paths]
    for m, p in zip(months, month_xlsx_paths):
        if m['mode'] != 'month':
            raise ValueError(f"{p}: expected a MONTHLY oracle workbook (got mode={m['mode']!r}) -- "
                              "aggregate 3 monthly files, don't pass an already-quarterly one")

    payload = _aggregate_quarter_payload(months)
    _add_headline_kpis(payload['current'])
    for sku in payload['skus_all']:
        sku['is_el_component'] = _is_el_component(sku.get('coll'))
    _strip_vestigial(payload)

    provenance = {
        'source': 'oracle_quarter_aggregate',
        'reconciled': True,
        'revenue_basis': 'net_sales_exvat_AB',
        'returns_netted': True,
        'fx_table': None,
        'enrichment_coverage': None,
        'unmatched_sku_revenue_share': None,
        'country_gaps_vs_oracle': None,
        # LY is real (reconstructed from each month's own LY_BLOCK); LQ
        # (previous quarter) has no data source this run -- see module
        # docstring. Both are disclosed here, not silently merged into one
        # "bootstrap" label that would overstate what's actually backed by data.
        'lq_ly_source': 'ly_from_monthly_oracle_blocks__lq_unavailable',
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

def emit_contract_from_matrixify_quarter(month_specs, oracle_quarter_gaps=None, out_path=None):
    """The Matrixify-sourced quarterly contract. month_specs: 3
    (period, uk_csv, us_csv) tuples in chronological order.

    NOT runnable today: only trading/source/orders_2026-05_{UK,US}.csv are
    committed -- April and June Matrixify exports don't exist in this repo
    (BRIEF step 5 out of scope: exporting them). Will raise FileNotFoundError
    naming the missing export rather than a cryptic failure deep in
    matrixify_source.py.

    Even once all 3 months' exports exist, this stays PROVISIONAL until the
    deferred order-scope reconciliation closes (ROADMAP.md §3) -- every
    input month is already individually unreconciled for that same reason,
    so the aggregate can never be more reconciled than its inputs. Expected,
    per BRIEF step 5 §5 -- not something to chase from here.
    """
    if len(month_specs) != 3:
        raise ValueError(f"emit_contract_from_matrixify_quarter needs exactly 3 months, got {len(month_specs)}")

    for period, uk_csv, us_csv in month_specs:
        for path in (uk_csv, us_csv):
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"emit_contract_from_matrixify_quarter: {period} Matrixify export not found: {path} "
                    "-- only May 2026's UK/US exports are committed today (see module docstring)."
                )

    month_contracts = [
        emit_contract_from_matrixify(period=period, uk_csv=uk_csv, us_csv=us_csv, oracle_gaps=oracle_quarter_gaps)
        for period, uk_csv, us_csv in month_specs
    ]
    months = [load_contract(c) for c in month_contracts]
    all_reconciled = all(c['provenance']['reconciled'] for c in month_contracts)

    payload = _aggregate_quarter_payload(months)
    _add_headline_kpis(payload['current'])
    for sku in payload['skus_all']:
        sku['is_el_component'] = _is_el_component(sku.get('coll'))
    _strip_vestigial(payload)

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
        'lq_ly_source': 'ly_from_monthly_matrixify_blocks__lq_unavailable',
        'aggregated_from': [p for p, _, _ in month_specs],
        'built_at': datetime.now(timezone.utc).isoformat(),
        'commit': _git_commit(),
    }
    contract = _wrap_contract(payload, provenance)
    if out_path:
        with open(out_path, 'w') as f:
            json.dump(contract, f, indent=2, default=str)
    return contract
