"""Render the catalog grid: one card per Item."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from fishpage.catalog import CLASSIFIERS, Card

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
) -> str:
    """Render the whole ``<ul>`` grid: one window of Item cards plus, when ``has_more``, the
    load-more sentinel. This is the fragment the full catalog page includes and the fragment an
    HTMX filter change swaps in, so both paths emit identical card markup.

    Each :class:`Card` carries its Item, resolved Classifiers, and image record (or ``None``), so a
    card renders correctly un-enriched, enriched, or manually-overridden from one template.
    ``images_enabled`` adds the per-card manual upload form only when an image bucket is configured
    to receive it. ``next_url`` is where the sentinel points for the next window."""
    return _env.get_template("_grid.html").render(
        cards=cards,
        classifiers=CLASSIFIERS,
        images_enabled=images_enabled,
        has_more=has_more,
        next_url=next_url,
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
        search=search,
        sort=sort,
        selected_classifiers=selected_classifiers or {},
        images_enabled=images_enabled,
    )


def render_upload(*, message: str = "", error: bool = False) -> str:
    """Render the Stocklist upload page, optionally carrying a post-submit status line.

    ``message`` is shown above the form after a POST — a success summary, or a rejection reason
    flagged by ``error`` so an undated or stale upload reads as a failure, not a quiet no-op.
    """
    return _env.get_template("upload.html").render(message=message, error=error)
