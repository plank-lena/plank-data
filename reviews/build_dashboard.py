"""Refresh the reviews artifacts (reviews.json + the four theme/quality CSVs)
-- the reviews twin of trading/build_matrixify_dashboard.py and
returns/build_dashboard.py, but not period-driven: reviews follow the
REGION filter only, never trading/returns' period-from-prompt (confirmed in
common/sources.check_yotpo_freshness's own docstring), and
review_feedback.scan_reviews() already processes the whole history in one
pass, breaking it out by month itself (themes_by_month.csv) rather than
needing to be told which month to run.

There is no standalone "reviews dashboard" HTML -- reviews.json is embedded
into the RETURNS dashboard's own review panel (returns/render.py's
DEFAULT_REVIEWS_JSON reads this file directly). Refresh reviews here, then
the next returns build picks it up automatically -- no wiring needed there,
same as build_matrixify_dashboard.py and returns/build_dashboard.py already
don't need to know about each other.

Source-agnostic by design: reads whatever's already landed at
common.sources.YOTPO_REVIEWS_SNAPSHOT, regardless of which normalizer put it
there -- normalize_yotpo_reviews_xlsx() (the Drive/Google-Sheet path) or
normalize_yotpo_reviews_from_csv() (a raw Yotpo export uploaded directly,
for when the Sheet's own Review IDs formula is stuck -- see that Sheet's
SOURCES note for the full story). This script doesn't care which; by the
time it runs, that choice has already been made by whoever normalized the
snapshot.

Run:
  python reviews/build_dashboard.py
  python reviews/build_dashboard.py --force
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from common.sources import YOTPO_REVIEWS_SNAPSHOT, LINE_DETAIL_SNAPSHOT, check_yotpo_freshness
from reviews.review_feedback import scan_reviews

OUTDIR = os.path.dirname(__file__)  # reviews/ itself -- matches where the
                                     # already-committed CSVs + reviews.json live
DASHBOARD_JSON = os.path.join(OUTDIR, "reviews.json")
OUTPUT_FILES = [
    os.path.join(OUTDIR, "themes_by_month.csv"),
    os.path.join(OUTDIR, "themes_by_product.csv"),
    os.path.join(OUTDIR, "review_flags.csv"),
    os.path.join(OUTDIR, "data_quality.csv"),
    DASHBOARD_JSON,
]


def build_reviews(snapshot_path=YOTPO_REVIEWS_SNAPSHOT, line_detail=LINE_DETAIL_SNAPSHOT,
                   force=False, as_of=None):
    if not os.path.exists(snapshot_path):
        raise FileNotFoundError(
            f"build_reviews: no snapshot at {snapshot_path} -- normalize a source into place "
            f"first (normalize_yotpo_reviews_xlsx for the Drive/Sheet path, or "
            f"normalize_yotpo_reviews_from_csv for a raw Yotpo export uploaded directly, e.g. "
            f"when the Sheet's own Review IDs formula is stuck)."
        )

    # Fail loud on staleness (warn, not abort -- matches check_yotpo_freshness's
    # own documented behavior) BEFORE writing anything, so a colleague sees the
    # warning even if they don't read stdout closely afterward.
    newest, staleness = check_yotpo_freshness(snapshot_path, as_of=as_of)

    existing = [p for p in OUTPUT_FILES if os.path.exists(p)]
    if existing and not force:
        raise FileExistsError(
            f"build_reviews: refusing to overwrite already-committed output(s): "
            f"{', '.join(os.path.basename(p) for p in existing)}. Pass force=True "
            f"(--force on the CLI) to intentionally refresh -- reviews.json feeds the "
            f"returns dashboard's review panel directly, so an accidental overwrite "
            f"has a wider blast radius than it might look like from here."
        )

    summary = scan_reviews(
        snapshot_path, outdir=OUTDIR, line_detail=line_detail,
        verbose=True, dashboard_json=DASHBOARD_JSON,
    )
    print(f"reviews.json: {DASHBOARD_JSON}")
    print(f"newest review: {newest} ({staleness} days old)")
    return summary


if __name__ == "__main__":
    force = "--force" in sys.argv[1:]
    build_reviews(force=force)
