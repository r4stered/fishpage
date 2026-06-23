"""Parse the SDC freshwater Stocklist PDF into structured Item records.

The Stocklist is a flat table whose ``special_price`` column is blank on most rows
and whose ``SIZE`` column is sometimes blank too, so naive whitespace-splitting of the
extracted text mis-aligns columns. We instead reconstruct columns from pdfplumber word
x-coordinates, anchored to the header row's column positions.
"""

import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from fishpage.models import Item

_log = logging.getLogger(__name__)

# Column boundaries (x0), derived from the header row of the sample Stocklist.
# A word belongs to a column if its x0 falls in [left, right).
_SIZE_LEFT = 85.0
_NAME_LEFT = 145.0
_RETAIL_LEFT = 320.0
_SPECIAL_LEFT = 385.0
_QTY_LEFT = 450.0

# A SKU is the supplier's six-digit identifier. A line is a data row only if its first token
# is exactly this — not merely a token that starts with a digit, which also matches dates and
# page-footer numbers.
_SKU_DIGITS = 6


class DuplicateSkuError(ValueError):
    """A single parsed Stocklist named the same SKU on more than one row.

    SKU is the permanent key and the same animal at two sizes is two *distinct*
    SKUs, so this shouldn't happen — but if it did, the store's
    ``ON CONFLICT(sku) DO UPDATE`` would silently keep only the last row. We fail
    the parse instead so the data loss surfaces.
    """


def check_unique_skus(items: list[Item]) -> None:
    """Raise :class:`DuplicateSkuError` if any SKU appears on more than one Item."""
    seen: set[str] = set()
    for item in items:
        if item.sku in seen:
            raise DuplicateSkuError(item.sku)
        seen.add(item.sku)


def parse_stocklist(path: str | Path) -> list[Item]:
    items: list[Item] = []
    skipped = 0
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            rows = _group_words_into_rows(page.extract_words())
            for words in rows:
                try:
                    item = _row_to_item(words)
                except (IndexError, ValueError, InvalidOperation) as exc:
                    skipped += 1
                    _log.warning(
                        "Skipping unparseable Stocklist row for SKU %s: %s", words[0]["text"], exc
                    )
                    continue
                if item is not None:
                    items.append(item)
    if skipped:
        _log.warning("Skipped %d unparseable Stocklist row(s).", skipped)
    check_unique_skus(items)
    return items


def _group_words_into_rows(words):
    by_top: dict[int, list] = {}
    for w in words:
        by_top.setdefault(round(w["top"]), []).append(w)
    return [sorted(by_top[t], key=lambda w: w["x0"]) for t in sorted(by_top)]


def _row_to_item(words) -> Item | None:
    sku = words[0]["text"] if words else ""
    if not (sku.isdigit() and len(sku) == _SKU_DIGITS):
        return None  # header / non-data line

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
    # Column words look like ["$", "12.99"]; drop the currency marker. A high price prints with
    # a thousands separator ("1,299.00") that Decimal can't read, so strip it.
    digits = [tok for tok in col if tok != "$"]
    return Decimal(digits[0].replace(",", ""))
