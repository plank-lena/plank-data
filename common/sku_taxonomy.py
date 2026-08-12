"""
sku_taxonomy.py — one place that turns a Plank SKU into a category tree.

WHY THIS EXISTS
---------------
Three parts of the returns dashboard were each classifying SKUs their own way:
the returns extract, review_feedback.py, and the planned category -> subcategory
-> SKU drill-down. They disagreed, so a review could never be lined up against
the returns for the same SKU. This module is the single answer both sides call,
so they always agree.

THE CANONICAL TREE  (Plank Domain Glossary §5)
----------------------------------------------
The glossary fixes three levels, top to bottom:

    department   broadest group      Cabinetry, Electric, Lighting, ...
    item_type    the product kind    Handle, Knob, Hook, Switch & Dimmer, ...
    style        the variant         Edge Pull, T Bar, D Bar, Toggle, Dimmer, ...

The dashboard's "category -> subcategory -> SKU" drill is just a choice of which
two of these levels to show (see RECOMMENDED_DRILL at the bottom). Keeping all
three means we don't have to re-decide that here.

WHERE THE ANSWER COMES FROM  (in priority order)
------------------------------------------------
    1. line_detail   Authoritative. Product Type + Product Category + Sub
                     Category columns (the Line Detail MASTER's own names --
                     item 7, 2026-08-12: department is now sourced from here
                     too, not just item_type/style; previously department
                     was ALWAYS SKU-code-derived even when Line Detail had a
                     real value, an inconsistency with item_type/style's own
                     "trust the authoritative source first" priority order).
    2. metafield     Future (Shopify detail.category / detail.subcategory).
                     Same shape as line_detail, so switching source is one line.
    3. sku_rule      Decode the SKU code itself (glossary §5). Covers most SKUs
                     that line_detail is missing (~the "~12% fall back" case) --
                     for ALL THREE levels now, not just item_type (item 7:
                     Lena confirmed returns/reviews keep the lenient SKU-code
                     fallback philosophy for department too, unlike trading's
                     own separate mechanism, which tags "Unknown" instead --
                     two different, deliberate answers, see trading/
                     line_detail.py's own docstring, not drift).
    4. unknown       Nothing resolved it -> flagged so a data owner can fix source.

Every result carries `.source` so you can always see which of these answered.

TWO GLOSSARY GOTCHAS THIS MODULE HANDLES FOR YOU
------------------------------------------------
  * The source sheets label "Product Type" and "Product Category" OPPOSITELY
    (glossary §5). review_feedback.py's old load_categories() read "Product
    Category" straight, which is the wrong level. This module normalises to the
    canonical names, so that latent bug goes away.
  * SKUs encode a market prefix (GB-, US-), KIT- for kits and -R for
    replacements. Those are stripped before decoding and surfaced as flags.

USING IT
--------
    from sku_taxonomy import SKUTaxonomy
    tax = SKUTaxonomy.load(line_detail="Line_Detail.csv")   # metafields=... later
    t = tax.classify("CDB-BOBB-175-1AB")
    t.department, t.item_type, t.style, t.source
    # -> ('Cabinetry', 'Handle', 'D Bar', 'line_detail')

    # See how well a set of SKUs resolves (drives the data-owner follow-up):
    report = tax.coverage(list_of_skus)
"""

from __future__ import annotations
import csv, json, os, re
from dataclasses import dataclass, field
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SEED = os.path.join(_HERE, "taxonomy_seed.json")


@dataclass
class Taxon:
    """The classification of one SKU. `source` says who answered."""
    sku: str
    department: str = ""          # Cabinetry / Electric / Lighting / ...
    item_type: str = ""           # Handle / Knob / Hook / Switch & Dimmer / ...
    style: str = ""               # Edge Pull / T Bar / Toggle / ...  ("" if unknown)
    source: str = "unknown"       # line_detail | metafield | sku_rule | unknown
    is_kit: bool = False
    is_replacement: bool = False
    market: str = ""              # GB / US / "" (global)
    raw_category: str = ""        # exactly what the source sheet said (for audit)
    raw_subcategory: str = ""

    @property
    def resolved(self) -> bool:
        return self.source != "unknown"

    @property
    def style_known(self) -> bool:
        return bool(self.style)


# Item 7 (2026-08-12): every Line Detail department is a live category
# EXCEPT Door -- shared with trading's own _DEAD_DEPARTMENTS (trading/
# contract.py) so Returns/Reviews cut the same dead category, without
# unifying trading onto this module's mechanism (Lena, explicit decision).
DEAD_DEPARTMENTS = {"Door"}


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


