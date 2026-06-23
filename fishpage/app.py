"""FastAPI catalog layer: serve stored Items as JSON and as a grid of cards."""

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fishpage.models import Item
from fishpage.render import render_catalog
from fishpage.search import match_names
from fishpage.store import all_items

_STATIC = Path(__file__).parent / "static"


def _item_dict(item: Item) -> dict:
    return {
        "sku": item.sku,
        "size": item.size,
        "name": item.name,
        "retail_price": str(item.retail_price),
        "special_price": None if item.special_price is None else str(item.special_price),
        "qty_avail": item.qty_avail,
        "category": item.category,
    }


def _by_category(items: list[Item], category: str | None) -> list[Item]:
    # The dropdown's "all categories" option submits an empty string, which means no filter.
    if not category:
        return items
    return [item for item in items if item.category == category]


def create_app(conn: sqlite3.Connection) -> FastAPI:
    app = FastAPI(title="Fishpage")
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    @app.get("/catalog")
    def catalog(
        include_out_of_stock: bool = False,
        category: str | None = None,
        search: str = "",
    ) -> JSONResponse:
        items = all_items(conn, include_out_of_stock=include_out_of_stock)
        items = _by_category(items, category)
        items = match_names(items, search)
        return JSONResponse([_item_dict(item) for item in items])

    @app.get("/", response_class=HTMLResponse)
    def index(
        include_out_of_stock: bool = False,
        category: str | None = None,
        search: str = "",
    ) -> HTMLResponse:
        # Load the whole catalog once: the dropdown lists every category regardless of the
        # active filters, so narrowing to In stock in SQL would force a second read for the
        # vocabulary. Both view filters are applied in process instead.
        items = all_items(conn, include_out_of_stock=True)
        categories = sorted({item.category for item in items})
        if not include_out_of_stock:
            items = [item for item in items if item.qty_avail > 0]
        items = _by_category(items, category)
        items = match_names(items, search)
        return HTMLResponse(
            render_catalog(
                items,
                include_out_of_stock=include_out_of_stock,
                categories=categories,
                selected_category=category,
                search=search,
            )
        )

    return app
