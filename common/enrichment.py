"""Line Detail enrichment helper.

Extracted from returns_builder_v2.py's per-SKU status/finish lookup, which
every report needs (a sales/returns row only carries a SKU; status and
finish live in Line Detail and must be joined in).

This is deliberately separate from common/sku_taxonomy.py: sku_taxonomy
resolves the department/item_type/style CATEGORY tree (with a SKU-code
fallback when Line Detail is missing a row); this helper is a plain
column-by-SKU lookup for whatever other Line Detail columns a report needs
(status, finish, etc.), with no fallback -- unmatched SKUs come back as NaN,
same as the original builder.
"""


def line_detail_lookup(ld, sku_col, column_map):
    """Build per-SKU lookup Series for arbitrary Line Detail columns.

    ld: the raw positional Line Detail DataFrame (as returned by
        common.io.load_positional_sheets).
    sku_col: the column holding the SKU (e.g. "l0").
    column_map: {output_name: source_col}, e.g. {"status": "l1", "finish": "l8"}.

    Returns {output_name: pandas.Series indexed by stripped SKU}, so callers
    do `s['status'] = s['sku'].map(lookup['status'])`.
    """
    keyed = ld.copy()
    keyed["sku"] = keyed[sku_col].astype(str).str.strip()
    keyed = keyed.drop_duplicates("sku").set_index("sku")
    return {out_col: keyed[src_col] for out_col, src_col in column_map.items()}
