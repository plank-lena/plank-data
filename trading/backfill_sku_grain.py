"""Back-fill SKU-grain cuts into an already-committed contract (1.0 -> 1.1).

    python trading/backfill_sku_grain.py "April 2026"
    python trading/backfill_sku_grain.py "April 2026" --write
    python trading/backfill_sku_grain.py "April 2026" --write --allow 12

Why this exists (2026-08-13, Lena: "back fill, don't leave things blank")
------------------------------------------------------------------------
The By-SKU companion tab was extended to match the hand-built Monthly
Trading Report's own By-SKU tab, which carries a channel x country cross and
a realised GM per cut. Contracts committed before 2026-08-13 are version 1.0
and don't hold those cuts, so April/May/June 2026 would render an incomplete
tab.

**The obvious shortcut is wrong and this script deliberately doesn't take
it.** The hand-built reports for those exact months sit in
trading/tests/fixtures/ and carry every one of those columns -- but they are
on a GROSS basis while this pipeline is NET, and they diverge from our own
per-SKU figures bidirectionally (median 2.2% for June, 5.6% for April; the
aggregate gap tracks in-window returns: Apr -7.4%, Jun -4.1%). Importing
those splits would put a UK+US+ROW breakdown on a row whose revenue figure
was computed a different way, so no share or margin on that row would be
true. Numbers here are re-derived from the same Matrixify order lines and
the same line_ab as the committed figures, or they aren't written.

What it does
------------
1. Reads the committed contract for the period (refuses to run if absent).
2. Re-derives the period from its order lines, exactly as the original build
   did -- same loader, same FX table, same Line Detail, same dead-department
   exclusion.
3. **Tie gate.** For every SKU, the re-derived revenue and units must match
   the committed ones within TOLERANCE. A mismatch means the inputs have
   moved since publication (typically a return that matured after the month
   was committed), and the cuts would not describe the published row. Those
   SKUs are reported and, by default, the run aborts. `--allow N` permits up
   to N mismatched SKUs, which are then written as `cuts: null` on that row
   and named in `provenance.sku_cuts_backfill.unmatched` -- an honest gap,
   never a scaled or apportioned guess.
4. Merges ONLY the new keys (row/row_u, item_type, material, style, is_kit,
   supplier_cost, cuts) into each existing skus_all entry. Every pre-existing
   key is left byte-identical, including gross, units, gm, lq, ly and the
   whole headline/current/lm/ly/statuses/prod_types/collections structure.
   The merge asserts this rather than trusting it.
5. Without --write, prints the diff summary and writes nothing. With
   --write, rewrites the contract in place and stamps provenance.

This is the one sanctioned way to touch a committed contract, and it is
sanctioned precisely because it cannot change a published number: the tie
gate fails the run instead.
"""
import argparse
import copy
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, ".."), os.path.join(_HERE, "dashboard")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common import sku_cuts
from common.period import parse_period  # period comes from the prompt, never a file header
from common.sources import matrixify_orders_snapshot, matrixify_orders_snapshot_covers
from contract import CONTRACT_VERSION, emit_contract_from_matrixify

CONTRACTS_DIR = os.path.join(_HERE, "contracts")

# A SKU whose re-derived revenue is within this of the committed figure is
# the same SKU on the same basis. Deliberately tight: this is the same
# computation over the same inputs, so a real match is exact to floating
# point and anything looser is a changed input, not rounding.
TOLERANCE = 0.005          # GBP, absolute
UNITS_TOLERANCE = 0        # units are integers; any difference is a real one

# Keys the back-fill is allowed to add. Anything else is a bug.
NEW_KEYS = ("row", "row_u", "item_type", "material", "style", "is_kit",
            "supplier_cost", "cuts")


def _committed_path(period_key):
    """Same convention as build_matrixify_dashboard.py -- one place, so a
    rename there doesn't leave this script writing to a ghost file."""
    return os.path.join(CONTRACTS_DIR, f"{period_key}-matrixify.json")


def _load_committed(period):
    path = _committed_path(period)
    if not os.path.exists(path):
        raise SystemExit(
            f"backfill: no committed contract at {path} -- nothing to back-fill. "
            f"If {period} has never been built, run the normal builder instead; it "
            f"emits {CONTRACT_VERSION} with cuts included."
        )
    with open(path, encoding="utf-8") as f:
        return path, json.load(f)


def _rederive(period):
    """Re-run the emitter for the period. Writes nothing: emit_contract_from_
    matrixify returns the payload, and the caller never calls the committer,
    so there is no path from this script to write_committed_file.

    Only the CM window matters here -- the back-fill touches skus_all and
    nothing else, so LM/LY bootstrap sources are deliberately not passed
    (they would only rebuild blocks this script then asserts are unchanged).
    """
    uk_csv = matrixify_orders_snapshot("uk")
    us_csv = matrixify_orders_snapshot("us")
    if not (matrixify_orders_snapshot_covers(uk_csv, period)
            or matrixify_orders_snapshot_covers(us_csv, period)):
        raise SystemExit(
            f"backfill: {period} is outside the order snapshot's window "
            f"({uk_csv} / {us_csv}), so it cannot be re-derived and its cuts cannot be "
            f"back-filled. Stage that period's archive export into the snapshot location "
            f"first (see docs on the Matrixify Drive bridge). Deriving the cuts from the "
            f"hand-built report instead is NOT an option -- that source is gross-basis and "
            f"diverges per SKU from what was published."
        )
    return emit_contract_from_matrixify(period=period, uk_csv=uk_csv, us_csv=us_csv)


