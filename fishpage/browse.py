"""Browse pipeline: filter and sort the catalog by the active controls."""

from datetime import date

from fishpage.models import Item
from fishpage.search import match_names

# The livestock size grades offered by the Size filter, in ascending order with the
# unspecified grade first. Plant and dry-goods rows carry packaging units in the same
# column and simply match none of these.
SIZE_GRADES = ("-", "S", "M", "L", "Jumbo")


def is_new_this_week(item: Item, latest: date | None) -> bool:
    """Whether ``item`` is a first-ever sighting in the latest Stocklist — "new this week".

    True only when the Item's first-sight date is the latest Stocklist date: a SKU that went out
    of stock and returned has an older ``first_seen`` and is not new, and an Item that predates
    first-sight tracking (``first_seen`` is ``None``) is never new.
    """
    return item.first_seen is not None and item.first_seen == latest


def browse(
    items: list[Item],
    *,
    category: str | None = None,
    size: str | None = None,
    on_special: bool = False,
    new_only: bool = False,
    latest_date: date | None = None,
    search: str = "",
    sort: str = "",
) -> list[Item]:
    """Apply the browse controls to ``items`` and return the survivors in display order.

    ``category`` keeps only Items in that Derived Category; ``size`` matches the Item's raw
    size token exactly (per the overloaded-column design, the catalog filters on the stored
    token, so a grade like ``M`` never matches a packaging unit like ``POTTED``);
    ``on_special`` keeps only Items carrying a special price; ``new_only`` keeps only Items new
    this week, judged against ``latest_date`` (the latest Stocklist date). A blank or omitted
    ``category``/``size`` is no filter.

    ``search`` narrows to fuzzy name matches and ranks survivors by relevance; an explicit
    ``sort`` then re-orders those survivors by effective price, overriding the relevance rank.
    """
    if category:
        items = [item for item in items if item.category == category]
    if size:
        items = [item for item in items if item.size == size]
    if on_special:
        items = [item for item in items if item.special_price is not None]
    if new_only:
        items = [item for item in items if is_new_this_week(item, latest_date)]
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
