"""Tokenise the current HTML once, then fill the template each month."""

import re
import sys
from pathlib import Path

# NOTE: the tokenise()/_HTML_STATIC/_JS_BLOCKS/_JS_STATIC machinery below is
# frozen, one-time bootstrap history -- it converted the pre-redesign
# dashboard_current.html into dashboard.template.html (BRIEF #4 step 4 §0:
# that source predates all 13 redesign changes). pipeline.py only calls
# tokenise() when TEMPLATE doesn't exist yet; the redesigned template is
# hand-authored directly and always exists now, so this path never runs
# again. Kept for history, same convention as this repo's other superseded
# paths (trading/shopify_feed.py, trading/build.py) -- not exercised, not
# deleted.


# ── One-time tokenisation ─────────────────────────────────────────────────────

# Simple ordered pairs: (old_string, token_or_replacement)
# Applied in order; earlier replacements are not re-scanned.
_HTML_STATIC = [
    # Title
    ('<title>May 2026 — Monthly Trading Report</title>',
     '<title>{{PAGE_TITLE}}</title>'),

    # H1
    ('<h1><em>May 2026</em> — <span>Monthly</span> Trading Report</h1>',
     '<h1><em>{{CM_LABEL}}</em> — <span>Monthly</span> Trading Report</h1>'),

    # Header sub
    ('Product &amp; Collection Performance · May 2026 · Buying &amp; Merchandising',
     'Product &amp; Collection Performance · {{CM_LABEL}} · Buying &amp; Merchandising'),

    # Period badges
    ('>May 2026 · Current Month<', '>{{CM_LABEL}} · Current Month<'),
    ('>vs Apr 2026 (LM)<',         '>vs {{LM_LABEL}} (LM)<'),
    ('>vs May 2025 (LY)<',         '>vs {{LY_LABEL}} (LY)<'),

    # KPI Total Revenue
    ('id="kpi-rev">£476K</div>', 'id="kpi-rev">{{KPI_REV_VAL}}</div>'),
    ('<div class="badge dn"><span class="badge-lbl">LM</span>-3.2%</div>',
     '<div class="badge {{KPI_REV_LM_CLS}}"><span class="badge-lbl">LM</span>{{KPI_REV_LM}}</div>'),
    ('<div class="badge up"><span class="badge-lbl">LY</span>+60.8%</div>',
     '<div class="badge {{KPI_REV_LY_CLS}}"><span class="badge-lbl">LY</span>{{KPI_REV_LY}}</div>'),

    # KPI Units
    ('<div class="kpi-val">25,809</div>',
     '<div class="kpi-val">{{KPI_UNITS_VAL}}</div>'),
    ('<div class="badge dn"><span class="badge-lbl">LM</span>-12.5%</div>',
     '<div class="badge {{KPI_UNITS_LM_CLS}}"><span class="badge-lbl">LM</span>{{KPI_UNITS_LM}}</div>'),
    ('<div class="badge up"><span class="badge-lbl">LY</span>+21.9%</div>',
     '<div class="badge {{KPI_UNITS_LY_CLS}}"><span class="badge-lbl">LY</span>{{KPI_UNITS_LY}}</div>'),

    # KPI Gross Margin
    ('<div class="kpi-val">74.3%</div>',
     '<div class="kpi-val">{{KPI_GM_VAL}}</div>'),
    ('<div class="badge flat">D2C 76.7%</div>',
     '<div class="badge flat">D2C {{KPI_GM_D2C}}</div>'),
    ('<div class="badge flat">B2B 68.7%</div>',
     '<div class="badge flat">B2B {{KPI_GM_B2B}}</div>'),

    # KPI D2C Share
    ('<div class="kpi-val">67.3%</div>',
     '<div class="kpi-val">{{KPI_D2C_SHARE}}</div>'),
    ('<div class="badge dn"><span class="badge-lbl">LM</span>was 72.9%</div>',
     '<div class="badge {{KPI_D2C_LM_CLS}}"><span class="badge-lbl">LM</span>{{KPI_D2C_LM}}</div>'),

    # KPI UK Revenue
    ('<div class="kpi-val" style="color:var(--uk)">£248K</div>',
     '<div class="kpi-val" style="color:var(--uk)">{{KPI_UK_VAL}}</div>'),
    ('<div class="badge dn"><span class="badge-lbl">LM</span>-7.3%</div>',
     '<div class="badge {{KPI_UK_LM_CLS}}"><span class="badge-lbl">LM</span>{{KPI_UK_LM}}</div>'),
    ('<div class="badge up"><span class="badge-lbl">LY</span>+13.3%</div>',
     '<div class="badge {{KPI_UK_LY_CLS}}"><span class="badge-lbl">LY</span>{{KPI_UK_LY}}</div>'),

    # KPI US Revenue
    ('<div class="kpi-val" style="color:var(--us)">£214K</div>',
     '<div class="kpi-val" style="color:var(--us)">{{KPI_US_VAL}}</div>'),
    ('<div class="badge up"><span class="badge-lbl">LM</span>+1.3%</div>',
     '<div class="badge {{KPI_US_LM_CLS}}"><span class="badge-lbl">LM</span>{{KPI_US_LM}}</div>'),
    ('<div class="badge up"><span class="badge-lbl">LY</span>+205.4%</div>',
     '<div class="badge {{KPI_US_LY_CLS}}"><span class="badge-lbl">LY</span>{{KPI_US_LY}}</div>'),

    # KPI Sell-Through / WC
    ('<div class="kpi-val">11.4%</div>',
     '<div class="kpi-val">{{KPI_ST_VAL}}</div>'),
    ('<div class="badge warn">WC 7.77 wks</div>',
     '<div class="badge warn">{{KPI_WC_VAL}}</div>'),
    ('<div class="badge flat">200K inv</div>',
     '<div class="badge flat">{{KPI_INV_VAL}}</div>'),

    # MoM Ribbon — Total trajectory
    ('<div class="qoq-period">May 2025 (LY)</div>',
     '<div class="qoq-period">{{RIB_TOTAL_LY_PERIOD}}</div>'),
    ('<div class="qoq-val" style="color:var(--muted)">£296K</div>',
     '<div class="qoq-val" style="color:var(--muted)">{{RIB_TOTAL_LY_VAL}}</div>'),
    ('<div class="qoq-arrow up">→</div>',
     '<div class="qoq-arrow {{RIB_TOTAL_ARR1_CLS}}">→</div>'),
    ('<div class="qoq-period">Apr 2026 (LM)</div>',
     '<div class="qoq-period">{{RIB_TOTAL_LM_PERIOD}}</div>'),
    ('<div class="qoq-val" style="color:var(--muted)">£492K</div>',
     '<div class="qoq-val" style="color:var(--muted)">{{RIB_TOTAL_LM_VAL}}</div>'),
    ('<div class="qoq-arrow dn">→</div>',
     '<div class="qoq-arrow {{RIB_TOTAL_ARR2_CLS}}">→</div>'),
    ('<div class="qoq-period">May 2026 (CM)</div>',
     '<div class="qoq-period">{{RIB_TOTAL_CM_PERIOD}}</div>'),
    ('<div class="qoq-val" style="color:var(--amber)">£476K</div>',
     '<div class="qoq-val" style="color:var(--amber)">{{RIB_TOTAL_CM_VAL}}</div>'),

    # MoM Ribbon — UK trajectory
    ('<span class="qoq-val" style="font-size:13px;color:var(--muted)">£219K</span>',
     '<span class="qoq-val" style="font-size:13px;color:var(--muted)">{{RIB_UK_LY_VAL}}</span>'),
    ('<span style="font-family:var(--font-m);font-size:10px;color:var(--green)">→</span>\n      <span class="qoq-val" style="font-size:13px;color:var(--muted)">£267K</span>',
     '<span style="font-family:var(--font-m);font-size:10px;color:{{RIB_UK_ARR1_COLOR}}">→</span>\n      <span class="qoq-val" style="font-size:13px;color:var(--muted)">{{RIB_UK_LM_VAL}}</span>'),
    ('<span style="font-family:var(--font-m);font-size:10px;color:var(--red)">→</span>\n      <span class="qoq-val" style="font-size:13px;color:var(--uk)">£248K</span>',
     '<span style="font-family:var(--font-m);font-size:10px;color:{{RIB_UK_ARR2_COLOR}}">→</span>\n      <span class="qoq-val" style="font-size:13px;color:var(--uk)">{{RIB_UK_CM_VAL}}</span>'),
    ('<span class="badge dn" style="margin-left:2px">-7.3% MoM</span>',
     '<span class="badge {{RIB_UK_LM_CLS}}" style="margin-left:2px">{{RIB_UK_LM_BADGE}}</span>'),
    ('<span class="badge up">+13.3% YoY</span>',
     '<span class="badge {{RIB_UK_LY_CLS}}">{{RIB_UK_LY_BADGE}}</span>'),

    # MoM Ribbon — US trajectory
    ('<span class="qoq-val" style="font-size:13px;color:var(--muted)">£70K</span>',
     '<span class="qoq-val" style="font-size:13px;color:var(--muted)">{{RIB_US_LY_VAL}}</span>'),
    ('<span style="font-family:var(--font-m);font-size:10px;color:var(--green)">→</span>\n      <span class="qoq-val" style="font-size:13px;color:var(--muted)">£211K</span>',
     '<span style="font-family:var(--font-m);font-size:10px;color:{{RIB_US_ARR1_COLOR}}">→</span>\n      <span class="qoq-val" style="font-size:13px;color:var(--muted)">{{RIB_US_LM_VAL}}</span>'),
    ('<span style="font-family:var(--font-m);font-size:10px;color:var(--green)">→</span>\n      <span class="qoq-val" style="font-size:13px;color:var(--us)">£214K</span>',
     '<span style="font-family:var(--font-m);font-size:10px;color:{{RIB_US_ARR2_COLOR}}">→</span>\n      <span class="qoq-val" style="font-size:13px;color:var(--us)">{{RIB_US_CM_VAL}}</span>'),
    ('<span class="badge up" style="margin-left:2px">+1.3% MoM</span>',
     '<span class="badge {{RIB_US_LM_CLS}}" style="margin-left:2px">{{RIB_US_LM_BADGE}}</span>'),
    ('<span class="badge up">+205.4% YoY</span>',
     '<span class="badge {{RIB_US_LY_CLS}}">{{RIB_US_LY_BADGE}}</span>'),

    # Section meta / labels with month names
    ('id="status-meta">May 2026 Sales mix</div>',
     'id="status-meta">{{CM_LABEL}} Sales mix</div>'),
    ('Collection bubbles · size = units · May 2026',
     'Collection bubbles · size = units · {{CM_LABEL}}'),
    ('<div class="sec-meta">vs Apr 2026</div>',
     '<div class="sec-meta">vs {{LM_LABEL}}</div>'),
    ('>May 2026 · all geographies · click headers to sort<',
     '>{{CM_LABEL}} · all geographies · click headers to sort<'),
    ('May 2026 · first-time trading SKUs only · UK &amp; US Newness status',
     '{{CM_LABEL}} · first-time trading SKUs only · UK &amp; US Newness status'),
]

