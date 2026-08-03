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
