"""The reconciliation gate — runs on every build; aborts (raises) rather than
letting a bad number through. See ROADMAP.md §5 for the full contract this
implements. A failure here means fix the source or the mapping, never
silence the assertion.

Returns-specific rules implemented here:
  - Additive measures (units, cash) must sum from a group-by block to its
    Total/Quarter row, within tolerance.
  - Order counts (orders, returned_orders) are distinct per grouping and must
    NOT be asserted additive -- one order can span multiple statuses/
    finishes/months. Callers must exclude these columns from the additive
    check; this module does not silently do it for you, so the exclusion is
    visible at the call site.
  - Every group label actually used must match a whitespace-normalised label
    in the data (catches the "Electric Accessory " trailing-space class of
    bug generically, for whatever grouping this is applied to).
  - The headline rate must be orders-based: returned_orders / orders.
  - No rate may exceed 100% (assert_no_impossible_rate) -- order-month
    cohort framing (D2 ruling 1) should make this structurally impossible.
  - Ranked SKU blocks must not include a row below the minimum distinct-
    orders floor (assert_min_orders_threshold, D2 §2).
  - Buckets that must always be surfaced (e.g. no-SKU refunds, D2 §3) must
    be reported even when zero, never silently dropped (assert_bucket_reported).
  - A returns export must actually overlap the sales cohort it's paired with,
    by a plausible margin (assert_returns_overlap_sales) -- learned the hard
    way: a wrong/partial returns file can join cleanly (no error) while
    matching almost none of the period's real orders, silently producing a
    near-empty, garbage-low return rate. Don't trust a source file by its
    filename; check that it actually covers what it's supposed to.

Trading-specific rule implemented here:
  - uk + us + row must equal an INDEPENDENTLY computed grand total (summed
    over every line regardless of country label), within tolerance -- catches
    a line silently falling into a 4th/unexpected country bucket that never
    lands in the uk/us/row sum. See ROADMAP.md §5.
"""

TOL = 0.001  # 0.1% relative, per ROADMAP.md


def _rel_diff(a, b):
    return abs(a - b) / abs(b) if b else (0.0 if abs(a) < 1e-9 else float("inf"))


def assert_additive(block, additive_cols, group_labels, total_label, tol=TOL):
    """Assert block.loc[total_label, col] == sum(block.loc[group_labels, col])
    for each column in additive_cols. Raises AssertionError naming the gap.

    Deliberately takes an explicit additive_cols allowlist rather than
    inferring it from the DataFrame -- order-count columns (orders,
    returned_orders) must never be passed here; ROADMAP.md §5 is explicit
    that they legitimately do not sum to the total.
    """
    for col in additive_cols:
        parts = block.loc[group_labels, col].sum()
        total = block.loc[total_label, col]
        rel = _rel_diff(parts, total)
        assert rel <= tol, (
            f"RECONCILE FAIL: {col} group-sum {parts} != {total_label} {total} "
            f"(gap {rel:.4%}, tolerance {tol:.1%})"
        )


def assert_labels_match(used_labels, available_labels):
    """Assert every label a grouping used is actually present in the data,
    after whitespace normalisation. Catches trailing/leading-space label
    bugs (e.g. "Electric Accessory ") before they silently read as zero.
    """
    normalised_available = {str(a).strip() for a in available_labels}
    for label in used_labels:
        normalised = str(label).strip()
        assert normalised in normalised_available, (
            f"RECONCILE FAIL: label {label!r} not found in data even after "
            f"whitespace normalisation (available: {sorted(normalised_available)})"
        )


def assert_orders_based_rate(block, rate_col="return_rate",
                              orders_col="orders", returned_col="returned_orders",
                              tol=TOL):
    """Assert the headline rate is returned_orders / orders, not a
    units-based rate -- guards against an accidental metric swap.
    """
    for label, row in block.iterrows():
        orders = row[orders_col]
        expected = (row[returned_col] / orders) if orders else 0
        rel = _rel_diff(row[rate_col], expected)
        assert rel <= tol, (
            f"RECONCILE FAIL: {label} {rate_col}={row[rate_col]} is not "
            f"orders-based (expected {returned_col}/{orders_col}={expected})"
        )


def assert_country_reconciles(country_totals, grand_total, tol=TOL):
    """Assert uk + us + row == an independently-computed grand total.

    country_totals: {"UK": ..., "US": ..., "ROW": ...}
    grand_total: summed over every line regardless of country label -- must
    be computed independently of country_totals by the caller, not derived
    from it, or this check is vacuous.
    """
    assert set(country_totals) == {"UK", "US", "ROW"}, (
        f"RECONCILE FAIL: expected exactly UK/US/ROW buckets, got {sorted(country_totals)}"
    )
    parts = sum(country_totals.values())
    rel = _rel_diff(parts, grand_total)
    assert rel <= tol, (
        f"RECONCILE FAIL: uk+us+row {parts} != grand total {grand_total} "
        f"(gap {rel:.4%}, tolerance {tol:.1%})"
    )


