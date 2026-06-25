"""FastAPI catalog layer: serve stored Items as JSON and as a grid of cards."""

import sqlite3
from pathlib import Path

from fastapi import FastAPI, Header, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fishpage import observability
from fishpage.browse import SIZE_GRADES, browse
from fishpage.ingest import ingest_pending, stocklist_date
from fishpage.models import Item
from fishpage.render import render_catalog, render_grid, render_upload
from fishpage.store import all_items, latest_stocklist_date

_STATIC = Path(__file__).parent / "static"


def _upload_error(message: str) -> HTMLResponse:
    """Re-render the upload page with a rejection message and a 400, so a bad upload reads as a
    failure to both a browser and a caller checking the status code."""
    return HTMLResponse(render_upload(message=message, error=True), status_code=400)


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


def create_app(
    conn: sqlite3.Connection,
    *,
    incoming_dir: Path | None = None,
    processed_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Fishpage")
    observability.instrument_fastapi(app)
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    @app.get("/upload", response_class=HTMLResponse)
    def upload_page() -> HTMLResponse:
        return HTMLResponse(render_upload())

    @app.post("/upload", response_class=HTMLResponse)
    async def upload(file: UploadFile) -> HTMLResponse:
        assert incoming_dir is not None and processed_dir is not None
        name = Path(file.filename or "").name
        try:
            # Validate the date before writing anything: the upload has no retry loop, so an
            # undated drop must be rejected at the door rather than silently parked in incoming/.
            stocklist_date(Path(name))
        except ValueError:
            return _upload_error(
                f"{name or 'That file'} carries no valid M-D-YY date in its name. "
                "Rename it to the Stocklist's date (e.g. Freshwater_Stocklist_6-26-26.pdf) "
                "and upload again."
            )

        incoming_dir.mkdir(parents=True, exist_ok=True)
        drop = incoming_dir / name
        drop.write_bytes(await file.read())

        ingested = ingest_pending(conn, incoming_dir, processed_dir)
        if any(path.name == name for path in ingested):
            total = len(all_items(conn, include_out_of_stock=True))
            return HTMLResponse(
                render_upload(message=f"Ingested {name}. The catalog now holds {total} Items.")
            )

        # The core kept the drop: it is older than the catalog (monotonicity) or parsed to no
        # rows. Clear the litter — nothing will retry it — and say why.
        drop.unlink(missing_ok=True)
        latest = latest_stocklist_date(conn)
        if latest is not None and stocklist_date(Path(name)) <= latest:
            reason = f"its date is not newer than the catalog's current {latest}."
        else:
            reason = "no Items could be parsed from it (is the PDF complete?)."
        return _upload_error(f"{name} was not ingested: {reason}")

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/catalog")
    def catalog(
        include_out_of_stock: bool = False,
        category: str | None = None,
        size: str | None = None,
        on_special: bool = False,
        search: str = "",
        sort: str = "",
    ) -> JSONResponse:
        items = all_items(conn, include_out_of_stock=include_out_of_stock)
        items = browse(
            items,
            category=category,
            size=size,
            on_special=on_special,
            search=search,
            sort=sort,
        )
        return JSONResponse([_item_dict(item) for item in items])

    @app.get("/", response_class=HTMLResponse)
    def index(
        include_out_of_stock: bool = False,
        category: str | None = None,
        size: str | None = None,
        on_special: bool = False,
        search: str = "",
        sort: str = "",
        hx_request: str | None = Header(default=None),
    ) -> HTMLResponse:
        # Load the whole catalog once: the dropdown lists every category regardless of the
        # active filters, so narrowing to In stock in SQL would force a second read for the
        # vocabulary. Both view filters are applied in process instead.
        items = all_items(conn, include_out_of_stock=True)
        categories = sorted({item.category for item in items})
        if not include_out_of_stock:
            items = [item for item in items if item.qty_avail > 0]
        items = browse(
            items,
            category=category,
            size=size,
            on_special=on_special,
            search=search,
            sort=sort,
        )
        # One route, header-sniffed: an HTMX filter change swaps just the grid fragment in place,
        # while a hard navigation to the same URL renders the whole page. The pushed URL and the
        # reloadable URL are identical because both go through here.
        if hx_request:
            return HTMLResponse(render_grid(items))
        return HTMLResponse(
            render_catalog(
                items,
                include_out_of_stock=include_out_of_stock,
                categories=categories,
                selected_category=category,
                sizes=list(SIZE_GRADES),
                selected_size=size,
                on_special=on_special,
                search=search,
                sort=sort,
            )
        )

    return app
