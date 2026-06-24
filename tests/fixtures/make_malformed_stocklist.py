"""Generate ``malformed_rows.pdf`` — a tiny Stocklist whose rows exercise the parser's
row-resilience: good rows, data rows that can't be parsed, and non-data lines.

Run with ``python tests/fixtures/make_malformed_stocklist.py`` to regenerate the committed
fixture. The parser reads column positions from the header row, so this fixture carries the
real Stocklist's header labels (``Sku``, ``SIZE``, ``nm``, ``retail_price``, ``special_price``,
``qty_avail``). Each header label's left edge becomes a column boundary; the data words below
are placed a comfortable margin inside their band so every column resolves unambiguously.
"""

from pathlib import Path

from fpdf import FPDF

# Header label x positions (points). Each one is the left edge of a column, and the parser
# turns the five right of "Sku" into the column boundaries.
_H_SKU = 55.0
_H_SIZE = 90.0
_H_NM = 150.0
_H_RETAIL = 325.0
_H_SPECIAL = 388.0
_H_QTY = 455.0

# Data x positions, each placed inside the band its header opens (left-aligned columns sit at
# their header's edge; the right-aligned price/qty columns sit further in, as in the real PDF).
_SKU_X = 55.0
_SIZE_X = 100.0
_NAME_X = 155.0
_NAME_X2 = 205.0
_NAME_X3 = 255.0
_RETAIL_DOLLAR_X = 335.0
_RETAIL_VAL_X = 360.0
# A misaligned row where the "$" marker drifted left into the name column, leaving two bare
# numbers in the retail band — the first would be silently read as the price.
_DRIFTED_DOLLAR_X = 305.0
_RETAIL_BLEED_X = 330.0
_RETAIL_BLEED_VAL_X = 362.0
_SPECIAL_DOLLAR_X = 398.0
_SPECIAL_VAL_X = 423.0
_QTY_X = 495.0

# Each row is (baseline_y, [(x, text), ...]). Rows are spaced far enough in y that the parser
# groups them into distinct lines.
_ROWS = [
    # Header line: the real Stocklist's labels at their column edges. The parser locates this
    # row and reads the column boundaries from it.
    (
        72.0,
        [
            (_H_SKU, "Sku"),
            (_H_SIZE, "SIZE"),
            (_H_NM, "nm"),
            (_H_RETAIL, "retail_price"),
            (_H_SPECIAL, "special_price"),
            (_H_QTY, "qty_avail"),
        ],
    ),
    # Good row, retail only.
    (
        92.0,
        [
            (_SKU_X, "100001"),
            (_SIZE_X, "M"),
            (_NAME_X, "Tetra"),
            (_NAME_X2, "Neon"),
            (_RETAIL_DOLLAR_X, "$"),
            (_RETAIL_VAL_X, "5.99"),
            (_QTY_X, "10"),
        ],
    ),
    # Malformed data row: 6-digit SKU but the QTY column is missing.
    (
        112.0,
        [
            (_SKU_X, "100002"),
            (_SIZE_X, "S"),
            (_NAME_X, "Barb"),
            (_NAME_X2, "Tiger"),
            (_RETAIL_DOLLAR_X, "$"),
            (_RETAIL_VAL_X, "3.99"),
        ],
    ),
    # Good row, with a special price.
    (
        132.0,
        [
            (_SKU_X, "100003"),
            (_NAME_X, "Pleco"),
            (_NAME_X2, "Gold"),
            (_RETAIL_DOLLAR_X, "$"),
            (_RETAIL_VAL_X, "19.99"),
            (_SPECIAL_DOLLAR_X, "$"),
            (_SPECIAL_VAL_X, "14.99"),
            (_QTY_X, "2"),
        ],
    ),
    # Malformed data row: the retail price token is non-numeric.
    (
        152.0,
        [
            (_SKU_X, "100004"),
            (_NAME_X, "Goby"),
            (_NAME_X2, "Bumblebee"),
            (_RETAIL_DOLLAR_X, "$"),
            (_RETAIL_VAL_X, "N/A"),
            (_QTY_X, "5"),
        ],
    ),
    # Good row: the retail price carries a thousands separator.
    (
        172.0,
        [
            (_SKU_X, "100005"),
            (_NAME_X, "Arowana"),
            (_NAME_X2, "Super"),
            (_NAME_X3, "Red"),
            (_RETAIL_DOLLAR_X, "$"),
            (_RETAIL_VAL_X, "1,299.00"),
            (_QTY_X, "1"),
        ],
    ),
    # Malformed data row: the QTY column is non-numeric.
    (
        192.0,
        [
            (_SKU_X, "100006"),
            (_NAME_X, "Snail"),
            (_NAME_X2, "Mystery"),
            (_RETAIL_DOLLAR_X, "$"),
            (_RETAIL_VAL_X, "4.99"),
            (_QTY_X, "CALL"),
        ],
    ),
    # Non-data line whose first token starts with a digit but is not a SKU (a printed date).
    # Columns are otherwise well-formed so loose "first char is a digit" detection would wrongly
    # mint it into an Item — row detection must require a full SKU to reject it.
    (
        212.0,
        [
            (_SKU_X, "6/19/26"),
            (_NAME_X, "Freshwater"),
            (_NAME_X2, "Stocklist"),
            (_RETAIL_DOLLAR_X, "$"),
            (_RETAIL_VAL_X, "0.00"),
            (_QTY_X, "0"),
        ],
    ),
    # All-digit token that isn't SKU length: a mis-detected data row, surfaced as a skip.
    (
        232.0,
        [
            (_SKU_X, "12345"),
            (_NAME_X, "Page"),
            (_NAME_X2, "footer"),
            (_RETAIL_DOLLAR_X, "$"),
            (_RETAIL_VAL_X, "0.00"),
            (_QTY_X, "0"),
        ],
    ),
    # Misaligned data row: the "$" marker has drifted left out of the retail column, leaving
    # two bare numbers in it. Reading the first as the price would silently record $12.50, so a
    # retail column that isn't the "$ <amount>" shape must be flagged rather than parsed.
    (
        252.0,
        [
            (_SKU_X, "100007"),
            (_NAME_X, "Loach"),
            (_NAME_X2, "Kuhli"),
            (_DRIFTED_DOLLAR_X, "$"),
            (_RETAIL_BLEED_X, "12.50"),
            (_RETAIL_BLEED_VAL_X, "5.99"),
            (_QTY_X, "3"),
        ],
    ),
]


def build(path: Path) -> None:
    pdf = FPDF(unit="pt", format="letter")
    pdf.add_page()
    pdf.set_font("Helvetica", size=9)
    for baseline_y, cells in _ROWS:
        for x, text in cells:
            pdf.text(x, baseline_y, text)
    path.write_bytes(bytes(pdf.output()))


if __name__ == "__main__":
    out = Path(__file__).parent / "malformed_rows.pdf"
    build(out)
    print(f"wrote {out}")
