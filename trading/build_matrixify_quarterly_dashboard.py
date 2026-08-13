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

Period-from-prompt (2026-08-12): accepts a single quarter string ("Q2
2026") -- common/period.py derives its 3 constituent months
(months_in_quarter), and each month's own lm/ly bootstraps from fresh
Matrixify pulls (requested_period_model, connector-first, same preference
order as build_matrixify_dashboard.py) rather than defaulting to that
month's oracle fixture. The 3-separate-YYYY-MM-args CLI form still works
unchanged, for anyone scripting around specific months directly.

Run:
  python trading/build_matrixify_quarterly_dashboard.py "Q2 2026"
  python trading/build_matrixify_quarterly_dashboard.py 2026-04 2026-05 2026-06
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

from contract import render_contract, write_committed_file
from quarterly import emit_contract_from_matrixify_quarter
from common.period import parse_period, months_in_quarter, month_period_string
from common.sources import matrixify_orders_snapshot, matrixify_orders_snapshot_covers
from build_matrixify_dashboard import _period_model_for

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


def build(periods, lq_contract=None, out_suffix="_matrixify", force_oracle_bootstrap=False, as_of=None,
          force=False, contract_out_path=None, html_out_path=None):
    if len(periods) != 3:
        raise ValueError(f"emit_contract_from_matrixify_quarter needs exactly 3 consecutive months, got {periods}")
    # 2026-08-12 (PII incident follow-up, docs/2026-08-12_matrixify_sheet_bridge.md):
    # ONE rolling ~400-day snapshot per store now, not a file per period --
    # same fix as build_matrixify_dashboard.py's build(). Each constituent
    # month still gets its own fail-loud coverage check below (a rolling
    # file existing doesn't prove it covers a given month).
    uk_csv, us_csv = matrixify_orders_snapshot("uk"), matrixify_orders_snapshot("us")
    for p in periods:
        if not (matrixify_orders_snapshot_covers(uk_csv, p) or matrixify_orders_snapshot_covers(us_csv, p)):
            raise FileNotFoundError(
                f"build_matrixify_quarterly_dashboard: {p} isn't inside the rolling Matrixify "
                f"snapshot's window ({uk_csv} / {us_csv}). This month has never been built and "
                f"falls outside the ~400-day rolling pull -- needs a one-off historical Matrixify "
                f"export first (see docs/2026-08-12_matrixify_sheet_bridge.md 'backfill' note)."
            )
    month_specs = [(p, uk_csv, us_csv) for p in periods]
    oracle_bootstrap_path = None
    fixture = _QUARTER_ORACLE_FIXTURES.get(tuple(periods))
    if fixture:
        oracle_bootstrap_path = os.path.join(ORACLE_FIXTURE_DIR, fixture)
    ly_month_contracts = []
    for p in periods:
        ly_fixture = _LY_MONTH_CONTRACTS.get(p)
        candidate = os.path.join(CONTRACTS_DIR, ly_fixture) if ly_fixture else None
        ly_month_contracts.append(candidate if candidate and os.path.exists(candidate) else None)

    # Period-from-prompt (2026-08-12): per-month connector-first bootstrap,
    # same preference order as build_matrixify_dashboard.py -- fresh
    # Matrixify LM/LY pulls beat that month's own oracle fixture, unless
    # --oracle-bootstrap was explicitly requested or the windows aren't
    # landed yet.
    month_oracle_bootstrap_paths = []
    month_requested_period_models = []
    for p in periods:
        _, month_pm = _period_model_for(p, as_of=as_of)
        lm_key, ly_key = month_pm["lm"]["key"], month_pm["ly"]["key"]
        matrixify_bootstrap_available = (
            (matrixify_orders_snapshot_covers(uk_csv, lm_key) or matrixify_orders_snapshot_covers(us_csv, lm_key))
            and (matrixify_orders_snapshot_covers(uk_csv, ly_key) or matrixify_orders_snapshot_covers(us_csv, ly_key))
        )
        mo_fixture = _MONTH_ORACLE_FIXTURES.get(p)
        mo_candidate = os.path.join(ORACLE_FIXTURE_DIR, mo_fixture) if mo_fixture else None
        mo_candidate = mo_candidate if mo_candidate and os.path.exists(mo_candidate) else None

        if force_oracle_bootstrap:
            month_oracle_bootstrap_paths.append(mo_candidate)
            month_requested_period_models.append(None)
        elif matrixify_bootstrap_available:
            month_oracle_bootstrap_paths.append(None)
            month_requested_period_models.append(month_pm)
        else:
            month_oracle_bootstrap_paths.append(mo_candidate)
            month_requested_period_models.append(None)
            if mo_candidate is None:
                print(f"build_matrixify_quarterly_dashboard: {p}'s LM/LY Matrixify exports "
                      f"aren't both landed and no oracle fixture exists -- that month's lm/ly "
                      f"will be zero.", file=sys.stderr)

    contract = emit_contract_from_matrixify_quarter(
        month_specs, lq_contract=lq_contract, oracle_bootstrap_path=oracle_bootstrap_path,
        ly_month_contracts=ly_month_contracts,
        month_oracle_bootstrap_paths=month_oracle_bootstrap_paths,
        month_requested_period_models=month_requested_period_models,
    )

    q_label = contract["period_model"]["cm"]["label"]  # e.g. "Q2 2026"
    contract_path = contract_out_path or os.path.join(CONTRACTS_DIR, f"{q_label.replace(' ', '-')}-matrixify.json")
    os.makedirs(os.path.dirname(contract_path), exist_ok=True)
    write_committed_file(json.dumps(contract, indent=2, default=str), contract_path,
                          force=force, label="contract")

    with open(TEMPLATE, encoding="utf-8") as f:
        template_html = f.read()
    html = render_contract(contract, template_html)

    label = q_label.replace(" ", "_")
    out_path = html_out_path or os.path.join(OUTPUT_DIR, f"{label}_dashboard{out_suffix}.html")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    write_committed_file(html, out_path, force=force, label="dashboard output")

    # Values-only Excel companion, automatic alongside the HTML (2026-08-13).
    # Constituent months are read from their own ALREADY-COMMITTED contracts
    # (ROADMAP.md §5's contract-chaining rule -- a quarter is only built once
    # its months are, so these should always exist; fails loud if not, rather
    # than silently reconstructing a month that hasn't actually been published).
    month_contracts = []
    month_names = {4: "Apr", 5: "May", 6: "Jun", 7: "Jul", 8: "Aug", 9: "Sep",
                   10: "Oct", 11: "Nov", 12: "Dec", 1: "Jan", 2: "Feb", 3: "Mar"}
    for p in periods:
        mpath = os.path.join(CONTRACTS_DIR, f"{p}-matrixify.json")
        mcontract = json.load(open(mpath))
        mlabel = f"{month_names[int(p.split('-')[1])]} {p.split('-')[0]}"
        month_contracts.append((mlabel, mcontract))
    companion_path = os.path.join(os.path.dirname(out_path), f"{label}_companion{out_suffix}.xlsx")
    if os.path.exists(companion_path) and not force:
        raise FileExistsError(
            f"refusing to overwrite existing committed companion Excel at {companion_path} -- "
            f"pass force=True (CLI: --force) to intentionally overwrite."
        )
    from excel_companion import build_companion
    build_companion(companion_path, q_label, contract, month_contracts)

    reconciled = contract["provenance"]["reconciled"]
    print(f"contract:  {contract_path}")
    print(f"dashboard: {out_path}")
    print(f"companion: {companion_path}")
    print(f"reconciled (structural leak check): {reconciled}")
    print(f"lq_ly_source: {contract['provenance']['lq_ly_source']}")
    if not reconciled:
        print("WARNING: not reconciled -- do not publish this build.", file=sys.stderr)
    return out_path, contract_path


def _quarter_string_to_periods(quarter_arg, as_of=None):
    """"Q2 2026" -> ["2026-04", "2026-05", "2026-06"] (YYYY-MM strings, the
    shape build()/month_specs already expects) -- fails loud via
    parse_period on an unparseable/future quarter or a non-quarter string.
    """
    pm = parse_period(quarter_arg, as_of=as_of)
    if pm["cm"]["kind"] != "quarter":
        raise ValueError(f"_quarter_string_to_periods: {quarter_arg!r} is not a quarter "
                          f"(\"Q2 2026\") -- got a month period instead")
    quarter_num, year = int(pm["cm"]["label"].split()[0][1:]), int(pm["cm"]["label"].split()[1])
    return [parse_period(m, as_of=as_of)["cm"]["key"] for m in months_in_quarter(quarter_num, year)]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python trading/build_matrixify_quarterly_dashboard.py <quarter> "
              "[--lq-contract path] [--oracle-bootstrap] [--force]\n"
              "       python trading/build_matrixify_quarterly_dashboard.py "
              "<YYYY-MM> <YYYY-MM> <YYYY-MM> [--lq-contract path] [--oracle-bootstrap] [--force]\n"
              "  <quarter>: \"Q2 2026\"\n"
              "  --force: allow overwriting an existing committed contract/output "
              "(refused by default -- see contract.py's write_committed_file)",
              file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) >= 4 and all(re.match(r"^\d{4}-\d{2}$", a) for a in sys.argv[1:4]):
        periods_arg = sys.argv[1:4]
        rest = sys.argv[4:]
    else:
        periods_arg = _quarter_string_to_periods(sys.argv[1])
        rest = sys.argv[2:]

    kwargs = {}
    i = 0
    while i < len(rest):
        if rest[i] == "--lq-contract":
            kwargs["lq_contract"] = rest[i + 1]
            i += 2
        elif rest[i] == "--oracle-bootstrap":
            kwargs["force_oracle_bootstrap"] = True
            i += 1
        elif rest[i] == "--force":
            kwargs["force"] = True
            i += 1
        else:
            print(f"Unknown argument: {rest[i]}", file=sys.stderr)
            sys.exit(1)
    build(periods_arg, **kwargs)