# JS block replacements — identified by exact start string; end is the matching
# closing bracket/brace sequence that follows.
# Format: (start_anchor, end_anchor, token_name)
_JS_BLOCKS = [
    ('const PERIODS = {',       '\n};',  'BLOCK_PERIODS'),
    ('const COLLECTIONS = [',   '\n];',  'BLOCK_COLLECTIONS'),
    ('const STATUSES = [',      '\n];',  'BLOCK_STATUSES'),
    ('const PROD_TYPES = [',    '\n];',  'BLOCK_PROD_TYPES'),
    ('const SKUS = [',          '\n];',  'BLOCK_SKUS'),
    ('const FINISH_DATA = {',   '\n};',  'BLOCK_FINISH_DATA'),
    ('const COLL_ANALYSIS = {', '\n};',  'BLOCK_COLL_ANALYSIS'),
    ('const NEWNESS_SKUS = [',  '\n];',  'BLOCK_NEWNESS_SKUS'),
    ('const CAT_SKUS = {',      '\n};',  'BLOCK_CAT_SKUS'),
]

_JS_STATIC = [
    # TOTAL_SALES const (single line)
    ('const TOTAL_SALES = 476275;', 'const TOTAL_SALES = {{TOTAL_SALES}};'),
    # periodLabels inside renderTrend
    ("const periodLabels = [\"May '25\",\"Apr '26\",\"May '26\"];",
     'const periodLabels = {{PERIOD_LABELS}};'),
    # vs labels in renderTypeChart
    ("'vs Apr 2026'",   "'{{VS_LM_LABEL}}'"),
    ("'vs May 2025'",   "'{{VS_LY_LABEL}}'"),
    # dc-bar-lbl current month label
    ('>May</span>',     '>{{DC_BAR_LBL}}</span>'),
    # Bug fix: hardcoded TOTAL_SALES value in renderCollections
    ('(v/476275*100)',   '(v/TOTAL_SALES*100)'),
    # Type-legend month strings (inside JS template literals)
    ('<span class="tl-label">May 2026</span>',
     '<span class="tl-label">${CM_LABEL}</span>'),
    ('<span class="tl-label">Apr 2026 (LQ)</span>',
     '<span class="tl-label">${LM_LABEL} (LQ)</span>'),
    # Cat analysis statsHTML month name
    ('May 2026 Sales</div>',    '${CM_LABEL} Sales</div>'),
    # vs LM / vs LY labels in cat analysis stats
    ("vs LM (Apr '26)</span>",  "vs LM (${LM_SHORT})</span>"),
    ("vs LY (May '25)</span>",  "vs LY (${LY_SHORT})</span>"),
    # Collection mover card "vs LM" label (inside JS template literal)
    ("${cls==='up'?'▲':'▼'} vs Apr 2026</div>",
     "${cls==='up'?'▲':'▼'} vs ${LM_LABEL}</div>"),
    # Finish pie label — source uses JS unicode escape · for the middle dot
    ('Finish share of sales \\u00b7 May 2026',
     'Finish share of sales \\u00b7 ${CM_LABEL}'),
    # Finish pie canvas centre label (fillText inside regular JS, not template literal)
    ("ctx.fillText('May 2026',", 'ctx.fillText(CM_LABEL,'),
    # Top SKUs headings inside JS template literals (two separate render functions)
    ('Top SKUs · May 2026</div>',  'Top SKUs · ${CM_LABEL}</div>'),
    # ro-split LM / LY period labels (both collection-analysis and channel sections)
    ('<div class="ro-split-lbl">Apr 2026</div>',
     '<div class="ro-split-lbl">${LM_LABEL}</div>'),
    ('<div class="ro-split-lbl">May 2025 (LY)</div>',
     '<div class="ro-split-lbl">${LY_LABEL} (LY)</div>'),
    # Dynamic tbl-meta textContent update (JS template literal)
    ('`May 2026 · sorted by', '`${CM_LABEL} · sorted by'),
    # Comments (neutral)
    ('// 3-period comparison data (LY Apr25, LM Mar26, Current Apr26)',
     '// 3-period comparison data (LY / LM / Current)'),
    ('// Product types – May 2026', '// Product types'),
    ('// ── COLLECTION ANALYSIS DATA (May 2026 monthly from Excel) ─────',
     '// ── COLLECTION ANALYSIS DATA (current month from Excel) ──────────'),
    ('// ── NEWNESS SKUS (May 2026 — sourced directly from By SKU sheet) ──',
     '// ── NEWNESS SKUS (current month — sourced directly from By SKU sheet) ──'),
]

