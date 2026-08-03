# Trading logic spec + gap list

**Reverse-engineering of the "Monthly Trading Report" Google Sheet**
Investigated read-only, 3 Aug 2026 · Sheet owner: maxine@plankhardware.com · [Sheet link](https://docs.google.com/spreadsheets/d/15eLJeFGAXvLFQoSgxUu86Zl2a-fCzNFLOErIT3-dFLc/edit)
Companion to `returns_spike_findings.md`; written to seed `trading/build.py`.

---

## Decisions locked (Lena, Aug 2026) — build to these

- **Path B confirmed.** Port the logic into `trading/build.py`, sourced from the Shopify connector (ShopifyQL primary; Matrixify/Orders as the raw fallback for the line-item key). Retire the Supermetrics dependency. Keep the existing sheet as the spec + regression oracle.
- **Headline basis stays static = match the current sheet.** Revenue is **net of discounts and net of in-window returns, ex-VAT** — i.e. Shopify "net sales" less tax, per line (`AB` formula). Do **not** switch the trading headline to true gross or pull returns out of it; the headline structure must stay identical to today's report so month-over-month history stays comparable. (Returns are still reported separately in the *returns* report — this decision applies only to the trading headline.)
- **VAT = subtract Shopify's per-line tax, not `/1.2`.** Retire the `UK_SALES_ARE_INC_VAT` `/1.2` toggle for trading.
- **FX must be made deterministic.** Replace live `GOOGLEFINANCE` with a frozen, dated GBP/USD rate stored in the repo, keyed by order date. This is the single change required to make the report reproducible/auditable; everything else is reproduced as-is.
- **Reconciliation gate unchanged:** `uk + us + row == total` within 0.1%, ROW present; regress against a committed month's `Monthly Summary` before shipping.

---

## 0. Headline conclusions (read these first)

1. **The revenue engine is fully transparent and fully reverse-engineered.** Unlike the returns workbook (Excel formulas over pasted feeds), the trading logic lives in **live array formulas** (`MAP`/`LAMBDA`/`SUMIFS`/`ARRAYFORMULA`) on the `* Shopify` tabs and `By SKU`. Every figure the dashboard consumes is traceable to a formula. No hand-carried numbers in the compute path — including **vs-LM and vs-LY, which are computed live** (this is the opposite of returns).

2. **The Shopify connection is Supermetrics**, not the native Sheets connector, not Matrixify, not a bound Apps Script. Proof: every data tab carries `zsupermetrics_*` named ranges (`Data ▸ Named ranges`), and the tab headers read *"Auto-refresh from Shopify API…"*. The custom **"Reports"** menu is the Supermetrics add-on. It writes **plain values** (verified: `LM Shopify!M5 = 119.4`, not a formula) into four tabs.

3. **VAT is removed by subtracting Shopify's own reported tax, not by a hardcoded `/1.2`.** There is no `/1.2` anywhere in the revenue path. This is more correct than the toggle assumed in the roadmap — but it means the ex-VAT figure depends on Shopify having taxes recorded correctly per line.

4. **ROW is produced correctly, from ship-to country** (`GB→UK`, `US→US`, everything-else→`ROW`), with a store-name fallback. `uk + us + row` reconciles to the total *by construction* (they partition the same measure).

5. **Three points where the sheet departs from a naive reading** — two are kept on purpose, one must be fixed: (a) the "gross sales" headline is actually **net of discounts and net of returns** at line level — **kept as-is by decision** (headline structure stays static); (b) "Weeks Cover" is really **months** of cover — reproduce the value, keep the dashboard's ×52/12 correction; (c) revenue for US sales is FX-converted with **live `GOOGLEFINANCE`**, so the sheet is **not deterministic** — the same month re-opened tomorrow can print a different US/ROW number. **This is the one thing Path B must fix** (freeze a dated FX rate).

---

## 1. Sheet structure (tabs, feeds, hidden sheets, named ranges)

**11 tabs, three layers:**

| Tab | Layer | Source | Notes |
|---|---|---|---|
| **Monthly Summary** | Output | formulas over `LM Shopify` | Row 7 = TOTAL; the dashboard's Monthly Summary. |
| **By Collection** | Output | formulas over `* Shopify` | Collection-level aggregation. |
| **By SKU** | Output/engine | `MAP`/`SUMIFS` over `* Shopify`, `Inventory`, `Cost for Trade`, `Line Detail` | The per-SKU compute engine. |
| **LM Shopify** | Feed + transform | **Supermetrics** (current month) + helper formula cols | Raw order-lines A–U; transform cols V–AD; enrichment AE onward. |
| **LM-1 Shopify** | Feed + transform | **Supermetrics** (last month) | Same shape; drives vs-LM. |
| **LY LM Shopify** | Feed + transform | **Supermetrics** (last-year same month) | Same shape; drives vs-LY. |
| **Inventory** | Feed | **Supermetrics** ("Inventory on hand") | Available / Committed / On Hand by location. |
| **Cost for Trade** | Feed | Separate inventory/margin report (not the sales add-on) | SKU → **Cost price**; drives GM. |
| **Line Detail** | Reference | Manual (Dropbox export, ~11,184 rows) | Product master: description, type, category, finish, status, flags. |
| **Old LD** | **Hidden** | — | Legacy Line Detail backup; not referenced by live formulas. |
| **FC** | Feed | Forecast | Feeds the "vs FC" columns / Forecast period. |

**Named ranges:** all are Supermetrics bookkeeping ranges named `zsupermetrics_…` on `LM Shopify`, `LM-1 Shopify`, `LY LM Shopify`, and `Inventory` (e.g. `'LM Shopify'!A2:AB1330`). No user-defined named ranges carry logic.

**External links / fragility:** No `IMPORTRANGE` or cross-file links. The external dependencies are (i) **Supermetrics** refresh, (ii) **`GOOGLEFINANCE`** live FX, (iii) manual pastes into **Line Detail** and **Cost for Trade**.

**Two Shopify stores** appear in the data: **`PH`** (Plank Hardware, UK) and **`P US`** (US). The `Site` field is used as a country fallback.

### Data flow

```
Supermetrics (Shopify API)                Manual / other
  ├─ LM Shopify      (current month)         Line Detail (Dropbox, ~11k rows)
  ├─ LM-1 Shopify    (last month)            Cost for Trade (cost price feed)
  ├─ LY LM Shopify   (last-year month)       FC (forecast)
  └─ Inventory       (on hand)
                    │
   helper cols V–AD add: SKU key, channel, country(UK/US/ROW),
   FX rate, ex-VAT revenue (AB), month key (AD)
                    │
     By SKU / By Collection / Monthly Summary  ── SUMIFS ──▶  dashboard .xlsx (values-only dump)
```

---

## 2. The transform columns on `LM Shopify` (where the real logic is)

Columns **A–U are Supermetrics-written values** ("Shopify order" fields). Columns **V onward are analyst-added formulas**. Row 4 holds headers; data starts row 5. Verbatim:

**Raw Supermetrics fields (A–U):**
`A Site · B Order name · C Order created date · D Last updated date · E "B2B?" · F Company name · G Shipping country · H Product SKU · I Product title · J image · K Product Qty · L Order count · M Product Gross (Unit price × Qty before discount, inc VAT) · N Discounts (inc VAT) · O Returns (inc VAT) · P Net sales (inc VAT) · Q Shipping charges · R Net Sales Tax · S Tax Returned · T Total Product Sales · U Total Order Sales (order-level, populated once per order)`

**W — normalised SKU key** (what everything joins on):
```
=ARRAYFORMULA(TO_TEXT(IF(H5:H<>"", H5:H, I5:I)))
```
Product SKU, falling back to product title. (Note: despite the header "Product/Bundle SKU", there is **no kit/bundle explosion** here — it is a straight SKU with a title fallback.)

**Y — Customer Type (channel):**
```
=ARRAYFORMULA(IF($B$5:$B="", "", IF($F$5:$F<>"", "B2B", $E$5:$E)))
```
B2B if a **Company name** exists, otherwise the raw `B2B?` flag.

**Z — Shipping country → UK / US / ROW:**
```
=ARRAYFORMULA(IF($A$5:$A="", "",
  IF($G$5:$G="N/A",
     IF($V$5:$V="PH","UK", IF($V$5:$V="P US","US","ROW")),
     SWITCH($G$5:$G, "GB","UK", "US","US", "ROW"))))
```
`GB→UK`, `US→US`, **anything else → ROW**. If ship-to country is missing (`N/A`), fall back to the store: `PH→UK`, `P US→US`, else `ROW`.

**AA — FX rate (US only):**
```
=BYROW($C$5:$C, LAMBDA(date, IF(ISBLANK(date), "",
  LET(rowIndex, ROW(date)-ROW(C$5)+1, bVal, INDEX($V$5:$V, rowIndex),
    SWITCH(bVal, "P US",
      IFERROR(INDEX(SORT(GOOGLEFINANCE("CURRENCY:GBPUSD","price", date-7, date), 1, FALSE), 2, 2), ""),
      1)))))
```
For US-store rows, GBP/USD at the order date (latest quote in the 7 days up to order date). Everything else = 1.

**AB — "Gross Product Sales" = the ex-VAT revenue measure** (this is the headline; `AB1` total = **£534,551.39**):
```
=ARRAYFORMULA(IF($A$5:$A="", "",
  IF(W5:W="Shipping", "",
    IF((ROUND(M5:M+N5:N+O5:O)=0)*(W5:W<>"Shipping"),
       (-O5:O + S5:S)/AA5:AA,
       IFERROR((T5:T - R5:R - O5:O)/AA5:AA, "")))))
```
Normal case: `(T − R − O) / AA` = **(net product sales inc-VAT − Shopify tax − returns) ÷ FX**. Shipping lines (`W="Shipping"`) are excluded from product sales.

**AC / AD — period keys:**
```
AD = ARRAYFORMULA(IF(C5:C="", "", TEXT(C5:C,"mmm") & " - " & YEAR(C5:C)))   → e.g. "Jul - 2026"
```
Buckets by **order-created month** (`C`). `AD` is the criterion the output tabs match on.

**AE onward — Line Detail enrichment** (looked up by SKU): Product Description, Product Type, Product Category, Finish, **Product Status** (used at `LM Shopify!AS`), and exclusion flags (e.g. the `"No"` flag at `AO` used to exclude screws/non-stock from unit counts).

---

## 3. Answers to the ten questions

### Q1 — Revenue & VAT
Revenue = **`(Total Product Sales inc-VAT − Net Sales Tax − Returns) / FX`** per line (`LM Shopify!AB`), summed. So:
- **UK lines are inc-VAT at source** (`M`, `P`, `T` all carry "inc VAT"). VAT is stripped by **subtracting Shopify's actual per-line tax `R`**, *not* by dividing by 1.2. Worked check on row 5: net sales `107.46` inc-VAT, tax `17.91`, ⇒ ex-VAT `89.55 = AB5`; and `107.46 / 1.2 = 89.55`, so the effective rate is 20% but it is derived from Shopify's tax, not assumed.
- **No `/1.2` exists anywhere** in the revenue path. (Kill the `UK_SALES_ARE_INC_VAT` `/1.2` toggle for trading — replace it with "subtract Shopify tax".)
- **GBP-inc-VAT vs USD-ex-tax asymmetry is reconciled automatically:** US lines carry little/no VAT in `R`, so `T − R` ≈ `T`; the `/AA` FX step then converts USD→GBP. UK lines have `AA=1` and a full VAT `R`. Both land ex-VAT in GBP.

### Q2 — Country / ROW
Each line is assigned by **`LM Shopify!Z`** from **ship-to country** (`GB→UK`, `US→US`, else `ROW`), with a store fallback when country is `N/A`. **ROW is genuinely produced** (dashboard `DN` column exists and is non-empty). Reconciliation holds *by construction*: `By SKU` UK/US/ROW columns are the identical `SUMIFS` on `AB` differing only by `Z=UK|US|ROW`, and `Monthly Summary` totals are `=sum(AT8:AT13)` etc. Because every line has exactly one `Z`, `uk + us + row = total` exactly (well within 0.1%).

### Q3 — Channel (D2C / B2B)
From **`LM Shopify!Y`**: `B2B` if a Company name is present, else the raw `B2B?` flag. `By SKU` D2C £ (`AC`) / B2B £ are `SUMIFS` on `AB` filtered by `Y`. They **do not partition the total**: ROW-shipped orders are bucketed into D2C/B2B inconsistently and shipping lines are dropped, so `D2C + B2B ≠ Total`. **Never reconcile from the channel split** — matches the contract.

### Q4 — Gross margin
`By SKU` GM% (`Y`) = `=IFERROR(($R5 − S5*$Z5)/$R5, 0)` = **(revenue − units × unit-cost) / revenue**. Unit cost (`By SKU!Z`):
```
=MAP($O$5:$O, LAMBDA(sku, IF(ISBLANK(sku),"",
  IFERROR(XLOOKUP(sku, 'Cost for Trade'!$C:$C, 'Cost for Trade'!$E:$E),
    IFERROR(XLOOKUP(sku, 'Line Detail'!$B$4:$B$11184,
      INDEX('Line Detail'!$4:$11184, 0, MATCH(Z$3,'Line Detail'!$3:$3,0))), "")))))
```
Cost = **`Cost for Trade!E` (Cost price) keyed by SKU**, falling back to Line Detail. Negative component GM is real (cost > price on some lines) — do not clamp.

### Q5 — Sell-through / weeks-cover
Inventory (`By SKU!V`) = `=SUMIFS(Inventory!$G:$G, Inventory!$B:$B, SKU, Inventory!$X:$X, "No")` — **On Hand** from the Supermetrics `Inventory` tab, excluding a flagged group (`X="No"`, i.e. "excl screws"). Then:
- **Sell-through (`W`)** = `S5/(S5+V5)` = units sold / (units sold + inventory).
- **Weeks Cover (`X`)** = `V5/S5` = inventory / monthly units → **this is *months* of cover, mislabelled "weeks"** (the dashboard already multiplies by 52/12 to fix it; keep that conversion).
- Units (`S`) = `SUMIFS(LM Shopify!X [Qty], W=SKU, AD=month, AO="No")`.

### Q6 — vs-LM / vs-LY
**Live, not hand-pasted.** `By SKU` vs-LM (`T`) = `=(R5 − CE5)/CE5`, where `CE` and `CT` are live `SUMIFS` on the separately-refreshed sibling tabs:
```
CE (last month) = SUMIFS('LM-1 Shopify'!$AB:$AB, 'LM-1 Shopify'!$W:$W, SKU, 'LM-1 Shopify'!$AD:$AD, 'LM-1 Shopify'!$E$2)
CT (last year)  = SUMIFS('LY LM Shopify'!$AB:$AB, 'LY LM Shopify'!$W:$W, SKU, 'LY LM Shopify'!$AD:$AD, 'LY LM Shopify'!$E$2)
```
This is the key contrast with returns: prior-period comparators here are **recomputed from raw Supermetrics pulls**, not carried by hand.

### Q7 — Primary key
**No stable Shopify line-item ID is exposed.** Supermetrics delivers `Order name` (e.g. `#1782155954`) + `Product SKU`; the join key is the **`order + SKU` composite** (`W`). This is exactly the composite that double-counted in the returns work when a SKU appears on multiple lines of one order — the builder must **de-dupe / sum on `order+SKU`**, or pull a real `line_item.id` from the Admin API (which Supermetrics does not surface here).

### Q8 — Sales basis (pre/post returns)
The revenue measure `AB` **subtracts returns (`O`) and uses net-of-discount sales (`T`)** — so the "gross sales" headline is actually **net of discounts and net of in-period returns**. The impact is small because the Supermetrics query *"adjusts returns to the week the order was placed"* (tab note `A2`), pushing most returns out of the current window. **Decision: keep this basis as-is** — the trading headline stays net-of-discounts/returns ex-VAT to keep the report structure and month-over-month history static. Reproduce `AB` exactly, including the zero-net edge branch (see gap 10).

### Q9 — The Shopify connection
**Supermetrics** (Google Sheets add-on; the "Reports" menu). Confirmed by `zsupermetrics_*` named ranges on all four data tabs and the "Auto-refresh from Shopify API" headers. It writes **values** (not formulas). It runs **four separate queries** → `LM Shopify`, `LM-1 Shopify`, `LY LM Shopify`, `Inventory`, each scoped by a month/date window (`E1/E2`, `F2/G2`). Cadence is add-on-driven (manual "Refresh"/scheduled), keyed off the `Month` selector. **Fields pulled:** order name, order/updated dates, B2B flag, company, shipping country, SKU, title, qty, order count, gross (before discount, inc-VAT), discounts, returns, net sales (inc-VAT), shipping charges, net sales tax, tax returned, total product/order sales.

**Can our connectors reproduce it? Yes.** These are standard Shopify "Sales" report metrics:
- **Shopify connector (`run-analytics-query`, ShopifyQL):** `gross_sales`, `discounts`, `returns`, `net_sales`, `taxes`, `ordered_item_quantity` by `product_variant_sku` × `shipping_region`/`billing` × time — reproduces `AB` directly (and gives ex-VAT via the tax line the same way). Orders/line items are also reachable via `list-orders` / GraphQL for the `order + line_item.id` key that Supermetrics omits.
- **Matrixify:** raw Orders export (line items with prices, tax lines, discount allocations, shipping address country) — a builder aggregates to the same measure and gets a real line-item id.
Either path removes the Supermetrics dependency.

### Q10 — Structure / mapping to the dashboard
`Monthly Summary` row 7 is the TOTAL and is built **directly from `LM Shopify`**, not from `By SKU`:
```
F7  (total)  = =sum(F8:F13)                                        ' sum of the 6 product-status rows
F8  (a row)  = =sumifs('LM Shopify'!$AB:$AB, 'LM Shopify'!$AS:$AS, $B8)   ' by Product Status (AS)
AT7 (UK)     = =sum(AT8:AT13)      DN7 (ROW) = =sum(DN8:DN13)
```
So the dashboard's `F7 / AT7 / CD7 / DN7` all trace to `SUMIFS` over `LM Shopify!AB`, split by status (`AS`) and country (`Z`). `By SKU` and `By Collection` are parallel `MAP`/`SUMIFS` aggregations of the same `AB` measure. Hidden `Old LD` and `FC` (forecast) are not on the revenue-reconciliation path.

---

## 4. Gap list — where the raw data does *not* determine the answer

*(Items 1–2, 3, 5 carry a locked decision from the top of this doc; the rest are builder cautions.)*

1. **"Gross sales" is net of discounts and returns — LOCKED: keep as-is.** `AB = (T − R − O)/FX` uses discounted, returns-adjusted sales. This departs from the "gross, returns-separate" phrasing of the contract, but is **kept by decision** so the trading headline structure stays static. Build to `AB`; do not switch to true gross.
2. **VAT basis is empirical, not fixed.** Ex-VAT comes from subtracting Shopify's `Net Sales Tax`, so it silently trusts Shopify tax config. A builder that instead assumes `/1.2` for all UK lines will diverge on zero-rated / mixed-rate lines. **Recommend: replicate "subtract Shopify tax," not `/1.2`.** (This retires the `UK_SALES_ARE_INC_VAT` toggle for trading.)
3. **Non-deterministic FX — LOCKED: must fix.** US/ROW revenue uses **live `GOOGLEFINANCE("CURRENCY:GBPUSD")`** at read time. The same historical month can reprint differently later — the sheet is not reproducible. The builder **freezes an FX rate per order/day** from a dated source and stores it in the repo, so runs are deterministic and auditable. This is the only definitional change from the current sheet.
4. **No line-item key.** Join is `order + SKU`; multi-line duplicates must be summed/de-duped (the returns double-count trap).
5. **"Weeks Cover" is months.** `V/S` is months of cover; only correct after ×52/12. Name it correctly in code.
6. **Manual / external inputs:** **Line Detail** (Dropbox paste; drives status, category, finish, cost fallback) and **Cost for Trade** (cost price feed, separate report) are hand-refreshed. Cost could instead come from Shopify variant `unit_cost` (InventoryItem) via the connector, removing a manual step.
7. **Channel completeness.** `Y` infers B2B purely from "Company name present." Genuine B2B orders placed without a company name fall to the raw flag; wholesale via a different store/tag isn't modelled. Don't treat D2C/B2B as exhaustive.
8. **`W` "Product/Bundle SKU" does not explode bundles/kits** despite its name — kit SKUs stand alone (consistent with the returns decision "kit/assembly SKUs stand alone, no rollup").
9. **Row-extent / staleness risk:** Supermetrics named ranges are fixed-height (`…:AB1330`, `…:T1329`). If a month's order-lines exceed the pulled range, the tail is silently dropped. A builder should assert row counts vs. Shopify totals.
10. **Zero-net edge branch** in `AB` (`ROUND(M+N+O)=0` ⇒ `(−O+S)/AA`) handles fully-returned/zero lines specially — port it explicitly or returns-only lines will be mishandled.

---

## 5. Feed → builder mapping (to seed `trading/build.py`)

| Builder needs | Source today | Connector path | Key transform to port |
|---|---|---|---|
| Order-line sales | `LM Shopify` A–U (Supermetrics) | ShopifyQL `run-analytics-query` **or** Matrixify Orders | `AB = (net_sales_incVAT − tax − returns)/FX`, exclude shipping lines |
| Prior periods | `LM-1`, `LY LM Shopify` | same query, shifted window | recompute live (don't hand-carry) |
| Country UK/US/ROW | ship-to country `G` + store `A` | `shipping_region` dimension / order `shipping_address.country` | `GB→UK, US→US, else ROW`; `N/A`→store fallback |
| Channel D2C/B2B | `E` flag + `F` company | order `company` / customer B2B flag | `B2B if company else flag` |
| FX (US) | `GOOGLEFINANCE` (live) | **dated FX table stored in repo** | freeze GBP/USD by order date |
| Unit cost | `Cost for Trade!E` (+ Line Detail) | Shopify variant `unit_cost` or committed cost file | XLOOKUP by SKU, Line Detail fallback |
| Inventory on hand | `Inventory!G` (Supermetrics) | Shopify `get-inventory-levels` | On Hand by SKU, exclude flagged group |
| Status/category/finish | `Line Detail` (Dropbox) | committed Line Detail file | enrich by SKU; canonical vocab |
| Period key | `AD = TEXT(C,"mmm")&" - "&YEAR(C)` | — | bucket by **order-created** month |
| Reconciliation | `F7=sum(status)`, UK+US+ROW | gate | `uk+us+row == total` ±0.1%; ROW present |

---

## 6. Decision — Path B (confirmed)

**Path B: port the logic into the code builder** (`trading/build.py`), sourcing from the Shopify connector (ShopifyQL primary, Matrixify/Orders as the raw fallback for the line-item key). Consistent with ROADMAP 2.2's Path 2 (hybrid), and low-risk because **the logic is fully specified above** — there are no black boxes left to discover.

Why not Path A (keep the sheet, automate only its refresh):
- The sheet is **not deterministic** (live `GOOGLEFINANCE` FX) and so **cannot serve as a clean regression oracle** unless the FX is frozen — which Path B does in code.
- Path A keeps three fragile external dependencies live (Supermetrics licence/quota, GOOGLEFINANCE, manual Line Detail/Cost pastes) and a schema (fixed-height named ranges, wide hand-added helper columns) that breaks silently.

**Build-to-match, with one deliberate change.** The builder must reproduce a committed month's `Monthly Summary` totals (`F7`, `AT7/CD7/DN7`, GM%, units) within tolerance before shipping. Reproduce the sheet's basis **exactly** — including net-of-discounts/returns revenue (`AB`), subtract-Shopify-tax VAT, and months-as-"weeks" cover. The **only** intentional deviation is freezing FX for determinism; because historical `GOOGLEFINANCE` rates are close to the frozen dated rate, this should keep the regression within tolerance.

**Sequencing:** (1) build the ex-VAT revenue + country/channel engine from ShopifyQL, reproducing `AB` (net-of-discount/returns), and reconcile to a stored month; (2) add GM (Shopify unit cost, Line Detail fallback) and inventory; (3) replace live FX with a committed dated table; (4) wire the reconciliation gate; (5) retire Supermetrics once the builder matches.

---

### Formula appendix (verbatim, as captured live)

```
# LM Shopify (transform columns)
W5  =ARRAYFORMULA(TO_TEXT(IF(H5:H<>"",H5:H, I5:I)))
Y5  =ARRAYFORMULA(IF($B$5:$B="","",IF($F$5:$F<>"","B2B",$E$5:$E)))
Z5  =ARRAYFORMULA(IF($A$5:$A="","",IF($G$5:$G="N/A",IF($V$5:$V="PH","UK",IF($V$5:$V="P US","US","ROW")),SWITCH($G$5:$G,"GB","UK","US","US","ROW"))))
AA5 =BYROW($C$5:$C, LAMBDA(date,IF(ISBLANK(date),"",LET(rowIndex,ROW(date)-ROW(C$5)+1,bVal,INDEX($V$5:$V,rowIndex),SWITCH(bVal,"P US",IFERROR(INDEX(SORT(GOOGLEFINANCE("CURRENCY:GBPUSD","price",date-7,date),1,FALSE),2,2),""),1)))))
AB5 =ARRAYFORMULA(IF($A$5:$A="","",IF(W5:W="Shipping","",IF((ROUND(M5:M+N5:N+O5:O)=0)*(W5:W<>"Shipping"),(-O5:O+S5:S)/AA5:AA,IFERROR((T5:T-R5:R-O5:O)/AA5:AA,"")))))
AD5 =ARRAYFORMULA(IF(C5:C="","",TEXT(C5:C,"mmm") & " - " & YEAR(C5:C)))

# By SKU (engine)
S5  =MAP($O$5:$O,LAMBDA(SKU,IF(SKU="","",SUMIFS('LM Shopify'!$X:$X,'LM Shopify'!$W:$W,SKU,'LM Shopify'!$AD:$AD,$O$1,'LM Shopify'!$AO:$AO,"No"))))
Y5  =IF($O$5:$O="","",IFERROR((($R5-S5*$Z5)/$R5),0))
Z5  =MAP($O$5:$O,LAMBDA(sku,IF(ISBLANK(sku),"",IFERROR(XLOOKUP(sku,'Cost for Trade'!$C:$C,'Cost for Trade'!$E:$E),IFERROR(XLOOKUP(sku,'Line Detail'!$B$4:$B$11184,INDEX('Line Detail'!$4:$11184,0,MATCH(Z$3,'Line Detail'!$3:$3,0))),"")))))
T5  =IF($O$5:$O="","",IFERROR((R5-CE5)/CE5,0))
V5  =MAP($O$5:$O,LAMBDA(SKU,IF(SKU="","",SUMIFS(Inventory!$G:$G,Inventory!$B:$B,SKU,Inventory!$X:$X,"No"))))
W5  =IF($O$5:$O="","",IFERROR(S5/(S5+V5),0))
X5  =IF($O$5:$O="","",IFERROR(V5/S5,0))
AC5 =MAP($O$5:$O,LAMBDA(sku,IF(sku="","",SUMIFS('LM Shopify'!$AB:$AB,'LM Shopify'!$W:$W,sku,'LM Shopify'!$AD:$AD,$O$1,'LM Shopify'!$Y:$Y,$AE$1))))   # D2C £ (Y=channel)
AQ5 =MAP($O$5:$O,LAMBDA(sku,IF(sku="","",SUMIFS('LM Shopify'!$AB:$AB,'LM Shopify'!$W:$W,sku,'LM Shopify'!$AD:$AD,$O$1,'LM Shopify'!$Z:$Z,$AS$1))))   # UK £ (Z=country)
CE5 =MAP($O$5:$O,LAMBDA(SKU,IF(SKU="","",SUMIFS('LM-1 Shopify'!$AB:$AB,'LM-1 Shopify'!$W:$W,SKU,'LM-1 Shopify'!$AD:$AD,'LM-1 Shopify'!$E$2))))     # last month
CT5 =MAP($O$5:$O,LAMBDA(SKU,IF(SKU="","",SUMIFS('LY LM Shopify'!$AB:$AB,'LY LM Shopify'!$W:$W,SKU,'LY LM Shopify'!$AD:$AD,'LY LM Shopify'!$E$2))))  # last year

# Monthly Summary (output)
F7  =sum(F8:F13)
F8  =sumifs('LM Shopify'!$AB:$AB,'LM Shopify'!$AS:$AS,$B8)   # by Product Status
AT7 =sum(AT8:AT13)      DN7 =sum(DN8:DN13)
```
