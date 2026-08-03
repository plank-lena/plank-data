"""Input schema assertions and output quality checks."""

import re
import sys


# ── Input checks ──────────────────────────────────────────────────────────────

def validate_input(raw):
    """
    Assert the raw data extracted from the xlsx matches expected schema.
    Returns a list of warning strings.
    Raises ValueError on any hard failure (run must not continue).
    """
    warnings = []
    errors = []

    # Row 7 label
    ws_ms = raw.get('_ws_ms')
    if ws_ms is not None:
        b7 = ws_ms['B7'].value
        if b7 != 'TOTAL':
            errors.append(
                f"Monthly Summary B7 is {b7!r}, expected 'TOTAL'. "
                "Row 7 may have moved — check config.py row mappings."
            )

    # Period cells must parse as "Month - YYYY"
    _period_re = re.compile(r'^\w+ - \d{4}$')
    from config import PERIOD_CELLS
    if ws_ms is not None:
        for key, cell in PERIOD_CELLS.items():
            val = ws_ms[cell].value
            if not val or not _period_re.match(str(val).strip()):
                errors.append(
                    f"Period cell {cell} ({key}) has unexpected value {val!r}. "
                    "Sheet structure may have changed."
                )

    # Collections and SKUs must be present
    if not raw.get('collections'):
        errors.append("By Collection sheet returned no rows with gross > 0.")
    if not raw.get('skus_all'):
        errors.append("By SKU sheet returned no rows with gross > 0.")

    # Reconciliation: regional breakdown must equal headline total
    # Hard failure: UK (AT7) + US (CD7) + ROW (DN7) must equal Total (F7) within 0.1%
    cur   = raw.get('current', {})
    total = cur.get('total_sales') or 0
    uk    = cur.get('uk_gbp') or 0
    us    = cur.get('us_gbp') or 0
    row_s = cur.get('row_gbp') or 0
    d2c   = cur.get('d2c_gbp') or 0
    b2b   = cur.get('b2b_gbp') or 0
    regional_sum = uk + us + row_s
    channel_sum  = d2c + b2b

    if total > 0:
        gap      = regional_sum - total
        rel_gap  = abs(gap) / total
        print(
            f"[validate] Reconciliation: UK({uk:,.0f}) + US({us:,.0f}) + ROW({row_s:,.0f})"
            f" = {regional_sum:,.0f}  |  Total(F7) = {total:,.0f}"
            f"  |  gap = £{gap:+,.0f} ({gap/total*100:+.2f}%)"
            f"  |  channel D2C+B2B = {channel_sum:,.0f}"
        )
        if rel_gap > 0.001:
            errors.append(
                f"Regional reconciliation FAILED: "
                f"UK({uk:,.0f}) + US({us:,.0f}) + ROW({row_s:,.0f}) = {regional_sum:,.0f} "
                f"vs Total(F7) = {total:,.0f} — gap = £{gap:+,.0f} ({gap/total*100:+.2f}%). "
                f"Exceeds 0.1% tolerance. Investigate source data before proceeding."
            )
    else:
        errors.append("Current total_sales is zero or missing (column F, row 7).")

    # Category coverage — warn if any category has fewer than 8 selling SKUs
    skus_all = raw.get('skus_all', [])
    cats = ['Cabinetry', 'Electric', 'Accessories', 'Lighting']
    for cat in cats:
        n = sum(1 for s in skus_all if s['type_'] == cat)
        if n < 8:
            warnings.append(
                f"Category '{cat}' has only {n} SKU(s) with gross > 0 in By SKU; "
                "expected at least 8 for a full category table."
            )

    if errors:
        for e in errors:
            print(f'[validate] ERROR: {e}', file=sys.stderr)
        raise ValueError(
            f"Input validation failed with {len(errors)} error(s). "
            "See messages above."
        )

    return warnings


# ── Output checks ─────────────────────────────────────────────────────────────

def validate_output(html, pm=None):
    """
    Check the rendered HTML for common generation errors.
    Returns a list of warning strings.
    pm is the period_model dict from extract (used to allow legit references).
    """
    warnings = []

    # Unfilled tokens
    leftover = set(re.findall(r'\{\{[A-Z_]+\}\}', html))
    if leftover:
        warnings.append(
            f"Unfilled template tokens found: {sorted(leftover)}. "
            "A token in render.py's token dict may be missing or misnamed."
        )

    # TOTAL_SALES must be a positive integer
    m = re.search(r'const TOTAL_SALES = (\d+);', html)
    if not m:
        warnings.append("const TOTAL_SALES not found in output HTML.")
    elif int(m.group(1)) == 0:
        warnings.append("const TOTAL_SALES is 0 in output HTML.")

    # Stale hardcoded month strings — these patterns should only appear as legitimate
    # "last month / last year" references, never as stale current-month names.
    # We simply count occurrences outside JS comments as a canary.
    suspicious = re.findall(
        r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+20\d\d',
        html
    )
    if len(suspicious) > 60:
        # Some month strings are expected (period labels, ribbon, etc.)
        # Only warn if the count is unusually high (suggests un-replaced tokens).
        warnings.append(
            f"High count of month strings in output ({len(suspicious)}). "
            "Check that all static month references were tokenised."
        )

    # JS syntax check via node --check if available
    import subprocess, tempfile
    try:
        scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
        if scripts:
            js = '\n'.join(scripts)
            with tempfile.NamedTemporaryFile(suffix='.js', mode='w', delete=False) as f:
                f.write(js)
                tmp = f.name
            result = subprocess.run(['node', '--check', tmp], capture_output=True, text=True)
            if result.returncode != 0:
                warnings.append(f"JS syntax error: {result.stderr.strip().splitlines()[0]}")
    except FileNotFoundError:
        pass  # node not installed; skip

    # JS syntax canaries
    if ';;' in html:
        warnings.append("Double semicolons (';;') found in output HTML — possible JS error.")
    if 'undefined' in html.split('const TOTAL_SALES')[0].split('// DATA')[-1][:200]:
        pass  # undefined before data section is fine (it's in function bodies)
    # Check data block region only
    data_start = html.find('const PERIODS = ')
    data_end   = html.find('\n};\n\n\n', html.find('const CAT_SKUS'))
    if data_start != -1 and data_end != -1:
        data_region = html[data_start:data_end]
        if 'undefined' in data_region:
            warnings.append(
                "'undefined' found in a JS data block — a Python None may not "
                "have been serialised to null."
            )

    return warnings


# ── Combined runner ───────────────────────────────────────────────────────────

def run_all(raw, html, pm=None):
    """Run input + output checks, print results, raise on hard failures."""
    # Input check (raises on error)
    in_warns = validate_input(raw)
    for w in in_warns:
        print(f'[validate] INPUT WARNING: {w}')

    # Output check
    out_warns = validate_output(html, pm=pm)
    for w in out_warns:
        print(f'[validate] OUTPUT WARNING: {w}')

    total_warnings = len(in_warns) + len(out_warns)
    if total_warnings == 0:
        print('[validate] All checks passed.')
    else:
        print(f'[validate] {total_warnings} warning(s) — review above.')
