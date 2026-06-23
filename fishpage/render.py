"""Render the catalog grid: one card per Item."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from fishpage.models import Item

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def render_catalog(items: list[Item]) -> str:
    return _env.get_template("catalog.html").render(items=items)
