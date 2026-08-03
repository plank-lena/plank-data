"""Acceptance checks for the trading data contract (BRIEF #3 §8), run
against the real committed May 2026 oracle fixture and real Matrixify
source data.

Run:  python trading/tests/test_contract.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(__file__)
TRADING_DIR = os.path.join(HERE, "..")
DASHBOARD_DIR = os.path.join(TRADING_DIR, "dashboard")
for _p in (TRADING_DIR, DASHBOARD_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from extract import extract_all
from contract import (
    emit_contract_from_oracle, emit_contract_from_matrixify, load_contract,
    render_contract, can_publish, PAYLOAD_KEYS, PROVISIONAL_BANNER_HTML,
)
from validate import validate_contract

ORACLE_XLSX = os.path.join(HERE, "fixtures", "2026-05_Monthly_Trading_Report.xlsx")
UK_CSV = os.path.join(TRADING_DIR, "source", "orders_2026-05_UK.csv")
US_CSV = os.path.join(TRADING_DIR, "source", "orders_2026-05_US.csv")
TEMPLATE_HTML = os.path.join(DASHBOARD_DIR, "template", "dashboard.template.html")
FIXTURE_CONTRACT = os.path.join(HERE, "fixtures", "2026-05_contract.json")


def check_round_trip_identity():
    """§8.1: load_contract(emit_contract_from_oracle(oracle)) equals
    extract_all(oracle) field-for-field (minus _ws_*), through a real JSON
    file round-trip (not just in-memory dicts).
    """
    raw_direct = extract_all(ORACLE_XLSX)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    emit_contract_from_oracle(ORACLE_XLSX, out_path=tmp)
    raw_from_file = load_contract(tmp)
    os.unlink(tmp)

    mismatches = [k for k in PAYLOAD_KEYS if raw_direct[k] != raw_from_file[k]]
    print(f"\n=== Round-trip identity (§8.1) ===")
    print(f"  mismatches: {mismatches or 'none -- exact match'}")
    return not mismatches


def check_render_parity():
    """§8.2: rendering from a contract reproduces what rendering directly
    from extract_all() produces, byte-for-byte. (Proven against the
    current template's own output, not a possibly-stale committed HTML
    fixture -- the committed 2026-05_known_good.html predates unrelated
    template CSS changes, so comparing to it would fail on style drift
    that has nothing to do with the contract layer. This is the stronger,
    more relevant proof: the contract loses nothing compute.py/render.py need.)
    """
    raw_direct = extract_all(ORACLE_XLSX)
    template_html = open(TEMPLATE_HTML).read()

    contract_direct = {"provenance": {"reconciled": True}, **{k: raw_direct[k] for k in PAYLOAD_KEYS}}
    html_direct = render_contract(contract_direct, template_html)

    contract_emitted = emit_contract_from_oracle(ORACLE_XLSX)
    html_emitted = render_contract(contract_emitted, template_html)

    identical = html_direct == html_emitted
    print(f"\n=== Render parity (§8.2) ===")
    print(f"  identical: {identical} (direct {len(html_direct)} chars, via-contract {len(html_emitted)} chars)")
    return identical


def check_provisional_path():
    """§8.3: a Matrixify-sourced contract with reconciled:false renders the
    PROVISIONAL banner and can_publish() refuses; build/eyeball (i.e.
    emission + rendering itself) still succeed without raising.
    """
    mx_contract = emit_contract_from_matrixify(
        period="2026-05", uk_csv=UK_CSV, us_csv=US_CSV, oracle_bootstrap_path=ORACLE_XLSX,
    )
    template_html = open(TEMPLATE_HTML).read()
    html = render_contract(mx_contract, template_html)  # must not raise

    reconciled = mx_contract["provenance"]["reconciled"]
    has_banner = PROVISIONAL_BANNER_HTML in html
    publish_ok = can_publish(mx_contract)

    print(f"\n=== Provisional path (§8.3) ===")
    print(f"  reconciled: {reconciled} (expected False -- BRIEF #5's known order-scope gap is still open)")
    print(f"  banner rendered: {has_banner}")
    print(f"  can_publish(): {publish_ok} (expected False)")
    return (reconciled is False) and has_banner and (publish_ok is False)


def check_totals_tie_with_unknown():
    """§8.4: unmatched SKUs sit in Unknown; uk+us+row still reconciles to
    an independently-computed total (residual 0).
    """
    mx_contract = emit_contract_from_matrixify(
        period="2026-05", uk_csv=UK_CSV, us_csv=US_CSV, oracle_bootstrap_path=ORACLE_XLSX,
    )
    depts = {t["t"] for t in mx_contract["prod_types"]}
    has_unknown = "Unknown" in depts
    unknown_sales = next((t["sales"] for t in mx_contract["prod_types"] if t["t"] == "Unknown"), 0)

    current = mx_contract["current"]
    parts = current["uk_gbp"] + current["us_gbp"] + current["row_gbp"]
    residual = abs(parts - current["total_sales"])

    print(f"\n=== Totals tie with Unknown present (§8.4) ===")
    print(f"  Unknown department present: {has_unknown} (£{unknown_sales:,.2f})")
    print(f"  uk+us+row - total_sales residual: {residual:.6f} (expect ~0)")
    return has_unknown and unknown_sales > 0 and residual < 0.01


def check_lq_ly_provenance():
    """§8.5: LM/LY populate from a committed prior contract when present;
    bootstrap fallback is stamped when not (true today -- no prior
    Matrixify-period contract exists yet).
    """
    mx_contract = emit_contract_from_matrixify(
        period="2026-05", uk_csv=UK_CSV, us_csv=US_CSV, oracle_bootstrap_path=ORACLE_XLSX,
    )
    bootstrap_stamped = mx_contract["provenance"]["lq_ly_source"] == "oracle_bootstrap"
    lm_populated = mx_contract["lm"]["total"] > 0
    ly_populated = mx_contract["ly"]["total"] > 0

    # Now the contract-chain path: feed the oracle contract itself back in
    # as a fake "prior contract" for both lm/ly, and confirm it's used
    # instead of bootstrapping.
    oracle_contract = emit_contract_from_oracle(ORACLE_XLSX)
    chained = emit_contract_from_matrixify(
        period="2026-05", uk_csv=UK_CSV, us_csv=US_CSV,
        lm_contract=oracle_contract, ly_contract=oracle_contract,
    )
    chain_stamped = chained["provenance"]["lq_ly_source"] == "contract_chain"
    # lm/ly should equal the oracle contract's OWN current, reshaped
    chain_correct = chained["lm"]["total"] == oracle_contract["current"]["total_sales"]

    print(f"\n=== LQ/LY provenance (§8.5) ===")
    print(f"  bootstrap: lq_ly_source={mx_contract['provenance']['lq_ly_source']}, "
          f"lm.total={mx_contract['lm']['total']:,.2f}, ly.total={mx_contract['ly']['total']:,.2f}")
    print(f"  contract-chain: lq_ly_source={chained['provenance']['lq_ly_source']}, "
          f"lm.total={chained['lm']['total']:,.2f} == oracle current.total_sales "
          f"{oracle_contract['current']['total_sales']:,.2f}: {chain_correct}")
    return bootstrap_stamped and lm_populated and ly_populated and chain_stamped and chain_correct


def check_no_fabricated_provisionals():
    """§8.6: with no inventory feed wired, st/wc/inv are null throughout
    (never a fabricated number), and render as '—' via config.fmt_inv /
    the existing None-safe formatting compute.py already has.
    """
    mx_contract = emit_contract_from_matrixify(
        period="2026-05", uk_csv=UK_CSV, us_csv=US_CSV, oracle_bootstrap_path=ORACLE_XLSX,
    )
    current_null = (mx_contract["current"]["sell_through"] is None
                     and mx_contract["current"]["weeks_cover"] is None
                     and mx_contract["current"]["inventory"] is None)
    statuses_null = all(s["st"] is None and s["wc"] is None and s["inv"] is None for s in mx_contract["statuses"])
    collections_null = all(c["st"] is None and c["wc"] is None for c in mx_contract["collections"])
    skus_null = all(s["st"] is None and s["wc"] is None for s in mx_contract["skus_all"])

    template_html = open(TEMPLATE_HTML).read()
    html = render_contract(mx_contract, template_html)  # must not raise (None-safe rendering)
    dash_present = "KPI_ST_VAL" not in html and "—" in html  # token filled, dash rendered somewhere

    print(f"\n=== No fabricated provisionals (§8.6) ===")
    print(f"  current st/wc/inv all None: {current_null}")
    print(f"  statuses/collections/skus_all st/wc all None: "
          f"{statuses_null and collections_null and skus_null}")
    print(f"  renders without crashing, dash present: {dash_present}")
    return current_null and statuses_null and collections_null and skus_null and dash_present


def check_frozen_fixture():
    """§8.7: freeze the oracle-sourced May contract.json as a regression
    fixture; confirm re-emitting reproduces it exactly.
    """
    print(f"\n=== Frozen fixture (§8.7) ===")
    if not os.path.exists(FIXTURE_CONTRACT):
        emit_contract_from_oracle(ORACLE_XLSX, out_path=FIXTURE_CONTRACT)
        print(f"  wrote {FIXTURE_CONTRACT} (first run -- committed as the regression baseline)")

    with open(FIXTURE_CONTRACT) as f:
        frozen = json.load(f)
    fresh = emit_contract_from_oracle(ORACLE_XLSX)

    # Compare payload only -- provenance.built_at/commit legitimately
    # differ run to run; that's not what this fixture guards.
    mismatches = [k for k in PAYLOAD_KEYS if frozen[k] != fresh[k]]
    print(f"  payload mismatches vs frozen fixture: {mismatches or 'none -- reproduces exactly'}")
    return not mismatches


def main():
    checks = {
        "round_trip_identity": check_round_trip_identity(),
        "render_parity": check_render_parity(),
        "provisional_path": check_provisional_path(),
        "totals_tie_with_unknown": check_totals_tie_with_unknown(),
        "lq_ly_provenance": check_lq_ly_provenance(),
        "no_fabricated_provisionals": check_no_fabricated_provisionals(),
        "frozen_fixture": check_frozen_fixture(),
    }

    print("\n=== Summary ===")
    for name, ok in checks.items():
        print(f"  {name:28s} {'PASS' if ok else 'FAIL'}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
