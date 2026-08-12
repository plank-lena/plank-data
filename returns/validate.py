"""Period-coverage + freshness guardrails for the ReturnZap connection
(period-from-prompt, 2026-08-12) -- all computed from the data itself,
never against a prior report (that's the reconciliation gate's job, in
common/reconciliation_gate.py, not this module). Fail loud, naming the
exact source/gap, per CLAUDE.md's "never hand-patch" rule.

ReturnZap is full-history (unlike Matrixify, which is filtered at the
EXPORT/fetch step for the requested period -- see common/sources.py's
matrixify_orders_snapshot) -- the builder filters to the requested period
here, by return-month, via slice_by_return_month(). Yotpo reviews are
NOT period-filtered at all (region filter only, per the returns brief) --
see reviews/review_feedback.py; nothing in this module applies to Yotpo.

common/reconciliation_gate.assert_returns_overlap_sales is unchanged and
still the join-level check (does the sliced returns cohort actually
overlap the paired sales cohort) -- called inside returns/build.py's
prep(), not duplicated here. This module's checks run BEFORE that, on the
raw sheet alone, so a coverage/staleness problem is named before the
pipeline gets far enough to produce a possibly-wrong number.

Each assert_* here also accepts an already-loaded `df` (the deduped
ReturnZap dataframe) to avoid re-reading/re-deduping the full sheet once
per check -- validate_period() loads it exactly once and threads it
through; call the assert_* functions individually with just csv_path if
you only need one check.
"""
import sys
from datetime import date

import pandas as pd

from common.sources import RETURNS_ZAP_SNAPSHOT, load_returns_zap_snapshot

FRESHNESS_DAYS = 14  # warn if the sheet's newest return is older than this, relative to as_of
MIN_PERIOD_ROWS = 5  # floor below which a "quiet period" reads as a pull gap, not reality


def _load_deduped(csv_path=None):
    return load_returns_zap_snapshot(csv_path or RETURNS_ZAP_SNAPSHOT)


def _resolve_df(df, csv_path):
    return df if df is not None else _load_deduped(csv_path)[0]


def _return_dates(df):
    return pd.to_datetime(df["Return Date"], utc=True, errors="coerce").dt.date


def slice_by_return_month(df, period_model):
    """Filter an already-deduped returns dataframe to rows whose Return
    Date falls inside the requested period's [start, end] window -- the
    "builder filters to the period" mechanism Part 3 describes.
    """
    cm = period_model["cm"]
    dates = _return_dates(df)
    return df[(dates >= cm["start"]) & (dates <= cm["end"])]


def assert_period_coverage(period_model, csv_path=None, df=None):
    """The sheet's min/max Return Date must bracket the requested period.
    If the window falls partly or wholly outside the covered range, abort
    naming the gap -- e.g. "returns sheet covers 2025-11 to 2026-07;
    requested Q2 2026 is only partially covered."
    """
    df = _resolve_df(df, csv_path)
    dates = _return_dates(df).dropna()
    covered_min, covered_max = dates.min(), dates.max()
    cm = period_model["cm"]
    assert covered_min <= cm["start"] and cm["end"] <= covered_max, (
        f"PERIOD COVERAGE FAILED: returns sheet covers {covered_min} to {covered_max}; "
        f"requested {cm['label']} ({cm['start']} to {cm['end']}) is "
        f"{'not covered at all' if cm['start'] > covered_max or cm['end'] < covered_min else 'only partially covered'} "
        f"-- refusing to build from a partial slice."
    )
    return covered_min, covered_max


def assert_period_non_empty(period_model, csv_path=None, min_rows=MIN_PERIOD_ROWS, df=None):
    """Zero (or near-zero) returns in a full requested period is almost
    certainly a pull gap, not reality.
    """
    df = _resolve_df(df, csv_path)
    sliced = slice_by_return_month(df, period_model)
    assert len(sliced) >= min_rows, (
        f"PERIOD NON-EMPTY CHECK FAILED: only {len(sliced)} return row(s) found for "
        f"{period_model['cm']['label']} -- almost certainly a pull gap, not a real quiet period."
    )
    return len(sliced)


def assert_period_both_markets(period_model, csv_path=None, df=None):
    """A missing ReturnZap store key must not silently drop a market --
    country is the reconciliation key. Checked on the PERIOD SLICE, not
    the whole-sheet check common/sources.preflight_returns_zap already
    does -- a market can be present overall but still missing for this
    specific requested window (exactly the 2025-03-2026-08 UK outage found
    2026-08-11/12: whole-sheet UK rows existed, but every recent period
    would have failed this check until the Apps Script was fixed).
    """
    df = _resolve_df(df, csv_path)
    sliced = slice_by_return_month(df, period_model)
    counts = sliced["Country"].value_counts()
    us_rows = int(counts.get("US", 0))
    uk_rows = int(counts.get("GB", 0) + counts.get("UK", 0))
    assert us_rows > 0, (
        f"PERIOD BOTH-MARKETS CHECK FAILED: zero US returns in {period_model['cm']['label']} "
        f"-- a missing store key must not silently drop a market."
    )
    assert uk_rows > 0, (
        f"PERIOD BOTH-MARKETS CHECK FAILED: zero UK returns in {period_model['cm']['label']} "
        f"-- a missing store key must not silently drop a market."
    )
    return us_rows, uk_rows


def check_freshness(as_of=None, csv_path=None, max_staleness_days=FRESHNESS_DAYS, df=None):
    """WARNS (does not abort -- a stale pull can still be legitimately
    used to build an OLDER period) if the sheet's newest Return Date is
    more than max_staleness_days behind as_of. Returns (newest_date,
    staleness_days) either way, so a caller building a RECENT period can
    decide to treat staleness as fatal for that specific case.
    """
    if as_of is None:
        as_of = date.today()
    df = _resolve_df(df, csv_path)
    newest = _return_dates(df).dropna().max()
    staleness = (as_of - newest).days
    if staleness > max_staleness_days:
        print(f"validate: WARNING -- returns sheet's newest Return Date is {newest} "
              f"({staleness} days before {as_of}) -- the getReturns Apps Script pull may not "
              f"have run recently; refresh the snapshot before trusting a build for a recent "
              f"period.", file=sys.stderr)
    return newest, staleness


def validate_period(period_model, csv_path=None, as_of=None):
    """Run every period-scoped guardrail for a requested build, loading
    the deduped sheet exactly once. Raises AssertionError naming the exact
    failure; returns a summary dict on success.
    """
    df, n_dropped = _load_deduped(csv_path)
    covered_min, covered_max = assert_period_coverage(period_model, df=df)
    n_rows = assert_period_non_empty(period_model, df=df)
    us_rows, uk_rows = assert_period_both_markets(period_model, df=df)
    newest, staleness = check_freshness(as_of, df=df)
    print(f"validate_period: {period_model['cm']['label']} -- {n_rows} return row(s) "
          f"(US={us_rows}, UK={uk_rows}); sheet covers {covered_min} to {covered_max}; "
          f"newest return {newest} ({staleness}d before as_of)")
    return {
        "period": period_model["cm"]["label"], "rows": n_rows,
        "us_rows": us_rows, "uk_rows": uk_rows,
        "sheet_covered_min": covered_min, "sheet_covered_max": covered_max,
        "newest_return_date": newest, "staleness_days": staleness,
    }
