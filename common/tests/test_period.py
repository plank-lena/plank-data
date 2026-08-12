"""Tests for common/period.py (period-from-prompt, 2026-08-12).

Proves: "Q2 2026" and "June 2026" resolve to the correct CM/LM/LY windows;
an out-of-coverage returns period and a future period both fail loud,
naming the exact problem, rather than silently building an empty/
understated dashboard.

Run:  python common/tests/test_period.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.period import parse_period, months_in_quarter, month_period_string, quarter_period_string

AS_OF = date(2026, 8, 12)


def check_quarter_resolves_correctly():
    """"Q2 2026" -> cm=Q2 2026 (Apr 1 - Jun 30), lm=Q1 2026 (Jan 1 - Mar 31),
    ly=Q2 2025 (Apr 1 - Jun 30 2025) -- exact dates, not just labels.
    """
    pm = parse_period("Q2 2026", as_of=AS_OF)
    checks = [
        (pm["cm"]["label"] == "Q2 2026", f"cm label: {pm['cm']['label']}"),
        (pm["cm"]["start"] == date(2026, 4, 1) and pm["cm"]["end"] == date(2026, 6, 30),
         f"cm window: {pm['cm']['start']} - {pm['cm']['end']}"),
        (pm["cm"]["key"] == "2026-Q2", f"cm key: {pm['cm']['key']}"),
        (pm["lm"]["label"] == "Q1 2026", f"lm label: {pm['lm']['label']}"),
        (pm["lm"]["start"] == date(2026, 1, 1) and pm["lm"]["end"] == date(2026, 3, 31),
         f"lm window: {pm['lm']['start']} - {pm['lm']['end']}"),
        (pm["ly"]["label"] == "Q2 2025", f"ly label: {pm['ly']['label']}"),
        (pm["ly"]["start"] == date(2025, 4, 1) and pm["ly"]["end"] == date(2025, 6, 30),
         f"ly window: {pm['ly']['start']} - {pm['ly']['end']}"),
    ]
    print("\n=== 'Q2 2026' resolves to the right CM/LM/LY windows ===")
    ok = True
    for passed, detail in checks:
        print(f"  {'PASS' if passed else 'FAIL'}: {detail}")
        ok = ok and passed
    return ok


def check_month_resolves_correctly():
    """"June 2026" -> cm=Jun 2026, lm=May 2026, ly=Jun 2025."""
    pm = parse_period("June 2026", as_of=AS_OF)
    checks = [
        (pm["cm"]["key"] == "2026-06", f"cm key: {pm['cm']['key']}"),
        (pm["lm"]["key"] == "2026-05", f"lm key: {pm['lm']['key']}"),
        (pm["ly"]["key"] == "2025-06", f"ly key: {pm['ly']['key']}"),
        (pm["cm"]["start"] == date(2026, 6, 1) and pm["cm"]["end"] == date(2026, 6, 30),
         f"cm window: {pm['cm']['start']} - {pm['cm']['end']}"),
    ]
    # January boundary: LM must roll back a year, not go to month 0.
    jan = parse_period("January 2026", as_of=date(2026, 12, 31))
    checks.append((jan["lm"]["key"] == "2025-12", f"Jan LM year-rollback: {jan['lm']['key']}"))

    print("\n=== 'June 2026' resolves to the right CM/LM/LY windows ===")
    ok = True
    for passed, detail in checks:
        print(f"  {'PASS' if passed else 'FAIL'}: {detail}")
        ok = ok and passed
    return ok


def check_months_in_quarter_matches_matrixify_fetch_convention():
    """months_in_quarter is what a quarterly Matrixify fetch/CLI loops over
    to land 3 monthly exports -- confirm it produces exactly the 3
    consecutive month strings a quarter needs, in order.
    """
    months = months_in_quarter(2, 2026)
    expected = ["April 2026", "May 2026", "June 2026"]
    ok = months == expected
    print("\n=== months_in_quarter(2, 2026) matches the Matrixify fetch convention ===")
    print(f"  {'PASS' if ok else 'FAIL'}: got {months}, expected {expected}")
    return ok


def check_future_period_fails_loud():
    """A period that hasn't started yet must raise, naming the exact date
    problem -- never silently produce an empty/zeroed report.
    """
    print("\n=== A future period fails loud (common/period.py) ===")
    try:
        parse_period("Q4 2027", as_of=AS_OF)
        print("  FAIL: no error raised for a period 14 months in the future")
        return False
    except ValueError as e:
        ok = "hasn't started" in str(e) and "Q4 2027" in str(e)
        print(f"  {'PASS' if ok else 'FAIL'}: {e}")
        return ok


def check_unparseable_period_fails_loud():
    print("\n=== An unparseable period fails loud ===")
    ok = True
    for bad in ("not a period", "Q5 2026", "Jan"):
        try:
            parse_period(bad, as_of=AS_OF)
            print(f"  FAIL: {bad!r} did not raise")
            ok = False
        except ValueError as e:
            print(f"  PASS: {bad!r} -> {e}")
    return ok


def check_out_of_coverage_returns_period_fails_loud():
    """An out-of-coverage RETURNS period must abort in returns/validate.py
    naming the sheet's actual coverage window -- requires the live
    ReturnZap snapshot (source/returns_zap.csv, gitignored per ROADMAP.md);
    skips (not fails) if it isn't present locally.
    """
    from common.sources import RETURNS_ZAP_SNAPSHOT

    print("\n=== An out-of-coverage returns period fails loud (returns/validate.py) ===")
    if not os.path.exists(RETURNS_ZAP_SNAPSHOT):
        print(f"  SKIP: {RETURNS_ZAP_SNAPSHOT} not present locally -- maintainer-local check")
        return True

    from returns.validate import validate_period

    pm = parse_period("Q1 2020", as_of=AS_OF)  # long before any real ReturnZap history
    try:
        validate_period(pm, as_of=AS_OF)
        print("  FAIL: no error raised for a period the sheet can't possibly cover")
        return False
    except AssertionError as e:
        ok = "PERIOD COVERAGE FAILED" in str(e) and "not covered at all" in str(e)
        print(f"  {'PASS' if ok else 'FAIL'}: {e}")
        return ok


def main():
    checks = {
        "quarter_resolves_correctly": check_quarter_resolves_correctly(),
        "month_resolves_correctly": check_month_resolves_correctly(),
        "months_in_quarter_matches_fetch_convention": check_months_in_quarter_matches_matrixify_fetch_convention(),
        "future_period_fails_loud": check_future_period_fails_loud(),
        "unparseable_period_fails_loud": check_unparseable_period_fails_loud(),
        "out_of_coverage_returns_period_fails_loud": check_out_of_coverage_returns_period_fails_loud(),
    }

    print("\n=== Summary ===")
    for name, ok in checks.items():
        print(f"  {name:50s} {'PASS' if ok else 'FAIL'}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
