"""Build a real Matrixify-sourced quarterly trading dashboard, end to end.

Same gap as build_matrixify_dashboard.py (T0), one level up:
emit_contract_from_matrixify_quarter() and render_contract() were each
proven in test_quarterly.py, but nothing chained them into a written
output file for the real Matrixify quarterly path.

Worth knowing before trusting a quarter built with this script (T0
finding, not silently patched): emit_contract_from_matrixify_quarter()
calls emit_contract_from_matrixify() per month, so each month's own LM/LY
sourcing is decided per-month by this script (same connector-first
preference as the standalone monthly build), and the quarter's own LQ
(previous quarter) stays unavailable until a prior quarterly contract
exists to chain from (lq_contract) -- see quarterly.py's own module
docstring for the full accounting rule.

Period-from-prompt (2026-08-12): accepts a single quarter string ("Q2
2026") -- common/period.py derives its 3 constituent months
(months_in_quarter), and each month's own lm/ly bootstraps from fresh
Matrixify pulls (requested_period_model) with no workbook at all. The
3-separate-YYYY-MM-args CLI form still works unchanged, for anyone
scripting around specific months directly.

No oracle-fixture fallback exists anymore (Matrixify Slice Architecture
brief, 2026-08-13, §6) -- if any constituent month has no committed
contract to chain LM/LY from AND isn't fully covered in the manifest, this
raises naming exactly which month/window is missing, rather than silently
falling back to a hand-built workbook or zeroing that month's LM/LY.

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
from common.sources import (
    matrixify_orders_snapshot, matrixify_orders_snapshot_covers,
    assert_orders_coverage, load_orders_manifest,
)
from build_matrixify_dashboard import _period_model_for, _period_fully_covered, _resolve_ly_month_contract

TEMPLATE = os.path.join(_DASHBOARD_DIR, "template", "dashboard.template.html")
SOURCE_DIR = os.path.join(_HERE, "source")
CONTRACTS_DIR = os.path.join(_HERE, "contracts")
OUTPUT_DIR = os.path.join(_DASHBOARD_DIR, "output")


def build(periods, lq_contract=None, out_suffix="_matrixify", as_of=None,
          force=False, force_settled=False, contract_out_path=None, html_out_path=None):
    if len(periods) != 3:
        raise ValueError(f"emit_contract_from_matrixify_quarter needs exactly 3 consecutive months, got {periods}")

    # Full-period, AND-across-stores coverage per constituent month (brief
    # §5) -- each month needs its own CM window covered; the old check was
    # OR'd across stores and only proved "at least one row" existed.
    for p in periods:
        assert_orders_coverage(p)

    manifest = load_orders_manifest()
    month_specs = [
        (p, matrixify_orders_snapshot("uk", p), matrixify_orders_snapshot("us", p))
        for p in periods
    ]

    ly_month_contracts = [_resolve_ly_month_contract(p) for p in periods]

    # Per-month connector-first LM/LY bootstrap, same preference as the
    # standalone monthly build -- fresh Matrixify pulls when both windows
    # are fully covered; no fallback left if not (§6: the oracle-fixture
    # escape hatch is retired, not fixed).
    month_requested_period_models = []
    month_source_slices = []
    for p in periods:
        _, month_pm = _period_model_for(p, as_of=as_of)
        lm_key, ly_key = month_pm["lm"]["key"], month_pm["ly"]["key"]

        this_month_slices = [
            {"store": s, "period": p, "sha256": manifest[(s, p)]["sha256"]}
            for s in ("uk", "us")
        ]

        if _period_fully_covered(lm_key, manifest) and _period_fully_covered(ly_key, manifest):
            month_requested_period_models.append(month_pm)
            for slot_key in (lm_key, ly_key):
                for s in ("uk", "us"):
                    this_month_slices.append(
                        {"store": s, "period": slot_key, "sha256": manifest[(s, slot_key)]["sha256"]}
                    )
        else:
            raise FileNotFoundError(
                f"build_matrixify_quarterly_dashboard: {p}'s LM ({lm_key}) / LY ({ly_key}) windows "
                f"aren't both fully covered in the manifest, and there is no oracle-fixture "
                f"fallback left (retired, brief §6). Land the missing slice(s) "
                f"(trading/tools/backfill_slice.py) before building this quarter."
            )
        month_source_slices.append(this_month_slices)

    contract = emit_contract_from_matrixify_quarter(
        month_specs, lq_contract=lq_contract,
        ly_month_contracts=ly_month_contracts,
        month_requested_period_models=month_requested_period_models,
        month_source_slices=month_source_slices,
    )

    q_label = contract["period_model"]["cm"]["label"]  # e.g. "Q2 2026"
    contract_path = contract_out_path or os.path.join(CONTRACTS_DIR, f"{q_label.replace(' ', '-')}-matrixify.json")
    os.makedirs(os.path.dirname(contract_path), exist_ok=True)
    write_committed_file(json.dumps(contract, indent=2, default=str), contract_path,
                          force=force, force_settled=force_settled, label="contract")

    with open(TEMPLATE, encoding="utf-8") as f:
        template_html = f.read()
    html = render_contract(contract, template_html)

    label = q_label.replace(" ", "_")
    out_path = html_out_path or os.path.join(OUTPUT_DIR, f"{label}_dashboard{out_suffix}.html")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    write_committed_file(html, out_path, force=force, force_settled=force_settled, label="dashboard output")

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
    from trading.excel_companion import build_companion
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
              "[--lq-contract path] [--force] [--force-settled]\n"
              "       python trading/build_matrixify_quarterly_dashboard.py "
              "<YYYY-MM> <YYYY-MM> <YYYY-MM> [--lq-contract path] [--force] [--force-settled]\n"
              "  <quarter>: \"Q2 2026\"\n"
              "  --force: allow overwriting an existing committed contract/output "
              "(refused by default -- see contract.py's write_committed_file)\n"
              "  --force-settled: also required once the existing contract has passed its own "
              "settled_at -- plain --force alone is no longer enough past that point",
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
        elif rest[i] == "--force":
            kwargs["force"] = True
            i += 1
        elif rest[i] == "--force-settled":
            kwargs["force_settled"] = True
            i += 1
        else:
            print(f"Unknown argument: {rest[i]}", file=sys.stderr)
            sys.exit(1)
    build(periods_arg, **kwargs)
