from dataclasses import replace
from datetime import date
from decimal import Decimal

from fishpage.browse import (
    browse,
    is_back_in_stock,
    is_new_this_week,
    price_change,
)
from fishpage.models import Item, PriorSnapshot

JUN19 = date(2026, 6, 19)
JUN26 = date(2026, 6, 26)


def _seen(item: Item, *, first: date, last: date) -> Item:
    """An ``Item`` stamped with first/last sighting dates, the storage facts browse derives on."""
    return replace(item, first_seen=first, last_seen=last)


# retail 10, no special → effective price 10
PLAIN = Item("1", "M", "Plain", Decimal("10.00"), None, 5)
# retail 100 but special 5 → effective price 5, cheaper than PLAIN despite the higher retail
DISCOUNTED = Item("2", "M", "Discounted", Decimal("100.00"), Decimal("5.00"), 5)

SMALL = Item("3", "S", "Small One", Decimal("4.00"), None, 5)
JUMBO = Item("4", "Jumbo", "Big One", Decimal("80.00"), None, 5)
# A plant row whose raw size token is a packaging unit, not a grade.
POTTED = Item("5", "POTTED", "Sword Plant", Decimal("3.50"), None, 5)


def test_new_this_week_is_a_first_ever_sighting_in_the_latest_stocklist():
    # First seen in the latest Stocklist → new this week.
    brand_new = _seen(PLAIN, first=JUN26, last=JUN26)
    # Seen before and back this week: last_seen is the latest, but first_seen is not — a returning
    # Item, not a new one.
    returning = _seen(DISCOUNTED, first=JUN19, last=JUN26)
    # Predates first-sight tracking → no first_seen → never new.
    legacy = _seen(SMALL, first=JUN19, last=JUN19)
    legacy = replace(legacy, first_seen=None)

    assert is_new_this_week(brand_new, JUN26) is True
    assert is_new_this_week(returning, JUN26) is False
    assert is_new_this_week(legacy, JUN26) is False


def test_new_only_keeps_only_items_new_in_the_latest_stocklist():
    brand_new = _seen(PLAIN, first=JUN26, last=JUN26)
    returning = _seen(DISCOUNTED, first=JUN19, last=JUN26)

    result = browse([brand_new, returning], new_only=True, latest_date=JUN26)

    assert [item.sku for item in result] == ["1"]


def test_new_only_off_keeps_everything():
    brand_new = _seen(PLAIN, first=JUN26, last=JUN26)
    returning = _seen(DISCOUNTED, first=JUN19, last=JUN26)

    result = browse([brand_new, returning], new_only=False, latest_date=JUN26)

    assert {item.sku for item in result} == {"1", "2"}


def test_price_change_reads_direction_and_delta_off_the_effective_price():
    # Retail rose 10 → 12 with no special either week: an up move of 2 on the effective price.
    item = replace(PLAIN, retail_price=Decimal("12.00"))
    prior = PriorSnapshot(Decimal("10.00"), None, 5)
    change = price_change(item, prior)
    assert change is not None
    assert change.direction == "up"
    assert change.delta == Decimal("2.00")

    # A special this week drops the effective price below last week's retail — a down move judged on
    # the price that actually applies, not the retail.
    discounted = replace(PLAIN, retail_price=Decimal("10.00"), special_price=Decimal("6.00"))
    down = price_change(discounted, prior)
    assert down is not None
    assert down.direction == "down"
    assert down.delta == Decimal("4.00")


def test_price_change_is_none_with_no_prior_or_an_unchanged_price():
    # A SKU new this week has no prior snapshot — nothing to compare.
    assert price_change(PLAIN, None) is None
    # Same effective price as last week → no change to surface.
    assert price_change(PLAIN, PriorSnapshot(Decimal("10.00"), None, 5)) is None


def test_back_in_stock_needs_a_zero_prior_and_a_positive_now():
    in_stock = replace(PLAIN, qty_avail=5)
    # Prior qty 0, now positive → back in stock.
    assert is_back_in_stock(in_stock, PriorSnapshot(Decimal("10.00"), None, 0)) is True
    # Prior was already in stock → not "back".
    assert is_back_in_stock(in_stock, PriorSnapshot(Decimal("10.00"), None, 3)) is False
    # No prior snapshot (new this week) → never back in stock.
    assert is_back_in_stock(in_stock, None) is False
    # Prior was 0 but still out of stock now → not back yet.
    out_now = replace(PLAIN, qty_avail=0)
    assert is_back_in_stock(out_now, PriorSnapshot(Decimal("10.00"), None, 0)) is False


