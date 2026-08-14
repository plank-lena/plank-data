"""Build a real Matrixify-sourced monthly trading dashboard, end to end.

Chains emit_contract_from_matrixify() -> render_contract() -> writes an
HTML file. Closes a real gap found doing the trading review-round-1 brief
(T0): contract emission and rendering were each proven correct in
test_contract.py, but nothing outside the test suite ever chained them
into a written output file -- the only trading HTML that existed anywhere
in this repo came from pipeline.py's ORACLE-sourced path (used to build
and prove the template, per contract.py's own docstring), never the real
Matrixify path this whole Phase-B rebuild was for.

Period comes from the PROMPT (period-from-prompt, 2026-08-12) -- accepts
either the existing "YYYY-MM" convention or a natural period string like
"June 2026", via common/period.py. LM/LY bootstrap preference, connector-
first:
  1. --lm-contract + --ly-contract together (contract-chaining, LOCKED --
     ROADMAP.md §5: always prefer this once a period has its own committed
     contract; never re-derive an already-published month fresh).
  2. Fresh Matrixify pulls for the LM/LY calendar windows (requested_
     period_model bootstrap) -- the DEFAULT for a period with no committed
     contract yet, so long as those windows' order CSVs are already landed.
     No workbook touched.
  3. The period's own oracle fixture (--oracle-bootstrap) -- kept only for
     explicit oracle-comparison/regression-parity runs; no longer the
     silent default (retired 2026-08-12, period-from-prompt build).
  4. Honest all-zero placeholder, if none of the above have what they need.

ROADMAP.md §5's contract-chaining discipline is about not re-deriving an
ALREADY-PUBLISHED month's own figures from a fresh Matrixify recompute
later (returns keep maturing) -- it doesn't apply to a period's first-ever
build, which is exactly when (2) or (3) apply. Pass --lm-contract and
--ly-contract together (never just one) once there's a real reason to
chain instead of bootstrap.

Run:
  python trading/build_matrixify_dashboard.py "June 2026"
  python trading/build_matrixify_dashboard.py 2026-06
  python trading/build_matrixify_dashboard.py 2026-06 \\
      --lm-contract trading/contracts/2026-05-matrixify.json
  python trading/build_matrixify_dashboard.py 2026-06 --oracle-bootstrap
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_DIR = os.path.join(_HERE, "dashboard")
_REPO_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _DASHBOARD_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from contract import emit_contract_from_matrixify, render_contract, write_committed_file
from common.period import parse_period, month_period_string
from common.sources import (
    matrixify_orders_snapshot, matrixify_orders_snapshot_covers,
    assert_orders_coverage, check_orders_manifest_staleness, load_orders_manifest,
)

_YYYY_MM_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _period_model_for(period_arg, as_of=None):
    """Accept either the existing 'YYYY-MM' convention or a natural period
    string ('June 2026') -- returns (period_key, PeriodModel). Fails loud
    (via parse_period) on an unparseable or future period.
    """
    m = _YYYY_MM_RE.match(period_arg)
    prompt_str = month_period_string(int(m.group(2)), int(m.group(1))) if m else period_arg
    pm = parse_period(prompt_str, as_of=as_of)
    return pm["cm"]["key"], pm

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

# T1: the immediately-prior consecutive month, used only to source
# current['trend_3mo']'s M-2 point (see contract.py's docstring) -- no
# entry for 2026-04 since there's no prior month's contract to read yet.
def _period_fully_covered(period, manifest=None, stores=("uk", "us")):
    """Soft (non-raising) twin of common.sources.assert_orders_coverage --
    True only when EVERY store's slice covers `period` in full. Used where a
    missing period is a legitimate branch rather than an error: deciding
    whether the LM/LY connector bootstrap can run. AND across stores, never
    OR (the OR form is the bug docs/2026-08-13_order_slices.md replaced).

    Defined here, and imported from here by
    build_matrixify_quarterly_dashboard.py, so monthly and quarterly share
    one definition. Added 2026-08-14: the quarterly builder was already
    importing this name and _resolve_ly_month_contract below, but neither
    was ever defined, so that module raised ImportError on load.
    """
    manifest = manifest if manifest is not None else load_orders_manifest()
    return all(matrixify_orders_snapshot_covers(s, period, manifest=manifest) for s in stores)


def _resolve_ly_month_contract(period):
    """Path to `period`'s prior-year same-month committed contract, or None
    if this period has no mapping or the file isn't present. Reads the
    existing _LY_MONTH_CONTRACTS table below rather than deriving the key, so
    the mapping stays declared in exactly one place.
    """
    fixture = _LY_MONTH_CONTRACTS.get(period)
    if not fixture:
        return None
    candidate = os.path.join(CONTRACTS_DIR, fixture)
    return candidate if os.path.exists(candidate) else None


_PRIOR_PERIOD = {
    "2026-05": "2026-04",
    "2026-06": "2026-05",
}

# B3 (round-2 review): real prior-year same-month Matrixify contracts,
# committed once the 2025 UK/US exports were pulled (see contract.py's
# ly_month_contract docstring for what this does and doesn't fix -- it
# backfills prod_types' vs_ly only, disclosed via ly_dept_unclassified_share).
_LY_MONTH_CONTRACTS = {
    "2026-04": "2025-04-matrixify.json",
    "2026-05": "2025-05-matrixify.json",
    "2026-06": "2025-06-matrixify.json",
}


def build(period_arg, lm_contract=None, ly_contract=None, out_suffix="_matrixify",
          force_oracle_bootstrap=False, as_of=None, force=False, contract_out_path=None,
          html_out_path=None):
    # emit_contract_from_matrixify only chains when BOTH are given -- passing
    # just one silently falls through to its zero/"none_available" branch
    # (a real bug hit building this script: April/May's first real builds
    # were bootstrapped correctly, but a --lm-contract-only May rebuild
    # zeroed LM *and* LY with no error). Fail loud instead.
    if (lm_contract is None) != (ly_contract is None):
        raise ValueError("build_matrixify_dashboard: pass --lm-contract and --ly-contract "
                          "together, or neither (to bootstrap LM/LY instead) -- partial "
                          "chaining silently zeros both in contract.py.")

    period, requested_pm = _period_model_for(period_arg, as_of=as_of)
    # 2026-08-14: migrated to the per-store, per-month slice convention
    # (docs/2026-08-13_order_slices.md). This call site was missed by the
    # 13 Aug migration -- it kept the pre-slice signature
    # (matrixify_orders_snapshot("uk") with no period) and so raised
    # TypeError before reading any data, i.e. the monthly builder could not
    # run at all from a clean clone. The coverage check below was also the
    # OR-across-stores version the brief replaced; it now calls the shared
    # assert_orders_coverage(), same as the quarterly builder.
    assert_orders_coverage(period)
    check_orders_manifest_staleness()
    uk_csv = matrixify_orders_snapshot("uk", period)
    us_csv = matrixify_orders_snapshot("us", period)


    oracle_bootstrap_path = None
    requested_period_model = None
    if lm_contract is None and ly_contract is None:
        lm_key, ly_key = requested_pm["lm"]["key"], requested_pm["ly"]["key"]
        manifest = load_orders_manifest()
        matrixify_bootstrap_available = (
            _period_fully_covered(lm_key, manifest) and _period_fully_covered(ly_key, manifest)
        )

        if force_oracle_bootstrap:
            fixture = _ORACLE_FIXTURES.get(period)
            if not fixture:
                raise FileNotFoundError(f"build_matrixify_dashboard: --oracle-bootstrap requested "
                                         f"but no oracle fixture exists for {period}")
            oracle_bootstrap_path = os.path.join(ORACLE_FIXTURE_DIR, fixture)
        elif matrixify_bootstrap_available:
            # Connector-first default (2026-08-12): real LM/LY from fresh
            # Matrixify pulls, no workbook touched.
            requested_period_model = requested_pm
        else:
            fixture = _ORACLE_FIXTURES.get(period)
            if fixture:
                oracle_bootstrap_path = os.path.join(ORACLE_FIXTURE_DIR, fixture)
                print(f"build_matrixify_dashboard: {lm_key}/{ly_key} Matrixify exports not both "
                      f"landed yet -- falling back to {period}'s oracle fixture for LM/LY "
                      f"(pass --lm-contract/--ly-contract once real prior contracts exist, or "
                      f"land the missing exports to prefer the connector-only bootstrap).",
                      file=sys.stderr)
            else:
                print(f"build_matrixify_dashboard: no oracle fixture for {period} and "
                      f"{lm_key}/{ly_key} Matrixify exports aren't both landed -- LM/LY will be zero.",
                      file=sys.stderr)

    prior_month_contract = None
    prior_period = _PRIOR_PERIOD.get(period)
    if prior_period:
        candidate = os.path.join(CONTRACTS_DIR, f"{prior_period}-matrixify.json")
        if os.path.exists(candidate):
            prior_month_contract = candidate

    ly_month_contract = None
    ly_fixture = _LY_MONTH_CONTRACTS.get(period)
    if ly_fixture:
        candidate = os.path.join(CONTRACTS_DIR, ly_fixture)
        if os.path.exists(candidate):
            ly_month_contract = candidate

    contract = emit_contract_from_matrixify(
        period=period, uk_csv=uk_csv, us_csv=us_csv,
        lm_contract=lm_contract, ly_contract=ly_contract,
        oracle_bootstrap_path=oracle_bootstrap_path,
        prior_month_contract=prior_month_contract,
        ly_month_contract=ly_month_contract,
        requested_period_model=requested_period_model,
    )

    contract_path = contract_out_path or os.path.join(CONTRACTS_DIR, f"{period}-matrixify.json")
    os.makedirs(os.path.dirname(contract_path), exist_ok=True)
    write_committed_file(json.dumps(contract, indent=2, default=str), contract_path,
                          force=force, label="contract")

    with open(TEMPLATE, encoding="utf-8") as f:
        template_html = f.read()
    html = render_contract(contract, template_html)

    label = contract["period_model"]["cm"]["label"].replace(" ", "_")
    out_path = html_out_path or os.path.join(OUTPUT_DIR, f"{label}_dashboard{out_suffix}.html")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    write_committed_file(html, out_path, force=force, label="dashboard output")

    # Values-only Excel companion, automatic alongside the HTML (2026-08-13) --
    # not a separate manual step. Monthly = a single-constituent call (see
    # excel_companion.py's module docstring for why that's not a special case
    # in the tab-building code, just constituent_contracts of length 1).
    period_label = contract["period_model"]["cm"]["label"]
    companion_path = os.path.join(os.path.dirname(out_path), f"{label}_companion{out_suffix}.xlsx")
    if os.path.exists(companion_path) and not force:
        raise FileExistsError(
            f"refusing to overwrite existing committed companion Excel at {companion_path} -- "
            f"pass force=True (CLI: --force) to intentionally overwrite."
        )
    from trading.excel_companion import build_companion
    # The By-SKU tab's LM-1 / LY LM blocks read the prior periods' own
    # contracts at SKU grain (2026-08-13). These are the same files already
    # resolved above for the LM/LY headline chain, so this adds no new source
    # and no re-derivation -- and it guarantees the comparison is against what
    # was published for those months, on the same net basis.
    def _load_if_present(path):
        if not path or not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    build_companion(companion_path, period_label, contract, [(period_label, contract)],
                    lm_contract=_load_if_present(prior_month_contract),
                    ly_contract=_load_if_present(ly_month_contract))

    reconciled = contract["provenance"]["reconciled"]
    print(f"contract:  {contract_path}")
    print(f"dashboard: {out_path}")
    print(f"companion: {companion_path}")
    print(f"reconciled (structural leak check): {reconciled}")
    if not reconciled:
        print("WARNING: not reconciled -- do not publish this build.", file=sys.stderr)
    return out_path, contract_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python trading/build_matrixify_dashboard.py <period> "
              "[--lm-contract path] [--ly-contract path] [--oracle-bootstrap] [--force]\n"
              "  <period>: \"June 2026\", \"2026-06\"\n"
              "  --force: allow overwriting an existing committed contract/output "
              "(refused by default -- see contract.py's write_committed_file)",
              file=sys.stderr)
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
        elif args[i] == "--oracle-bootstrap":
            kwargs["force_oracle_bootstrap"] = True
            i += 1
        elif args[i] == "--force":
            kwargs["force"] = True
            i += 1
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)
    build(period_arg, **kwargs)
