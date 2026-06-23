"""Parse the SDC freshwater Stocklist PDF into structured Item records.

The Stocklist is a flat table whose ``special_price`` column is blank on most rows
and whose ``SIZE`` column is sometimes blank too, so naive whitespace-splitting of the
extracted text mis-aligns columns. We instead reconstruct columns from pdfplumber word
x-coordinates, anchored to the header row's column positions.
"""

from decimal import Decimal
from pathlib import Path

import pdfplumber

from fishpage.models import Item

# Column boundaries (x0), derived from the header row of the sample Stocklist.
# A word belongs to a column if its x0 falls in [left, right).
_SIZE_LEFT = 85.0
_NAME_LEFT = 145.0
_RETAIL_LEFT = 320.0
_SPECIAL_LEFT = 385.0
_QTY_LEFT = 450.0


def parse_stocklist(path: str | Path) -> list[Item]:
    items: list[Item] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            rows = _group_words_into_rows(page.extract_words())
            for words in rows:
                item = _row_to_item(words)
                if item is not None:
                    items.append(item)
    return items


def _group_words_into_rows(words):
    by_top: dict[int, list] = {}
    for w in words:
        by_top.setdefault(round(w["top"]), []).append(w)
    return [sorted(by_top[t], key=lambda w: w["x0"]) for t in sorted(by_top)]


def _row_to_item(words) -> Item | None:
    if not words or not words[0]["text"][0].isdigit():
        return None  # header / non-data line

    sku = words[0]["text"]
    size_col = [w["text"] for w in words if _SIZE_LEFT <= w["x0"] < _NAME_LEFT]
    name_col = [w["text"] for w in words if _NAME_LEFT <= w["x0"] < _RETAIL_LEFT]
    retail_col = [w["text"] for w in words if _RETAIL_LEFT <= w["x0"] < _SPECIAL_LEFT]
    special_col = [w["text"] for w in words if _SPECIAL_LEFT <= w["x0"] < _QTY_LEFT]
    qty_col = [w["text"] for w in words if w["x0"] >= _QTY_LEFT]

    return Item(
        sku=sku,
        size=" ".join(size_col) if size_col else "-",
        name=" ".join(name_col),
        retail_price=_money(retail_col),
        special_price=_money(special_col) if special_col else None,
        qty_avail=int(qty_col[0]),
    )


def _money(col) -> Decimal:
    # Column words look like ["$", "12.99"]; drop the currency marker.
    digits = [tok for tok in col if tok != "$"]
    return Decimal(digits[0])
