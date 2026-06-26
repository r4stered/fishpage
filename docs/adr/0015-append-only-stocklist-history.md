# Capture a per-Stocklist price/quantity history, append-only

*Status: proposed — decided in a design session, not yet built.*

Ingestion upserts by SKU and overwrites the prior price and quantity
([ADR 0001](0001-sku-permanent-key-upsert-never-delete.md)), so the week-over-week change a buyer most
wants — "price went up", "back in stock" — is destroyed the moment a new Stocklist lands. The live row
knows only the current state.

Alongside the upsert, ingestion appends an immutable per-SKU snapshot — Stocklist date, retail and
special price, quantity — to a history table that is never updated or deleted. This is a deliberate
deviation from ADR 0001's overwrite model: the live row stays the fast current-state read, while
history is a separate append-only ledger. It powers the weekly delta view (new / back-in-stock /
price-changed since last week) and feeds the ingestion sanity report that flags a structurally
implausible parse.

The cost is DB growth in the Litestream-streamed WAL, but it is bounded and small — roughly one row
per Item per Stocklist, on the order of 50k rows per year at the current catalog size — so it does not
threaten the single-Machine, single-file SQLite posture of
[ADR 0008](0008-sqlite-litestream-object-storage.md).
