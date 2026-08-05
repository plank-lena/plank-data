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
    _add_headline_kpis, _strip_vestigial, _exclude_dead_categories,
    _normalize_oracle_prod_types, _is_el_component,
)
from validate import validate_contract

ORACLE_XLSX = os.path.join(HERE, "fixtures", "2026-05_Monthly_Trading_Report.xlsx")
UK_CSV = os.path.join(TRADING_DIR, "source", "orders_2026-05_UK.csv")
US_CSV = os.path.join(TRADING_DIR, "source", "orders_2026-05_US.csv")
TEMPLATE_HTML = os.path.join(DASHBOARD_DIR, "template", "dashboard.template.html")
# BRIEF #4 step 4 §6/§12: the redesign extends the contract shape (new
# headline KPIs, is_el_component, st/wc/inv dropped) -- the OLD fixture
# below is the pre-redesign shape, kept as historical reference; the new
# one is the regression baseline going forward.
FIXTURE_CONTRACT = os.path.join(HERE, "fixtures", "2026-05_contract.json")
FIXTURE_CONTRACT_REDESIGN = os.path.join(HERE, "fixtures", "2026-05_contract_redesign.json")


def _apply_contract_layer_mutations(raw):
    """Mirror emit_contract_from_oracle's own payload mutations (BRIEF #4
    step 4: headline KPIs added, is_el_component added, st/wc/inv stripped;
    review-round-1 T2a: dead departments like Door dropped; T2b: prod_types
    normalised to carry d2c/b2b/uk/us/lq_sales) on a plain
    extract_all() payload -- used by the two checks below to compare a
    "direct" path against the emitted path on equal footing, since step 4
    deliberately makes the CONTRACT layer richer than extract_all()'s own
    raw shape, not just a repackaging of it.
    """
    payload = {k: raw[k] for k in PAYLOAD_KEYS}
    _add_headline_kpis(payload["current"])
    for sku in payload["skus_all"]:
        sku["is_el_component"] = _is_el_component(sku.get("coll"))
    _strip_vestigial(payload)
    _exclude_dead_categories(payload)
    _normalize_oracle_prod_types(payload)
    return payload


