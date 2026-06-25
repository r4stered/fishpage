"""Render the catalog grid: one card per Item."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from fishpage.models import Item

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def render_grid(
    items: list[Item],
    image_skus: set[str] | None = None,
    *,
    images_enabled: bool = False,
) -> str:
    """Render just the grid of Item cards, the fragment shared by the full catalog page and the
    HTMX swap. The page includes the same partial, so both paths emit identical card markup.

    ``image_skus`` is the set of SKUs with a stored image; a card in it points at the proxy route,
    the rest fall back to the placeholder. ``images_enabled`` adds the per-card manual upload form
    only when an image bucket is configured to receive it."""
    return _env.get_template("_grid.html").render(
        items=items, image_skus=image_skus or set(), images_enabled=images_enabled
    )


def render_catalog(
    items: list[Item],
    *,
    include_out_of_stock: bool = False,
    categories: list[str] | None = None,
    selected_category: str | None = None,
    sizes: list[str] | None = None,
    selected_size: str | None = None,
    on_special: bool = False,
    search: str = "",
    sort: str = "",
    image_skus: set[str] | None = None,
    images_enabled: bool = False,
) -> str:
    return _env.get_template("catalog.html").render(
        items=items,
        include_out_of_stock=include_out_of_stock,
        categories=categories or [],
        selected_category=selected_category,
        sizes=sizes or [],
        selected_size=selected_size,
        on_special=on_special,
        search=search,
        sort=sort,
        image_skus=image_skus or set(),
        images_enabled=images_enabled,
    )


def render_upload(*, message: str = "", error: bool = False) -> str:
    """Render the Stocklist upload page, optionally carrying a post-submit status line.

    ``message`` is shown above the form after a POST — a success summary, or a rejection reason
    flagged by ``error`` so an undated or stale upload reads as a failure, not a quiet no-op.
    """
    return _env.get_template("upload.html").render(message=message, error=error)
