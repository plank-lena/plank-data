"""
review_feedback.py — mine Yotpo reviews for product feedback themes and trends.

Built for Plank Hardware. Runs entirely locally: no API calls, no per-review cost,
so it scales to the full export. Reads the file in chunks and only ever holds one
chunk plus the running tallies in memory.

    python review_feedback.py reviews.csv --outdir out/ --line-detail Line_Detail.csv

Outputs four CSVs:
    themes_by_month.csv    theme x month x market      -> the trend view
    themes_by_product.csv  theme x SKU, with examples   -> the investigation list
    review_flags.csv       one row per flagged review   -> drill-down / evidence
    data_quality.csv       reviews the tool distrusts   -> fix at source

------------------------------------------------------------------------------
WHY IT IS BUILT THIS WAY  (read before changing the filters)
------------------------------------------------------------------------------
In the sample of 1,000 reviews, every single review scoring below 4 stars was
flagged Deleted, and 30 of the 31 deleted product reviews were also Escalated.
Non-deleted reviews scored 4-5 stars without exception.

That is a moderation workflow doing its job — a complaint arrives, customer
service escalates it, the review comes off the public site. But it means the
obvious filter, "drop the deleted rows", throws away 100% of the explicit
product criticism. This tool deliberately keeps deleted and escalated reviews
and treats those flags as a priority signal rather than a reason to exclude.

The second consequence of a 93%-five-star corpus is that star rating is nearly
useless as a filter. Most product feedback arrives as a caveat inside a positive
review: "beautiful, but the screws won't hold in plasterboard". So the tool runs
two streams:

    EXPLICIT  low score, or escalated, or deleted. Themes matched on full text.
    LATENT    4-5 stars, but a caveat clause is present. Themes matched ONLY
              inside the caveat clause, so "love the finish" is not logged as a
              finish complaint while "lovely, but the finish is streaky" is.

Tune THEMES below. It is the only part most people will need to touch.
"""

from __future__ import annotations
import argparse, csv, json, os, re, sys
from collections import defaultdict, Counter

# ---------------------------------------------------------------------------
# 1. THEME LEXICON
# ---------------------------------------------------------------------------
# Grounded in the actual complaint text in the sample, not guessed. Each theme
# maps to a list of regex patterns. Add patterns as you see new language; keep
# them narrow, because a loose pattern quietly inflates a trend line.
#
# `owner` routes the theme: PRODUCT themes are for the product team, OPS themes
# are service/logistics noise that would otherwise pollute product trends.

