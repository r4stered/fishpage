import logging
from decimal import Decimal
from pathlib import Path

import pytest

from fishpage.models import Item
from fishpage.parser import DuplicateSkuError, check_unique_skus, parse_stocklist

FIXTURE = Path(__file__).parent / "fixtures" / "Freshwater_Stocklist_6-19-26.pdf"
MALFORMED = Path(__file__).parent / "fixtures" / "malformed_rows.pdf"


def by_sku(items):
    return {item.sku: item for item in items}


def test_duplicate_sku_within_one_stocklist_is_rejected():
    # Two distinct rows claiming the same SKU — ON CONFLICT would silently keep only
    # the last, since SKU is the permanent key. The parse must fail loudly instead.
    ornate_m = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
    ornate_l = Item("110042", "L", "Bichir Ornate", Decimal("49.99"), None, 4)

    with pytest.raises(DuplicateSkuError):
        check_unique_skus([ornate_m, ornate_l])


def test_distinct_skus_pass_the_guard():
    # The same animal at two sizes is two distinct SKUs — that is allowed.
    ornate_m = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
    ornate_l = Item("110043", "L", "Bichir Ornate", Decimal("49.99"), None, 4)

    check_unique_skus([ornate_m, ornate_l])  # does not raise


def test_same_animal_two_sizes_are_two_items():
    items = by_sku(parse_stocklist(FIXTURE))

    ornate_m = items["110042"]
    ornate_l = items["110043"]

    assert ornate_m.name == "Bichir Ornate"
    assert ornate_m.size == "M"
    assert ornate_m.retail_price == Decimal("28.99")

    assert ornate_l.name == "Bichir Ornate"
    assert ornate_l.size == "L"
    assert ornate_l.retail_price == Decimal("49.99")


def test_special_price_is_captured_alongside_retail():
    leaf = by_sku(parse_stocklist(FIXTURE))["110092"]

    assert leaf.name == "Leaf Fish Leopard Ctenopoma"
    assert leaf.retail_price == Decimal("5.99")
    assert leaf.special_price == Decimal("4.99")


def test_blank_special_price_is_none():
    butterflyfish = by_sku(parse_stocklist(FIXTURE))["110012"]

    assert butterflyfish.name == "African Butterflyfish"
    assert butterflyfish.retail_price == Decimal("12.99")
    assert butterflyfish.special_price is None


def test_each_size_grade_is_parsed():
    items = by_sku(parse_stocklist(FIXTURE))

    assert items["110012"].size == "-"  # African Butterflyfish (unspecified)
    assert items["120091"].size == "S"  # Angelfish Full Black
    assert items["110042"].size == "M"  # Bichir Ornate
    assert items["110043"].size == "L"  # Bichir Ornate
    assert items["150013"].size == "Jumbo"  # Eel Fire


def test_blank_size_becomes_dash_and_packaging_unit_is_kept_raw():
    items = by_sku(parse_stocklist(FIXTURE))

    # A row whose SIZE cell is empty: name slides left but size is still "-".
    glofish = items["300262"]
    assert glofish.name == "GloFish Cory Pink"
    assert glofish.size == "-"

    # A plant row carries a packaging unit in the SIZE column; we keep it verbatim.
    micro_sword = items["757141"]
    assert micro_sword.name == "Micro Sword"
    assert micro_sword.size == "POTTED"


def test_a_malformed_row_is_skipped_not_fatal():
    # A row that can't be parsed (missing column, non-numeric price) must not sink the
    # whole batch — the good rows around it still parse.
    items = by_sku(parse_stocklist(MALFORMED))

    assert items["100001"].name == "Tetra Neon"
    assert items["100001"].qty_avail == 10
    assert items["100003"].name == "Pleco Gold"
    assert items["100003"].special_price == Decimal("14.99")

    # The malformed data rows are dropped rather than crashing or appearing half-parsed.
    assert "100002" not in items  # missing qty column
    assert "100004" not in items  # non-numeric retail price
    assert "100006" not in items  # non-numeric qty


def test_non_data_lines_are_not_parsed_as_items():
    # A line whose first token merely starts with a digit (a printed date, a page-footer
    # number) is not a data row. Row detection requires a full-length all-digit SKU, so these
    # lines are dropped rather than minted into bogus Items.
    items = by_sku(parse_stocklist(MALFORMED))

    assert "6/19/26" not in items  # a date footer
    assert "12345" not in items  # too short to be a SKU


def test_non_numeric_quantity_is_skipped_not_fatal():
    # A row whose qty cell isn't a number (a stray "CALL", a dash) can't yield a quantity —
    # it is dropped like any other unparseable row rather than crashing the batch.
    items = by_sku(parse_stocklist(MALFORMED))

    assert "100006" not in items
    assert items["100005"].name == "Arowana Super Red"  # the good row after it still parses


def test_thousands_separator_in_price_is_parsed_not_skipped():
    # A real high-priced Item prints its retail with a thousands separator. That comma is a
    # display artifact, not a malformed row — the price parses and the row survives.
    arowana = by_sku(parse_stocklist(MALFORMED))["100005"]

    assert arowana.name == "Arowana Super Red"
    assert arowana.retail_price == Decimal("1299.00")


def test_skipped_rows_are_logged_with_sku_and_a_summary_count(caplog):
    with caplog.at_level(logging.WARNING, logger="fishpage.parser"):
        parse_stocklist(MALFORMED)

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]

    # Each unparseable data row is named individually by its SKU.
    assert any("100002" in m for m in warnings)  # missing qty column
    assert any("100004" in m for m in warnings)  # non-numeric price
    assert any("100006" in m for m in warnings)  # non-numeric qty

    # Non-data lines are ignored up front, not counted as skipped rows.
    assert not any("6/19/26" in m for m in warnings)
    assert not any("12345" in m for m in warnings)

    # The batch surfaces how many rows it dropped.
    assert any("skip" in m.lower() and "3" in m for m in warnings)


def test_parses_every_row_with_unique_skus():
    items = parse_stocklist(FIXTURE)

    assert len(items) == 969
    assert len({item.sku for item in items}) == 969
