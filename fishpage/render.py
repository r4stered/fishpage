"""Render the catalog grid: one card per Item."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from fishpage.models import Item

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def render_catalog(
    items: list[Item],
    *,
    include_out_of_stock: bool = False,
    categories: list[str] | None = None,
    selected_category: str | None = None,
    search: str = "",
) -> str:
    return _env.get_template("catalog.html").render(
        items=items,
        include_out_of_stock=include_out_of_stock,
        categories=categories or [],
        selected_category=selected_category,
        search=search,
    )