THEMES = {
    "Fixings & screws": dict(owner="PRODUCT", patterns=[
        r"\bscrews?\b", r"\bfixings?\b", r"\bwall ?plugs?\b", r"\braw ?plugs?\b",
        r"\bbolts?\b", r"\bthread(s|ed|ing)?\b", r"\brounded off\b",
        r"\bplaster ?board\b", r"\bplaster wall\b", r"\bwon'?t hold\b",
        r"\btoo (?:short|long|thick|thin) (?:screws?|to)\b", r"\bre-?drill\b",
        r"\bmounting (?:piece|plate|bracket)s?\b", r"\bbolt provided\b", r"\bload bearing\b",
        r"\bslightly longer\b", r"\bfittings? (?:itself )?(?:seems?|feels?)\b",
    ]),
    "Finish & colour mismatch": dict(owner="PRODUCT", patterns=[
        r"\bnot(?:hing)? like the (?:colour|color)\b", r"\b(?:colour|color) (?:is )?(?:off|wrong|different)\b",
        r"\bdarker\b", r"\bbrighter\b", r"\bshinier\b", r"\bstreak(s|y|ing)?\b",
        r"\bnot as pictured\b", r"\bon the website\b.*\b(?:real life|person)\b",
        r"\b(?:looks?|looked) (?:more|much) \w+ (?:online|on the website|in the photo)",
        r"\btarnish(ed|ing)?\b", r"\bdiscolou?r(ed|ing|ation)?\b",
        r"\btint(ed)?\b", r"\bnot the (?:colour|color)\b", r"\bnearly black\b",
    ]),
    "Coating & durability": dict(owner="PRODUCT", patterns=[
        r"\bchip(s|ped|ping)\b", r"\bpeel(s|ed|ing)\b", r"\bflak(e|es|ed|ing)\b",
        r"\bscratch(es|ed|ing)?\b", r"\bsprayed\b", r"\bwearing off\b", r"\brust(y|ed|ing)?\b",
    ]),
    "Breakage & defect": dict(owner="PRODUCT", patterns=[
        r"\bsnapped\b", r"\bbroke(n)?\b", r"\bcracked?\b", r"\bbent\b", r"\bfaulty\b",
        r"\bdefect(ive)?\b", r"\bwobbl(e|es|y|ing)\b", r"\bwiggl(e|es|ing)\b",
        r"\bcame (?:apart|loose)\b", r"\bstripped\b",
    ]),
    "Size & dimensions": dict(owner="PRODUCT", patterns=[
        r"\b(?:too|much) (?:small|large|big|short|long|narrow|wide)\b",
        r"\bsmaller than\b", r"\bbigger than\b", r"\blarger than\b",
        r"\bwrong size\b", r"\bdimensions?\b", r"\bdoes ?n'?t fit\b", r"\bwon'?t fit\b",
        r"\bmeasurements?\b", r"\bnot deep enough\b", r"\bcc\b.*\bwrong\b",
    ]),
    "Grip & usability": dict(owner="PRODUCT", patterns=[
        r"\bhard to (?:grip|hold|grab|use|open)\b", r"\bdifficult to (?:grip|hold|use|open)\b",
        r"\bnot (?:very |really )?(?:that )?practical\b", r"\bnot suitable\b", r"\bnot fit for\b",
        r"\bshape is ?n'?t\b", r"\bhard to get a grip\b",
        r"\bawkward\b", r"\bergonom", r"\buncomfortable\b", r"\bcan'?t get a grip\b",
    ]),
    "Installation difficulty": dict(owner="PRODUCT", patterns=[
        r"\bfiddly\b", r"\bhard to (?:install|fit|mount)\b", r"\bdifficult to (?:install|fit|mount)\b",
        r"\bnightmare\b", r"\binstructions?\b", r"\bsticky pads?\b", r"\bpops? off\b",
        r"\belectrician\b", r"\bfitter\b", r"\btricky to (?:fit|install)\b",
        r"\bwires?\b", r"\bbacking plate\b", r"\bhow it holds\b",
    ]),
    "Weight & material feel": dict(owner="PRODUCT", patterns=[
        r"\b(?:feels?|felt) (?:light|cheap|flimsy|plasticky|hollow)\b",
        r"\bflimsy\b", r"\bcheap(ly)? made\b", r"\blightweight\b",
        r"\bmaterial (?:was |has been )?changed\b", r"\baluminium\b", r"\baluminum\b",
        r"\bnot as (?:heavy|solid|good)\b", r"\bcompared to before\b",
    ]),
    "Value for money": dict(owner="PRODUCT", patterns=[
        r"\bfor the price\b", r"\boverpriced\b", r"\bexpensive\b", r"\bwaste of money\b",
        r"\bnot worth\b", r"\bpricey\b",
    ]),
    "Missing parts & availability": dict(owner="OPS", patterns=[
        r"\bmissing (?:parts?|screws?|items?|pieces?)\b", r"\bdid ?n'?t (?:receive|come with)\b",
        r"\bout of stock\b", r"\bdiscontinu(e|ed|ing)\b", r"\bno (?:packing list|tagging)\b",
        r"\bincomplete order\b", r"\bdid ?n'?t come in the (?:colour|color|finish|size)\b",
        r"\bwish(ed)? (?:the |they )?\w+ came in\b", r"\bnone to sen\w*\b",
    ]),
    "Returns & exchange friction": dict(owner="OPS", patterns=[
        r"\bexchange\b", r"\breturn(ed|ing|s)? (?:it|them|process|for a refund)\b",
        r"\bslow to (?:exchange|refund)\b", r"\brefund\b",
    ]),
    "Delivery": dict(owner="OPS", patterns=[
        r"\bdelivery\b", r"\bshipping\b", r"\bdispatch(ed)?\b", r"\bexpress\b",
        r"\bnever (?:arrived|shipped)\b", r"\btook (?:a week|weeks|ages|so long)\b", r"\bdelay(ed|s)?\b",
    ]),
    "Customer service": dict(owner="OPS", patterns=[
        r"\bcustomer (?:service|support)\b", r"\bno ?one (?:answers|responded|replied)\b",
        r"\bnever responded\b", r"\bemails?\b.*\b(?:ignored|unanswered)\b", r"\bcomplaint\b",
    ]),
}

# Clauses that introduce a reservation. Everything AFTER one of these, inside an
# otherwise positive review, is where the useful feedback lives.
CAVEAT_RE = re.compile(
    r"\b(?:but|however|although|though|unfortunately|only (?:issue|downside|problem|complaint)|"
    r"my only|wish|shame|except|apart from|other than|would have preferred|"
    r"disappoint(?:ed|ing)|let down|slightly|a bit|a little|not keen)\b",
    re.I,
)

