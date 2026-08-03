"""Input schema assertions and output quality checks."""

import re
import sys

STATUS_BUCKETS = ("Continuity", "Newness", "Discontinued", "Dead")


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


# ── Contract-mode checks (BRIEF #3 §7) ───────────────────────────────────────

def validate_contract(contract, tol=0.001):
    """Validate a contract's OWN numbers directly, rather than re-deriving
    _ws_ms cell reads (a contract, esp. Matrixify-sourced, has no worksheet
    to read). Returns a list of warning strings; raises ValueError on a
    hard failure (structural problems only -- a known, disclosed revenue
    gap on a Matrixify-sourced contract is exactly what provenance.
    reconciled: False already communicates, and is NOT re-raised here).
    """
    import os
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from common.reconciliation_gate import assert_country_reconciles

    warnings = []
    errors = []
    current = contract.get("current", {})

    # Leak check: current's own uk_gbp+us_gbp+row_gbp must tie to current's
    # own total_sales. Both front-ends compute these independently of each
    # other (extract_headline reads AT7/CD7/DN7 and F7 as separate cells;
    # emit_contract_from_matrixify accumulates country_totals and
    # grand_total in separate running sums over the same per-line loop --
    # see BRIEF #5's assert_country_reconciles docstring for why that
    # independence is what makes this check non-vacuous), so this catches
    # a real leak (a line silently missing its country bucket) without
    # reaching into a different sheet/cut that may have its own, unrelated
    # basis differences (By Collection's total is known to differ from
    # Monthly Summary's by ~0.09% in the real May oracle -- a pre-existing
    # cross-sheet quirk in the source spreadsheet, not a contract bug; NOT
    # what this check is for).
    total_sales = current.get("total_sales") or 0
    country_totals = {
        "UK": current.get("uk_gbp", 0), "US": current.get("us_gbp", 0), "ROW": current.get("row_gbp", 0),
    }
    try:
        assert_country_reconciles(country_totals, total_sales, tol=tol)
    except AssertionError as e:
        errors.append(str(e))

    # Status-bucket enum + coverage. NOT asserted additive: confirmed
    # against the real oracle (config.STATUS_ROWS) that its own statuses
    # table tracks exactly these 4 buckets and deliberately excludes
    # "Not For Sale" revenue (£2,453.99 in the May fixture) entirely --
    # i.e. the oracle itself isn't additive to total_sales here, so
    # asserting our own contract must be would be a stricter, mismatched
    # standard. Reported as a coverage diagnostic instead, same treatment
    # as finishes below.
    statuses = contract.get("statuses", [])
    status_names = {s["s"] for s in statuses}
    unknown_buckets = status_names - set(STATUS_BUCKETS)
    if unknown_buckets:
        errors.append(f"statuses has bucket(s) outside the enum {STATUS_BUCKETS}: {unknown_buckets}")
    status_sales_sum = sum(s["sales"] for s in statuses)
    if total_sales:
        status_share = status_sales_sum / total_sales
        warnings.append(f"statuses cover {status_share:.1%} of total_sales (not asserted additive -- see oracle's own Not For Sale gap)")

    # Finish coverage is NOT asserted additive -- config.FINISH_COLORS is a
    # curated top-8 palette (see trading/contract.py's emit_contract_from_
    # matrixify), so finishes legitimately cover only part of total_sales.
    # Reported as a diagnostic, not a failure.
    finishes = contract.get("finishes", {})
    finish_sales_sum = sum(f["total"] for f in finishes.values())
    if total_sales:
        finish_share = finish_sales_sum / total_sales
        warnings.append(f"curated finishes cover {finish_share:.1%} of total_sales (by design, not 100%)")

    # Enrichment coverage threshold -- warn, don't hard-block (known,
    # surfaced in metadata; BRIEF #3 §7 is explicit this isn't a gate).
    coverage = contract.get("provenance", {}).get("enrichment_coverage")
    if coverage is not None and coverage < 0.99:
        warnings.append(f"enrichment_coverage {coverage:.2%} is below the 99% target")

    if errors:
        for e in errors:
            print(f"[validate] CONTRACT ERROR: {e}", file=sys.stderr)
        raise ValueError(f"Contract validation failed with {len(errors)} error(s). See messages above.")

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
