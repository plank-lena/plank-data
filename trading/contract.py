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
    up into the same shape. Stamps reconciled: True only if the gate
    (common/reconciliation_gate, reused from #5) actually passes; on
    failure the contract is still WRITTEN (never silently dropped) but
    stamped reconciled: False with country_gaps_vs_oracle populated, and
    can_publish() below returns False.

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
    "&#9888; PROVISIONAL / UNRECONCILED &mdash; Matrixify-sourced figures have not "
    "passed the reconciliation gate. Do not publish. See country_gaps_vs_oracle "
    "in the contract for the open gap.</div>"
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
        compute_skus, compute_newness_skus, compute_collections,
        compute_finish_data, compute_coll_analysis, compute_kpi_tokens, compute_ribbon_tokens,
        compute_category_top_collections, compute_movers, compute_matrix,
        js_block_periods, js_block_collections, js_block_statuses, js_block_prod_types,
        js_block_skus, js_block_finish_data, js_block_coll_analysis,
        js_block_newness_skus,
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
    newness_data = compute_newness_skus(raw["skus_all"])
    coll_data = compute_collections(raw["collections"])
    finish_data = compute_finish_data(raw["finishes"], raw["skus_all"])
    coll_analysis = compute_coll_analysis(raw["collections"], raw["skus_all"])
    kpi_tokens = compute_kpi_tokens(raw["current"], raw["lm"], pm, mode=mode)
    ribbon_tokens = compute_ribbon_tokens(raw["current"], raw["lm"], raw["ly"], pm, mode=mode)
    cat_top_collections = compute_category_top_collections(coll_data)
    movers = compute_movers(raw["skus_all"])
    matrix = compute_matrix(raw["collections"])

    tokens = build_token_dict(
        js_block_periods(periods_data), js_block_collections(coll_data),
        js_block_statuses(statuses_data), js_block_prod_types(types_data),
        js_block_skus(skus_data), js_block_finish_data(finish_data), total_sales,
        js_block_coll_analysis(coll_analysis), js_block_newness_skus(newness_data),
        kpi_tokens, ribbon_tokens,
        js_block_cat_top_collections(cat_top_collections), js_block_movers(movers), js_block_matrix(matrix),
    )
    tokens["PROVISIONAL_BANNER"] = "" if can_publish(contract) else PROVISIONAL_BANNER_HTML
    html = fill_template(template_html, tokens)
    return html


# ── Matrixify front-end ──────────────────────────────────────────────────────

_MONTH_NAMES = ("January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December")


def _period_label(period_str):
    """'2026-05' -> {'label': 'May 2026', 'short': "May '26"}."""
    year, month = period_str.split("-")
    name = _MONTH_NAMES[int(month) - 1]
    return {"label": f"{name} {year}", "short": f"{name[:3]} '{year[2:]}"}


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
                                  oracle_gaps=None, out_path=None):
    """The real builder: BRIEF #5's ship-to reconcile + BRIEF #2's Line
    Detail enrichment, rolled up into extract_all()'s exact payload shape.

    lm_contract / ly_contract: a loaded prior contract dict (or path) for
    the previous month / same month last year, per §5 -- when given, lm/ly
    come from these (provenance.lq_ly_source: "contract_chain").
    oracle_bootstrap_path: when no prior contract exists yet (true today --
    this is the FIRST committed contract), fall back ONCE to this oracle
    xlsx's own lm/ly blocks (provenance.lq_ly_source: "oracle_bootstrap").
    oracle_gaps: {"uk":..,"us":..,"row":..,"total":..} ground truth to
    diff against for provenance.country_gaps_vs_oracle + the gate: defaults
    to BRIEF #5's MAY_THREE_WAY when period == "2026-05" (the only period
    with a committed oracle right now), else None (gate/gaps skipped,
    reconciled left False -- no ground truth to reconcile against yet).

    Never fabricates: st/wc/inv are None throughout (no inventory feed
    wired -- BRIEF #3 §6/§9); unmatched SKUs roll into department/
    collection "Unknown", never dropped, so totals still tie.
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
    enriched = enrich_lines(all_lines, ld_index)

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
        })
        dept["sales"] += ab
        dept["units"] += line["units"]
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
                "total": 0.0, "units": 0, "d2c": 0.0, "b2b": 0.0, "uk": 0.0, "us": 0.0,
            })
            f["total"] += ab
            f["units"] += line["units"]
            f["d2c" if chan == "D2C" else "b2b"] += ab
            if bucket == "UK":
                f["uk"] += ab
            elif bucket == "US":
                f["us"] += ab

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
    # whole emission (BRIEF #3 §4). The CLI-facing build_matrixify.py path
    # keeps its own raising gate_check_combined() for its own use.
    oracle = oracle_gaps or (MAY_THREE_WAY if period == "2026-05" else None)
    reconciled = False
    country_gaps_vs_oracle = None
    if oracle is not None:
        try:
            assert_country_reconciles(country_totals, grand_total)
            computed = {"uk": country_totals["UK"], "us": country_totals["US"],
                        "row": country_totals["ROW"], "total": grand_total}
            country_gaps_vs_oracle = {
                k: round((computed[k] - oracle[k]) / oracle[k], 4) for k in computed
            }
            reconciled = all(abs(g) <= 0.001 for g in country_gaps_vs_oracle.values())
        except AssertionError as e:
            print(f"contract: gate FAILED -- {e}", file=sys.stderr)
            computed = {"uk": country_totals["UK"], "us": country_totals["US"],
                        "row": country_totals["ROW"], "total": grand_total}
            country_gaps_vs_oracle = {
                k: round((computed[k] - oracle[k]) / oracle[k], 4) if oracle.get(k) else None
                for k in computed
            }

    # ── lm/ly: contract-chain when given, else bootstrap once from the oracle ──
    ly_dept_sales = {}  # department name -> LY sales, for prod_types' vs_ly (§6)
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
    else:
        lm = ly = _current_to_lm_shape({
            "total_sales": 0, "d2c_gbp": 0, "b2b_gbp": 0, "uk_gbp": 0, "us_gbp": 0, "row_gbp": 0,
            "units": 0, "d2c_units": 0, "b2b_units": 0, "uk_units": 0, "us_units": 0, "row_units": 0,
        })
        period_model = {"cm": _period_label(period), "lm": _period_label(period), "ly": _period_label(period)}
        lq_ly_source = "none_available"

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

    statuses = [{
        "s": b, "sales": v["sales"], "units": v["units"], "vs_lq": None, "vs_ly": None,
        "gm": (v["gm_num"] / v["gm_den"]) if v["gm_den"] else None,
    } for b, v in status_totals.items()]

    prod_types = [{
        "t": dept, "sales": v["sales"], "units": v["units"], "vs_lq": None,
        "vs_ly": _vs(v["sales"], ly_dept_sales.get(dept)) if ly_dept_sales.get(dept) else None,
        "gm": (v["gm_num"] / v["gm_den"]) if v["gm_den"] else None,
        "subcats": [
            {"name": name, "sales": sc["sales"], "units": sc["units"], "vs_ly": None}
            for name, sc in v["subcats"].items()
        ],
    } for dept, v in dept_totals.items()]

    # Every finish that had any revenue this month renders -- BRIEF #4 step 4
    # §5/§10 retires the previous curated-8 (config.FINISH_COLORS) filter;
    # colour assignment is now by rank (config.finish_color), not by a fixed
    # name lookup, so an arbitrary-length finish list always gets a colour.
    finishes = {
        name: {
            "total": v["total"], "units": v["units"], "vsLQ": None, "vsLY": None,
            "d2c": v["d2c"], "b2b": v["b2b"], "uk": v["uk"], "us": v["us"],
        } for name, v in finish_totals.items()
    }

    collections_sorted = sorted(coll_totals.items(), key=lambda kv: -kv[1]["ts"])
    collections = [{
        "r": i + 1, "t": dept, "c": coll, "ts": v["ts"], "tu": v["tu"], "vs_lq": None,
        "gm": (v["gm_num"] / v["gm_den"]) if v["gm_den"] else None,
        "d2c": v["d2c"], "b2b": v["b2b"], "uk_s": v["uk_s"], "us_s": v["us_s"], "row_s": v["row_s"],
        "lq_total": 0.0, "lq_uk": 0.0, "lq_us": 0.0, "uk_vs": None, "us_vs": None,
        "skus": len(v["skus"]),
    } for i, ((dept, coll), v) in enumerate(collections_sorted)]

    skus_all = [{
        "rank": None, "sku": sku, "desc": v["desc"] or sku, "coll": v["coll"], "type_": v["type_"],
        "finish": v["finish"] or "", "uk_status": v["uk_status"] or "", "us_status": v["us_status"] or "",
        "gross": v["gross"], "units": v["units"], "vslq": None, "gm": v["gm"],
        "d2c": v["d2c"], "b2b": v["b2b"], "uk": v["uk"], "uk_u": v["uk_u"],
        "us": v["us"], "us_u": v["us_u"], "lq": None, "ly": None,
        "is_el_component": v["is_el_component"],
    } for sku, v in sku_totals.items()]

    payload = {
        "mode": "month",
        "period_model": period_model,
        "current": current, "lm": lm, "ly": ly,
        "statuses": statuses, "prod_types": prod_types, "finishes": finishes,
        "collections": collections, "skus_all": skus_all,
    }
    _strip_vestigial(payload)

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
        "built_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git_commit(),
    }

    contract = _wrap_contract(payload, provenance)
    if out_path:
        with open(out_path, "w") as f:
            json.dump(contract, f, indent=2, default=str)
    return contract