def assert_matches_oracle(computed, oracle, tol=TOL):
    """Assert every key present in both `computed` and `oracle` agrees within
    tolerance -- the regression-parity check against a committed fixture's
    ground-truth figures (e.g. a Monthly Summary row). Keys present only in
    `oracle` (e.g. a units column the caller wants reported but not gated)
    are silently skipped; the caller decides what to gate by what it passes
    in `computed`.
    """
    for key, expected in oracle.items():
        if key not in computed:
            continue
        rel = _rel_diff(computed[key], expected)
        assert rel <= tol, (
            f"RECONCILE FAIL: {key} computed {computed[key]} != oracle {expected} "
            f"(gap {rel:.4%}, tolerance {tol:.1%})"
        )


def assert_bucketed_by(index_labels, expected_labels):
    """Structural check that a block's rows are exactly the expected set of
    buckets (e.g. order-month labels + the period total) -- confirms the
    block wasn't built against some other bucketing.
    """
    got = set(index_labels)
    expected = set(expected_labels)
    assert got == expected, (
        f"RECONCILE FAIL: bucket labels {sorted(got)} != expected {sorted(expected)}"
    )


def assert_no_impossible_rate(block, rate_cols=("return_rate",), max_rate=1.0):
    """Assert no rate in `block` exceeds `max_rate` (100%).

    Returns dashboard D2, ruling 1: order-month cohort bucketing should make
    a >100% rate structurally impossible (every return maps to an in-period
    order), so this is a fail-loud tripwire, not an expected-to-fire check --
    if it trips, the cohort join has a bug, not a data quirk to explain away.
    """
    for col in rate_cols:
        bad = block[block[col] > max_rate]
        assert bad.empty, (
            f"RECONCILE FAIL: impossible rate(s) in {col!r} "
            f"(> {max_rate:.0%}): {bad[col].to_dict()}"
        )


def assert_min_orders_threshold(rows, orders_col, min_orders):
    """Assert every row in a ranked/published block clears the minimum
    distinct-orders floor (returns D2 §2: 20-order minimum to appear in the
    SKU tracker). Rows below the floor must be filtered out before ranking,
    never just greyed -- this catches a filter that got dropped or loosened.
    """
    for key, row in rows.items():
        orders = row[orders_col] if isinstance(row, dict) else row
        assert orders >= min_orders, (
            f"RECONCILE FAIL: {key!r} has {orders} orders, below the "
            f"{min_orders}-order minimum -- should have been filtered out"
        )


def assert_bucket_reported(value, label):
    """Assert a bucket that must always be surfaced (e.g. the no-SKU bucket,
    returns D2 §3) is actually present and not None -- guards against it
    being silently absorbed or dropped rather than reported as zero/footnoted.
    """
    assert value is not None, (
        f"RECONCILE FAIL: {label} bucket missing -- must be reported "
        f"explicitly (even if zero), never silently dropped"
    )


def assert_returns_overlap_sales(returned_orders, sales_orders, min_rate=0.01, min_absolute=5):
    """Assert a returns export actually covers the sales cohort it's paired
    with, by a plausible margin -- not just that the join ran without error.

    Learned the hard way building the Q2 dashboard: a plausibly-named but
    wrong/partial returns export (source/ytd_returns.csv, 1,040 rows) joined
    cleanly against Q1 sales with zero errors, but matched only 54 of 9,768
    orders (0.55%) -- an implausible return rate for a real store, and a
    silent sign the file was a sample/wrong scope, not the thing it claimed
    to be. The real export (ytd_returns_2.numbers) matched 715/9,768 (7.3%).

    returned_orders: distinct sales orders in this period with >=1 matching
        return (i.e. ret["order"].nunique() after the period join).
    sales_orders: distinct sales orders in this period (s["order"].nunique()).
    min_rate/min_absolute: generous floors, not a precise expectation -- this
    catches "the file is obviously wrong," not "the rate looks a bit off."
    min_absolute's actual job is different from min_rate's: min_rate alone
    would let a single coincidental match in a small population pass (1 of
    50 orders = 2%, above a 1% floor, off pure chance) -- min_absolute=5
    exists to require more than one lucky coincidence before trusting the
    join. That reasoning only holds when the population is big enough for
    5 to be a small fraction of it; scaled down here (2026-08-13, added
    when returns/build.py started deriving a real ROW bucket instead of
    folding everything into UK/US -- ROW is genuinely small, e.g. 45 orders
    in a typical month, and demanding 5 of 45 is a ~11% floor, disproportionate
    versus UK/US's own effectively-~0.05% floor at their scale) so the same
    anti-coincidence intent applies at any population size instead of one
    fixed number calibrated for thousands of orders.
    """
    scaled_min_absolute = min(min_absolute, max(1, round(sales_orders * min_rate)))
    rate = returned_orders / sales_orders if sales_orders else 0
    assert returned_orders >= scaled_min_absolute and rate >= min_rate, (
        f"RECONCILE FAIL: only {returned_orders} of {sales_orders} sales orders "
        f"({rate:.2%}) have a matching return in this period -- implausibly low "
        f"for a real returns export. Check the returns source file actually "
        f"covers this period/cohort; don't trust it by filename alone."
    )
