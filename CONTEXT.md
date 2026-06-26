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
plant safety, aggression. Where possible a Classifier is enum-valued, so it doubles as a browse
filter. The set of Classifiers is a fixed, curated vocabulary — extending it is a deliberate change,
not an open-ended runtime registry. Each Item's Classifier value carries its own Provenance, so a
human correction to one attribute stands even while the rest remain `ai-generated`.

**Enrichment**:
The best-effort process of populating an Item's image and Classifiers from outside the Stocklist.
Care Classifiers are AI-generated (the LLM normalises the Item's trade name to a species and emits
enum-valued care attributes); the image comes from an external source keyed by that species, with
manual upload as the fallback. Coverage is partial: oddball species may have no usable source and
fall back to manual entry. Runs on first sight of a new SKU and on demand.
_Avoid_: scraping (one possible mechanism, not the concept)

**Strain**:
A line-bred or fancy variant traded under a name (e.g. *Guppy Tequila Sunrise*, *Gold Marble Angel*)
that resolves to a wild-type species whose own photo is the *wrong fish* — the variant looks nothing
like the wild type. Enrichment flags an Item as strain-specific so automatic image acquisition skips
it: a sourced species photo would mislead an order, so manual upload stays the image path for these.
Roughly a quarter of the catalog is strain-specific.
_Avoid_: species (a Strain resolves *to* a species but is not one); morph, variant used loosely.

**Provenance**:
The recorded origin of an enriched value on an Item — one of `manual`, `wikimedia`, or
`ai-generated` — carried per attribute, so the catalog can show which Classifiers and images are a
human fact versus a best-effort AI guess. `manual` is authoritative: re-running Enrichment never
overwrites a `manual` value, the way an Item's SKU is never deleted.
_Avoid_: confidence, score (Provenance is the source of a value, not a numeric certainty about it)

**Actor**:
The Cloudflare Access identity behind a mutating request — the authenticated email Access injects on
every request that reaches the origin, recorded as the *who* on each audit event (a Classifier
correction, a re-enrichment, a Stocklist upload). The app owns no login or session of its own; Access
is the sole authority on who a request is, and the identity is trusted without verifying its JWT
because no route reaches the origin except through Access. An off-edge run (local, tests) has no real
Actor and falls back to a neutral placeholder.
_Avoid_: user, session, login (the app owns none of these — the identity is request-scoped and
external); confusing with the *login* event itself, which happens at the Access edge and is audited
by Cloudflare, not by the app.

**Uploader**:
The Actor credited with a `manual` image — the special case where the identity is durably stored on
the image row, not just emitted to a log. Records *which* human stands behind a `manual` Provenance,
where Provenance alone says only *that* a human did. Absent on non-`manual` images, which have no
human uploader.
_Avoid_: confusing with `attribution` (the external photographer credited on a `wikimedia` image — a
different "who"); author, owner.

**Pick list**:
The set of Items an Actor has gathered to order from the supplier, held per Actor and keyed by the
Cloudflare Access email. The app has no checkout: a Pick list exists only to be exported as a list the
buyer places through SDC, and is cleared on export. It is the app's only owned per-Actor persisted state.
_Avoid_: cart, basket (no storefront or checkout); order (an actual order placed with SDC, which the
app never does); inventory.