def check_round_trip_identity():
    """§8.1: load_contract(emit_contract_from_oracle(oracle)) equals
    extract_all(oracle)'s payload plus BRIEF #4 step 4's known, deliberate
    contract-layer additions (yoy_growth_pct/b2b_share/is_el_component) and
    subtractions (st/wc/inv), through a real JSON file round-trip (not just
    in-memory dicts) -- proving the JSON write/read itself loses nothing,
    which is what this check is actually for.
    """
    raw_direct = _apply_contract_layer_mutations(extract_all(ORACLE_XLSX))
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
    from extract_all() (plus the same known contract-layer mutations --
    see _apply_contract_layer_mutations) produces, byte-for-byte. (Proven
    against the current template's own output, not a possibly-stale
    committed HTML fixture -- the committed 2026-05_known_good.html
    predates the redesign, so comparing to it would fail on the redesign
    itself, not a contract-layer bug. This is the stronger, more relevant
    proof: the contract loses nothing compute.py/render.py need.)
    """
    raw_direct = _apply_contract_layer_mutations(extract_all(ORACLE_XLSX))
    template_html = open(TEMPLATE_HTML).read()

    contract_direct = {"provenance": {"reconciled": True}, **raw_direct}
    html_direct = render_contract(contract_direct, template_html)

    contract_emitted = emit_contract_from_oracle(ORACLE_XLSX)
    html_emitted = render_contract(contract_emitted, template_html)

    identical = html_direct == html_emitted
    print(f"\n=== Render parity (§8.2) ===")
    print(f"  identical: {identical} (direct {len(html_direct)} chars, via-contract {len(html_emitted)} chars)")
    return identical


def check_reconciled_independent_of_oracle():
    """§8.3, REVISED 2026-08-05 (ROADMAP.md §5): reconciled/can_publish() are
    gated on the STRUCTURAL leak check only (uk+us+row ties to an
    independent grand total) -- matching the hand-built oracle to 0.1% is
    no longer a publishing requirement (the oracle's returns figure is an
    early, still-maturing snapshot that a deterministic rebuild can't
    reproduce by design). This month's real Matrixify contract structurally
    reconciles, so it publishes cleanly, no banner -- even though
    country_gaps_vs_oracle (still computed, informational only) shows real
    gaps against the May 2026 oracle.
    """
    mx_contract = emit_contract_from_matrixify(
        period="2026-05", uk_csv=UK_CSV, us_csv=US_CSV, oracle_bootstrap_path=ORACLE_XLSX,
    )
    template_html = open(TEMPLATE_HTML).read()
    html = render_contract(mx_contract, template_html)  # must not raise

    reconciled = mx_contract["provenance"]["reconciled"]
    has_banner = PROVISIONAL_BANNER_HTML in html
    publish_ok = can_publish(mx_contract)
    gaps = mx_contract["provenance"]["country_gaps_vs_oracle"]

    print(f"\n=== Reconciled independent of the oracle (§8.3, revised) ===")
    print(f"  reconciled: {reconciled} (expected True -- structural leak check only)")
    print(f"  country_gaps_vs_oracle (informational, NOT gating): {gaps}")
    print(f"  banner rendered: {has_banner} (expected False)")
    print(f"  can_publish(): {publish_ok} (expected True)")
    return (reconciled is True) and (not has_banner) and (publish_ok is True) and (gaps is not None)


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
    """§8.6, updated by BRIEF #4 step 4 §1/§6: with no inventory feed
    wired AND the redesign dropping st/wc/inv entirely, these keys must be
    ABSENT from the contract (not merely None) -- a stronger guarantee than
    before that nothing vestigial leaks back in.
    """
    mx_contract = emit_contract_from_matrixify(
        period="2026-05", uk_csv=UK_CSV, us_csv=US_CSV, oracle_bootstrap_path=ORACLE_XLSX,
    )
    current_absent = not ({"sell_through", "weeks_cover", "inventory"} & mx_contract["current"].keys())
    statuses_absent = all(not ({"st", "wc", "inv"} & s.keys()) for s in mx_contract["statuses"])
    collections_absent = all(not ({"st", "wc", "inv"} & c.keys()) for c in mx_contract["collections"])
    skus_absent = all(not ({"st", "wc", "inv"} & s.keys()) for s in mx_contract["skus_all"])

    template_html = open(TEMPLATE_HTML).read()
    html = render_contract(mx_contract, template_html)  # must not raise (None-safe rendering)
    no_leftover_tokens = "KPI_ST_VAL" not in html and "{{" not in html

    print(f"\n=== No fabricated provisionals (§8.6) ===")
    print(f"  current st/wc/inv keys absent: {current_absent}")
    print(f"  statuses/collections/skus_all st/wc/inv keys absent: "
          f"{statuses_absent and collections_absent and skus_absent}")
    print(f"  renders without crashing, no leftover tokens: {no_leftover_tokens}")
    return current_absent and statuses_absent and collections_absent and skus_absent and no_leftover_tokens


def check_frozen_fixture():
    """§8.7 / BRIEF #4 step 4 §6/§12: freeze the oracle-sourced May contract
    in the REDESIGN shape as the new regression fixture; confirm
    re-emitting reproduces it exactly. The old 2026-05_contract.json is the
    pre-redesign shape -- kept as historical reference, no longer checked
    here (it will never match post-redesign by design: new headline KPIs,
    is_el_component, st/wc/inv dropped).
    """
    print(f"\n=== Frozen fixture (§8.7) ===")
    if not os.path.exists(FIXTURE_CONTRACT_REDESIGN):
        emit_contract_from_oracle(ORACLE_XLSX, out_path=FIXTURE_CONTRACT_REDESIGN)
        print(f"  wrote {FIXTURE_CONTRACT_REDESIGN} (first run -- committed as the redesign regression baseline)")
        print(f"  ({FIXTURE_CONTRACT} kept as the pre-redesign historical fixture, not compared against)")

    with open(FIXTURE_CONTRACT_REDESIGN) as f:
        frozen = json.load(f)
    fresh = emit_contract_from_oracle(ORACLE_XLSX)

    # Compare payload only -- provenance.built_at/commit legitimately
    # differ run to run; that's not what this fixture guards.
    mismatches = [k for k in PAYLOAD_KEYS if frozen[k] != fresh[k]]
    print(f"  payload mismatches vs frozen redesign fixture: {mismatches or 'none -- reproduces exactly'}")
    return not mismatches


def main():
    checks = {
        "round_trip_identity": check_round_trip_identity(),
        "render_parity": check_render_parity(),
        "reconciled_independent_of_oracle": check_reconciled_independent_of_oracle(),
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
