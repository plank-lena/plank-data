"""Build a real Matrixify-sourced monthly trading dashboard, end to end.

Chains emit_contract_from_matrixify() -> render_contract() -> writes an
HTML file. Closes a real gap found doing the trading review-round-1 brief
(T0): contract emission and rendering were each proven correct in
test_contract.py, but nothing outside the test suite ever chained them
into a written output file -- the only trading HTML that existed anywhere
in this repo came from pipeline.py's ORACLE-sourced path (used to build
and prove the template, per contract.py's own docstring), never the real
Matrixify path this whole Phase-B rebuild was for.

Bootstraps a month's LM/LY from its OWN oracle workbook (oracle_bootstrap_
path) by default -- the same mechanism test_contract.py's
check_reconciled_independent_of_oracle() already exercises for May, just
applied to whichever month is being built. April/May/June 2026 were all
built this way (2026-08-05): bootstrapping gives each month a REAL LM/LY
(the oracle sheet's own embedded prior-period columns) with no dependency
on the others, which is actually the better choice here, not just the
simpler one -- there is no real prior-year (2025) Matrixify data to chain
LY from regardless, and contract.py only chains when BOTH lm_contract and
ly_contract are supplied together (see the guard in build() below).
ROADMAP.md §5's contract-chaining discipline is about not re-deriving an
ALREADY-PUBLISHED month's own figures from a fresh Matrixify recompute
later (returns keep maturing) -- it doesn't apply to a period's first-ever
build. Pass --lm-contract and --ly-contract together (never just one) once
there's a real reason to chain instead of bootstrap.

Run:
  python trading/build_matrixify_dashboard.py 2026-06
  python trading/build_matrixify_dashboard.py 2026-06 \\
      --lm-contract trading/contracts/2026-05-matrixify.json
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_DIR = os.path.join(_HERE, "dashboard")
for _p in (_HERE, _DASHBOARD_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from contract import emit_contract_from_matrixify, render_contract

TEMPLATE = os.path.join(_DASHBOARD_DIR, "template", "dashboard.template.html")
SOURCE_DIR = os.path.join(_HERE, "source")
ORACLE_FIXTURE_DIR = os.path.join(_HERE, "tests", "fixtures")
CONTRACTS_DIR = os.path.join(_HERE, "contracts")
OUTPUT_DIR = os.path.join(_DASHBOARD_DIR, "output")

# Committed oracle fixtures usable as a first-build LM/LY bootstrap source,
# one per period this repo currently has Matrixify exports for.
_ORACLE_FIXTURES = {
    "2026-04": "2026-04_Monthly_Trading_Report.xlsx",
    "2026-05": "2026-05_Monthly_Trading_Report.xlsx",
    "2026-06": "2026-06_Monthly_Trading_Report.xlsx",
}


def build(period, lm_contract=None, ly_contract=None, out_suffix="_matrixify"):
    # emit_contract_from_matrixify only chains when BOTH are given -- passing
    # just one silently falls through to its zero/"none_available" branch
    # (a real bug hit building this script: April/May's first real builds
    # were bootstrapped correctly, but a --lm-contract-only May rebuild
    # zeroed LM *and* LY with no error). Fail loud instead.
    if (lm_contract is None) != (ly_contract is None):
        raise ValueError("build_matrixify_dashboard: pass --lm-contract and --ly-contract "
                          "together, or neither (to bootstrap from this period's own oracle "
                          "fixture) -- partial chaining silently zeros both in contract.py.")

    uk_csv = os.path.join(SOURCE_DIR, f"orders_{period}_UK.csv")
    us_csv = os.path.join(SOURCE_DIR, f"orders_{period}_US.csv")

    oracle_bootstrap_path = None
    if lm_contract is None and ly_contract is None:
        fixture = _ORACLE_FIXTURES.get(period)
        if fixture:
            oracle_bootstrap_path = os.path.join(ORACLE_FIXTURE_DIR, fixture)
        else:
            print(f"build_matrixify_dashboard: no oracle fixture for {period} to bootstrap "
                  f"LM/LY from, and no --lm-contract/--ly-contract given -- LM/LY will be zero.",
                  file=sys.stderr)

    contract = emit_contract_from_matrixify(
        period=period, uk_csv=uk_csv, us_csv=us_csv,
        lm_contract=lm_contract, ly_contract=ly_contract,
        oracle_bootstrap_path=oracle_bootstrap_path,
    )

    os.makedirs(CONTRACTS_DIR, exist_ok=True)
    contract_path = os.path.join(CONTRACTS_DIR, f"{period}-matrixify.json")
    with open(contract_path, "w") as f:
        json.dump(contract, f, indent=2, default=str)

    with open(TEMPLATE, encoding="utf-8") as f:
        template_html = f.read()
    html = render_contract(contract, template_html)

    label = contract["period_model"]["cm"]["label"].replace(" ", "_")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"{label}_dashboard{out_suffix}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    reconciled = contract["provenance"]["reconciled"]
    print(f"contract:  {contract_path}")
    print(f"dashboard: {out_path}")
    print(f"reconciled (structural leak check): {reconciled}")
    if not reconciled:
        print("WARNING: not reconciled -- do not publish this build.", file=sys.stderr)
    return out_path, contract_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python trading/build_matrixify_dashboard.py <YYYY-MM> "
              "[--lm-contract path] [--ly-contract path]", file=sys.stderr)
        sys.exit(1)
    period_arg = sys.argv[1]
    kwargs = {}
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--lm-contract":
            kwargs["lm_contract"] = args[i + 1]
            i += 2
        elif args[i] == "--ly-contract":
            kwargs["ly_contract"] = args[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)
    build(period_arg, **kwargs)
