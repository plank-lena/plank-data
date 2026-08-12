"""Single period parser both builders call -- the reporting period comes
from the maintainer's/colleague's PROMPT ("generate the returns dashboard
for Q2 2026"), never inferred from a workbook's internal header cell. See
ROADMAP.md and CLAUDE.md's Data connections section for why: a connector
run has no workbook to read a period out of at all.

parse_period("June 2026") / parse_period("Q2 2026") -> a PeriodModel with
cm (current), lm (previous month, or previous quarter for a quarterly
request), and ly (same month/quarter last year), each carrying label/short
(matching the existing trading/contract.py _period_label/_period_str_from_label
and trading/quarterly.py's _q_label conventions -- compute_periods()'s
PERIODS.q1_25/q4_25/q1_26 JS keys read pm['ly']/['lm']/['cm'] respectively,
unchanged by this module) PLUS start/end dates, which neither of those
older helpers ever carried -- the new capability this module adds is a
real date range per slot, for driving Matrixify export filters and
ReturnZap/Yotpo period-coverage checks.

Downstream consumers derive every label from this model -- never hardcode
"vs LM"/"vs LY"/a month name; read pm['lm']['label'] etc.
"""
import calendar
import re
from datetime import date

MONTH_NAMES = ("January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December")
_MONTH_ABBR_TO_NUM = {name[:3].lower(): i + 1 for i, name in enumerate(MONTH_NAMES)}
_MONTH_NAME_TO_NUM = {name.lower(): i + 1 for i, name in enumerate(MONTH_NAMES)}

_MONTH_RE = re.compile(r"^([A-Za-z]+)\s+(\d{4})$")
_QUARTER_RE = re.compile(r"^[Qq](\d)\s+(\d{4})$")


def _month_label(month_num, year):
    name = MONTH_NAMES[month_num - 1]
    return {"label": f"{name[:3]} {year}", "short": f"{name[:3]} '{year % 100:02d}"}


def _quarter_label(quarter_num, year):
    return {"label": f"Q{quarter_num} {year}", "short": f"Q{quarter_num} '{year % 100:02d}"}


def _month_bounds(month_num, year):
    last_day = calendar.monthrange(year, month_num)[1]
    return date(year, month_num, 1), date(year, month_num, last_day)


def _quarter_bounds(quarter_num, year):
    first_month = (quarter_num - 1) * 3 + 1
    start, _ = _month_bounds(first_month, year)
    _, end = _month_bounds(first_month + 2, year)
    return start, end


def _prev_month(month_num, year):
    return (12, year - 1) if month_num == 1 else (month_num - 1, year)


def _prev_quarter(quarter_num, year):
    return (4, year - 1) if quarter_num == 1 else (quarter_num - 1, year)


class PeriodModel(dict):
    """A dict of {cm, lm, ly}, each {label, short, start, end, key, kind}
    (kind: "month" | "quarter"). Subclasses dict so existing code that
    expects a plain period_model (pm['cm']['label'], etc.) keeps working
    unchanged -- this is a strict superset of the old label-only shape.
    """

    @property
    def mode(self):
        return self["cm"]["kind"]


def _month_slot(month_num, year):
    start, end = _month_bounds(month_num, year)
    return {**_month_label(month_num, year), "start": start, "end": end,
            "key": f"{year}-{month_num:02d}", "kind": "month"}


def _quarter_slot(quarter_num, year):
    start, end = _quarter_bounds(quarter_num, year)
    return {**_quarter_label(quarter_num, year), "start": start, "end": end,
            "key": f"{year}-Q{quarter_num}", "kind": "quarter"}


def parse_period(period_str, as_of=None):
    """period_str: "June 2026" (a month) or "Q2 2026" (a quarter) -- case-
    insensitive, full or 3-letter month names both accepted ("Jun 2026").

    as_of: the date to sanity-check "not in the future" against. Defaults
    to today's real date if not given -- pass it explicitly in tests so
    they stay deterministic and don't start failing on some future date.

    Raises ValueError, naming the exact problem, on: an unparseable string,
    an out-of-range month/quarter number, or a period whose START date is
    after as_of (a report can't be run for a period that hasn't started).
    """
    if as_of is None:
        as_of = date.today()
    s = period_str.strip()

    m = _QUARTER_RE.match(s)
    if m:
        quarter_num, year = int(m.group(1)), int(m.group(2))
        if not (1 <= quarter_num <= 4):
            raise ValueError(f"parse_period: {period_str!r} has an invalid quarter number "
                              f"{quarter_num} (must be 1-4)")
        cm = _quarter_slot(quarter_num, year)
        lm_q, lm_y = _prev_quarter(quarter_num, year)
        lm = _quarter_slot(lm_q, lm_y)
        ly = _quarter_slot(quarter_num, year - 1)
    else:
        m = _MONTH_RE.match(s)
        if not m:
            raise ValueError(f"parse_period: {period_str!r} is not a recognized period -- "
                              f"expected a month (\"June 2026\") or a quarter (\"Q2 2026\")")
        month_name, year = m.group(1), int(m.group(2))
        month_num = _MONTH_NAME_TO_NUM.get(month_name.lower()) or _MONTH_ABBR_TO_NUM.get(month_name.lower())
        if month_num is None:
            raise ValueError(f"parse_period: {period_str!r} has an unrecognized month name {month_name!r}")
        cm = _month_slot(month_num, year)
        lm_m, lm_y = _prev_month(month_num, year)
        lm = _month_slot(lm_m, lm_y)
        ly = _month_slot(month_num, year - 1)

    if cm["start"] > as_of:
        raise ValueError(f"parse_period: {period_str!r} starts {cm['start'].isoformat()}, "
                          f"which is after {as_of.isoformat()} -- refusing to build a report "
                          f"for a period that hasn't started yet")

    return PeriodModel(cm=cm, lm=lm, ly=ly)


def month_period_string(month_num, year):
    """(6, 2026) -> "June 2026" -- the inverse of parse_period's month
    branch, for callers building a period string from calendar arithmetic
    (e.g. iterating months in a quarter) rather than a literal prompt.
    """
    return f"{MONTH_NAMES[month_num - 1]} {year}"


def quarter_period_string(quarter_num, year):
    """(2, 2026) -> "Q2 2026" -- the inverse of parse_period's quarter branch."""
    return f"Q{quarter_num} {year}"


def months_in_quarter(quarter_num, year):
    """Q2 2026 -> ["April 2026", "May 2026", "June 2026"] -- the 3 monthly
    period strings a quarterly Matrixify build needs to fetch/aggregate.
    """
    first_month = (quarter_num - 1) * 3 + 1
    return [month_period_string(first_month + i, year) for i in range(3)]
