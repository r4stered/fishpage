from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Item:
    """One row of the Stocklist — a specific livestock product at a specific size.

    Keyed permanently by ``sku`` (see ADR-0001). ``size`` is the raw supplier
    grade/unit token (see ADR-0002); ``special_price`` is present on only some rows.

    ``last_seen`` is the date the SKU last appeared in a Stocklist — a storage fact set
    by ingestion (see ADR-0001), not a parse fact, so a freshly-parsed Item leaves it
    ``None`` until it is reconciled into the store.
    """

    sku: str
    size: str
    name: str
    retail_price: Decimal
    special_price: Decimal | None
    qty_avail: int
    last_seen: date | None = None
