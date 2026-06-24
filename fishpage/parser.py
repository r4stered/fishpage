"""Parse the SDC freshwater Stocklist PDF into structured Item records.

The Stocklist is a flat table whose ``special_price`` column is blank on most rows
and whose ``SIZE`` column is sometimes blank too, so naive whitespace-splitting of the
extracted text mis-aligns columns. We instead reconstruct columns from pdfplumber word
x-coordinates, anchored to the header row's column positions.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from fishpage.models import Item

_log = logging.getLogger(__name__)

# The Stocklist's column headers, left to right. Their on-page left edges anchor the
# column boundaries, so re-tuning to a relabelled Stocklist means editing these strings.
_HEADER_LABELS = ("Sku", "SIZE", "nm", "retail_price", "special_price", "qty_avail")


@dataclass(frozen=True)
class RowColumns:
    """One Stocklist row's words bucketed into their columns (the SKU excepted —
    it is always the row's first word)."""

    size: list[str]
    name: list[str]
    retail: list[str]
    special: list[str]
    qty: list[str]


@dataclass(frozen=True)
class ColumnLayout:
    """The x-boundaries between Stocklist columns, derived from the header row.

    Each boundary is the left edge of the *next* column's header label: a word belongs to
    the last column whose header starts at or before it. The supplier left-aligns every
    column under its header, so each column's leftmost token (a price's ``$``, a name's
    first word) starts at roughly its header's left edge, while the column to its left
    never reaches that far. Anchoring to the next header's left edge therefore holds for
    both the text columns and the right-aligned price/quantity columns, and it does not
    depend on a header label being as wide as its column — ``nm`` is far narrower than the
    names beneath it. A Stocklist whose columns shift keeps parsing correctly as long as
    its header shifts with them. The SKU is taken as the row's first word, so only the
    five boundaries right of it are tracked.
    """

    _size_left: float
    _name_left: float
    _retail_left: float
    _special_left: float
    _qty_left: float

    @classmethod
    def from_page_words(cls, words) -> ColumnLayout | None:
        """Build a layout from a page's words, or ``None`` if it has no header row."""
        left_edges: dict[str, float] = {}
        for w in words:
            if w["text"] in _HEADER_LABELS and w["text"] not in left_edges:
                left_edges[w["text"]] = w["x0"]
        if len(left_edges) < len(_HEADER_LABELS):
            return None
        # Boundaries are the left edges of every column after the SKU.
        return cls(*(left_edges[label] for label in _HEADER_LABELS[1:]))

    def split_row(self, words) -> RowColumns:
        def band(low: float, high: float) -> list[str]:
            return [w["text"] for w in words if low <= w["x0"] < high]

        return RowColumns(
            size=band(self._size_left, self._name_left),
            name=band(self._name_left, self._retail_left),
            retail=band(self._retail_left, self._special_left),
            special=band(self._special_left, self._qty_left),
            qty=[w["text"] for w in words if w["x0"] >= self._qty_left],
        )


# A SKU is the supplier's six-digit identifier. A line is a non-data row (header, date, text
# footer) when its first token isn't all digits; an all-digit token of a different length looks
# like a mis-detected data row instead and is surfaced as a skip rather than dropped silently.
_SKU_DIGITS = 6


class MissingHeaderError(ValueError):
    """No page of the Stocklist carried the header row that anchors the columns.

    Column positions are read from the header rather than hardcoded, so without it there
    is nothing to align rows against. Rather than guess and risk silently mis-columned
    data, the parse fails — a truncated or unrecognised drop is surfaced, not ingested.
    """


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
        pages = [page.extract_words() for page in pdf.pages]

    layout = next(
        (lay for lay in map(ColumnLayout.from_page_words, pages) if lay is not None), None
    )
    if layout is None:
        raise MissingHeaderError(path)

    for page_words in pages:
        for words in _group_words_into_rows(page_words):
            try:
                item = _row_to_item(words, layout)
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


def _row_to_item(words, layout: ColumnLayout) -> Item | None:
    sku = words[0]["text"] if words else ""
    if not sku.isdigit():
        return None  # header, date, or text footer — not a data row at all
    if len(sku) != _SKU_DIGITS:
        raise ValueError(f"SKU {sku!r} is not {_SKU_DIGITS} digits")

    cols = layout.split_row(words)
    return Item(
        sku=sku,
        size=" ".join(cols.size) if cols.size else "-",
        name=" ".join(cols.name),
        retail_price=_money(cols.retail),
        special_price=_money(cols.special) if cols.special else None,
        qty_avail=int(cols.qty[0]),
    )


def _money(col) -> Decimal:
    # A price column is the "$" marker followed by exactly one amount, e.g. ["$", "12.99"].
    # Any other shape — a bare number with no marker, a stray token alongside the price — means
    # a word from an adjacent column drifted in, so the row is misaligned: raising surfaces it
    # as a skip rather than silently reading the wrong token as the price. A high price prints a
    # thousands separator ("1,299.00") that Decimal can't read, so strip it.
    if col[:1] != ["$"] or len(col) != 2:
        raise ValueError(f"price column is not '$ <amount>': {col!r}")
    return Decimal(col[1].replace(",", ""))
