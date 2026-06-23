# Fishpage

An internal browsing tool that turns Sea Dwelling Creatures' (SDC) freshwater wholesale
stocklist PDF into a searchable, filterable, image-rich catalog — used to decide what
livestock to order, not a public storefront.

## Language

**Stocklist**:
The source PDF published by the supplier (SDC), listing every item the supplier carries
with its SKU, size, price, and current availability. The single source of truth for the catalog.
_Avoid_: catalog (reserved for our rendered view), inventory (that's the supplier's, not ours)

**Item**:
One row of the stocklist — a specific livestock product at a specific size. The same animal
at two sizes (e.g. Bichir Ornate M and L) is two distinct Items with distinct SKUs and prices.
_Avoid_: product, fish, species

**SKU**:
The supplier's identifier for an Item, and our permanent primary key. An Item is never deleted;
nightly ingestion upserts by SKU and zeroes the quantity of any SKU absent from that night's
Stocklist. SKUs are not known to be reused, but a reuse guard watches for a SKU reappearing
under a materially different name.

**Last seen**:
The date an Item's SKU last appeared in a Stocklist. Distinguishes "out of stock this week"
(recent Last seen, qty 0) from "probably discontinued" (Last seen months ago).

**Size**:
The supplier's value in the Stocklist's `SIZE` column. For **livestock** Items it is a size
grade — one of `-` (unspecified), `S`, `M`, `L`, `Jumbo`. But the same column is overloaded:
for plants and dry goods it instead carries a **packaging unit** (`POTTED`, `BUNCH`, `w/weight`,
`ON MAT`, `12 PC CASE`, `1/2 SQ. FT.`, …) — not a size grade at all. Which interpretation applies
is tied to the Item's Derived Category (the size grades describe fish; the units describe everything
else). We store the raw column token verbatim (a blank cell becomes `-`); turning it into a clean,
category-aware grade-or-unit is deferred — see `docs/adr/0002-size-column-is-overloaded.md`.
_Avoid_: assuming `size` is always one of the five grades — most rows are, but plant/dry-goods rows are not.

**Retail price**:
The price as printed in the stocklist's `retail_price` column. Despite the name, this is the
supplier's wholesale price to us — we are the buyer, not the seller.

**Special price**:
A discounted price the supplier is offering on an Item, present on only some rows.

**In stock**:
An Item with `qty_avail > 0`. Most of the stocklist is out of stock at any given time.

**Derived Category**:
The supplier's own grouping (Angelfish, Discus, Goby, Barb, Eel, monster/oddball, …), inferred
from the SKU prefix block and cross-checked against the leading word of the name. Computed purely
from parsed Stocklist data — no enrichment needed — and used as the primary browse filter.
_Avoid_: type, group, classifier (a Classifier is enriched care data, a different thing entirely)

**Classifier**:
A care attribute we attach to an Item that is not in the Stocklist — e.g. difficulty of care,
plant safety, aggression. Drawn from a flexible registry of classifier definitions (so new ones
can be added without a schema change) and, where possible, enum-valued so it can double as a filter.

**Enrichment**:
The best-effort process of populating an Item's image and Classifiers from an external fish-info
source, AI-assisted for name-matching and field extraction. Coverage is partial: oddball species
may have no source page and fall back to manual entry. Runs on first sight of a new SKU and on demand.
_Avoid_: scraping (one possible mechanism, not the concept)