def test_back_in_stock_only_keeps_only_returned_items():
    returned = replace(PLAIN, qty_avail=5)  # was 0, now 5
    steady = replace(DISCOUNTED, qty_avail=5)  # was in stock all along
    priors = {
        returned.sku: PriorSnapshot(Decimal("10.00"), None, 0),
        steady.sku: PriorSnapshot(Decimal("100.00"), Decimal("5.00"), 5),
    }

    result = browse([returned, steady], back_in_stock_only=True, priors=priors)

    assert [item.sku for item in result] == ["1"]


def test_sort_newest_orders_by_first_sight_descending_unknowns_last():
    this_week = _seen(PLAIN, first=JUN26, last=JUN26)
    last_week = _seen(DISCOUNTED, first=JUN19, last=JUN26)
    legacy = replace(SMALL, first_seen=None, last_seen=JUN26)

    result = browse([last_week, legacy, this_week], sort="newest")

    # Newest first sighting leads; the Item with no first-sight date sorts last.
    assert [item.sku for item in result] == ["1", "2", "3"]


def test_sort_price_asc_orders_by_effective_price_not_retail():
    result = browse([PLAIN, DISCOUNTED], sort="price_asc")

    # DISCOUNTED's special (5) beats PLAIN's retail (10), even though its retail is higher,
    # so the effective price — not the retail — decides the order.
    assert [item.sku for item in result] == ["2", "1"]


def test_sort_price_desc_orders_by_descending_effective_price():
    result = browse([DISCOUNTED, PLAIN], sort="price_desc")

    # PLAIN's effective 10 leads DISCOUNTED's effective 5 when sorting high to low.
    assert [item.sku for item in result] == ["1", "2"]


def test_size_filter_keeps_only_the_matching_raw_grade():
    result = browse([PLAIN, SMALL, JUMBO, POTTED], size="M")

    # Exact match on the raw size token: only the M grade survives; the packaging-unit
    # row never matches a grade value.
    assert [item.sku for item in result] == ["1"]


def test_empty_size_filters_nothing():
    result = browse([PLAIN, SMALL, JUMBO], size="")

    assert {item.sku for item in result} == {"1", "3", "4"}


def test_on_special_keeps_only_items_with_a_special_price():
    result = browse([PLAIN, DISCOUNTED, SMALL], on_special=True)

    # Only DISCOUNTED carries a special price; the retail-only Items drop out.
    assert [item.sku for item in result] == ["2"]


def test_on_special_off_keeps_everything():
    result = browse([PLAIN, DISCOUNTED], on_special=False)

    assert {item.sku for item in result} == {"1", "2"}


# SKU block + leading name word drive the Derived Category. 120... + "Angelfish" → Angelfish.
ANGEL = Item("120091", "S", "Angelfish Full Black", Decimal("9.99"), None, 5)
BARB = Item("170011", "-", "Barb Cherry", Decimal("3.99"), None, 5)


def test_category_filter_keeps_only_the_matching_category():
    result = browse([ANGEL, BARB], category="Angelfish")

    assert [item.sku for item in result] == ["120091"]


def test_empty_category_filters_nothing():
    result = browse([ANGEL, BARB], category="")

    assert {item.sku for item in result} == {"120091", "170011"}


ANGEL_KOI = Item("120093", "M", "Angelfish Koi", Decimal("12.99"), None, 5)
ANGEL_KOI_EXACT = Item("120094", "M", "Angel Koi", Decimal("14.99"), None, 5)


def test_search_filters_and_ranks_by_relevance():
    result = browse([ANGEL_KOI, ANGEL_KOI_EXACT, BARB], search="angel koi")

    # The Barb drops out; the tighter "Angel Koi" outranks the padded "Angelfish Koi".
    assert [item.sku for item in result] == ["120094", "120093"]


def test_price_sort_overrides_search_relevance_order():
    result = browse([ANGEL_KOI, ANGEL_KOI_EXACT], search="angel koi", sort="price_asc")

    # Both match the search, but an explicit price sort wins over relevance ranking:
    # the cheaper Angelfish Koi (12.99) leads the pricier exact match (14.99).
    assert [item.sku for item in result] == ["120093", "120094"]
