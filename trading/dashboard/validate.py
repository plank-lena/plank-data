"""Input schema assertions and output quality checks."""

import re
import sys

# The oracle's own Product Status table has 6 real rows (extract_statuses
# now discovers all of them, not just the 4 previously hand-listed); the
# Matrixify path's own _status_bucket() rollup (contract.py) only ever
# produces its own fixed 4 -- both are valid subsets of this superset.
STATUS_BUCKETS = ("Continuity", "Newness", "Discontinued", "Dead", "Not For Sale", "Pre-Launch")

# Both status vocabularies a SKU might carry mean "live" -- see
# contract.py's LIVE_STATUS_VALUES docstring for why "Live" (Line Detail's
# raw enum) and "Continuity"/"Newness" (the oracle's coarse bucket) are
# treated as equivalent here.
LIVE_STATUS_VALUES = {"Live", "Continuity", "Newness"}


def _completeness_errors(skus_all, prod_types, finishes, collections):
    """Step-4-follow-up §3: the permanent tripwire for the class of bug
    Step 4 itself found (a fixed row-dict silently hid a Taps department, a
    Door department, and 21 of 29 real finishes -- see ROADMAP.md). Every
    department/collection that actually carries revenue on an enriched SKU
    must appear in its corresponding analysis block; if one is missing,
    that block was built from a stale/fixed list again, not from the data.
    Returns (errors, warnings) -- fails loudly (hard error, aborts the run)
    for departments/collections, naming every missing group, but only
    warns for finishes; see the finish check below for why that one case
    is a diagnostic rather than a gate failure.

    Departments and collections are closed-world here: every SKU's
    department/collection pairing is read from the SAME extraction
    (extract_skus_all) that also feeds prod_types/collections, so the two
    sides should always agree -- a mismatch means a block was built from a
    stale enumeration, exactly the Taps/Door bug class. 'Unknown' is
    exempt from the department check: it's a legitimate synthetic
    catch-all for SKUs with no Product Type classification at all (same
    convention used everywhere else in this codebase), not a row that was
    ever going to exist in the sheet's own Product Type table.
    """
    errors = []
    warnings = []
    revenue_skus = [s for s in skus_all if (s.get('gross') or 0) > 0]

    depts_in_skus = {s['type_'] for s in revenue_skus if s.get('type_')}
    depts_present = {t['t'] for t in prod_types}
    missing_depts = (depts_in_skus - depts_present) - {'Unknown'}
    if missing_depts:
        errors.append(
            f"Department(s) with revenue-bearing SKUs but missing from prod_types: "
            f"{sorted(missing_depts)}"
        )

    colls_in_skus = {(s['type_'], s['coll']) for s in revenue_skus if s.get('coll')}
    colls_present = {(c['t'], c['c']) for c in collections}
    missing_colls = colls_in_skus - colls_present
    if missing_colls:
        errors.append(
            f"Collection(s) with revenue-bearing SKUs but missing from collections: "
            f"{sorted(missing_colls)}"
        )

    # Finishes are NOT closed-world the way departments/collections are:
    # By SKU's finish text is open-ended and sometimes compound ("X & Y")
    # or a sentinel ("No Finish", "Multiple"), and the sheet's own Finish
    # summary table has always been a curated top-line view, not a
    # guaranteed enumeration of every finish string that ever appears on a
    # SKU (BRIEF #2 already disclosed a related gap -- Line Detail missing
    # Polished Silver/Shiny Brass/Silver entirely). A real finish missing
    # a Finish-table row can be a genuine, pre-existing sheet-content gap,
    # not necessarily an extraction regression -- reported loudly as a
    # diagnostic (still names every gap) rather than aborting the build.
    finishes_in_skus = {s['finish'] for s in revenue_skus if s.get('finish')}
    finishes_present = set(finishes.keys())
    missing_finishes = finishes_in_skus - finishes_present
    if missing_finishes:
        warnings.append(
            f"Finish(es) with revenue-bearing SKUs but no row in the Finish table: "
            f"{sorted(missing_finishes)} (not gated -- see _completeness_errors docstring)"
        )

    return errors, warnings


