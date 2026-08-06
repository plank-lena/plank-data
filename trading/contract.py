"""Trading data-contract emission (BRIEF #3 / step 3 / D1).

The contract IS extract_all()'s return shape (trading/dashboard/extract.py),
minus the `_ws_*` openpyxl handles (not JSON-serialisable, and the renderer
never reads them directly off `raw` -- only compute.py's functions do, and
those take plain dicts/lists), plus an additive metadata header. Two
front-ends populate the identical payload shape:

  emit_contract_from_oracle(oracle_xlsx)   -- literally extract_all() + a
    metadata wrapper. Numbers are correct by definition (source: "oracle").
  emit_contract_from_matrixify(period, ...) -- the real builder: BRIEF #5's
    ship-to three-way reconcile + BRIEF #2's Line Detail enrichment, rolled
    up into the same shape. Stamps reconciled: True only if the STRUCTURAL
    leak check (common/reconciliation_gate.assert_country_reconciles --
    uk+us+row ties to an independently-computed grand total) passes; on
    failure the contract is still WRITTEN (never silently dropped) but
    stamped reconciled: False, and can_publish() below returns False.
    RELEASED 2026-08-05 (ROADMAP.md §5): matching the hand-built oracle to
    0.1% is no longer part of this gate -- the oracle reflects an early,
    still-maturing returns snapshot, not a reproducible target. When an
    oracle is available, country_gaps_vs_oracle is still populated as
    historical context, but never affects reconciled/can_publish().

load_contract(path) reverses either into the exact dict shape
compute.py/render.py already consume -- neither file changes, and neither
knows or cares which front-end produced the file.

Known, deliberately-not-closed-here gaps (BRIEF #5 / #2's already-surfaced,
already-deferred items) are carried into metadata rather than hidden or
tuned away -- see each field's comment below and trading/RECONCILE_HANDOFF.md.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_DIR = os.path.join(_HERE, "dashboard")
for _p in (_HERE, _DASHBOARD_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from extract import extract_all  # trading/dashboard/extract.py
from matrixify_source import load_rows, build_lines, order_month_london
from revenue import country_bucket, line_ab
from line_detail import build_line_detail_index, enrich_lines
from build_matrixify import _fx_rate_for, channel_from_company, MAY_THREE_WAY
from common.reconciliation_gate import assert_country_reconciles
from common.fx import DEFAULT_PATH as FX_RATES_PATH

CONTRACT_VERSION = "1.0"

# extract_all()'s payload keys, i.e. everything it returns EXCEPT the
# _ws_* openpyxl handles. This is the exact shape compute.py's functions
# and render.py's tokens are written against -- frozen here as the schema.
PAYLOAD_KEYS = (
    "mode", "period_model", "current", "lm", "ly",
    "statuses", "prod_types", "finishes", "collections", "skus_all",
)

STATUS_BUCKETS = ("Continuity", "Newness", "Discontinued", "Dead")
DEPARTMENTS = ("Cabinetry", "Electric", "Accessories", "Lighting", "Components", "Taps", "Unknown")

# Trading review round 1, T2a (Lena, unilateral): Door is a dead category --
# cut it entirely (headline, country, channel, groupings, charts, SKU lists,
# movers, matrix), everywhere. One filter at line-enrichment time, applied
# before any aggregation, so it propagates to every downstream cut without
# re-hardcoding a department list anywhere else -- department discovery
# elsewhere stays fully dynamic (Taps stays; a future real department isn't
# at risk of being caught by this). The leak check still passes on the
# Door-excluded set (uk+us+row ties to the new, smaller independent total);
# the vs-oracle gap widening by Door's revenue is the intended effect of
# de-scoping a dead category, not a regression to chase.
_DEAD_DEPARTMENTS = {"Door"}

# BRIEF #4 step 4 §1/§6: st/wc/inv are vestigial as of the redesign -- the
# Sell-Through/WC KPI is removed and trading drops the inventory feed
# dependency entirely. Stripped from every nested block at emission time
# (not just left unread) so a stale reference elsewhere fails loudly rather
# than silently rendering an old number.
_VESTIGIAL_HEADLINE_KEYS = ("sell_through", "weeks_cover", "inventory")
_VESTIGIAL_ROW_KEYS = ("st", "wc", "inv")

# Line Detail statuses (raw enum) vs. the oracle's own coarse SKU-level
# bucket use different vocabularies for the same underlying concept -- see
# line_detail.py's STATUS_ENUM vs the oracle's By SKU status columns
# (Continuity/Newness/Discontinued/Dead/Not For Sale/Pre-Launch, no "Live"
# value at all). BRIEF #4 step 4 item 4's "Live-status only" movers filter
# is defined here across both vocabularies so it means the same thing
# regardless of source: a SKU counts as live if EITHER market's status is
# in this set, matching Line Detail's own is_live_uk/is_live_us definition
# (status == "Live") translated into the coarse bucket's equivalent
# (Continuity/Newness both presuppose the SKU is live in that market).
LIVE_STATUS_VALUES = {"Live", "Continuity", "Newness"}


def _strip_vestigial(payload):
    """Mutate payload in place, deleting st/wc/inv-family keys from every
    nested block. Both front-ends call this right before wrapping so the
    contract never carries these fields (BRIEF #4 step 4 §1/§6).
    """
    for k in _VESTIGIAL_HEADLINE_KEYS:
        payload["current"].pop(k, None)
    for block_name in ("statuses", "collections", "skus_all"):
        for row in payload.get(block_name, []):
            for k in _VESTIGIAL_ROW_KEYS:
                row.pop(k, None)
    return payload


def _exclude_dead_categories(payload):
    """Mutate payload in place, dropping _DEAD_DEPARTMENTS from prod_types/
    collections/skus_all (T2a, Lena unilateral: Door is a dead category, cut
    entirely). Both front-ends call this right after _strip_vestigial.

    Does NOT adjust headline totals (current.total_sales/uk_gbp/etc) here --
    emit_contract_from_matrixify instead excludes dead departments at the
    LINE level (its own `enriched` filter, before any aggregation), which is
    what actually keeps headline/country totals consistent with the
    breakdown views for that front-end; this function is a harmless no-op
    there in practice (a dead department's lines are already gone by the
    time this runs). The oracle front-end has no line-level data to
    re-aggregate from (it reads pre-aggregated sheet cells), so this really
    is a display-only filter there -- safe today because every dead
    department's own sales figure is 0 in the source sheet (that's what
    "dead" means in practice: an empty row Step 4's dynamic discovery
    stopped hiding), but would silently leave a small residual in the oracle
    headline if that ever stopped being true.
    """
    payload["prod_types"] = [t for t in payload["prod_types"] if t["t"] not in _DEAD_DEPARTMENTS]
    payload["collections"] = [c for c in payload["collections"] if c["t"] not in _DEAD_DEPARTMENTS]
    payload["skus_all"] = [s for s in payload["skus_all"] if s.get("type_") not in _DEAD_DEPARTMENTS]
    return payload


def _normalize_oracle_prod_types(payload):
    """Mutate payload in place. extract_product_types() never carries
    d2c/b2b/uk/us (the oracle sheet's Product Type table has no such column
    -- genuinely unavailable at this grain, not an oversight) or lq_sales
    (an absolute figure; the sheet only gives the vs_lq RATIO). Normalise
    every department to the same shape emit_contract_from_matrixify's
    prod_types now carries (T2b, 2026-08-05), so compute.py/the template
    have one shape to read regardless of source. lq_sales IS reconstructable
    here (unlike d2c/b2b/uk/us) since the oracle's own vs_lq is real -- same
    sales/(1+vs_lq) technique used for the Matrixify path's oracle-
    bootstrapped lq_sales. Called by emit_contract_from_oracle.
    """
    for t in payload["prod_types"]:
        vs_lq = t.get("vs_lq")
        lq_sales = None
        if isinstance(vs_lq, (int, float)) and abs(1 + vs_lq) > 1e-9 and t.get("sales"):
            lq_sales = round(t["sales"] / (1 + vs_lq), 2)
        t.setdefault("d2c", 0.0)
        t.setdefault("b2b", 0.0)
        t.setdefault("uk", 0.0)
        t.setdefault("us", 0.0)
        t["lq_sales"] = lq_sales
    return payload


def _is_el_component(coll_name):
    return str(coll_name or "").strip().upper() == "EL COMPONENT"


def _add_headline_kpis(current):
    """Mutate current in place, adding BRIEF #4 step 4 §1/§6's two new
    headline KPIs. Both are already derivable from fields current carries
    (vs_ly is the same YoY figure the old GM-slot KPI is replaced by;
    b2b_gbp/total_sales is B2B's SHARE of revenue, not a channel split of
    the total -- D2C% + B2B% will not sum to 100 once ROW enters the
    denominator on one side only, per the brief's explicit warning).
    """
    current["yoy_growth_pct"] = current.get("vs_ly")
    total = current.get("total_sales") or 0
    current["b2b_share"] = (current.get("b2b_gbp", 0) / total) if total else None


def _git_commit():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_HERE, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


def _period_str_from_label(label):
    """'May 2026' -> '2026-05'; 'Q2 2026' -> '2026-Q2'.

    Bugfix (found building the Step 5 quarterly aggregator): the period
    cell's month name is always a 3-letter abbreviation ('Apr - 2026',
    'Jun - 2026' -- see extract._parse_period), which datetime's '%B'
    (full month name) never matches. This only ever worked for May, since
    'May' happens to be spelled identically as an abbreviation and a full
    name -- every other month raised ValueError. Never exercised before
    because May was the only month ever run through this path.
    """
    part1, year = label.split()
    if part1.upper().startswith('Q') and part1[1:].isdigit():
        return f"{year}-{part1.upper()}"
    month = datetime.strptime(part1, "%b").month
    return f"{year}-{month:02d}"


def _wrap_contract(payload, provenance):
    contract = {
        "contract_version": CONTRACT_VERSION,
        "period": _period_str_from_label(payload["period_model"]["cm"]["label"]),
        "provenance": provenance,
    }
    contract.update({k: payload[k] for k in PAYLOAD_KEYS})
    return contract


# ── Oracle front-end ─────────────────────────────────────────────────────────

def emit_contract_from_oracle(oracle_xlsx, out_path=None):
    """The oracle-sourced contract: extract_all() output, repackaged. Used
    to build/prove the emission layer and step 4's template right now,
    independent of the deferred Matrixify reconciliation (BRIEF #3 §3).
    """
    raw = extract_all(oracle_xlsx)
    payload = {k: raw[k] for k in PAYLOAD_KEYS}
    _add_headline_kpis(payload["current"])
    for sku in payload["skus_all"]:
        sku["is_el_component"] = _is_el_component(sku.get("coll"))
    _strip_vestigial(payload)
    _exclude_dead_categories(payload)
    _normalize_oracle_prod_types(payload)
    provenance = {
        "source": "oracle",
        "reconciled": True,
        "revenue_basis": "net_sales_exvat_AB",
        "returns_netted": True,
        "fx_table": None,  # the hand sheet bakes in its own (live GOOGLEFINANCE) FX already
        "enrichment_coverage": None,  # N/A -- the oracle's own By SKU sheet already carries
        "unmatched_sku_revenue_share": None,  # dept/coll/finish; no join was performed here
        "country_gaps_vs_oracle": None,  # N/A -- this contract IS the oracle
        "lq_ly_source": "oracle_native",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git_commit(),
    }
    contract = _wrap_contract(payload, provenance)
    if out_path:
        with open(out_path, "w") as f:
            json.dump(contract, f, indent=2, default=str)
    return contract


def load_contract(path_or_contract):
    """Reverse a contract (path to .json, or an already-loaded dict) back
    into the exact raw-dict shape extract_all() returns (minus _ws_*) --
    what compute.py's functions and pipeline.py's call sequence expect.
    """
    if isinstance(path_or_contract, dict):
        contract = path_or_contract
    else:
        with open(path_or_contract) as f:
            contract = json.load(f)
    return {k: contract[k] for k in PAYLOAD_KEYS}


def can_publish(contract):
    """The publish gate BRIEF #3 §4 describes: build/eyeball always work
    (the contract is written either way); only a reconciled contract may
    go out the door. No GitHub Pages automation exists in this repo yet
    (publishing is a manual, Cloudflare-Access-gated step per CLAUDE.md) --
    this is the check that step would consult.
    """
    return bool(contract.get("provenance", {}).get("reconciled"))


PROVISIONAL_BANNER_HTML = (
    '<div class="provisional-banner">'
    "&#9888; PROVISIONAL / UNRECONCILED &mdash; uk+us+row does not tie to an "
    "independently-computed grand total (a genuine bucketing leak). Do not "
    "publish. This is NOT about matching the hand-built oracle (released as a "
    "publishing requirement 2026-08-05, see ROADMAP.md §5) -- it means a "
    "line silently fell outside all three country buckets.</div>"
)


def render_contract(contract, template_html):
    """Thin wrapper around the UNCHANGED compute.py/render.py pipeline: runs
    the identical extract -> compute -> js_block -> token -> fill_template
    sequence pipeline.run() uses, sourcing `raw` from this contract instead
    of extract_all(). Fills the {{PROVISIONAL_BANNER}} token (BRIEF #4 step
    4's redesigned template places it just inside <body>, styled via the
    template's own .provisional-banner CSS) with PROVISIONAL_BANNER_HTML
    when reconciled is False, empty string otherwise -- still a single
    token fill, not a hand-patch of the rendered output.
    """
    from compute import (
        compute_periods, compute_total_sales, compute_statuses, compute_prod_types,
        compute_skus, compute_newness_skus, compute_collections, compute_bottom_skus,
        compute_finish_data, compute_coll_analysis, compute_kpi_tokens, compute_ribbon_tokens,
        compute_category_top_collections, compute_movers, compute_matrix,
        js_block_periods, js_block_collections, js_block_statuses, js_block_prod_types,
        js_block_skus, js_block_finish_data, js_block_coll_analysis,
        js_block_newness_skus, js_block_bottom_skus,
        js_block_cat_top_collections, js_block_movers, js_block_matrix,
    )
    from render import fill_template, build_token_dict

    raw = load_contract(contract)
    pm = raw["period_model"]
    # BRIEF step 5: period_type wiring -- 'quarter' switches every MoM/LM ->
    # QoQ/LQ label the template's tokens resolve to (REPORT_TYPE,
    # CM_PERIOD_NOUN, PREV_PERIOD_ABBR, PERIOD_COMP_LABEL, ...); the
    # template itself has no quarterly fork, only these token values do.
    mode = raw.get("mode", "month")

    periods_data = compute_periods(raw["current"], raw["lm"], raw["ly"], pm)
    total_sales = compute_total_sales(raw["current"])
    statuses_data = compute_statuses(raw["statuses"])
    types_data = compute_prod_types(raw["prod_types"])
    skus_data = compute_skus(raw["skus_all"])
    bottom_skus_data = compute_bottom_skus(raw["skus_all"])
    newness_data = compute_newness_skus(raw["skus_all"])
    coll_data = compute_collections(raw["collections"])
    finish_data = compute_finish_data(raw["finishes"], raw["skus_all"])
    coll_analysis = compute_coll_analysis(raw["collections"], raw["skus_all"], total_sales)
    kpi_tokens = compute_kpi_tokens(raw["current"], raw["lm"], pm, mode=mode)
    ribbon_tokens = compute_ribbon_tokens(raw["current"], raw["lm"], raw["ly"], pm, mode=mode)
    cat_top_collections = compute_category_top_collections(coll_data, skus_all=raw["skus_all"])
    movers = compute_movers(raw["skus_all"])
    matrix = compute_matrix(raw["collections"])

    tokens = build_token_dict(
        js_block_periods(periods_data), js_block_collections(coll_data),
        js_block_statuses(statuses_data), js_block_prod_types(types_data),
        js_block_skus(skus_data), js_block_finish_data(finish_data), total_sales,
        js_block_coll_analysis(coll_analysis), js_block_newness_skus(newness_data),
        kpi_tokens, ribbon_tokens,
        js_block_cat_top_collections(cat_top_collections), js_block_movers(movers), js_block_matrix(matrix),
        bottom_skus_js=js_block_bottom_skus(bottom_skus_data),
    )
    tokens["PROVISIONAL_BANNER"] = "" if can_publish(contract) else PROVISIONAL_BANNER_HTML
    # B3 (round-2 review): department-level vs-LY caveat -- see
    # emit_contract_from_matrixify's ly_month_contract docstring. None for
    # the oracle path and for any Matrixify build without ly_month_contract,
    # which the template must treat as "no caveat needed", not a zero.
    # Read off `contract` (the original dict), not `raw` -- load_contract()
    # deliberately strips provenance down to PAYLOAD_KEYS (it reconstructs
    # extract_all()'s raw shape), so raw["provenance"] never exists.
    ly_dept_unclassified_pct = contract.get("provenance", {}).get("ly_dept_unclassified_share")
    tokens["BLOCK_PROD_TYPES"] += f"\nconst LY_DEPT_UNCLASSIFIED_PCT = {json.dumps(ly_dept_unclassified_pct)};"
    # Shared design tokens (2026-08-05 CSS-overhaul brief §2) -- see
    # trading/dashboard/pipeline.py's identical fill for the full rationale.
    with open(os.path.join(_HERE, "..", "common", "dashboard_tokens.css"), encoding="utf-8") as _fh:
        tokens["DASHBOARD_TOKENS"] = _fh.read()
    html = fill_template(template_html, tokens)
    return html


# ── Matrixify front-end ──────────────────────────────────────────────────────

_MONTH_NAMES = ("January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December")


def _period_label(period_str):
    """'2026-05' -> {'label': 'May 2026', 'short': "May '26"}.

    `label` uses the 3-letter abbreviation, matching the oracle sheet's own
    convention (extract.py's _parse_period reads e.g. "Apr - 2026" verbatim)
    and what _period_str_from_label parses back via strptime's "%b" -- this
    is load-bearing, not cosmetic. Found building the Matrixify quarterly
    glue script (T0, 2026-08-05): the previous version put the FULL month
    name in `label` ('April'), which _period_str_from_label's "%b" format
    can't parse ('unconverted data remains: il') -- the exact %B-vs-%b bug
    class ROADMAP.md §2 already documents finding once in the parser itself,
    recurring here on the generating side. Invisible for May specifically
    (spelled identically abbreviated or full) and never caught because
    emit_contract_from_matrixify_quarter -- the only caller that reaches
    this function without going through the oracle-bootstrap branch, which
    sources period_model from extract_all() instead -- had never actually
    been run for April or June before this script's first real invocation.
    """
    year, month = period_str.split("-")
    name = _MONTH_NAMES[int(month) - 1][:3]
    return {"label": f"{name} {year}", "short": f"{name} '{year[2:]}"}


def _status_bucket(line):
    """Roll BRIEF #2's per-market status_uk/status_us + newness_bucket into
    a coarse FOUR-bucket rollup (Continuity/Newness/Discontinued/Dead) for
    the Matrixify path's own `statuses` block -- a deliberate simplification
    of Line Detail's full status enum, not an attempt to mirror the oracle's
    Product Status table row-for-row. (The oracle's own table actually has
    SIX rows -- extract_statuses discovers Not For Sale and Pre-Launch too,
    once-hidden by a fixed row dict the same way Product Type/Finish were;
    see BRIEF step 5. This rollup still only produces four buckets on
    purpose: Matrixify carries In Development/Launching/Not Sold in this
    Market/Not For Sale/Disco to Resource as real per-SKU statuses, but
    BRIEF #2/#3 never asked for a matching Matrixify-side bucket for each
    of those, so lines in them return None here -- "no bucket" -- rather
    than inventing a catch-all un-spec'd by either brief.)

    Neither #2 nor #3 pins the per-line mapping explicitly -- a SKU can be
    e.g. Discontinued in the UK and Live in the US -- so this priority rule
    is a deliberate, disclosed choice, not a given spec:
      1. live in either market -> its newness_bucket (Newness/Continuity)
      2. else Discontinued in either market -> Discontinued
      3. else Dead in either market -> Dead
      4. else -> None (In Development, Not Sold in this Market, Disco to
         Resource, Not For Sale, Pre-Launch, blank)
    """
    if line["newness_bucket"] is not None:
        return line["newness_bucket"]
    if line["status_uk"] == "Discontinued" or line["status_us"] == "Discontinued":
        return "Discontinued"
    if line["status_uk"] == "Dead" or line["status_us"] == "Dead":
        return "Dead"
    return None


def _current_to_lm_shape(current):
    """A prior contract's 'current' dict (MS_ROW7 keys) -> the lm/ly block
    shape (LM_BLOCK/LY_BLOCK keys) BRIEF #5's compute_periods expects.
    """
    return {
        "total": current["total_sales"], "d2c": current["d2c_gbp"], "b2b": current["b2b_gbp"],
        "uk": current["uk_gbp"], "us": current["us_gbp"], "row": current["row_gbp"],
        "total_u": current["units"], "d2c_u": current["d2c_units"], "b2b_u": current["b2b_units"],
        "uk_u": current["uk_units"], "us_u": current["us_units"], "row_u": current["row_units"],
    }


def _vs(curr, prev):
    return (curr - prev) / prev if prev else None


def emit_contract_from_matrixify(period, uk_csv, us_csv, line_detail_path=None, as_of=None,
                                  lm_contract=None, ly_contract=None, oracle_bootstrap_path=None,
                                  oracle_gaps=None, out_path=None, prior_month_contract=None,
                                  ly_month_contract=None):
    """The real builder: BRIEF #5's ship-to reconcile + BRIEF #2's Line
    Detail enrichment, rolled up into extract_all()'s exact payload shape.

    lm_contract / ly_contract: a loaded prior contract dict (or path) for
    the previous month / same month last year, per §5 -- when given, lm/ly
    come from these (provenance.lq_ly_source: "contract_chain").
    CALLING CONVENTION (2026-08-05, load-bearing for cross-dashboard
    consistency -- see ROADMAP.md §5's "contract chaining is the only
    source of truth for LM/LY" note): always pass the previous month's
    ALREADY-COMMITTED contract here once one exists. Never re-derive a
    past month's own figures by re-running this function against that
    month's Matrixify export again -- returns keep maturing for weeks
    after a month closes (see RECONCILE_HANDOFF.md's maturity findings),
    so a fresh recompute of an old month will NOT match that month's own
    previously-published headline, breaking the invariant that a month's
    number is identical whether viewed as CM today or as LM in next
    month's dashboard. lm_contract=None is only correct for a period with
    no prior committed contract at all (the very first month built).
    oracle_bootstrap_path: when no prior contract exists yet (true today --
    this is the FIRST committed contract), fall back ONCE to this oracle
    xlsx's own lm/ly blocks (provenance.lq_ly_source: "oracle_bootstrap").
    oracle_gaps: {"uk":..,"us":..,"row":..,"total":..} historical hand-built
    figures to diff against for provenance.country_gaps_vs_oracle --
    INFORMATIONAL ONLY as of 2026-08-05 (see ROADMAP.md §5): the hand-built
    oracle reflects an early, ~9-15-day-post-close snapshot of returns that
    keeps maturing for weeks afterward, so it is not a reproducible target
    for a deterministic rebuild and no longer gates `reconciled` or
    `can_publish()`. Defaults to BRIEF #5's MAY_THREE_WAY when
    period == "2026-05", else None (gaps simply not computed -- this no
    longer affects reconciled/publishability either way).

    Never fabricates: st/wc/inv are None throughout (no inventory feed
    wired -- BRIEF #3 §6/§9); unmatched SKUs roll into department/
    collection "Unknown", never dropped, so totals still tie.

    Per-collection LQ (2026-08-05, trading review round 1 T4a): when
    oracle_bootstrap_path is given, its own "collections" block already
    carries real prior-period lq_total/lq_uk/lq_us per (department,
    collection) -- extract.py's extract_collections reads them from the
    oracle sheet's own LQ columns regardless of which month's oracle it is.
    Matched by (department, collection) name against this function's own
    Matrixify-computed collections; unmatched ones (new/renamed collection,
    or a genuine oracle/Matrixify department-naming mismatch) keep the
    honest lq_total=0.0/vs_lq=None placeholder rather than a guess -- see
    the match-rate line printed to stderr. Collections' LQ is NOT populated
    via lm_contract/ly_contract chaining -- only the oracle bootstrap path
    carries collection-grain history today.

    Per-SKU vs-LM (2026-08-05, follow-up after T4a/T4b): same technique,
    one grain finer -- skus_all's own vslq/lq were hardcoded None/None
    throughout this whole function until now (a real, disclosed gap:
    Movers and the SKU Performance tables' vs-LM columns were always empty
    on this path). Matched by exact SKU code against the oracle bootstrap's
    own per-SKU vslq ratio -- a cleaner match than the (department,
    collection) name pairing above, since a SKU code is an exact catalog
    identifier, not a display name that can drift between the two sources.
    Also NOT populated via lm_contract/ly_contract chaining, same reasoning
    as collections' LQ.

    prior_month_contract (2026-08-05, T1): the immediately-prior month's own
    already-committed Matrixify contract (dict or path), used only to derive
    current['trend_3mo'] -- a real [M-2, M-1, M] revenue series for the
    headline KPI's trend arrows. Independent of lm_contract/ly_contract
    (which decide whether headline LM/LY itself is bootstrapped or chained);
    pass this in addition to oracle_bootstrap_path, not instead of it. None
    (current['trend_3mo'] stays None, not fabricated) when no prior month's
    contract exists yet.

    ly_month_contract (2026-08-06, round-2 review B3): a real prior-year
    SAME-MONTH Matrixify contract (dict or path) -- e.g. 2025-04's own
    contract when building 2026-04 -- used ONLY to backfill prod_types'
    per-department vs_ly (ly_dept_sales), independent of lm_contract/
    ly_contract. Headline vs_ly was never the gap: oracle_bootstrap_path
    already gives that real (the oracle sheet carries a genuine LY column
    at headline grain). The gap is one grain finer -- the oracle sheet has
    no per-department LY column, only vs_lq, so prod_types' vs_ly stayed
    None on this path even after oracle_bootstrap_path. Applied whenever
    ly_dept_sales is still empty after the branch above, so it composes
    with oracle_bootstrap_path (real LM+LY headline) rather than requiring
    the stricter both-or-neither lm_contract/ly_contract chain.
    Disclosed, not silently trusted: a same-month-last-year Matrixify pull
    classifies department by the CURRENT sku_taxonomy.py seed, and a real
    chunk of a year-ago revenue used SKU-naming conventions (e.g. legacy
    "KH-"/"KTH-KH-" prefixes) that seed doesn't recognise, landing in
    "Unknown" instead of their real department -- 27-33% of Apr-Jun 2025
    revenue, vs ~3% for the equivalent 2026 months. provenance.
    ly_dept_unclassified_share carries that fraction through so the
    template can caveat the vs-LY view rather than present department YoY
    growth as more precise than it is (some of it is a classification
    artifact, not real movement). None when ly_month_contract isn't given.
    """
    line_detail_path = line_detail_path or os.path.join(_HERE, "source", "line_detail.xlsx")
    as_of_year, as_of_month = (int(x) for x in period.split("-"))
    from datetime import date as _date
    as_of = as_of or _date(as_of_year, as_of_month + 1 if as_of_month < 12 else 1, 1)

    ld_index = build_line_detail_index(line_detail_path, as_of=as_of)

    all_lines = []
    for csv_path, store_label in ((uk_csv, "uk"), (us_csv, "us")):
        rows = load_rows(csv_path)
        lines, _, _ = build_lines(rows, store_label)
        fx_rate = _fx_rate_for(store_label, period)
        for line in lines:
            if line["order_month"] == period:
                line["fx_rate"] = fx_rate
                all_lines.append(line)
    enriched = [l for l in enrich_lines(all_lines, ld_index) if l["department"] not in _DEAD_DEPARTMENTS]

    country_totals = {"UK": 0.0, "US": 0.0, "ROW": 0.0}
    units_totals = {"UK": 0, "US": 0, "ROW": 0}
    channel_totals = {"D2C": 0.0, "B2B": 0.0}
    channel_units = {"D2C": 0, "B2B": 0}
    status_totals = {b: {"sales": 0.0, "units": 0, "gm_num": 0.0, "gm_den": 0.0} for b in STATUS_BUCKETS}
    dept_totals = {}
    finish_totals = {}
    coll_totals = {}
    sku_totals = {}
    grand_total = 0.0  # independent of country_totals -- see BRIEF #5's assert_country_reconciles
    gm_num = gm_den = 0.0
    d2c_gm_num = d2c_gm_den = b2b_gm_num = b2b_gm_den = 0.0
    unmatched_ab = 0.0

    for line in enriched:
        ab = line_ab(line["net_of_discount"], line["tax"], line["returns_inc_vat"],
                     line["tax_returned"], line["fx_rate"])
        grand_total += ab
        if not line["enriched"]:
            unmatched_ab += ab

        bucket = country_bucket(line["ship_country_code"], line["store_label"])
        country_totals[bucket] += ab
        units_totals[bucket] += line["units"]

        chan = channel_from_company(line["company"])
        channel_totals[chan] += ab
        channel_units[chan] += line["units"]

        sb = _status_bucket(line)
        if sb is not None:
            status_totals[sb]["sales"] += ab
            status_totals[sb]["units"] += line["units"]

        dept = dept_totals.setdefault(line["department"], {
            "sales": 0.0, "units": 0, "gm_num": 0.0, "gm_den": 0.0, "subcats": {},
            "d2c": 0.0, "b2b": 0.0, "uk": 0.0, "us": 0.0,
        })
        dept["sales"] += ab
        dept["units"] += line["units"]
        dept["d2c" if chan == "D2C" else "b2b"] += ab
        if bucket == "UK":
            dept["uk"] += ab
        elif bucket == "US":
            dept["us"] += ab
        # item_type is Line Detail's "Product Category" grain -- the same
        # subcategory breakdown the oracle's Product Type table carries
        # (BRIEF #4 step 4 §2/§6). Unenriched lines fall into "Unknown",
        # same convention as department itself.
        subcat = dept["subcats"].setdefault(line.get("item_type") or "Unknown", {"sales": 0.0, "units": 0})
        subcat["sales"] += ab
        subcat["units"] += line["units"]

        if line["gm_pct"] is not None:
            gm_num += ab * line["gm_pct"]
            gm_den += ab
            if sb is not None:
                status_totals[sb]["gm_num"] += ab * line["gm_pct"]
                status_totals[sb]["gm_den"] += ab
            dept["gm_num"] += ab * line["gm_pct"]
            dept["gm_den"] += ab
            if chan == "D2C":
                d2c_gm_num += ab * line["gm_pct"]
                d2c_gm_den += ab
            else:
                b2b_gm_num += ab * line["gm_pct"]
                b2b_gm_den += ab

        if line["finish"]:
            f = finish_totals.setdefault(line["finish"], {
                "total": 0.0, "units": 0, "d2c": 0.0, "b2b": 0.0,
                "uk": 0.0, "us": 0.0, "uk_u": 0, "us_u": 0,
            })
            f["total"] += ab
            f["units"] += line["units"]
            f["d2c" if chan == "D2C" else "b2b"] += ab
            if bucket == "UK":
                f["uk"] += ab
                f["uk_u"] += line["units"]
            elif bucket == "US":
                f["us"] += ab
                f["us_u"] += line["units"]

        ck = (line["department"], line["collection"])
        c = coll_totals.setdefault(ck, {
            "ts": 0.0, "tu": 0, "d2c": 0.0, "b2b": 0.0, "uk_s": 0.0, "us_s": 0.0, "row_s": 0.0,
            "gm_num": 0.0, "gm_den": 0.0, "skus": set(),
        })
        c["ts"] += ab
        c["tu"] += line["units"]
        c["d2c" if chan == "D2C" else "b2b"] += ab
        c[{"UK": "uk_s", "US": "us_s", "ROW": "row_s"}[bucket]] += ab
        c["skus"].add(line["sku"])
        if line["gm_pct"] is not None:
            c["gm_num"] += ab * line["gm_pct"]
            c["gm_den"] += ab

        s = sku_totals.setdefault(line["sku"], {
            "gross": 0.0, "units": 0, "d2c": 0.0, "b2b": 0.0, "uk": 0.0, "uk_u": 0, "us": 0.0, "us_u": 0,
            "desc": line["description"], "coll": line["collection"], "type_": line["department"],
            "finish": line["finish"], "uk_status": line["status_uk"], "us_status": line["status_us"],
            "gm": line["gm_pct"], "is_el_component": line["is_el_component"],
            # T4b: split movers into Newness/Continuity sections -- a static
            # per-SKU catalog attribute (line_detail.py's own newness_bucket,
            # already used to build LIVE_STATUS_VALUES' per-line status
            # rollup), not something that varies line to line for one SKU.
            "newness_bucket": line["newness_bucket"],
        })
        s["gross"] += ab
        s["units"] += line["units"]
        s["d2c" if chan == "D2C" else "b2b"] += ab
        if bucket == "UK":
            s["uk"] += ab
            s["uk_u"] += line["units"]
        elif bucket == "US":
            s["us"] += ab
            s["us_u"] += line["units"]

    total_lines = len(enriched)
    enrichment_coverage = ((grand_total - unmatched_ab) / grand_total) if grand_total else 1.0

    # ── Gate (reused from BRIEF #5) -- non-raising here: a failure must
    # still WRITE the contract (stamped reconciled: False), never abort the
    # whole emission (BRIEF #3 §4).
    #
    # RELEASED 2026-08-05 (ROADMAP.md §5, RECONCILE_HANDOFF.md): reconciled/
    # can_publish() are gated ONLY on the structural leak check
    # (assert_country_reconciles -- uk+us+row must tie to an independently-
    # computed grand total; this is a property of the bucketing logic being
    # correct, not of matching a historical number). Matching the hand-built
    # oracle to 0.1% is NO LONGER a publishing requirement: the oracle
    # reflects an early, ~9-15-day-post-close snapshot of returns that keeps
    # maturing for weeks afterward, so a deterministic rebuild cannot
    # reproduce it exactly by design, not by defect. country_gaps_vs_oracle
    # is still computed/reported when an oracle target is available, purely
    # as historical context -- it must never be read as a pass/fail signal.
    oracle = oracle_gaps or (MAY_THREE_WAY if period == "2026-05" else None)
    country_gaps_vs_oracle = None
    try:
        assert_country_reconciles(country_totals, grand_total)
        reconciled = True
    except AssertionError as e:
        print(f"contract: leak check FAILED -- {e}", file=sys.stderr)
        reconciled = False
    if oracle is not None:
        computed = {"uk": country_totals["UK"], "us": country_totals["US"],
                    "row": country_totals["ROW"], "total": grand_total}
        country_gaps_vs_oracle = {
            k: round((computed[k] - oracle[k]) / oracle[k], 4) if oracle.get(k) else None
            for k in computed
        }

    # ── lm/ly: contract-chain when given, else bootstrap once from the oracle ──
    ly_dept_sales = {}  # department name -> LY sales, for prod_types' vs_ly (§6)
    # (department, collection) -> that collection's own LQ row from the oracle
    # bootstrap file, when one is given. Any oracle workbook already carries
    # real prior-period collection figures in its own LQ columns (extract.py's
    # extract_collections reads them unconditionally) -- this is genuinely
    # better than trying to chain a prior month's own Matrixify contract,
    # since it works even for the very first month built (no prior Matrixify
    # contract needed at all). Populated only in the oracle_bootstrap branch
    # below; stays empty in the contract_chain/none_available branches, so the
    # collections block's LQ fields fall back to their honest 0.0/None
    # placeholder in that case.
    oracle_collections_by_key = {}
    # department name -> reconstructed LQ sales (T2b), from the oracle
    # bootstrap file's own real vs_lq RATIO (extract_product_types reads it
    # straight from the sheet) -- the oracle table has no absolute prior-
    # period column for departments, only this ratio, so the absolute
    # figure is reconstructed as sales/(1+vs_lq), same technique quarterly.
    # py's _recompute_yoy already uses for the analogous vs_ly-ratio case.
    oracle_prod_type_lq = {}
    # SKU code -> reconstructed LQ sales (found post-review, per-SKU vslq
    # was hardcoded None throughout -- same reconstruction technique as
    # oracle_prod_type_lq, just matched by exact SKU code instead of a
    # (department, collection) name pair, which should be a cleaner match
    # than either of those since a SKU code is an exact identifier, not a
    # display name that can drift between the two sources.
    oracle_sku_lq = {}
    if lm_contract is not None and ly_contract is not None:
        lm_c = lm_contract if isinstance(lm_contract, dict) else load_contract(lm_contract)
        ly_c = ly_contract if isinstance(ly_contract, dict) else load_contract(ly_contract)
        lm = _current_to_lm_shape(lm_c["current"])
        ly = _current_to_lm_shape(ly_c["current"])
        period_model = {"cm": _period_label(period), "lm": lm_c["period_model"]["cm"], "ly": ly_c["period_model"]["cm"]}
        lq_ly_source = "contract_chain"
        ly_dept_sales = {t["t"]: t["sales"] for t in ly_c.get("prod_types", [])}
    elif oracle_bootstrap_path is not None:
        oracle_headline = extract_all(oracle_bootstrap_path)
        lm, ly = oracle_headline["lm"], oracle_headline["ly"]
        period_model = oracle_headline["period_model"]
        lq_ly_source = "oracle_bootstrap"
        oracle_collections_by_key = {
            (c["t"], c["c"]): c for c in oracle_headline.get("collections", [])
        }
        for t in oracle_headline.get("prod_types", []):
            vs_lq = t.get("vs_lq")
            if isinstance(vs_lq, (int, float)) and abs(1 + vs_lq) > 1e-9 and t.get("sales"):
                oracle_prod_type_lq[t["t"]] = t["sales"] / (1 + vs_lq)
        for sk in oracle_headline.get("skus_all", []):
            vslq = sk.get("vslq")
            if isinstance(vslq, (int, float)) and abs(1 + vslq) > 1e-9 and sk.get("gross"):
                oracle_sku_lq[sk["sku"]] = sk["gross"] / (1 + vslq)
    else:
        lm = ly = _current_to_lm_shape({
            "total_sales": 0, "d2c_gbp": 0, "b2b_gbp": 0, "uk_gbp": 0, "us_gbp": 0, "row_gbp": 0,
            "units": 0, "d2c_units": 0, "b2b_units": 0, "uk_units": 0, "us_units": 0, "row_units": 0,
        })
        period_model = {"cm": _period_label(period), "lm": _period_label(period), "ly": _period_label(period)}
        lq_ly_source = "none_available"

    # B3 (round-2 review): independent department-level LY backfill from a
    # real prior-year same-month Matrixify contract -- composes with
    # oracle_bootstrap_path above (which gives headline LY but not
    # department-grain LY) rather than requiring it. Only applied if the
    # branch above left ly_dept_sales empty, so an explicit contract_chain
    # (which already carries real ly_dept_sales) is never overridden.
    ly_dept_unclassified_share = None
    if ly_month_contract is not None and not ly_dept_sales:
        ly_month_c = ly_month_contract if isinstance(ly_month_contract, dict) else load_contract(ly_month_contract)
        ly_dept_sales = {t["t"]: t["sales"] for t in ly_month_c.get("prod_types", [])}
        ly_month_total = ly_month_c.get("current", {}).get("total_sales")
        ly_month_unknown = ly_dept_sales.get("Unknown")
        if ly_month_total:
            ly_dept_unclassified_share = round((ly_month_unknown or 0) / ly_month_total, 4)

    current = {
        "total_sales": grand_total,
        "units": sum(units_totals.values()),
        "gm_pct": (gm_num / gm_den) if gm_den else None,
        "d2c_gbp": channel_totals["D2C"], "b2b_gbp": channel_totals["B2B"],
        "uk_gbp": country_totals["UK"], "us_gbp": country_totals["US"], "row_gbp": country_totals["ROW"],
        "d2c_units": channel_units["D2C"], "b2b_units": channel_units["B2B"],
        "uk_units": units_totals["UK"], "us_units": units_totals["US"], "row_units": units_totals["ROW"],
        "d2c_gm": (d2c_gm_num / d2c_gm_den) if d2c_gm_den else None,
        "b2b_gm": (b2b_gm_num / b2b_gm_den) if b2b_gm_den else None,
    }
    current["vs_lm"] = _vs(current["total_sales"], lm["total"])
    current["vs_ly"] = _vs(current["total_sales"], ly["total"])
    current["units_vs_lm"] = _vs(current["units"], lm["total_u"])
    current["units_vs_ly"] = _vs(current["units"], ly["total_u"])
    current["uk_vs_lm"] = _vs(current["uk_gbp"], lm["uk"])
    current["uk_vs_ly"] = _vs(current["uk_gbp"], ly["uk"])
    current["us_vs_lm"] = _vs(current["us_gbp"], lm["us"])
    current["us_vs_ly"] = _vs(current["us_gbp"], ly["us"])
    _add_headline_kpis(current)

    # T1 (trading review round 1): a genuine trailing-3-consecutive-months
    # revenue trend for the headline KPI's arrows -- distinct from the
    # existing MoM ribbon's LY/LM/CM trajectory (a year-ago point plus one
    # trailing month, not 3 consecutive months). `lm` above is real (this
    # month's own LM), so the only missing point is M-2; prior_month_
    # contract's own "lm" block IS M-2 (it's M-1's own LM), already real if
    # that prior contract itself came from an oracle bootstrap or a real
    # chain -- no new data fetch needed, just reading one field off a
    # contract the caller already has on disk. None (not fabricated) when
    # no prior month's contract is available, e.g. the earliest month this
    # repo has Matrixify exports for.
    current["trend_3mo"] = None
    # CA2 (round-3 review): per-department UK/US vs-LM, keyed off the same
    # prior_month_contract T1 already threads through for trend_3mo -- its
    # own prod_types carry real absolute uk/us per department (computed
    # fresh from line-level data every month, not a reconstructed ratio),
    # so this is a genuine month-over-month comparison, not a guess. None
    # per department when prior_month_contract is absent (April, the first
    # month with no prior contract at all) or that department didn't exist
    # last month -- never fabricated.
    pmc_dept_uk_us = {}
    if prior_month_contract is not None:
        pmc = prior_month_contract if isinstance(prior_month_contract, dict) else load_contract(prior_month_contract)
        mm2_total = pmc.get("lm", {}).get("total")
        if mm2_total is not None:
            current["trend_3mo"] = [round(mm2_total, 2), round(lm["total"], 2), round(current["total_sales"], 2)]
        pmc_dept_uk_us = {t["t"]: (t.get("uk"), t.get("us")) for t in pmc.get("prod_types", [])}

    statuses = [{
        "s": b, "sales": v["sales"], "units": v["units"], "vs_lq": None, "vs_ly": None,
        "gm": (v["gm_num"] / v["gm_den"]) if v["gm_den"] else None,
    } for b, v in status_totals.items()]

    prod_types = []
    for dept, v in dept_totals.items():
        lq_sales = oracle_prod_type_lq.get(dept)
        pmc_uk, pmc_us = pmc_dept_uk_us.get(dept, (None, None))
        prod_types.append({
            "t": dept, "sales": v["sales"], "units": v["units"],
            "vs_lq": _vs(v["sales"], lq_sales) if lq_sales is not None else None,
            "vs_ly": _vs(v["sales"], ly_dept_sales.get(dept)) if ly_dept_sales.get(dept) else None,
            "gm": (v["gm_num"] / v["gm_den"]) if v["gm_den"] else None,
            # T2b: per-department channel/country split (Channel/Country
            # toggle views) -- real for the Matrixify path (line-level data);
            # the oracle path has no such column, so these are always 0.0
            # there, same "genuinely unavailable at this grain" limitation
            # as T2b's LQ note below.
            "d2c": v["d2c"], "b2b": v["b2b"], "uk": v["uk"], "us": v["us"],
            # CA2: per-market vs-LM, see pmc_dept_uk_us above.
            "uk_vs_lq": _vs(v["uk"], pmc_uk) if pmc_uk is not None else None,
            "us_vs_lq": _vs(v["us"], pmc_us) if pmc_us is not None else None,
            # T2b: LQ ghost-bar value -- real once oracle_prod_type_lq has
            # this department (reconstructed from the oracle bootstrap's own
            # vs_lq ratio, see above); None otherwise, never fabricated.
            "lq_sales": round(lq_sales, 2) if lq_sales is not None else None,
            "subcats": [
                {"name": name, "sales": sc["sales"], "units": sc["units"], "vs_ly": None}
                for name, sc in v["subcats"].items()
            ],
        })

    # Every finish that had any revenue this month renders -- BRIEF #4 step 4
    # §5/§10 retires the previous curated-8 (config.FINISH_COLORS) filter;
    # colour assignment is now by rank (config.finish_color), not by a fixed
    # name lookup, so an arbitrary-length finish list always gets a colour.
    finishes = {
        name: {
            "total": v["total"], "units": v["units"], "vsLQ": None, "vsLY": None,
            "d2c": v["d2c"], "b2b": v["b2b"], "uk": v["uk"], "us": v["us"],
            # T3b: units-by-country, needed for a genuine Units x UK/US
            # toggle at finish grain (cash-by-country already existed).
            "uk_u": v["uk_u"], "us_u": v["us_u"],
        } for name, v in finish_totals.items()
    }

    collections_sorted = sorted(coll_totals.items(), key=lambda kv: -kv[1]["ts"])
    collections = []
    for i, ((dept, coll), v) in enumerate(collections_sorted):
        oracle_row = oracle_collections_by_key.get((dept, coll))
        lq_total = oracle_row["lq_total"] if oracle_row else 0.0
        lq_uk = oracle_row["lq_uk"] if oracle_row else 0.0
        lq_us = oracle_row["lq_us"] if oracle_row else 0.0
        collections.append({
            "r": i + 1, "t": dept, "c": coll, "ts": v["ts"], "tu": v["tu"],
            "vs_lq": _vs(v["ts"], lq_total) if oracle_row else None,
            "gm": (v["gm_num"] / v["gm_den"]) if v["gm_den"] else None,
            "d2c": v["d2c"], "b2b": v["b2b"], "uk_s": v["uk_s"], "us_s": v["us_s"], "row_s": v["row_s"],
            "lq_total": lq_total, "lq_uk": lq_uk, "lq_us": lq_us,
            "uk_vs": _vs(v["uk_s"], lq_uk) if oracle_row else None,
            "us_vs": _vs(v["us_s"], lq_us) if oracle_row else None,
            "skus": len(v["skus"]),
        })
    if oracle_collections_by_key:
        matched = sum(1 for c in collections if c["lq_total"] != 0.0)
        print(f"contract: {matched}/{len(collections)} collections matched the oracle bootstrap's "
              f"own LQ columns by (department, collection) name -- unmatched ones get lq_total=0.0/"
              f"vs_lq=None (a new/renamed collection this period, or a genuine name mismatch "
              f"between the oracle's and Matrixify's department classification), never fabricated.",
              file=sys.stderr)

    skus_all = []
    for sku, v in sku_totals.items():
        lq_sales = oracle_sku_lq.get(sku)
        skus_all.append({
            "rank": None, "sku": sku, "desc": v["desc"] or sku, "coll": v["coll"], "type_": v["type_"],
            "finish": v["finish"] or "", "uk_status": v["uk_status"] or "", "us_status": v["us_status"] or "",
            "gross": v["gross"], "units": v["units"],
            # Per-SKU vs-LM (2026-08-05, follow-up to T4a/T4b): reconstructed
            # from the oracle bootstrap's own real per-SKU vslq ratio when a
            # match exists by exact SKU code; None otherwise (a genuinely new
            # SKU this period, or no oracle bootstrap given at all) -- never
            # fabricated. This is what feeds Movers and the SKU Performance
            # tables' vs-LM columns, previously always None on this path.
            "vslq": _vs(v["gross"], lq_sales) if lq_sales is not None else None,
            "gm": v["gm"],
            "d2c": v["d2c"], "b2b": v["b2b"], "uk": v["uk"], "uk_u": v["uk_u"],
            "us": v["us"], "us_u": v["us_u"],
            "lq": round(lq_sales, 2) if lq_sales is not None else None, "ly": None,
            "is_el_component": v["is_el_component"], "newness_bucket": v["newness_bucket"],
        })
    if oracle_sku_lq:
        matched = sum(1 for s in skus_all if s["vslq"] is not None)
        print(f"contract: {matched}/{len(skus_all)} SKUs matched the oracle bootstrap's own "
              f"per-SKU vs-LM ratio by exact SKU code -- unmatched ones get vslq=None/lq=None "
              f"(a genuinely new SKU this period, or one the oracle sheet doesn't carry), "
              f"never fabricated.", file=sys.stderr)

    payload = {
        "mode": "month",
        "period_model": period_model,
        "current": current, "lm": lm, "ly": ly,
        "statuses": statuses, "prod_types": prod_types, "finishes": finishes,
        "collections": collections, "skus_all": skus_all,
    }
    _strip_vestigial(payload)
    _exclude_dead_categories(payload)

    fx_date = f"{period}-01"
    fx_rows = {}
    try:
        with open(FX_RATES_PATH) as f:
            import csv as _csv
            fx_rows = {r["date"]: r for r in _csv.DictReader(f)}
    except FileNotFoundError:
        pass
    fx_table = f"frozen:{fx_date}" if fx_date in fx_rows else None

    provenance = {
        "source": "matrixify",
        "reconciled": reconciled,
        "revenue_basis": "net_sales_exvat_AB",
        "returns_netted": True,
        "fx_table": fx_table,
        "enrichment_coverage": round(enrichment_coverage, 4),
        "unmatched_sku_revenue_share": round(1 - enrichment_coverage, 4),
        "country_gaps_vs_oracle": country_gaps_vs_oracle,
        "lq_ly_source": lq_ly_source,
        "ly_dept_unclassified_share": ly_dept_unclassified_share,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git_commit(),
    }

    contract = _wrap_contract(payload, provenance)
    if out_path:
        with open(out_path, "w") as f:
            json.dump(contract, f, indent=2, default=str)
    return contract
