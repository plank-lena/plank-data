"""Acceptance checks for the quarterly trading builder (BRIEF step 5), run
against the real committed Q2 2026 (Apr+May+Jun) oracle monthly fixtures,
chained off the real committed Q1 2026 quarterly contract as LQ (Lena
supplied the Q1 2026 quarterly oracle workbook after the initial Step 5
build, closing what was the LQ-unavailable bootstrap gap -- see
quarterly.py's module docstring and ROADMAP.md).

Run:  python trading/tests/test_quarterly.py
"""
import json
import os
import sys

HERE = os.path.dirname(__file__)
TRADING_DIR = os.path.join(HERE, "..")
DASHBOARD_DIR = os.path.join(TRADING_DIR, "dashboard")
for _p in (TRADING_DIR, DASHBOARD_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from quarterly import emit_contract_from_oracle_quarter
from contract import render_contract, can_publish, PAYLOAD_KEYS
from validate import validate_contract, validate_template_source, validate_output

MONTH_XLSX = [
    os.path.join(HERE, "fixtures", f"2026-{m}_Monthly_Trading_Report.xlsx")
    for m in ("04", "05", "06")
]
Q1_CONTRACT = os.path.join(HERE, "fixtures", "2026-Q1_contract.json")
TEMPLATE_HTML = os.path.join(DASHBOARD_DIR, "template", "dashboard.template.html")
FIXTURE_CONTRACT = os.path.join(HERE, "fixtures", "2026-Q2_contract.json")

# BRIEF step 5 §2: reconciliation targets, aggregated from the three
# monthly oracles. uk+us+row ties to total exactly; units are diagnostic
# only (the sheet's own country-unit columns don't foot to its total-unit
# column, the same documented monthly quirk, just at quarter scale).
Q2_TARGETS = {
    "total": 1472202.82, "uk": 751715.96, "us": 676915.09, "row": 43571.77,
    "units": 85400,
}


def check_additive_components_match_targets():
    """§6 bullet 1: additive measures (revenue, units) = sum of the three
    monthly oracles within 0.1%, matching brief §2's target table exactly.
    LQ chaining (Q1 as lq_contract) only affects vs_lq fields, never the
    additive components themselves -- targets hold with or without it.
    """
    contract = emit_contract_from_oracle_quarter(MONTH_XLSX, lq_contract=Q1_CONTRACT)
    cur = contract["current"]
    computed = {
        "total": cur["total_sales"], "uk": cur["uk_gbp"], "us": cur["us_gbp"],
        "row": cur["row_gbp"], "units": cur["units"],
    }
    print("\n=== Additive components vs §2 targets ===")
    ok = True
    for key, target in Q2_TARGETS.items():
        gap = abs(computed[key] - target) / abs(target) if target else 0
        status = "PASS" if gap <= 0.001 else "FAIL"
        if gap > 0.001:
            ok = False
        print(f"  {key:6s} computed {computed[key]:>15,.2f}  target {target:>15,.2f}  gap {gap:.4%}  {status}")
    return ok, contract


def check_rate_recomputed_not_averaged(contract):
    """§6 bullet 2: quarter rate = Σnumerator ÷ Σdenominator, not the mean
    of the three monthly rate OUTPUTS. Proven by recomputing both ways
    from the same 3 monthly oracle files and showing they differ (unless
    coincidental) -- and that the contract's own figure matches the
    correct (summed-components) recomputation, not the naive mean.
    """
    from extract import extract_all
    months = [extract_all(p) for p in MONTH_XLSX]

    # The WRONG way: a plain mean of the 3 monthly gm_pct outputs.
    naive_mean_gm = sum(m["current"]["gm_pct"] for m in months) / 3

    # The RIGHT way (what the builder must do): Σ(gm_pct·revenue)/Σrevenue.
    num = sum(m["current"]["gm_pct"] * m["current"]["total_sales"] for m in months)
    den = sum(m["current"]["total_sales"] for m in months)
    correct_weighted_gm = num / den

    contract_gm = contract["current"]["gm_pct"]
    matches_correct = abs(contract_gm - correct_weighted_gm) < 1e-9
    differs_from_naive = abs(contract_gm - naive_mean_gm) > 1e-6

    print("\n=== Rate recomputed from summed components, not averaged (§6 bullet 2) ===")
    print(f"  naive mean of 3 monthly GM%:      {naive_mean_gm:.6f}")
    print(f"  Σ(GM%·revenue)/Σrevenue (correct): {correct_weighted_gm:.6f}")
    print(f"  contract's own current.gm_pct:    {contract_gm:.6f}")
    print(f"  matches the correct recomputation: {matches_correct}")
    print(f"  differs from the naive mean:       {differs_from_naive}")
    return matches_correct and differs_from_naive


def check_country_ties_and_row_present(contract):
    """§6 bullet 3: uk+us+row ties at quarter level; ROW present and
    non-zero (£43,571.77 per §2).
    """
    cur = contract["current"]
    parts = cur["uk_gbp"] + cur["us_gbp"] + cur["row_gbp"]
    residual = abs(parts - cur["total_sales"])
    row_nonzero = cur["row_gbp"] > 0
    print("\n=== uk+us+row ties; ROW present (§6 bullet 3) ===")
    print(f"  uk+us+row - total residual: {residual:.6f} (expect ~0)")
    print(f"  ROW = £{cur['row_gbp']:,.2f}, non-zero: {row_nonzero}")
    return residual < 0.01 and row_nonzero


def check_completeness_tripwire(contract):
    """§6 bullet 4: completeness tripwire passes on the unioned group set
    (BRIEF step 5 §4 -- a collection present in only one of the three
    months must still appear in the quarter's blocks).
    """
    from validate import _completeness_errors
    errors, warnings = _completeness_errors(
        contract["skus_all"], contract["prod_types"], contract["finishes"], contract["collections"],
    )
    print("\n=== Completeness tripwire on the unioned group set (§6 bullet 4) ===")
    print(f"  errors: {errors or 'none'}")
    print(f"  warnings (finishes, not gated): {warnings or 'none'}")
    return not errors


def check_lq_ly_provenance(contract):
    """§6 bullet 5: LQ/LY provenance stamped. Originally LQ was unavailable
    (no committed prior quarterly contract existed) -- now that Q1 2026 is
    committed and passed as lq_contract, LQ is real too: both are checked
    as real, non-fabricated figures, matching quarterly.py's "self-heals
    once a prior quarter is committed" design (ROADMAP.md).
    """
    prov = contract["provenance"]
    pm = contract["period_model"]
    print("\n=== LQ/LY provenance stamped (§6 bullet 5) ===")
    print(f"  lq_ly_source: {prov['lq_ly_source']}, lq_source_period: {prov.get('lq_source_period')}")
    print(f"  cm label: {pm['cm']['label']}, lm label: {pm['lm']['label']}, ly label: {pm['ly']['label']}")
    print(f"  ly.total (real, reconstructed): £{contract['ly']['total']:,.2f}")
    print(f"  lm.total (LQ, now real via Q1): £{contract['lm']['total']:,.2f}")
    labels_correct = pm["cm"]["label"] == "Q2 2026" and pm["lm"]["label"] == "Q1 2026" and pm["ly"]["label"] == "Q2 2025"
    ly_real = contract["ly"]["total"] > 0
    lq_real = contract["lm"]["total"] > 0 and prov["lq_source_period"] == "Q1 2026"
    return labels_correct and ly_real and lq_real


def check_movers_populate_from_real_lq(contract):
    """Once real LQ SKU-level data exists (Q1 as lq_contract), QoQ movers
    must actually populate -- the whole point of chaining a prior quarter
    in. An empty movers list here would mean the self-heal silently isn't
    working, not just an unavailable-data disclosure.
    """
    from compute import compute_movers
    movers = compute_movers(contract["skus_all"])
    n_with_real_vslq = sum(1 for s in contract["skus_all"] if s.get("vslq") is not None)
    print("\n=== QoQ movers populate now that Q1 is a real LQ ===")
    print(f"  SKUs with a real vs_lq: {n_with_real_vslq} of {len(contract['skus_all'])}")
    print(f"  rising: {len(movers['rising'])}, falling: {len(movers['falling'])}")
    return n_with_real_vslq > 0 and len(movers["rising"]) == 10 and len(movers["falling"]) == 10


def check_template_renders_no_monthly_leak(contract):
    """§6 bullet 6: template renders with no monthly-string leak (MoM/LM
    guard) and toggle-state reconciliation holds at quarter level.
    """
    from validate import _toggle_reconciliation_errors
    template_html = open(TEMPLATE_HTML).read()
    source_warnings = validate_template_source(template_html)

    html = render_contract(contract, template_html)
    output_warnings = validate_output(html)
    toggle_errors = _toggle_reconciliation_errors(contract["collections"])

    has_qoq = "QoQ" in html.split("<script>")[0]  # header/body text, not the JS data block
    no_leftover_tokens = "{{" not in html

    print("\n=== Template renders correctly for quarter (§6 bullet 6) ===")
    print(f"  template source MoM/LM guard warnings: {source_warnings or 'none'}")
    print(f"  output warnings: {output_warnings or 'none'}")
    print(f"  toggle-reconciliation errors: {toggle_errors or 'none'}")
    print(f"  renders 'QoQ' somewhere in static HTML: {has_qoq}")
    print(f"  no leftover {{{{TOKENS}}}}: {no_leftover_tokens}")
    return (not source_warnings and not output_warnings and not toggle_errors
            and has_qoq and no_leftover_tokens)


def check_frozen_fixture():
    """§6 bullet 7: freeze 2026-Q2_contract.json (chained off the real Q1
    2026 quarterly contract) as the quarterly regression fixture; confirm
    re-emitting reproduces it exactly.
    """
    print("\n=== Frozen fixture (§6 bullet 7) ===")
    if not os.path.exists(FIXTURE_CONTRACT):
        emit_contract_from_oracle_quarter(MONTH_XLSX, lq_contract=Q1_CONTRACT, out_path=FIXTURE_CONTRACT)
        print(f"  wrote {FIXTURE_CONTRACT} (first run -- committed as the quarterly regression baseline)")

    with open(FIXTURE_CONTRACT) as f:
        frozen = json.load(f)
    fresh = emit_contract_from_oracle_quarter(MONTH_XLSX, lq_contract=Q1_CONTRACT)

    mismatches = [k for k in PAYLOAD_KEYS if frozen[k] != fresh[k]]
    print(f"  payload mismatches vs frozen fixture: {mismatches or 'none -- reproduces exactly'}")
    return not mismatches


def check_valid_and_publishable(contract):
    """Sanity: validate_contract raises nothing, and the oracle-sourced
    quarter is publishable per BRIEF step 5 §5 ("the oracle-sourced
    quarter is correct and is what you build/validate against now").
    """
    print("\n=== validate_contract + can_publish ===")
    try:
        warnings = validate_contract(contract)
        print(f"  validate_contract warnings: {warnings}")
        ok = True
    except ValueError as e:
        print(f"  validate_contract FAILED: {e}")
        ok = False
    publishable = can_publish(contract)
    print(f"  can_publish(): {publishable} (expected True -- oracle-sourced, reconciled)")
    return ok and publishable


def main():
    additive_ok, contract = check_additive_components_match_targets()
    checks = {
        "additive_components_match_targets": additive_ok,
        "rate_recomputed_not_averaged": check_rate_recomputed_not_averaged(contract),
        "country_ties_and_row_present": check_country_ties_and_row_present(contract),
        "completeness_tripwire": check_completeness_tripwire(contract),
        "lq_ly_provenance": check_lq_ly_provenance(contract),
        "movers_populate_from_real_lq": check_movers_populate_from_real_lq(contract),
        "template_renders_no_monthly_leak": check_template_renders_no_monthly_leak(contract),
        "frozen_fixture": check_frozen_fixture(),
        "valid_and_publishable": check_valid_and_publishable(contract),
    }

    print("\n=== Summary ===")
    for name, ok in checks.items():
        print(f"  {name:34s} {'PASS' if ok else 'FAIL'}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