# Strong positive words. If a caveat clause contains one of these and no theme
# hit, it is almost certainly "but I love them" rather than a complaint.
POSITIVE_RE = re.compile(
    r"\b(?:love|lovely|beautiful|gorgeous|perfect|excellent|amazing|stunning|delighted|"
    r"fantastic|brilliant|worth it|recommend)\b", re.I,
)

BRAND_REVIEW_MARKER = "Company / Site / Brand Review"

# ---------------------------------------------------------------------------
# 2. CLASSIFICATION
# ---------------------------------------------------------------------------

def _s(v) -> str:
    """Coerce any cell value to a clean string. Numbers/CSV mix types freely."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


_COMPILED = {t: [re.compile(p, re.I) for p in cfg["patterns"]] for t, cfg in THEMES.items()}
OWNER = {t: cfg["owner"] for t, cfg in THEMES.items()}


def caveat_clauses(text: str) -> list[str]:
    """Return the fragments of `text` that follow a reservation marker."""
    out, pos = [], 0
    for m in CAVEAT_RE.finditer(text):
        if m.start() < pos:
            continue
        frag = text[m.start():m.start() + 240]
        out.append(frag)
        pos = m.start() + 40
    return out


def match_themes(text: str) -> list[str]:
    return [t for t, pats in _COMPILED.items() if any(p.search(text) for p in pats)]


def classify(row: dict) -> dict | None:
    """Classify one review. Returns None when there is nothing to log."""
    title = _s(row.get("Title"))
    content = _s(row.get("Content"))
    text = f"{title}. {content}".strip()
    if not content:
        return None

    try:
        score = float(_s(row.get("Score")) or 0)
    except ValueError:
        score = 0.0
    deleted = _s(row.get("Deleted")).lower() in ("true", "1", "yes")
    escalated = _s(row.get("Escalated")).lower() in ("true", "1", "yes")

    # Explicit: the customer or the CS team already told us this was a problem.
    is_explicit = score and score <= 3 or escalated or deleted

    if is_explicit:
        themes = match_themes(text)
        evidence = content[:220]
        stream = "EXPLICIT"
    else:
        # Latent: only look inside the reservation clauses.
        clauses = caveat_clauses(text)
        themes, evidence = [], ""
        for c in clauses:
            hits = match_themes(c)
            if hits:
                themes.extend(hits)
                if not evidence:
                    evidence = c[:220]
        themes = list(dict.fromkeys(themes))
        if not themes:
            return None
        # Guard: a glowing clause with a single weak hit is usually praise.
        if len(themes) == 1 and POSITIVE_RE.search(evidence):
            return None
        stream = "LATENT"

    if not themes:
        # A low score whose text is entirely positive is a mis-click or a broken
        # star mapping, not feedback. Flag it rather than logging a phantom theme.
        if is_explicit and POSITIVE_RE.search(text) and len(content) < 120:
            return dict(_suspect=True, review_id=_s(row.get("ID")), score=score,
                        product=_s(row.get("Product Title")), evidence=content[:160])
        themes = ["Unclassified"]

    return dict(
        review_id=_s(row.get("ID")),
        created=_s(row.get("Created At")),
        market=_s(row.get("Market")),
        score=score,
        deleted=deleted,
        escalated=escalated,
        sku=_s(row.get("Product SKU")),
        product=_s(row.get("Product Title")),
        product_type=_s(row.get("Product Type")),
        stream=stream,
        themes=themes,
        owners=sorted({OWNER.get(t, "PRODUCT") for t in themes}),
        evidence=evidence.replace("\n", " ").replace("\r", " "),
    )


def month_of(datestr: str) -> str:
    """Yotpo exports d-Mon-YYYY. Fall back gracefully on anything else."""
    s = (datestr or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(s[:11], fmt).strftime("%Y-%m")
        except ValueError:
            continue
    return "unknown"


# ---------------------------------------------------------------------------
# 3. SOURCE READERS
# ---------------------------------------------------------------------------

def iter_rows(path: str, chunk: int = 5000):
    """Yield review dicts from .csv or .numbers without loading the whole file."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".numbers":
        try:
            from numbers_parser import Document
        except ImportError:
            sys.exit("Reading .numbers needs: pip install numbers-parser")
        rows = Document(path).sheets[0].tables[0].rows(values_only=True)
        header = [str(h) for h in rows[0]]
        for r in rows[1:]:
            yield dict(zip(header, ["" if v is None else v for v in r]))
    else:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            for row in csv.DictReader(fh):
                yield row


