"""Render the catalog grid: one card per Item."""

from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from fishpage.catalog import CLASSIFIERS, Card
from fishpage.models import PickLine

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def render_grid(
    cards: list[Card],
    *,
    images_enabled: bool = False,
    has_more: bool = False,
    next_url: str = "",
    total: int | None = None,
    oob: bool = False,
) -> str:
    """Render the whole ``<ul>`` grid: one window of Item cards plus, when ``has_more``, the
    load-more sentinel. This is the fragment the full catalog page includes and the fragment an
    HTMX filter change swaps in, so both paths emit identical card markup.

    Each :class:`Card` carries its Item, resolved Classifiers, and image record (or ``None``), so a
    card renders correctly un-enriched, enriched, or manually-overridden from one template.
    ``images_enabled`` adds the per-card manual upload form only when an image bucket is configured
    to receive it. ``next_url`` is where the sentinel points for the next window.

    The item count sits outside the grid, so an HTMX filter swap can't reach it by swapping the
    grid alone. ``oob`` appends an out-of-band ``#item-count`` carrying ``total`` (the size of the
    filtered set), updating the count in the same response; it stays off on the include path the
    full page uses, which renders the count itself."""
    return _env.get_template("_grid.html").render(
        cards=cards,
        classifiers=CLASSIFIERS,
        images_enabled=images_enabled,
        has_more=has_more,
        next_url=next_url,
        total=total,
        oob=oob,
    )


def render_cards(
    cards: list[Card],
    *,
    images_enabled: bool = False,
    has_more: bool = False,
    next_url: str = "",
) -> str:
    """Render just the cards of one window plus the trailing sentinel — the inner fragment, with no
    surrounding ``<ul>``. This is what a load-more request returns: HTMX replaces the spent sentinel
    with these, appending the next window into the existing grid in place."""
    return _env.get_template("_cards.html").render(
        cards=cards,
        classifiers=CLASSIFIERS,
        images_enabled=images_enabled,
        has_more=has_more,
        next_url=next_url,
    )


def render_catalog(
    cards: list[Card],
    *,
    include_out_of_stock: bool = False,
    categories: list[str] | None = None,
    selected_category: str | None = None,
    sizes: list[str] | None = None,
    selected_size: str | None = None,
    on_special: bool = False,
    new_only: bool = False,
    search: str = "",
    sort: str = "",
    selected_classifiers: dict[str, set[str]] | None = None,
    images_enabled: bool = False,
    total: int | None = None,
    has_more: bool = False,
    next_url: str = "",
) -> str:
    return _env.get_template("catalog.html").render(
        cards=cards,
        classifiers=CLASSIFIERS,
        total=len(cards) if total is None else total,
        has_more=has_more,
        next_url=next_url,
        include_out_of_stock=include_out_of_stock,
        categories=categories or [],
        selected_category=selected_category,
        sizes=sizes or [],
        selected_size=selected_size,
        on_special=on_special,
        new_only=new_only,
        search=search,
        sort=sort,
        selected_classifiers=selected_classifiers or {},
        images_enabled=images_enabled,
    )


def render_pick_button(sku: str, *, on_list: bool) -> str:
    """Render the card's Pick-list control: the "Add to Pick list" button, or — once the Item is on
    the list — the non-actionable "On Pick list ✓" marker the add swaps in over the button."""
    return _env.get_template("_pick_button.html").render(sku=sku, on_list=on_list)


def render_pick_list_fragment(lines: list[PickLine], total: Decimal) -> str:
    """Render just the Pick-list table fragment — the swap target. A quantity change or a line
    removal returns this so the lines and the running total below them stay in step in one swap."""
    return _env.get_template("_pick_list.html").render(lines=lines, total=total)


def render_pick_list(lines: list[PickLine], total: Decimal) -> str:
    """Render the whole Pick-list page: the chrome plus the same fragment an HTMX mutation swaps."""
    return _env.get_template("pick_list.html").render(lines=lines, total=total)


def pick_list_export_text(lines: list[PickLine]) -> str:
    """Build the order-ready plain-text Pick list the buyer pastes into the supplier's order.

    One tab-separated line per Item — SKU, name, quantity — so it pastes cleanly into a spreadsheet
    or order form. A line whose Item has dropped to zero availability is kept, never silently
    dropped, but tagged ``[OUT OF STOCK — last seen <date>]`` so the buyer notices before ordering;
    the last-seen date lets them tell a this-week stockout from a long-discontinued SKU.
    """
    rows = []
    for line in lines:
        cells = [line.item.sku, line.item.name, str(line.quantity)]
        if line.item.qty_avail == 0:
            seen = "never" if line.item.last_seen is None else line.item.last_seen.isoformat()
            cells.append(f"[OUT OF STOCK — last seen {seen}]")
        rows.append("\t".join(cells))
    return "\n".join(rows)


def render_upload(*, message: str = "", error: bool = False) -> str:
    """Render the Stocklist upload page, optionally carrying a post-submit status line.

    ``message`` is shown above the form after a POST — a success summary, or a rejection reason
    flagged by ``error`` so an undated or stale upload reads as a failure, not a quiet no-op.
    """
    return _env.get_template("upload.html").render(message=message, error=error)
