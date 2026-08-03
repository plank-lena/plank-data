"""Frozen, dated GBP/USD FX table -- fetch-and-freeze.

fx_rates.csv (repo root) is the source of truth: columns date, gbp_usd,
source. gbp_usd is USD per GBP 1 -- matches TRADING_logic_spec.md's `AA`
column (GBP/USD), used as a divisor: gbp_amount = usd_amount / AA.

A row is IMMUTABLE once its source is real (anything other than
PLACEHOLDER) -- past rates are never silently rewritten. On each run, only
dates actually needed by the build that are still missing or PLACEHOLDER get
fetched from a daily reference series (default: Frankfurter, ECB-backed) and
written back with a real source label. UK/non-US lines never touch this
table -- they use 1.0.

This is the one deliberate deviation from the live sheet (which uses live
GOOGLEFINANCE and is therefore not reproducible) -- see ROADMAP.md §4.
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
    """{date_str: (rate, source)}, date_str is ISO (YYYY-MM-DD)."""
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


def _fetch_rate(date_str, source_url):
    resp = requests.get(source_url.format(date=date_str), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    rate = data["rates"]["USD"]
    actual_date = data.get("date", date_str)
    return rate, actual_date


def _nearest_on_or_before(date_str, table_dates):
    candidates = [d for d in table_dates if d <= date_str]
    if not candidates:
        raise KeyError(
            f"fx_rates.csv has no date on or before {date_str} -- add earlier "
            "business-day rows to the table before running the build for this period"
        )
    return max(candidates)


def ensure_dates(order_dates, path=DEFAULT_PATH, source_url=DEFAULT_SOURCE_URL,
                  source_label=DEFAULT_SOURCE_LABEL):
    """Fetch-and-freeze: for every table date actually needed to look up the
    given US order dates (nearest business day on or before each), fetch a
    real rate if it's missing or still PLACEHOLDER. Never touches a row that
    already has a real source. Persists the table if anything changed.

    Returns the updated {date_str: (rate, source)} table.
    """
    rows = load(path)
    table_dates = list(rows.keys())
    needed = {_nearest_on_or_before(od, table_dates) for od in set(order_dates)}

    changed = False
    for date_str in sorted(needed):
        rate, source = rows[date_str]
        if source != PLACEHOLDER:
            continue  # immutable -- already real
        fetched_rate, actual_date = _fetch_rate(date_str, source_url)
        label = source_label if actual_date == date_str else f"{source_label}, nearest available: {actual_date}"
        rows[date_str] = (fetched_rate, label)
        changed = True
        print(f"fx: fetched {date_str} = {fetched_rate} ({label})", file=sys.stderr)

    if changed:
        save(rows, path)
    return rows


def lookup(order_date_str, rows):
    """GBP/USD rate for a US order: nearest table date on or before the
    order date. Raises if that date is still PLACEHOLDER (ensure_dates
    should have been called first) or if no table date exists at all.
    """
    nearest = _nearest_on_or_before(order_date_str, list(rows.keys()))
    rate, source = rows[nearest]
    if source == PLACEHOLDER:
        raise ValueError(
            f"FX rate for {nearest} (needed for order date {order_date_str}) is "
            "still PLACEHOLDER -- call ensure_dates() before building"
        )
    return rate
