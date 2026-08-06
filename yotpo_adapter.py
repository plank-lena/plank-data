"""
yotpo_adapter.py — validate and land a manually-exported Yotpo reviews CSV
into source/reviews.csv, in the exact shape review_feedback.py reads.

Workflow: someone periodically exports reviews from Yotpo's dashboard and
drops the CSV here; this script validates it and lands a clean, diffable
snapshot. No live API calls, no OAuth, no Matrixify product join — the
export already carries Product SKU / Market / Product Type per row.

(An earlier version of this script pulled reviews live via Yotpo's OAuth2
API. That path is gone: a real pull returned zero deleted/escalated reviews
across the full account history despite those fields existing on the review
object, which never squared with the domain fact that ~3% of reviews are
deleted and carry 100% of the explicit product criticism. Rather than debug
an API/permissions mismatch, the ingestion reverts to the manual export this
replaced.)

Design contract: validate (required columns present, labels whitespace-
stripped) -> assert (fail loud on a suspiciously clean pull) -> land CSV.
Nothing here computes anything — the landed CSV is a faithful snapshot.
"""

from __future__ import annotations
import argparse, csv, os, sys

# review_feedback.py's classify()/scan_reviews() read these exact column names:
REQUIRED_COLUMNS = [
    "ID", "Title", "Content", "Score", "Created At", "Market",
    "Deleted", "Escalated", "Product SKU", "Product Title", "Product Type",
]


def _clean(v):
    return v.strip() if isinstance(v, str) else ("" if v is None else v)


def load_rows(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [(h or "").strip() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            sys.exit(f"ABORT: export is missing required columns: {missing}")
        # whitespace-sensitive labels are a known gotcha in this repo (see
        # CLAUDE.md) -- strip every header and cell value on the way in.
        rows = [
            {(k or "").strip(): _clean(v) for k, v in r.items() if k is not None}
            for r in reader
        ]
    return rows


def assert_ok(rows: list[dict], allow_empty_flags: bool) -> None:
    if not rows:
        sys.exit("ABORT: zero reviews in export — check the file.")
    deleted   = sum(1 for r in rows if r.get("Deleted", "").upper() == "TRUE")
    escalated = sum(1 for r in rows if r.get("Escalated", "").upper() == "TRUE")
    print(f"  rows={len(rows)}  deleted={deleted}  escalated={escalated}")
    if (deleted == 0 or escalated == 0) and not allow_empty_flags:
        sys.exit(
            "ABORT: no deleted/escalated reviews in this export.\n"
            "  ~3% of reviews are typically deleted, and that is where 100% of\n"
            "  the explicit product criticism lives. Zero almost certainly means\n"
            "  a stripped or filtered export — re-export from Yotpo, or pass\n"
            "  --allow-empty-flags if that is truly correct this time.")


def land(rows: list[dict], path: str) -> None:
    extra  = [c for c in rows[0] if c not in REQUIRED_COLUMNS]
    header = REQUIRED_COLUMNS + extra
    rows   = sorted(rows, key=lambda r: str(r.get("ID", "")))   # stable git diff
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  landed -> {path}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Validate + land a manually-exported Yotpo reviews CSV into source/.")
    ap.add_argument("export", help="Path to the CSV exported from Yotpo's dashboard.")
    ap.add_argument("--out", default="source/reviews.csv", help="Landed CSV path.")
    ap.add_argument("--allow-empty-flags", action="store_true",
                    help="Permit an export with no deleted/escalated reviews (normally an error).")
    args = ap.parse_args()

    print("validate...")
    rows = load_rows(args.export)
    print("assert...")
    assert_ok(rows, args.allow_empty_flags)
    print("land...")
    land(rows, args.out)
    print("\ndone. next:")
    print(f"  python review_feedback.py {args.out} --outdir out/ --line-detail <path to Line Detail file>")


if __name__ == "__main__":
    main()
