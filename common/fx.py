"""Frozen, dated GBP/USD FX table -- fetch-and-freeze, ONE RATE PER MONTH.

fx_rates.csv (repo root) is the source of truth: columns date, gbp_usd,
source, one row per report month keyed on that month's 1st (e.g.
"2026-07-01"). gbp_usd is USD per GBP 1 -- matches TRADING_logic_spec.md's
`AA` column (GBP/USD), used as a divisor: gbp_amount = usd_amount / AA.

Confirmed 2026-08-03: the live sheet pins every US order's date to the 1st
of the report month before the GOOGLEFINANCE lookup, so it resolves to ONE
GBP/USD rate for the whole month, not one per order date. This is a
deliberate BUG-FOR-BUG match of that artifact -- do not "fix" it to a daily
rate; that would stop reproducing the sheet's actual numbers. UK/non-US
lines never touch this table -- they use 1.0. Order bucketing/counting is
unaffected -- only the FX lookup is pinned to the month.

A row is IMMUTABLE once its source is real (anything other than
PLACEHOLDER) -- past rates are never silently rewritten. On each run, only
the month actually needed is fetched if it's still PLACEHOLDER (default
source: Frankfurter, ECB-backed -- matching GOOGLEFINANCE's "latest quote in
the 7 days up to the date" behaviour, since Frankfurter also returns the
nearest earlier trading day's rate for a non-trading-day request). Some
months (e.g. July 2026) are instead seeded directly from a value read off
the live sheet -- see seed_confirmed() -- rather than fetched, when that's
the more authoritative source.

This is the one deliberate deviation from the live sheet's live
GOOGLEFINANCE (non-reproducible) -- see ROADMAP.md §4.
"""
import csv
import os
import sys

import requests

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "..", "fx_rates.csv")
DEFAULT_SOURCE_URL = "https://api.frankfurter.app/{date}?from=GBP&to=USD"
DEFAULT_SOURCE_LABEL = "frankfurter.app (ECB reference rate)"
PLACEHOLDER = "PLACEHOLDER"


def load(path=DEFAULT_PATH):
    """{date_str: (rate, source)}, date_str is ISO (YYYY-MM-DD), always the
    1st of a report month.
    """
    rows = {}
    if os.path.exists(path):
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                rows[row["date"]] = (float(row["gbp_usd"]), row["source"])
    return rows


def save(rows, path=DEFAULT_PATH):
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "gbp_usd", "source"])
        for date in sorted(rows):
            rate, source = rows[date]
            writer.writerow([date, f"{rate:.4f}", source])


class FxUnavailable(RuntimeError):
    """The month's rate isn't in the frozen table and couldn't be fetched.

    Raised instead of letting requests' own HTTPError/ConnectionError escape.
    Added 2026-08-14: inside a Claude session api.frankfurter.app is not in
    the network allowlist, so this is the FIRST thing a colleague hits when
    they ask for a month whose rate was never seeded -- and what they saw was
    a bare `403 Client Error: Forbidden` traceback with no indication that a
    person simply needs to add one row to a CSV. The build must still stop:
    a guessed rate would distort every US line, and the country
    reconciliation gate CANNOT catch it, because the total and the
    uk+us+row split both derive from the same rows and would tie either way.
    """


def _fetch_rate(date_str, source_url):
    try:
        resp = requests.get(source_url.format(date=date_str), timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        raise FxUnavailable(
            f"No GBP/USD rate for {date_str} in fx_rates.csv, and it could not be "
            f"fetched ({type(exc).__name__}: {exc}).\n"
            f"\n"
            f"This period cannot be built until that rate is added -- guessing one "
            f"would silently distort every US order line, and the UK+US+ROW "
            f"reconciliation check would still pass, so nothing downstream would "
            f"catch it.\n"
            f"\n"
            f"To fix, from a machine with normal internet access:\n"
            f"    python -c \"from common.fx import ensure_month; ensure_month('{date_str[:7]}')\"\n"
            f"then commit fx_rates.csv. Or read the month's rate off the live trading "
            f"sheet's AA column and seed it directly:\n"
            f"    python -c \"from common.fx import seed_confirmed; "
            f"seed_confirmed('{date_str[:7]}', <rate>, 'googlefinance (sheet AA, month rate)')\"\n"
            f"\n"
            f"One row per report month; it only needs doing once per month, and it is "
            f"the only step in a monthly build that a Claude session cannot do for itself."
        ) from exc
    data = resp.json()
    rate = data["rates"]["USD"]
    actual_date = data.get("date", date_str)
    return rate, actual_date


def ensure_month(month_str, path=DEFAULT_PATH, source_url=DEFAULT_SOURCE_URL,
                  source_label=DEFAULT_SOURCE_LABEL):
    """Fetch-and-freeze the single FX rate for a report month (e.g. "2026-05"),
    keyed at the month's 1st. Only fetches if that row is missing or still
    PLACEHOLDER; never touches an already-real row. Persists the table if
    anything changed. Returns the updated {date_str: (rate, source)} table.
    """
    date_str = f"{month_str}-01"
    rows = load(path)
    existing = rows.get(date_str)
    if existing is not None and existing[1] != PLACEHOLDER:
        return rows  # immutable -- already real

    fetched_rate, actual_date = _fetch_rate(date_str, source_url)
    label = source_label if actual_date == date_str else f"{source_label}, nearest available: {actual_date}"
    rows[date_str] = (fetched_rate, label)
    save(rows, path)
    print(f"fx: fetched monthly rate {date_str} = {fetched_rate} ({label})", file=sys.stderr)
    return rows


def seed_confirmed(month_str, rate, source_label, path=DEFAULT_PATH):
    """Directly seed a report month's rate from a value read off the live
    sheet (more authoritative than an independent fetch for a month we've
    actually checked). Refuses to silently overwrite a different existing
    real value -- past rates are immutable; re-seeding the SAME value/label
    is a no-op.
    """
    date_str = f"{month_str}-01"
    rows = load(path)
    existing = rows.get(date_str)
    if existing is not None and existing[1] != PLACEHOLDER:
        if existing == (rate, source_label):
            return rows
        raise ValueError(
            f"{date_str} already has a real rate {existing} -- refusing to "
            f"overwrite with ({rate}, {source_label}); past rates are immutable"
        )
    rows[date_str] = (rate, source_label)
    save(rows, path)
    print(f"fx: seeded confirmed monthly rate {date_str} = {rate} ({source_label})", file=sys.stderr)
    return rows


def lookup_month(month_str, rows):
    """The single GBP/USD rate for a report month. Raises if missing or
    still PLACEHOLDER (ensure_month/seed_confirmed should have been called
    first).
    """
    date_str = f"{month_str}-01"
    if date_str not in rows:
        raise KeyError(f"No FX rate for {date_str} -- call ensure_month() or seed_confirmed() first")
    rate, source = rows[date_str]
    if source == PLACEHOLDER:
        raise ValueError(f"FX rate for {date_str} is still PLACEHOLDER")
    return rate
