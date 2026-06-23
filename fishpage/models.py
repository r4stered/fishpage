from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fishpage.category import derive_category


@dataclass(frozen=True)
class Item:
    """One row of the Stocklist — a specific livestock product at a specific size.

    Keyed permanently by ``sku``. ``size`` is the raw supplier grade/unit token;
    ``special_price`` is present on only some rows.

    ``last_seen`` is the date the SKU last appeared in a Stocklist — a storage fact set
    by ingestion, not a parse fact, so a freshly-parsed Item leaves it ``None`` until it
    is reconciled into the store.

    ``reuse_flagged`` is likewise a storage fact: ingestion sets it when this SKU
    reappeared under a materially different name, marking the Item for human review. A
    freshly-parsed Item is never flagged.
    """

    sku: str
    size: str
    name: str
    retail_price: Decimal
    special_price: Decimal | None
    qty_avail: int
    last_seen: date | None = None
    reuse_flagged: bool = False

    @property
    def category(self) -> str:
        """The Derived Category, computed purely from the SKU and name — never stored."""
        return derive_category(self.sku, self.name)