# JS variable block to inject right after the DATA header comment
_JS_PERIOD_VARS = '''\
// Derived period labels (used by rendering functions)
const CM_LABEL = PERIODS.q1_26.label;
const LM_LABEL = PERIODS.q4_25.label;
const LY_LABEL = PERIODS.q1_25.label;
const CM_SHORT = PERIODS.q1_26.short;
const LM_SHORT = PERIODS.q4_25.short;
const LY_SHORT = PERIODS.q1_25.short;
'''


def tokenise(src_html: str) -> str:
    """Convert a month-specific HTML to a reusable template."""
    html = src_html

    # 1. Static HTML replacements
    for old, new in _HTML_STATIC:
        html = html.replace(old, new)

    # 2. JS block replacements (replace entire const declarations)
    for start, end_anchor, token in _JS_BLOCKS:
        idx = html.find(start)
        if idx == -1:
            print(f'[tokenise] WARNING: block not found: {start!r}', file=sys.stderr)
            continue
        end_idx = html.find(end_anchor, idx)
        if end_idx == -1:
            print(f'[tokenise] WARNING: end anchor not found for {start!r}', file=sys.stderr)
            continue
        end_idx += len(end_anchor)
        html = html[:idx] + '{{' + token + '}}' + html[end_idx:]

    # 3. JS static string replacements
    for old, new in _JS_STATIC:
        html = html.replace(old, new)

    return html


