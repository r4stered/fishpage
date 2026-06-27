"""Browse pipeline: filter and sort the catalog by the active controls."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fishpage.models import Item, PriorSnapshot
from fishpage.search import match_names

# The livestock size grades offered by the Size filter, in ascending order with the
# unspecified grade first. Plant and dry-goods rows carry packaging units in the same
# column and simply match none of these.
SIZE_GRADES = ("-", "S", "M", "L", "Jumbo")


@dataclass(frozen=True)
class PriceChange:
    """An Item's effective-price move since the previous Stocklist: which way and by how much.

    ``direction`` is ``"up"`` or ``"down"``; ``delta`` is the absolute size of the move, always
    positive. Derived only when there is a previous snapshot and the price actually moved — an
    unchanged price yields no :class:`PriceChange` at all.
    """

    direction: str
    delta: Decimal


def is_new_this_week(item: Item, latest: date | None) -> bool:
    """Whether ``item`` is a first-ever sighting in the latest Stocklist — "new this week".

    True only when the Item's first-sight date is the latest Stocklist date: a SKU that went out
    of stock and returned has an older ``first_seen`` and is not new, and an Item that predates
    first-sight tracking (``first_seen`` is ``None``) is never new.
    """
    return item.first_seen is not None and item.first_seen == latest


def price_change(item: Item, prior: PriorSnapshot | None) -> PriceChange | None:
    """The Item's effective-price move since its previous snapshot, or ``None``.

    Compares the price that actually applies now (Special when present, else Retail) against the one
    that applied in the previous Stocklist. ``None`` when there is no prior snapshot (a SKU new this
    week has nothing to compare) or when the effective price is unchanged.
    """
    if prior is None:
        return None
    now = item.effective_price
    then = prior.effective_price
    if now == then:
        return None
    direction = "up" if now > then else "down"
    return PriceChange(direction=direction, delta=abs(now - then))


def is_back_in_stock(item: Item, prior: PriorSnapshot | None) -> bool:
    """Whether the Item was out of stock last Stocklist and is in stock now.

    True only when a previous snapshot exists, its quantity was zero, and the Item's current
    quantity is positive — a SKU new this week (no prior) is never "back" in stock.
    """
    return prior is not None and prior.qty == 0 and item.qty_avail > 0


def browse(
    items: list[Item],
    *,
    category: str | None = None,
    size: str | None = None,
    on_special: bool = False,
    new_only: bool = False,
    back_in_stock_only: bool = False,
    latest_date: date | None = None,
    priors: dict[str, PriorSnapshot] | None = None,
    search: str = "",
    sort: str = "",
) -> list[Item]:
    """Apply the browse controls to ``items`` and return the survivors in display order.

    ``category`` keeps only Items in that Derived Category; ``size`` matches the Item's raw
    size token exactly (per the overloaded-column design, the catalog filters on the stored
    token, so a grade like ``M`` never matches a packaging unit like ``POTTED``);
    ``on_special`` keeps only Items carrying a special price; ``new_only`` keeps only Items new
    this week, judged against ``latest_date`` (the latest Stocklist date); ``back_in_stock_only``
    keeps only Items whose previous snapshot in ``priors`` had zero quantity. A blank or omitted
    ``category``/``size`` is no filter.

    ``search`` narrows to fuzzy name matches and ranks survivors by relevance; an explicit
    ``sort`` then re-orders those survivors by effective price, overriding the relevance rank.
    """
    priors = priors or {}
    if category:
        items = [item for item in items if item.category == category]
    if size:
        items = [item for item in items if item.size == size]
    if on_special:
        items = [item for item in items if item.special_price is not None]
    if new_only:
        items = [item for item in items if is_new_this_week(item, latest_date)]
    if back_in_stock_only:
        items = [item for item in items if is_back_in_stock(item, priors.get(item.sku))]
    items = match_names(items, search)
    if sort == "price_asc":
        return sorted(items, key=lambda item: item.effective_price)
    if sort == "price_desc":
        return sorted(items, key=lambda item: item.effective_price, reverse=True)
    if sort == "newest":
        # Most recent first sighting first; an Item with no first-sight date sorts as oldest, so
        # the unknowns settle at the end rather than leading.
        return sorted(items, key=lambda item: item.first_seen or date.min, reverse=True)
    return list(items)
