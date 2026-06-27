from dataclasses import dataclass
from datetime import date, datetime
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

    ``uploaded_by`` and ``uploaded_at`` are the Uploader — which human attached a ``MANUAL`` image
    and when. Both are meaningful only for ``MANUAL`` images; the auto-source path has no human
    Uploader and leaves them ``None``, the way it leaves ``license``/``attribution`` unset.
    """

    object_key: str
    license: str | None
    attribution: str | None
    source_url: str | None
    provenance: Provenance
    uploaded_by: str | None = None
    uploaded_at: datetime | None = None


@dataclass(frozen=True)
class Item:
    """One row of the Stocklist — a specific livestock product at a specific size.

    Keyed permanently by ``sku``. ``size`` is the raw supplier grade/unit token;
    ``special_price`` is present on only some rows.

    ``last_seen`` is the date the SKU last appeared in a Stocklist — a storage fact set
    by ingestion, not a parse fact, so a freshly-parsed Item leaves it ``None`` until it
    is reconciled into the store.

    ``first_seen`` is the date the SKU *first* appeared in a Stocklist — stamped once on
    insert and never advanced, so it distinguishes a first-ever sighting from a SKU that
    went out of stock and returned (which advances ``last_seen`` but not ``first_seen``).
    Like ``last_seen`` it is a storage fact, ``None`` until reconciled and on rows that
    predate the column.

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
    first_seen: date | None = None
    reuse_flagged: bool = False

    @property
    def category(self) -> str:
        """The Derived Category, computed purely from the SKU and name — never stored."""
        return derive_category(self.sku, self.name)

    @property
    def effective_price(self) -> Decimal:
        """The price that actually applies: the Special price when present, else Retail."""
        return self.retail_price if self.special_price is None else self.special_price


@dataclass(frozen=True)
class PriorSnapshot:
    """One SKU's price and quantity as the *previous* Stocklist printed them.

    The append-only history's most recent row strictly before the current Stocklist date, used to
    derive the week-over-week deltas the live Item row can no longer show: a price change since last
    week and a return to stock. ``effective_price`` mirrors :class:`Item` — the Special when
    present, else the Retail — so a change is judged on the price that actually applied last week.
    """

    retail_price: Decimal
    special_price: Decimal | None
    qty: int

    @property
    def effective_price(self) -> Decimal:
        return self.retail_price if self.special_price is None else self.special_price


@dataclass(frozen=True)
class PickLine:
    """One line of an Actor's Pick list: an Item gathered to order, with how many of it are wanted.

    The Item is carried whole so a line shows its SKU, name, and effective price without a second
    read, and ``line_total`` is what that line contributes to the Pick list's running total.
    """

    item: Item
    quantity: int

    @property
    def line_total(self) -> Decimal:
        """What this line adds to the running total: effective price times the quantity."""
        return self.item.effective_price * self.quantity
