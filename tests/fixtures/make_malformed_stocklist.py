"""Generate ``malformed_rows.pdf`` — a tiny Stocklist whose rows exercise the parser's
row-resilience: good rows, data rows that can't be parsed, and non-data lines.

Run with ``python tests/fixtures/make_malformed_stocklist.py`` to regenerate the committed
fixture. Text is placed at absolute point coordinates so each word lands in the column band
the parser reads (SKU left of 85, SIZE 85-145, NAME 145-320, RETAIL 320-385, SPECIAL 385-450,
QTY from 450). Keeping the x positions here in step with ``fishpage.parser`` is what makes the
extracted words reconstruct into the columns below.
"""

from pathlib import Path

from fpdf import FPDF

# x positions (points) chosen to fall inside the parser's column bands.
_SKU_X = 40.0
_SIZE_X = 95.0
_NAME_X = 150.0
_NAME_X2 = 200.0
_NAME_X3 = 250.0
_RETAIL_DOLLAR_X = 325.0
_RETAIL_VAL_X = 335.0
_SPECIAL_DOLLAR_X = 390.0
_SPECIAL_VAL_X = 400.0
_QTY_X = 460.0

# Each row is (baseline_y, [(x, text), ...]). Rows are spaced far enough in y that the parser
# groups them into distinct lines.
_ROWS = [
    # Header line: first token is not a SKU, so it is a non-data line.
    (
        72.0,
        [
            (_SKU_X, "SKU"),
            (_SIZE_X, "SIZE"),
            (_NAME_X, "NAME"),
            (_RETAIL_DOLLAR_X, "RETAIL"),
            (_SPECIAL_DOLLAR_X, "SPECIAL"),
            (_QTY_X, "QTY"),
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
