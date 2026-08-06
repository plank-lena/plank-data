"""Build a real Matrixify-sourced quarterly trading dashboard, end to end.

Same gap as build_matrixify_dashboard.py (T0), one level up:
emit_contract_from_matrixify_quarter() and render_contract() were each
proven in test_quarterly.py, but nothing chained them into a written
output file for the real Matrixify quarterly path.

Worth knowing before trusting a quarter built with this script (T0
finding, not silently patched): emit_contract_from_matrixify_quarter()
calls emit_contract_from_matrixify() per month WITHOUT any lm_contract/
ly_contract/oracle_bootstrap_path, so each month's own contract inside the
aggregate carries zero lm/ly, and the quarter's own LY (_aggregate_ly,
summing each month's 'ly' block) inherits that zero. This differs from
emit_contract_from_oracle_quarter(), whose docstring explicitly promises
"LY is always real" -- that promise does not currently hold for this
Matrixify front-end. Fixing it means deciding how each month should source
its own lm/ly inside the aggregate (bootstrap each month from its own
oracle fixture too? chain from already-committed monthly Matrixify
contracts?) -- a real decision for whoever owns quarterly.py next, not
something this glue script should decide silently. (Separately: round-2
review's B3 -- vs-LY is unavailable at the QUARTER headline grain for the
same underlying reason, no 2025 Matrixify data exists to source it from;
that's a data-staging decision, not something this script can fix either.)

Auto-detects a quarterly oracle bootstrap file (_QUARTER_ORACLE_FIXTURES)
for the SKU-level vs-LQ backfill (B2, round-2 review) -- see
emit_contract_from_matrixify_quarter's own docstring for what this does
and doesn't cover.

Run:
  python trading/build_matrixify_quarterly_dashboard.py 2026-04 2026-05 2026-06
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_DIR = os.path.join(_HERE, "dashboard")
for _p in (_HERE, _DASHBOARD_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from contract import render_contract
from quarterly import emit_contract_from_matrixify_quarter

TEMPLATE = os.path.join(_DASHBOARD_DIR, "template", "dashboard.template.html")
SOURCE_DIR = os.path.join(_HERE, "source")
ORACLE_FIXTURE_DIR = os.path.join(_HERE, "tests", "fixtures")
CONTRACTS_DIR = os.path.join(_HERE, "contracts")
OUTPUT_DIR = os.path.join(_DASHBOARD_DIR, "output")

# B2: committed quarterly-mode oracle workbooks usable as a per-SKU vs-LQ
# bootstrap source, keyed by the 3 consecutive months they cover.
_QUARTER_ORACLE_FIXTURES = {
    ("2026-04", "2026-05", "2026-06"): "2026-Q2_Quarterly_Trading_Report.xlsx",
}

# B3 (round-2 review): real prior-year same-month Matrixify contracts, one
# per constituent month -- same mapping as build_matrixify_dashboard.py's
# _LY_MONTH_CONTRACTS, forwarded per-month via ly_month_contracts. See
# emit_contract_from_matrixify's ly_month_contract docstring for what this
# does and doesn't fix (department-grain vs_ly only, disclosed caveat).
_LY_MONTH_CONTRACTS = {
    "2026-04": "2025-04-matrixify.json",
    "2026-05": "2025-05-matrixify.json",
    "2026-06": "2025-06-matrixify.json",
}

# QQ1 (round-3 review): the same MONTHLY-mode oracle fixtures build_
# matrixify_dashboard.py's own _ORACLE_FIXTURES uses for each standalone
# monthly build, forwarded per-month via month_oracle_bootstrap_paths so
# each constituent month gets a real headline lm/ly inside the quarterly
# aggregate too -- see emit_contract_from_matrixify_quarter's own
# docstring for why this was the actual "quarterly has no YoY" gap.
_MONTH_ORACLE_FIXTURES = {
    "2026-04": "2026-04_Monthly_Trading_Report.xlsx",
    "2026-05": "2026-05_Monthly_Trading_Report.xlsx",
    "2026-06": "2026-06_Monthly_Trading_Report.xlsx",
}


def build(periods, lq_contract=None, out_suffix="_matrixify"):
    if len(periods) != 3:
        raise ValueError(f"emit_contract_from_matrixify_quarter needs exactly 3 consecutive months, got {periods}")
    month_specs = [
        (p, os.path.join(SOURCE_DIR, f"orders_{p}_UK.csv"), os.path.join(SOURCE_DIR, f"orders_{p}_US.csv"))
        for p in periods
    ]
    oracle_bootstrap_path = None
    fixture = _QUARTER_ORACLE_FIXTURES.get(tuple(periods))
    if fixture:
        oracle_bootstrap_path = os.path.join(ORACLE_FIXTURE_DIR, fixture)
    ly_month_contracts = []
    for p in periods:
        ly_fixture = _LY_MONTH_CONTRACTS.get(p)
        candidate = os.path.join(CONTRACTS_DIR, ly_fixture) if ly_fixture else None
        ly_month_contracts.append(candidate if candidate and os.path.exists(candidate) else None)
    month_oracle_bootstrap_paths = []
    for p in periods:
        mo_fixture = _MONTH_ORACLE_FIXTURES.get(p)
        candidate = os.path.join(ORACLE_FIXTURE_DIR, mo_fixture) if mo_fixture else None
        month_oracle_bootstrap_paths.append(candidate if candidate and os.path.exists(candidate) else None)
    contract = emit_contract_from_matrixify_quarter(
        month_specs, lq_contract=lq_contract, oracle_bootstrap_path=oracle_bootstrap_path,
        ly_month_contracts=ly_month_contracts,
        month_oracle_bootstrap_paths=month_oracle_bootstrap_paths,
    )

    q_label = contract["period_model"]["cm"]["label"]  # e.g. "Q2 2026"
    os.makedirs(CONTRACTS_DIR, exist_ok=True)
    contract_path = os.path.join(CONTRACTS_DIR, f"{q_label.replace(' ', '-')}-matrixify.json")
    with open(contract_path, "w") as f:
        json.dump(contract, f, indent=2, default=str)

    with open(TEMPLATE, encoding="utf-8") as f:
        template_html = f.read()
    html = render_contract(contract, template_html)

    label = q_label.replace(" ", "_")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"{label}_dashboard{out_suffix}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    reconciled = contract["provenance"]["reconciled"]
    print(f"contract:  {contract_path}")
    print(f"dashboard: {out_path}")
    print(f"reconciled (structural leak check): {reconciled}")
    print(f"lq_ly_source: {contract['provenance']['lq_ly_source']}")
    if not reconciled:
        print("WARNING: not reconciled -- do not publish this build.", file=sys.stderr)
    return out_path, contract_path


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python trading/build_matrixify_quarterly_dashboard.py "
              "<YYYY-MM> <YYYY-MM> <YYYY-MM> [--lq-contract path]", file=sys.stderr)
        sys.exit(1)
    periods_arg = sys.argv[1:4]
    kwargs = {}
    rest = sys.argv[4:]
    if rest and rest[0] == "--lq-contract":
        kwargs["lq_contract"] = rest[1]
    build(periods_arg, **kwargs)
