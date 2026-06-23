from decimal import Decimal
from pathlib import Path

import pytest

from fishpage.models import Item
from fishpage.parser import DuplicateSkuError, check_unique_skus, parse_stocklist

FIXTURE = Path(__file__).parent / "fixtures" / "Freshwater_Stocklist_6-19-26.pdf"


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


def test_parses_every_row_with_unique_skus():
    items = parse_stocklist(FIXTURE)

    assert len(items) == 969
    assert len({item.sku for item in items}) == 969