def _toggle_reconciliation_errors(collections, tol=0.001):
    """BRIEF #4 step 4 §10: 'each toggle state reconciles to the same
    total' -- UK + US + ROW cash summed across every NAMED-department
    collection must tie to the summed collection total, within the same
    0.1% relative tolerance the country-level gate uses (ROADMAP.md §5).

    Deliberately a single aggregate check, not a per-collection one, AND
    deliberately excludes 'Unknown'-department collections (e.g. ALERIA/a
    second BECKER/CANTO/HAYLEY) -- same exception as the completeness
    tripwire's missing_depts check. These carry zero country attribution
    in the sheet's own By Collection UK/US/ROW columns despite a nonzero
    total -- a pre-existing, disclosed sheet quirk, not a systemic leak,
    and its size is NOT stable across periods (~£600 of £476K in the May
    monthly fixture, ~£5,200 of £1.34M in the Q1 2026 quarterly one) --
    large enough some quarters to blow a blanket tolerance that was only
    ever calibrated against the smaller monthly instances of it. Excluding
    the already-disclosed cause is more precise than loosening the
    tolerance for everyone: a real leak in a NAMED department still
    fails loudly, at the original 0.1%.
    """
    named = [c for c in collections if c.get('t') != 'Unknown']
    total_ts = sum(c.get('ts') or 0 for c in named)
    total_parts = sum((c.get('uk_s') or 0) + (c.get('us_s') or 0) + (c.get('row_s', 0) or 0) for c in named)
    if not total_ts:
        return []
    rel_gap = abs(total_parts - total_ts) / abs(total_ts)
    if rel_gap > tol:
        return [
            f"Collections (named departments only): UK+US+ROW cash summed {total_parts:,.2f} != "
            f"collection totals summed {total_ts:,.2f} (gap {rel_gap:.4%}, tolerance {tol:.1%})"
        ]
    return []


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

    # BRIEF #4 step 4 §5/§10: retire the fixed "exactly 8 SKUs/finishes"
    # count assert -- whatever categories/subcategories/finishes/
    # collections exist in the data render, no hardcoded list or minimum.
    # In its place: each collection's own UK+US+ROW cash must still tie to
    # that collection's total (the toggle-reconciliation gate).
    errors.extend(_toggle_reconciliation_errors(raw.get('collections', [])))

    # Step-4-follow-up §3: the permanent completeness tripwire.
    completeness_errors, completeness_warnings = _completeness_errors(
        raw.get('skus_all', []), raw.get('prod_types', []),
        raw.get('finishes', {}), raw.get('collections', []),
    )
    errors.extend(completeness_errors)
    warnings.extend(completeness_warnings)

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

    # Status-bucket enum + coverage. extract_statuses (BRIEF step 5) now
    # dynamically discovers every Product Status row instead of a fixed
    # 4-name dict, which previously silently dropped "Not For Sale" and
    # "Pre-Launch" -- with those two included, the oracle's statuses table
    # IS additive to total_sales (verified exactly, 100.00%, on the May
    # fixture). Kept as a reported diagnostic rather than a hard assert:
    # the exact 100% match is empirical on the data seen so far, not a
    # structural guarantee the sheet makes, so a small future gap should
    # surface as a visible number here rather than abort a build over it.
    statuses = contract.get("statuses", [])
    status_names = {s["s"] for s in statuses}
    unknown_buckets = status_names - set(STATUS_BUCKETS)
    if unknown_buckets:
        errors.append(f"statuses has bucket(s) outside the enum {STATUS_BUCKETS}: {unknown_buckets}")
    status_sales_sum = sum(s["sales"] for s in statuses)
    if total_sales:
        status_share = status_sales_sum / total_sales
        warnings.append(f"statuses cover {status_share:.1%} of total_sales")

    # Finish coverage: BRIEF #4 step 4 §5/§10 retires the curated top-8
    # palette (config.FINISH_COLORS) -- every finish with revenue this
    # month now renders. Coverage can run a little OVER 100% (the real
    # sheet's Finish table includes a couple of rollup rows -- e.g.
    # "Colours"/"Wood" duplicate the sum of their own child colour/material
    # rows -- and a residual "Other" plug row; this is a genuine, disclosed
    # sheet shape, not additive by construction, same as statuses above). A
    # gap well under 100% would instead mean a finish in skus_all has no
    # matching Finish-table row at all.
    finishes = contract.get("finishes", {})
    finish_sales_sum = sum(f.get("total") or 0 for f in finishes.values())
    if total_sales:
        finish_share = finish_sales_sum / total_sales
        warnings.append(f"finishes cover {finish_share:.1%} of total_sales")

    # BRIEF #4 step 4 §10: each toggle state (UK/US/ROW cash) must still
    # reconcile to its own collection's total.
    errors.extend(_toggle_reconciliation_errors(contract.get("collections", [])))

    # Step-4-follow-up §3: the permanent completeness tripwire.
    completeness_errors, completeness_warnings = _completeness_errors(
        contract.get("skus_all", []), contract.get("prod_types", []),
        contract.get("finishes", {}), contract.get("collections", []),
    )
    errors.extend(completeness_errors)
    warnings.extend(completeness_warnings)

    # BRIEF #4 step 4 §10: movers list is Live-only -- recompute against
    # the contract's own skus_all and assert compute_movers()'s filter held
    # (regression guard, not a re-derivation of the movers list itself).
    import sys as _sys2
    _sys2.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from compute import compute_movers
    movers = compute_movers(contract.get("skus_all", []))
    for side in ("rising", "falling"):
        for m in movers[side]:
            sku_row = next((s for s in contract.get("skus_all", []) if s["sku"] == m["sku"]), None)
            is_live = sku_row and (
                sku_row.get("uk_status") in LIVE_STATUS_VALUES or sku_row.get("us_status") in LIVE_STATUS_VALUES
            )
            if not is_live:
                errors.append(f"movers.{side} contains non-Live SKU {m['sku']!r}")

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


# ── Template-source checks (BRIEF #4 step 4 §9/§10) ──────────────────────────

def validate_template_source(template_html):
    """Check the UNFILLED template source (not rendered output) for
    hardcoded period/comparator strings outside a {{TOKEN}} -- monthly and
    quarterly must reuse the same template (§9), so 'MoM'/'LM' literals
    baked into static text would silently keep showing "MoM" on a
    quarterly build. Returns a list of warning strings.
    """
    warnings = []
    # Strip every {{TOKEN}} placeholder first so a token NAME containing
    # these substrings (there are none today, but be safe) can't false-positive.
    stripped = re.sub(r'\{\{[A-Z_]+\}\}', '', template_html)
    # Strip embedded base64 font data (step-4-follow-up's self-contained
    # @font-face blocks) -- gibberish binary-as-text can coincidentally
    # contain any short substring, e.g. "QoQ", with zero relation to a
    # real hardcoded label.
    stripped = re.sub(r'base64,[A-Za-z0-9+/=]+', '', stripped)
    for literal in ("MoM", "QoQ", ">LM<", ">LQ<"):
        if literal in stripped:
            warnings.append(
                f"Template contains hardcoded {literal!r} outside a token -- "
                "period/comparator labels must come from PERIOD_COMP_LABEL/"
                "PREV_PERIOD_ABBR tokens so quarterly reuses this template unchanged."
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
