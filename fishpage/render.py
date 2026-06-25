"""Render the catalog grid: one card per Item."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from fishpage.catalog import CLASSIFIERS, Card

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def render_grid(cards: list[Card], *, images_enabled: bool = False) -> str:
    """Render just the grid of Item cards, the fragment shared by the full catalog page and the
    HTMX swap. The page includes the same partial, so both paths emit identical card markup.

    Each :class:`Card` carries its Item, resolved Classifiers, and image record (or ``None``), so a
    card renders correctly un-enriched, enriched, or manually-overridden from one template.
    ``images_enabled`` adds the per-card manual upload form only when an image bucket is configured
    to receive it."""
    return _env.get_template("_grid.html").render(
        cards=cards, classifiers=CLASSIFIERS, images_enabled=images_enabled
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
) -> str:
    return _env.get_template("catalog.html").render(
        cards=cards,
        classifiers=CLASSIFIERS,
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
