# SKU is a permanent primary key; ingestion upserts and zeroes-out rather than mirroring the PDF

Each night's Stocklist PDF is the supplier's current truth, and the obvious design is to mirror
it exactly — delete any Item whose SKU is absent. We deliberately don't. Ingestion upserts by SKU
and sets `qty_avail = 0` for SKUs missing from that night's list, keeping the Item with its
`last_seen` date, image, and Classifiers intact. The reason is that enrichment (images + care
Classifiers) is an investment we accumulate per Item over time, and items routinely stock out and
return; mirroring the PDF would discard that work on every stock-out and re-create the item later
as if brand new.

## Consequences

- The database intentionally does **not** match the latest PDF — it is a superset that grows over
  time. A future reader comparing the two will see "extra" zero-quantity rows; that is by design.
- Permanently keying on SKU assumes SKUs are never recycled for a different animal. SDC has not been
  observed to do this, but it could happen, so ingestion runs a **reuse guard**: a SKU reappearing
  under a materially different name is flagged for review rather than silently overwriting the prior
  Item's enrichment.
- `last_seen` is needed to distinguish a temporary stock-out (recent `last_seen`, qty 0) from a
  probably-discontinued item (old `last_seen`), since neither is deleted.
