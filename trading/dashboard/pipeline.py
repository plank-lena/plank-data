"""Orchestrate extract → compute → render → validate for one monthly report."""

import sys
from pathlib import Path

# Allow running as `python src/pipeline.py` from the repo root
_SRC = Path(__file__).parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from extract import extract_all
from compute import (
    compute_periods, compute_total_sales,
    compute_statuses, compute_prod_types,
    compute_skus, compute_newness_skus,
    compute_collections, compute_finish_data, compute_coll_analysis,
    compute_kpi_tokens, compute_ribbon_tokens,
    compute_category_top_collections, compute_movers, compute_matrix,
    js_block_periods, js_block_collections, js_block_statuses,
    js_block_prod_types, js_block_skus, js_block_finish_data,
    js_block_coll_analysis, js_block_newness_skus,
    js_block_cat_top_collections, js_block_movers, js_block_matrix,
)
from render import tokenise, fill_template, build_token_dict
from validate import run_all

# In the repo pipeline.py lives in src/; in a bundle it sits at the root next to template/.
_HERE     = Path(__file__).parent
REPO_ROOT = _HERE if (_HERE / 'template').is_dir() else _HERE.parent
SRC_TEMPLATE = REPO_ROOT / 'template' / 'dashboard_current.html'
TEMPLATE     = REPO_ROOT / 'template' / 'dashboard.template.html'
OUTPUT_DIR   = REPO_ROOT / 'output'


def run(xlsx_path: Path) -> Path:
    print(f'[pipeline] Source: {xlsx_path}')

    # ── 1. Extract ────────────────────────────────────────────────────────────
    print('[pipeline] Extracting...')
    raw = extract_all(xlsx_path)
    pm  = raw['period_model']

    # ── 2. Validate input ─────────────────────────────────────────────────────
    print('[pipeline] Validating input...')
    from validate import validate_input
    in_warns = validate_input(raw)
    for w in in_warns:
        print(f'[validate] INPUT WARNING: {w}')

    # ── 3. Compute ────────────────────────────────────────────────────────────
    print('[pipeline] Computing...')
    periods_data  = compute_periods(raw['current'], raw['lm'], raw['ly'], pm)
    total_sales   = compute_total_sales(raw['current'])
    statuses_data = compute_statuses(raw['statuses'])
    types_data    = compute_prod_types(raw['prod_types'])
    skus_data     = compute_skus(raw['skus_all'])
    newness_data  = compute_newness_skus(raw['skus_all'])
    coll_data     = compute_collections(raw['collections'])
    finish_data   = compute_finish_data(raw['finishes'], raw['skus_all'])
    coll_analysis = compute_coll_analysis(raw['collections'], raw['skus_all'])
    kpi_tokens    = compute_kpi_tokens(raw['current'], raw['lm'], pm)
    ribbon_tokens = compute_ribbon_tokens(raw['current'], raw['lm'], raw['ly'], pm)
    cat_top_collections = compute_category_top_collections(coll_data)
    movers  = compute_movers(raw['skus_all'])
    matrix  = compute_matrix(raw['collections'])

    # ── 4. Serialise JS blocks ────────────────────────────────────────────────
    periods_js      = js_block_periods(periods_data)
    collections_js  = js_block_collections(coll_data)
    statuses_js     = js_block_statuses(statuses_data)
    prod_types_js   = js_block_prod_types(types_data)
    skus_js         = js_block_skus(skus_data)
    finish_data_js  = js_block_finish_data(finish_data)
    coll_analysis_js = js_block_coll_analysis(coll_analysis)
    newness_skus_js  = js_block_newness_skus(newness_data)
    cat_top_collections_js = js_block_cat_top_collections(cat_top_collections)
    movers_js  = js_block_movers(movers)
    matrix_js  = js_block_matrix(matrix)

    tokens = build_token_dict(
        periods_js, collections_js, statuses_js, prod_types_js, skus_js,
        finish_data_js, total_sales, coll_analysis_js, newness_skus_js,
        kpi_tokens, ribbon_tokens,
        cat_top_collections_js, movers_js, matrix_js,
    )
    # This path reads directly from the oracle xlsx -- no provenance/gate
    # concept here (that's contract.py's render_contract), so never provisional.
    tokens['PROVISIONAL_BANNER'] = ''

    # Shared design tokens (2026-08-05 CSS-overhaul brief §2): the :root custom
    # properties + embedded @font-face rules live once in common/dashboard_
    # tokens.css and get inlined here verbatim, so trading and returns can't
    # drift apart on palette/type again. Moving this out of the template
    # (previously hardcoded there) is intentionally a no-op to rendered output.
    DASHBOARD_TOKENS_CSS = _HERE / '..' / '..' / 'common' / 'dashboard_tokens.css'
    tokens['DASHBOARD_TOKENS'] = DASHBOARD_TOKENS_CSS.read_text(encoding='utf-8')

    # ── 5. Tokenise template (once) ───────────────────────────────────────────
    if not TEMPLATE.exists():
        print(f'[pipeline] Tokenising {SRC_TEMPLATE.name} → {TEMPLATE.name}')
        src_html = SRC_TEMPLATE.read_text(encoding='utf-8')
        TEMPLATE.write_text(tokenise(src_html), encoding='utf-8')

    # ── 6. Render ─────────────────────────────────────────────────────────────
    print('[pipeline] Rendering...')
    template_html = TEMPLATE.read_text(encoding='utf-8')
    from validate import validate_template_source
    for w in validate_template_source(template_html):
        print(f'[validate] TEMPLATE WARNING: {w}')
    html = fill_template(template_html, tokens)

    # ── 7. Validate output ────────────────────────────────────────────────────
    print('[pipeline] Validating output...')
    from validate import validate_output
    out_warns = validate_output(html, pm=pm)
    for w in out_warns:
        print(f'[validate] OUTPUT WARNING: {w}')
    total_w = len(in_warns) + len(out_warns)
    if total_w == 0:
        print('[validate] All checks passed.')
    else:
        print(f'[validate] {total_w} warning(s) — review above.')

    # ── 8. Write output ───────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(exist_ok=True)
    label = pm['cm']['label'].replace(' ', '_')   # e.g. "May_2026"
    out_path = OUTPUT_DIR / f'{label}_dashboard.html'
    out_path.write_text(html, encoding='utf-8')
    print(f'[pipeline] Written: {out_path}')
    return out_path


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python src/pipeline.py <path/to/report.xlsx>', file=sys.stderr)
        sys.exit(1)
    run(Path(sys.argv[1]))