class SKUTaxonomy:
    """Loads the sources once, then classifies SKUs against them."""

    def __init__(self, line_detail_map=None, metafield_map=None, seed=None):
        # {sku: (raw_department, raw_category, raw_subcategory)} from Line
        # Detail / metafields -- department added 2026-08-12 (item 7), same
        # dict shape as before with one more tuple element, not a new dict.
        self.line_detail = line_detail_map or {}
        self.metafield = metafield_map or {}
        seed = seed or {}
        self.department_codes = seed.get("department_codes", {})
        self.market_prefixes = tuple(seed.get("market_prefixes", ("GB-", "US-")))
        self.modifier_prefixes = tuple(seed.get("modifier_prefixes", ("S5-", "KTH-", "KIT-")))
        self.code3_to_item_type = seed.get("code3_to_item_type", {})
        self.ambiguous_codes = seed.get("ambiguous_codes_need_authoritative", {})

    # ---- construction -----------------------------------------------------
    @classmethod
    def load(cls, line_detail=None, metafields=None, seed=DEFAULT_SEED):
        """
        line_detail : path to the Line Detail CSV (authoritative), or None.
        metafields  : path to a Shopify metafield export, or None (future §8 switch).
        seed        : path to taxonomy_seed.json (the editable SKU-rule fallback).
        """
        with open(seed, encoding="utf-8") as fh:
            seed_obj = json.load(fh)
        return cls(
            # Candidate column names, tried in order. Glossary §5 warns the source
            # sheets label Type/Category oppositely, so we accept the likely names
            # rather than hard-coding one. Add the real header here once confirmed.
            # dept_cols added 2026-08-12 (item 7) -- "Product Type" is Line
            # Detail's own name for the department level (glossary §5); NOT
            # "Category" (that's item_type-grain here, same reversed-label
            # gotcha the module docstring already warns about for cat_cols).
            line_detail_map=_load_triples(
                line_detail,
                dept_cols=("department", "Product Type"),
                cat_cols=("item_type", "Product Category", "Category"),
                sub_cols=("style", "Sub Category", "Subcategory", "Sub-Category"),
            ),
            metafield_map=_load_triples(
                metafields,
                dept_cols=("detail.department", "department"),
                cat_cols=("detail.category", "category", "Category"),
                sub_cols=("detail.subcategory", "subcategory", "Sub Category"),
            ),
            seed=seed_obj,
        )

    # ---- the one call everything uses ------------------------------------
    def classify(self, sku) -> Taxon:
        sku = _s(sku)
        if not sku or sku.lower() in ("(no sku)", "nan", "parent-sku"):
            return Taxon(sku=sku, item_type="Unattributed (no SKU)", source="unknown")

        market, is_kit, is_repl, core = self._strip(sku)
        t = Taxon(sku=sku, market=market, is_kit=is_kit, is_replacement=is_repl)

        # 1 + 2: authoritative sources (Line Detail, then metafields)
        for src_name, src in (("line_detail", self.line_detail),
                              ("metafield", self.metafield)):
            if sku in src:
                dept, cat, sub = src[sku]
                t.raw_category, t.raw_subcategory = cat, sub
                t.item_type = cat or t.item_type
                t.style = sub
                # item 7 (2026-08-12): department now trusts this source
                # FIRST, same priority order item_type/style already had --
                # previously always SKU-code-derived even when Line Detail
                # had a real "Product Type" value, the one inconsistency in
                # an otherwise-consistent "authoritative source wins" design.
                # Code-derived only when this source has no value for this
                # SKU (the "lenient fallback" philosophy Lena confirmed
                # keeping for returns/reviews, unlike trading's own separate
                # mechanism).
                t.department = dept or self._department_of(core, fallback=t.department)
                t.source = src_name
                return t

        # 3: decode the SKU code (glossary §5). Fills department + item_type.
        code = core[:3]
        item = self.code3_to_item_type.get(code)
        if item:
            t.item_type = item
            t.department = self._department_of(core)
            t.source = "sku_rule"
            # style is NOT encoded reliably in the SKU -> leave "" (needs source 1/2)
            return t

        # 4: could not resolve -> flag. Still try to name the department.
        t.department = self._department_of(core)
        t.source = "unknown"
        return t

    # ---- helpers ----------------------------------------------------------
    def _strip(self, sku: str):
        """Peel market prefix / KIT- / -R off, return (market, is_kit, is_repl, core)."""
        s = sku
        market = ""
        for p in self.market_prefixes:
            if s.startswith(p):
                market = p.rstrip("-")
                s = s[len(p):]
                break
        is_kit = False
        for p in self.modifier_prefixes:
            if s.startswith(p):
                if p.startswith("KIT"):
                    is_kit = True
                s = s[len(p):]
        is_repl = s.endswith("-R")
        return market, is_kit, is_repl, s

    def _department_of(self, core: str, fallback: str = "") -> str:
        first = core[:1].upper()
        return self.department_codes.get(first, fallback)

    def family_of(self, sku) -> str:
        """Display label grouping SKU variants of the same product, e.g. the
        KEPLER 160/220/280 sizes -> "Kepler". Strips market/kit/replacement
        affixes (via _strip()), drops the leading type-code token (the same
        code classify() reads via code3_to_item_type), then drops a trailing
        size token and a trailing finish-code token off what's left.

        A display convenience for the SKU row (glossary §5.2), not a new
        taxonomy level -- deliberately approximate; eyeball it against real
        SKUs rather than trusting it blindly on an unfamiliar naming pattern.
        """
        sku = _s(sku)
        if not sku or sku.lower() in ("(no sku)", "nan", "parent-sku"):
            return ""
        _, _, is_repl, core = self._strip(sku)
        if is_repl and core.endswith("-R"):
            core = core[: -len("-R")]  # _strip() flags -R but doesn't remove it
        tokens = [t for t in core.split("-") if t]
        if len(tokens) > 1:
            tokens = tokens[1:]  # drop the leading type code (KH, CDB, CBP, ...)
        if len(tokens) >= 3:
            tokens = tokens[:-2]  # drop size, then finish
        elif len(tokens) == 2:
            tokens = tokens[:-1]  # ambiguous with 2 left -- drop the trailing one
        return " ".join(tokens).title() if tokens else core.title()

    # ---- coverage report (drives the data-owner follow-up) ---------------
    def coverage(self, skus) -> dict:
        """How well does a list of SKUs resolve? Lists what still needs an owner."""
        by_source = Counter()
        no_style, unresolved = [], []
        seen = set()
        for sku in skus:
            sku = _s(sku)
            if not sku or sku in seen:
                continue
            seen.add(sku)
            t = self.classify(sku)
            by_source[t.source] += 1
            if t.resolved and not t.style_known:
                no_style.append(sku)
            if not t.resolved:
                unresolved.append(sku)
        total = len(seen)
        return {
            "total_skus": total,
            "by_source": dict(by_source),
            "pct_resolved": round(100 * (total - by_source["unknown"]) / total, 1) if total else 0.0,
            "resolved_but_no_style": no_style,   # department/item_type ok, subcategory pending
            "unresolved": unresolved,            # need Line Detail / metafield ownership
        }


