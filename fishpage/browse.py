"""Browse pipeline: filter and sort the catalog by the active controls."""

from fishpage.models import Item
from fishpage.search import match_names

# The livestock size grades offered by the Size filter, in ascending order with the
# unspecified grade first. Plant and dry-goods rows carry packaging units in the same
# column and simply match none of these.
SIZE_GRADES = ("-", "S", "M", "L", "Jumbo")


def browse(
    items: list[Item],
    *,
    category: str | None = None,
    size: str | None = None,
    on_special: bool = False,
    search: str = "",
    sort: str = "",
) -> list[Item]:
    """Apply the browse controls to ``items`` and return the survivors in display order.

    ``category`` keeps only Items in that Derived Category; ``size`` matches the Item's raw
    size token exactly (per the overloaded-column design, the catalog filters on the stored
    token, so a grade like ``M`` never matches a packaging unit like ``POTTED``);
    ``on_special`` keeps only Items carrying a special price. A blank or omitted
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
    items = match_names(items, search)
    if sort == "price_asc":
        return sorted(items, key=lambda item: item.effective_price)
    if sort == "price_desc":
        return sorted(items, key=lambda item: item.effective_price, reverse=True)
    return list(items)