def _tie_report(committed, fresh):
    """Per-SKU comparison of the committed figures against the re-derived
    ones. Returns (matched, unmatched, missing, extra).
    """
    C = {s["sku"]: s for s in committed["skus_all"]}
    F = {s["sku"]: s for s in fresh["skus_all"]}
    matched, unmatched = [], []
    for sku, cs in C.items():
        fs = F.get(sku)
        if fs is None:
            unmatched.append((sku, cs.get("gross"), None, "absent from re-derivation"))
            continue
        dg = abs((fs["gross"] or 0) - (cs["gross"] or 0))
        du = abs((fs["units"] or 0) - (cs["units"] or 0))
        if dg <= TOLERANCE and du <= UNITS_TOLERANCE:
            matched.append(sku)
        else:
            unmatched.append((sku, cs.get("gross"), fs.get("gross"),
                              f"revenue delta {dg:.4f}, units delta {du}"))
    extra = sorted(set(F) - set(C))
    return matched, unmatched, extra


def _merge(committed, fresh, unmatched_skus):
    """Additive merge. Returns a new payload; asserts no existing key moved."""
    out = copy.deepcopy(committed)
    F = {s["sku"]: s for s in fresh["skus_all"]}
    unmatched = set(unmatched_skus)

    for entry in out["skus_all"]:
        sku = entry["sku"]
        before = {k: v for k, v in entry.items()}
        src = F.get(sku)
        if src is None or sku in unmatched:
            # Honest gap: the row keeps every published figure and declares
            # that its cuts are unavailable. The companion writer prints the
            # reason, not an empty cell.
            entry["cuts"] = None
            for k in NEW_KEYS:
                entry.setdefault(k, None)
            continue
        for k in NEW_KEYS:
            entry[k] = src.get(k)
        for k, v in before.items():
            if k in NEW_KEYS:
                continue
            if entry[k] != v:
                raise AssertionError(
                    f"backfill: refusing to write -- merging {sku} changed existing "
                    f"key {k!r} from {v!r} to {entry[k]!r}. The back-fill is additive "
                    f"by contract; this is a bug in _merge, not a data condition."
                )

    # Every non-skus_all block must be untouched.
    for block in [k for k in committed if k != "skus_all"]:
        if block == "provenance":
            continue
        if out[block] != committed[block]:
            raise AssertionError(f"backfill: refusing to write -- block {block!r} changed")

    prov = out.setdefault("provenance", {})
    prov["sku_cuts_backfill"] = {
        "at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "from_version": committed.get("contract_version"),
        "to_version": CONTRACT_VERSION,
        "basis": "re-derived from the period's own Matrixify order lines "
                 "(same line_ab as the committed figures); NOT from the "
                 "hand-built report, which is a gross-basis source",
        "skus_backfilled": sum(1 for s in out["skus_all"] if s.get("cuts")),
        "unmatched": sorted(unmatched),
        "gated_figures_changed": False,
    }
    out["contract_version"] = CONTRACT_VERSION
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("period", help='e.g. "April 2026" -- from the prompt, not a file header')
    ap.add_argument("--write", action="store_true",
                    help="rewrite the committed contract in place (default: dry run)")
    ap.add_argument("--allow", type=int, default=0, metavar="N",
                    help="tolerate up to N SKUs that no longer tie; they get cuts=null")
    args = ap.parse_args(argv)

    period = parse_period(args.period)["cm"]["key"]
    path, committed = _load_committed(period)
    print(f"backfill: committed contract {path} (version {committed.get('contract_version')}, "
          f"{len(committed['skus_all'])} SKUs)")

    if committed.get("contract_version") == CONTRACT_VERSION and \
            all(s.get("cuts") for s in committed["skus_all"]):
        print(f"backfill: {period} already carries SKU cuts for every SKU -- nothing to do.")
        return 0

    fresh = _rederive(period)
    matched, unmatched, extra = _tie_report(committed, fresh)

    print(f"backfill: tie check -- {len(matched)} SKUs match the committed figures exactly, "
          f"{len(unmatched)} do not, {len(extra)} SKU(s) present only in the re-derivation")
    for sku, cg, fg, why in unmatched[:20]:
        print(f"    {sku}: committed={cg} re-derived={fg} ({why})")
    if len(unmatched) > 20:
        print(f"    ... and {len(unmatched) - 20} more")
    if extra:
        print(f"backfill: NOTE -- {len(extra)} SKU(s) appear in the re-derivation but not the "
              f"committed contract; they are NOT added (that would change the published "
              f"population): {extra[:10]}")

    if len(unmatched) > args.allow:
        raise SystemExit(
            f"\nbackfill: ABORTED. {len(unmatched)} SKU(s) no longer tie to the committed "
            f"contract, above the --allow {args.allow} threshold. The period's inputs have "
            f"moved since it was published (most likely returns that matured afterwards), so "
            f"cuts derived now would not describe the published rows. Either raise --allow to "
            f"accept those rows as declared gaps, or decide with Lena whether {period} should "
            f"be re-published outright -- that is a --force rebuild, not a back-fill."
        )

    merged = _merge(committed, fresh, [u[0] for u in unmatched])
    filled = merged["provenance"]["sku_cuts_backfill"]["skus_backfilled"]
    print(f"backfill: merged cuts onto {filled}/{len(merged['skus_all'])} SKUs; "
          f"every pre-existing key verified unchanged")

    if not args.write:
        print("backfill: DRY RUN -- nothing written. Re-run with --write to commit.")
        return 0

    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=1, sort_keys=False)
    print(f"backfill: wrote {path} at version {CONTRACT_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