def _load_triples(path, cat_cols, sub_cols, dept_cols=()) -> dict:
    """{sku: (department, category, subcategory)} from a CSV, or {} if no
    path. dept_cols added 2026-08-12 (item 7) -- department was previously
    never read from this source at all, always SKU-code-derived; empty
    dept_cols (the default) preserves the old two-level behaviour exactly
    for any caller that doesn't pass it.

    cat_cols / sub_cols / dept_cols are candidate header names tried in
    order; the first present in the file wins. This is the ONE place the
    source column names live, so the glossary §5 Type/Category label
    confusion is handled once.
    """
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        dept_col = next((c for c in dept_cols if c in headers), None)
        cat_col = next((c for c in cat_cols if c in headers), None)
        sub_col = next((c for c in sub_cols if c in headers), None)
        sku_col = next((c for c in ("SKU", "Product SKU") if c in headers), "SKU")
        for row in reader:
            sku = _s(row.get(sku_col))
            if not sku:
                continue
            dept = _s(row.get(dept_col)) if dept_col else ""
            cat = _s(row.get(cat_col)) if cat_col else ""
            sub = _s(row.get(sub_col)) if sub_col else ""
            if dept or cat or sub:
                out[sku] = (dept, cat, sub)
    return out


# The dashboard's "category -> subcategory" drill is a choice of two levels.
# Recommended, and what §6's Edge Pull / T Bar / D Bar ranking implies:
#     category    = item_type   (Handle, Knob, Hook, Switch & Dimmer, ...)
#     subcategory = style       (Edge Pull, T Bar, D Bar, Toggle, Dimmer, ...)
# The trade-report style nav ("Cabinetry -> Handles/Knobs") is department ->
# item_type instead; both are available on the Taxon, so this stays a display
# choice, not a data change.
RECOMMENDED_DRILL = ("item_type", "style")


if __name__ == "__main__":
    import sys
    tax = SKUTaxonomy.load(line_detail=(sys.argv[1] if len(sys.argv) > 1 else None))
    for s in ["CDB-BOBB-175-1AB", "COH-FOLD-160-1AB", "CKN-BOBB-025-1AB",
              "GB-KIT-ETOG-KEPL-1G-1AB", "KH-KEPLER-160-AB", "(no sku)"]:
        t = tax.classify(s)
        print(f"{s:28s} -> dept={t.department:11s} type={t.item_type:20s} "
              f"style={t.style or '-':10s} src={t.source} "
              f"{'[kit]' if t.is_kit else ''}{'[repl]' if t.is_replacement else ''}")