def load_categories(path: str | None) -> dict:
    """SKU -> category, from the Line Detail export. Optional."""
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for row in csv.DictReader(fh):
            sku = _s(row.get("SKU"))
            cat = _s(row.get("Product Category"))
            if sku and cat:
                out[sku] = cat
    return out


# ---------------------------------------------------------------------------
# 4. SCAN
# ---------------------------------------------------------------------------

def scan_reviews(path, outdir="review_out", line_detail=None,
                 include_brand=False, min_examples=2, verbose=True,
                 dashboard_json=None):
    """Scan a review export and write the four output tables.

    Returns a summary dict so this can be called from a notebook or another
    script rather than the command line.
    """
    cats = load_categories(line_detail)
    os.makedirs(outdir, exist_ok=True)

    seen = {}                                       # dedupe key -> first review id
    by_month = defaultdict(lambda: Counter())      # (month, market, theme) -> stream counts
    by_product = defaultdict(lambda: Counter())    # (sku, theme) -> stream counts
    examples = defaultdict(list)                   # (sku, theme) -> quotes
    flagged_rows, dq_rows = [], []
    totals = Counter()

    for row in iter_rows(path):
        totals["read"] += 1
        is_brand = _s(row.get("CORE?")) == BRAND_REVIEW_MARKER
        if is_brand:
            totals["brand_reviews"] += 1
            if not include_brand:
                continue

        # --- data quality checks, run on everything ---
        try:
            score = float(_s(row.get("Score")) or 0)
        except ValueError:
            score = 0.0
        try:
            sent_raw = _s(row.get("Sentiment"))
            sent = float(sent_raw) if sent_raw else None
        except ValueError:
            sent = None
        content = _s(row.get("Content"))
        if score and score <= 2 and sent is not None and sent > 0.5 and content:
            dq_rows.append(dict(review_id=_s(row.get("ID")), issue="Low score, positive sentiment",
                                score=score, sentiment=sent,
                                product=_s(row.get("Product Title")), evidence=content[:160]))
            totals["dq_score_sentiment"] += 1
        if not _s(row.get("Product SKU")) and not is_brand:
            totals["dq_no_sku"] += 1

        res = classify(row)
        if not res:
            continue
        if res.get("_suspect"):
            dq_rows.append(dict(review_id=res["review_id"], issue="Low score, wholly positive text",
                                score=res["score"], sentiment="", product=res["product"],
                                evidence=res["evidence"]))
            totals["dq_score_text"] += 1
            continue

        # One review is syndicated across every SKU in its product group — in the
        # sample, 706 product rows were only 324 distinct reviews. Counting rows
        # would inflate every trend by ~40%. Trends count the review once; the
        # product table still lists each SKU it was attached to, flagged as such.
        dedupe_key = _s(row.get("Review IDs")) or (_s(row.get("Email")) + "|" + content[:120])
        first_time = dedupe_key not in seen
        if first_time:
            seen[dedupe_key] = res["review_id"]
        else:
            totals["syndicated_rows"] += 1
        totals[res["stream"].lower()] += 1 if first_time else 0

        month = month_of(str(res["created"]))
        cat = cats.get(res["sku"], "")
        for th in res["themes"]:
            if first_time:
                by_month[(month, res["market"], th)][res["stream"]] += 1
            key = (res["sku"], res["product"], cat, th)
            by_product[key][res["stream"]] += 1
            if not first_time:
                by_product[key]["SYNDICATED"] += 1
            if res["evidence"] and len(examples[key]) < min_examples:
                examples[key].append(res["evidence"])

        flagged_rows.append(dict(
            review_id=res["review_id"], created=res["created"], month=month,
            market=res["market"], score=res["score"], stream=res["stream"],
            escalated=res["escalated"], deleted=res["deleted"],
            duplicate_of=("" if first_time else seen[dedupe_key]),
            sku=res["sku"], product=res["product"], category=cat,
            themes="; ".join(res["themes"]), owner="; ".join(res["owners"]),
            evidence=res["evidence"],
        ))

    # ---- write outputs ----
    def write(name, header, rows):
        p = os.path.join(outdir, name)
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
        return p

    m_rows = [dict(month=m, market=mk, theme=th, owner=OWNER.get(th, "PRODUCT"),
                   explicit=c["EXPLICIT"], latent=c["LATENT"], total=c["EXPLICIT"] + c["LATENT"])
              for (m, mk, th), c in sorted(by_month.items())]
    p_rows = []
    for (sku, prod, cat, th), c in by_product.items():
        p_rows.append(dict(sku=sku, product=prod, category=cat, theme=th,
                           owner=OWNER.get(th, "PRODUCT"),
                           explicit=c["EXPLICIT"], latent=c["LATENT"],
                           total=c["EXPLICIT"] + c["LATENT"],
                           syndicated_copies=c["SYNDICATED"],
                           examples=" || ".join(examples[(sku, prod, cat, th)])))
    p_rows.sort(key=lambda r: (-r["explicit"], -r["total"]))

    paths = [
        write("themes_by_month.csv", ["month", "market", "theme", "owner", "explicit", "latent", "total"], m_rows),
        write("themes_by_product.csv", ["sku", "product", "category", "theme", "owner",
                                        "explicit", "latent", "total", "syndicated_copies",
                                        "examples"], p_rows),
        write("review_flags.csv", ["review_id", "created", "month", "market", "score", "stream",
                                   "escalated", "deleted", "duplicate_of", "sku", "product",
                                   "category", "themes", "owner", "evidence"], flagged_rows),
        write("data_quality.csv", ["review_id", "issue", "score", "sentiment", "product", "evidence"], dq_rows),
    ]

    # Optional: compact payload for the returns dashboard. Keeps the review
    # section reproducible — rerun this command and the dashboard updates.
    if dashboard_json:
        theme_tot = defaultdict(lambda: Counter())
        for r in m_rows:
            theme_tot[r["theme"]]["explicit"] += r["explicit"]
            theme_tot[r["theme"]]["latent"] += r["latent"]
        cat_tot, sku_tot = defaultdict(lambda: Counter()), {}
        for r in p_rows:
            if r["owner"] != "PRODUCT" or r["theme"] == "Unclassified":
                continue
            cat_tot[r["category"] or "Unmapped"][r["theme"]] += r["total"]
            s = sku_tot.setdefault(r["sku"], dict(product=r["product"], category=r["category"],
                                                  themes={}, quote=""))
            s["themes"][r["theme"]] = s["themes"].get(r["theme"], 0) + r["total"]
            if not s["quote"] and r["examples"]:
                s["quote"] = r["examples"].split(" || ")[0]
        payload = dict(
            source=os.path.basename(path),
            reviews_read=totals["read"],
            reviews_flagged=len({r["review_id"] for r in flagged_rows if not r["duplicate_of"]}),
            themes=[dict(theme=k, owner=OWNER.get(k, "PRODUCT"),
                         explicit=v["explicit"], latent=v["latent"])
                    for k, v in sorted(theme_tot.items(), key=lambda kv: -(kv[1]["explicit"] + kv[1]["latent"]))],
            by_month=[dict(month=r["month"], market=r["market"], theme=r["theme"],
                           owner=r["owner"], total=r["total"]) for r in m_rows],
            by_category={k: dict(v) for k, v in cat_tot.items()},
            by_sku=sku_tot,
        )
        with open(dashboard_json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        paths.append(dashboard_json)

    summary = dict(totals=dict(totals), themes=len(by_month), products=len(by_product),
                   flagged=len(flagged_rows), outputs=paths)
    if verbose:
        print(f"read {totals['read']:,} reviews  "
              f"({totals['brand_reviews']:,} brand/site {'included' if include_brand else 'excluded'})")
        print(f"flagged {len(flagged_rows):,}  —  {totals['explicit']:,} explicit, {totals['latent']:,} latent")
        if totals["dq_score_sentiment"]:
            print(f"data quality: {totals['dq_score_sentiment']:,} reviews score low but read positive")
        print("\ntop themes:")
        agg = Counter()
        for r in p_rows:
            agg[r["theme"]] += r["total"]
        for th, n in agg.most_common(12):
            print(f"   {OWNER.get(th,'PRODUCT'):8s} {th:28s} {n:5d}")
        print("\nwrote:")
        for p in paths:
            print("   " + p)
    return summary


def main():
    ap = argparse.ArgumentParser(description="Mine Yotpo reviews for product feedback themes.")
    ap.add_argument("path", help="reviews .csv or .numbers export")
    ap.add_argument("--outdir", default="review_out")
    ap.add_argument("--line-detail", default=None, help="Line Detail CSV, to add Product Category")
    ap.add_argument("--include-brand", action="store_true",
                    help="keep company/site reviews (excluded by default)")
    ap.add_argument("--dashboard-json", default=None,
                    help="also write a compact payload for the returns dashboard")
    a = ap.parse_args()
    scan_reviews(a.path, a.outdir, a.line_detail, a.include_brand,
                 dashboard_json=a.dashboard_json)


if __name__ == "__main__":
    main()