# ── Monthly template fill ─────────────────────────────────────────────────────

def fill_template(template: str, tokens: dict) -> str:
    """Replace every {{TOKEN}} in template with its computed value."""
    html = template
    for key, value in tokens.items():
        html = html.replace('{{' + key + '}}', str(value))
    # Check for any remaining unfilled tokens
    remaining = re.findall(r'\{\{[A-Z_]+\}\}', html)
    if remaining:
        print('[render] Unfilled tokens:', set(remaining), file=sys.stderr)
    return html


def build_token_dict(
    periods_js, collections_js, statuses_js, prod_types_js, skus_js,
    finish_data_js, total_sales, coll_analysis_js, newness_skus_js,
    kpi_tokens, ribbon_tokens,
    cat_top_collections_js=None, movers_js=None, matrix_js=None, bottom_skus_js=None,
):
    """Combine all token sources into a single flat dict for fill_template."""
    toks = {}
    toks.update(kpi_tokens)
    toks.update(ribbon_tokens)
    toks['BLOCK_PERIODS']       = periods_js + '\n\n' + _JS_PERIOD_VARS
    toks['BLOCK_COLLECTIONS']   = collections_js
    toks['BLOCK_STATUSES']      = statuses_js
    toks['BLOCK_PROD_TYPES']    = prod_types_js
    toks['BLOCK_SKUS']          = skus_js
    toks['BLOCK_FINISH_DATA']   = finish_data_js
    toks['TOTAL_SALES']         = total_sales
    toks['BLOCK_COLL_ANALYSIS'] = coll_analysis_js
    toks['BLOCK_NEWNESS_SKUS']  = newness_skus_js
    toks['BLOCK_CAT_TOP_COLLECTIONS'] = cat_top_collections_js or 'const CAT_TOP_COLLECTIONS = {};'
    toks['BLOCK_MOVERS']              = movers_js or 'const MOVERS = {rising:[],falling:[]};'
    toks['BLOCK_MATRIX']              = matrix_js or 'const MATRIX = {points:[],size_key:{min:0,max:0,label:"units"}};'
    # SKU2 (round-3 review): bottom-20 SKUs by cash, excluding Components.
    toks['BLOCK_BOTTOM_SKUS']         = bottom_skus_js or 'const BOTTOM_SKUS = [];'
    return toks
