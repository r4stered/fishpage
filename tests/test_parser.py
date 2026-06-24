import logging
from decimal import Decimal
from pathlib import Path

import pytest
from fpdf import FPDF

from fishpage.models import Item
from fishpage.parser import (
    ColumnLayout,
    DuplicateSkuError,
    MissingHeaderError,
    check_unique_skus,
    parse_stocklist,
)

FIXTURE = Path(__file__).parent / "fixtures" / "Freshwater_Stocklist_6-19-26.pdf"
MALFORMED = Path(__file__).parent / "fixtures" / "malformed_rows.pdf"


def by_sku(items):
    return {item.sku: item for item in items}


def test_column_layout_boundaries_follow_the_header_labels():
    # The column a word lands in is decided by the header, not fixed coordinates: each
    # boundary is the left edge of the next column's header. A long name word (here at
    # x0=300, far right of the narrow "nm" label) still stays in the name column because
    # the boundary is anchored to where the retail header — not the name header — starts.
    header = [
        {"text": "Sku", "x0": 50.0},
        {"text": "SIZE", "x0": 90.0},
        {"text": "nm", "x0": 150.0},
        {"text": "retail_price", "x0": 328.0},
        {"text": "special_price", "x0": 390.0},
        {"text": "qty_avail", "x0": 458.0},
    ]
    layout = ColumnLayout.from_page_words(header)
    assert layout is not None

    cols = layout.split_row(
        [
            {"text": "110042", "x0": 54.0},
            {"text": "M", "x0": 92.0},
            {"text": "Bichir", "x0": 151.0},
            {"text": "Longfin", "x0": 300.0},  # wide name, well right of the "nm" label
            {"text": "$", "x0": 329.0},
            {"text": "28.99", "x0": 354.0},  # right-aligned: starts left of its label
            {"text": "15", "x0": 495.0},  # right-aligned: starts right of its label
        ]
    )

    assert cols.size == ["M"]
    assert cols.name == ["Bichir", "Longfin"]
    assert cols.retail == ["$", "28.99"]
    assert cols.special == []
    assert cols.qty == ["15"]


def test_header_labels_out_of_column_order_are_rejected():
    # All six labels are present, but "nm" and "retail_price" sit at swapped x positions, so
    # the edges no longer read left to right. Building bands from them would make a band's low
    # exceed its high and silently empty a column, so the degenerate header is rejected loudly.
    header = [
        {"text": "Sku", "x0": 50.0},
        {"text": "SIZE", "x0": 90.0},
        {"text": "nm", "x0": 330.0},  # where retail should be
        {"text": "retail_price", "x0": 150.0},  # where nm should be
        {"text": "special_price", "x0": 390.0},
        {"text": "qty_avail", "x0": 458.0},
    ]

    with pytest.raises(MissingHeaderError):
        ColumnLayout.from_page_words(header)


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
    # A line whose first token isn't all digits (a printed date, a header) is not a data row,
    # so it is dropped rather than minted into a bogus Item.
    items = by_sku(parse_stocklist(MALFORMED))

    assert "6/19/26" not in items  # a date footer


def test_wrong_length_sku_is_logged_not_silently_dropped(caplog):
    # An all-digit token that isn't SKU length looks like a data row that got mis-detected,
    # not a header or date. Dropping it silently could hide a real Item, so it is surfaced as a
    # skipped row rather than ignored like a genuine non-data line.
    with caplog.at_level(logging.WARNING, logger="fishpage.parser"):
        items = by_sku(parse_stocklist(MALFORMED))

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]

    assert "12345" not in items
    assert any("12345" in m for m in warnings)


def test_name_word_bleeding_into_price_column_is_flagged_not_silently_parsed(caplog):
    # A stray numeric token drifted into the retail column ahead of the price. Taking the
    # first number would silently record the wrong price, so a price column that isn't the
    # "$ <amount>" shape is treated as a misaligned row: skipped and named in the log.
    with caplog.at_level(logging.WARNING, logger="fishpage.parser"):
        items = by_sku(parse_stocklist(MALFORMED))

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]

    assert "100007" not in items  # not parsed into a corrupt $12.50 Item
    assert any("100007" in m for m in warnings)


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
    assert any("100007" in m for m in warnings)  # price column missing its "$" marker

    # A genuine non-data line (a date) is ignored up front, not counted as a skipped row.
    assert not any("6/19/26" in m for m in warnings)

    # The batch surfaces how many rows it dropped: the 4 unparseable data rows above plus the
    # wrong-length token, which is treated as a mis-detected data row rather than ignored.
    assert any("Skipped 5 unparseable" in m for m in warnings)


def test_stocklist_with_no_header_raises(tmp_path):
    # Column positions come from the header, so a Stocklist that never carries one (a
    # truncated drop, or an unrecognised layout) cannot be aligned. Rather than guess with
    # stale coordinates and risk silently mis-columned data, the parse fails loudly.
    pdf = FPDF(unit="pt", format="letter")
    pdf.add_page()
    pdf.set_font("Helvetica", size=9)
    data_row = [
        (55.0, "100001"),
        (100.0, "M"),
        (155.0, "Tetra"),
        (335.0, "$"),
        (360.0, "5.99"),
        (495.0, "10"),
    ]
    for x, text in data_row:
        pdf.text(x, 92.0, text)
    headerless = tmp_path / "headerless.pdf"
    headerless.write_bytes(bytes(pdf.output()))

    with pytest.raises(MissingHeaderError):
        parse_stocklist(headerless)


def test_parses_every_row_with_unique_skus():
    items = parse_stocklist(FIXTURE)

    assert len(items) == 969
    assert len({item.sku for item in items}) == 969
