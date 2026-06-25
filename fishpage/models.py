from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from fishpage.category import derive_category


class Provenance(StrEnum):
    """The recorded origin of an enriched value on an Item.

    ``MANUAL`` is authoritative: re-running Enrichment never overwrites it. The sourced values come
    from the best-effort image/Classifier pipeline.
    """

    MANUAL = "manual"
    WIKIMEDIA = "wikimedia"
    AI_GENERATED = "ai-generated"


@dataclass(frozen=True)
class ImageRecord:
    """One Item's image metadata — the R2 object key plus its license/attribution, never the bytes.

    ``provenance`` records who supplied it; a ``MANUAL`` image is un-clobberable by re-enrichment.
    """

    object_key: str
    license: str | None
    attribution: str | None
    source_url: str | None
    provenance: Provenance


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

    @property
    def effective_price(self) -> Decimal:
        """The price that actually applies: the Special price when present, else Retail."""
        return self.retail_price if self.special_price is None else self.special_price
