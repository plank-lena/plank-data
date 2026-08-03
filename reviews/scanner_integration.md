# Wiring `sku_taxonomy.py` into `review_feedback.py`

The scanner currently classifies SKUs with its own `load_categories()`, which
reads Line Detail's **"Product Category"** column directly. Per the Plank
glossary §5 the source sheets label Type/Category oppositely, so that read is at
the wrong level — and it has no subcategory at all. Replace it with the shared
module so the scanner and the returns extract agree.

## Change 1 — replace the loader (top of `scan_reviews`)

```python
# OLD
cats = load_categories(line_detail)
...
cat = cats.get(res["sku"], "")

# NEW
from sku_taxonomy import SKUTaxonomy
tax = SKUTaxonomy.load(line_detail=line_detail)   # metafields=... later (§8)
...
t = tax.classify(res["sku"])
cat, subcat = t.item_type, t.style        # style may be "" until Line Detail lands
```

`load_categories()` and its Line-Detail column read can then be deleted.

## Change 2 — carry subcategory into the outputs

Add `subcat` to the theme-by-product key and the row writers so the review side
rolls up the SAME three-level tree (department → item_type → style) as the
returns tracker:

```python
key = (res["sku"], res["product"], cat, subcat, th)   # was (sku, product, cat, th)
```

and add a `"subcategory"` column to `themes_by_product.csv` and to the
`by_sku` payload in `reviews.json`.

## Change 3 — nothing else moves

Syndication dedupe, the deleted/escalated-keep rule, and the latent/explicit
streams are untouched. Only the SKU→category source changes.

## Same call on the returns side

The returns extract uses the identical call, so a SKU lands in the same
(department, item_type, style) on both halves — which is what lets a review be
shown next to its SKU in the merged category tracker.

```python
t = tax.classify(sku)
row.category, row.subcategory = t.item_type, t.style
```
