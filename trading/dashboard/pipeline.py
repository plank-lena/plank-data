"""Orchestrate extract → contract → render → validate for one monthly report."""

import sys
from pathlib import Path

# Allow running as `python src/pipeline.py` from the repo root
_SRC = Path(__file__).parent
_TRADING_DIR = _SRC.parent
for _p in (_SRC, _TRADING_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from extract import extract_all
from render import tokenise
from contract import emit_contract_from_oracle, render_contract
from validate import run_all

# In the repo pipeline.py lives in src/; in a bundle it sits at the root next to template/.
_HERE     = Path(__file__).parent
REPO_ROOT = _HERE if (_HERE / 'template').is_dir() else _HERE.parent
SRC_TEMPLATE = REPO_ROOT / 'template' / 'dashboard_current.html'
TEMPLATE     = REPO_ROOT / 'template' / 'dashboard.template.html'
OUTPUT_DIR   = REPO_ROOT / 'output'


def run(xlsx_path: Path) -> Path:
    """Delegates the actual contract-building + rendering to contract.py's
    emit_contract_from_oracle()/render_contract() (2026-08-05, trading review
    round 1 T2a) -- this function used to duplicate that whole extract ->
    compute -> js_block -> token -> fill_template sequence itself, calling
    compute.py/render.py directly rather than through the contract layer.
    That meant this path silently never got any mutation contract.py applies
    on top of extract_all()'s raw shape: not just T2a's dead-category
    exclusion, but BRIEF #4 step 4's own headline KPIs (yoy_growth_pct/
    b2b_share) and is_el_component too -- three real, disclosed gaps between
    this file's output and render_contract's, not just one. Delegating means
    this can't silently drift out of sync with contract.py again. Still runs
    validate_input() against the RAW extract_all() output first (not the
    round-tripped contract), since that check reads worksheet handles
    (raw['_ws_ms']) the contract layer never carries.
    """
    print(f'[pipeline] Source: {xlsx_path}')

    # ── 1. Extract (for validate_input's worksheet-handle checks only) ───────
    print('[pipeline] Extracting...')
    raw = extract_all(xlsx_path)
    pm  = raw['period_model']

    # ── 2. Validate input ─────────────────────────────────────────────────────
    print('[pipeline] Validating input...')
    from validate import validate_input
    in_warns = validate_input(raw)
    for w in in_warns:
        print(f'[validate] INPUT WARNING: {w}')

    # ── 3. Build the contract (extract + all of contract.py's mutations) ─────
    print('[pipeline] Building contract...')
    contract = emit_contract_from_oracle(xlsx_path)

    # ── 4. Tokenise template (once) ───────────────────────────────────────────
    if not TEMPLATE.exists():
        print(f'[pipeline] Tokenising {SRC_TEMPLATE.name} → {TEMPLATE.name}')
        src_html = SRC_TEMPLATE.read_text(encoding='utf-8')
        TEMPLATE.write_text(tokenise(src_html), encoding='utf-8')

    # ── 5. Render ─────────────────────────────────────────────────────────────
    print('[pipeline] Rendering...')
    template_html = TEMPLATE.read_text(encoding='utf-8')
    from validate import validate_template_source
    for w in validate_template_source(template_html):
        print(f'[validate] TEMPLATE WARNING: {w}')
    html = render_contract(contract, template_html)

    # ── 6. Validate output ────────────────────────────────────────────────────
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

    # ── 7. Write output ───────────────────────────────────────────────────────
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
