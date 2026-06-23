"""FastAPI catalog layer: serve stored Items as JSON and as a grid of cards."""

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fishpage.models import Item
from fishpage.render import render_catalog
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
    }


def create_app(conn: sqlite3.Connection) -> FastAPI:
    app = FastAPI(title="Fishpage")
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    @app.get("/catalog")
    def catalog() -> JSONResponse:
        return JSONResponse([_item_dict(item) for item in all_items(conn)])

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(render_catalog(all_items(conn)))

    return app
