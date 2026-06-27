"""FastAPI catalog layer: serve stored Items as JSON and as a grid of cards."""

import logging
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Form, Header, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from fishpage import observability
from fishpage.access import ACCESS_EMAIL_HEADER, actor_from_header
from fishpage.browse import SIZE_GRADES, browse
from fishpage.catalog import build_cards, filter_cards_by_classifiers
from fishpage.images import ImageDecodeError, ImageStore, store_image
from fishpage.ingest import ingest_pending, stocklist_date
from fishpage.models import Item, Provenance
from fishpage.render import (
    render_cards,
    render_catalog,
    render_grid,
    render_pick_button,
    render_pick_list,
    render_pick_list_fragment,
    render_upload,
)
from fishpage.store import (
    add_to_pick_list,
    all_classifier_overrides,
    all_enrichments,
    all_images,
    all_items,
    clear_enrichment,
    image_for,
    item_exists,
    latest_stocklist_date,
    pick_list_for,
    remove_from_pick_list,
    set_classifier_override,
    set_pick_list_quantity,
)

_STATIC = Path(__file__).parent / "static"

_log = logging.getLogger("fishpage")


def _upload_error(message: str) -> HTMLResponse:
    """Re-render the upload page with a rejection message and a 400, so a bad upload reads as a
    failure to both a browser and a caller checking the status code."""
    return HTMLResponse(render_upload(message=message, error=True), status_code=400)


def create_app(
    conn: sqlite3.Connection,
    *,
    incoming_dir: Path | None = None,
    processed_dir: Path | None = None,
    image_store: ImageStore | None = None,
    image_max_dimension: int = 1024,
    page_size: int = 60,
) -> FastAPI:
    app = FastAPI(title="Fishpage")
    observability.instrument_fastapi(app)
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    @app.get("/upload", response_class=HTMLResponse)
    def upload_page() -> HTMLResponse:
        return HTMLResponse(render_upload())

    @app.post("/upload", response_class=HTMLResponse)
    async def upload(
        file: UploadFile,
        access_email: str | None = Header(default=None, alias=ACCESS_EMAIL_HEADER),
    ) -> HTMLResponse:
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
            # Audit the mutation: a Stocklist landed through the HTTP route. The Actor and file name
            # ride as indexed fields, so the upload joins the same actor query as overrides and
            # re-enrichments.
            _log.info(
                "Stocklist %s ingested",
                name,
                extra={"actor": actor_from_header(access_email), "file": name},
            )
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

    @app.post("/items/{sku}/image")
    async def upload_image(
        sku: str,
        file: UploadFile,
        access_email: str | None = Header(default=None, alias=ACCESS_EMAIL_HEADER),
    ) -> Response:
        # Manual image upload: hand the raw bytes to the shared store_image seam, which optimizes to
        # WebP, puts them in the bucket, and records only the object key plus manual Provenance. The
        # bytes never touch SQLite — only the key does — so the WAL Litestream streams stays small.
        # A manual image is un-clobberable by re-enrichment.
        #
        # Access authenticates the human at the edge and injects their email; we credit it as the
        # Uploader, trusting it without verifying the JWT because no route reaches the origin
        # without passing through Access. Off the edge the header is absent and a neutral
        # placeholder stands in.
        if image_store is None:
            return JSONResponse({"detail": "image storage is not configured"}, status_code=503)
        if not item_exists(conn, sku):
            return JSONResponse({"detail": f"unknown SKU {sku}"}, status_code=404)
        try:
            store_image(
                image_store,
                conn,
                sku,
                await file.read(),
                provenance=Provenance.MANUAL,
                uploaded_by=actor_from_header(access_email),
                max_dimension=image_max_dimension,
            )
        except ImageDecodeError:
            # A non-image or corrupt upload can't be transcoded; reject it at the door rather than
            # storing a file the proxy could never serve.
            return JSONResponse(
                {"detail": "uploaded file is not a decodable image"}, status_code=400
            )
        # Post/redirect/get back to the catalog so a browser form lands on the refreshed grid (the
        # card now shows the proxied image) without re-posting on reload, and works with no JS.
        return RedirectResponse(url="/", status_code=303)

    @app.get("/items/{sku}/image")
    def serve_image(sku: str) -> Response:
        # Proxy the bytes through the app rather than redirecting to a public bucket URL, so the
        # image stays behind the Access edge exactly like the wholesale prices.
        if image_store is None:
            return Response(status_code=404)
        record = image_for(conn, sku)
        stored = None if record is None else image_store.get(record.object_key)
        if stored is None:
            return Response(status_code=404)
        return Response(content=stored.data, media_type=stored.content_type)

    @app.post("/items/{sku}/classifier")
    def override_classifier(
        sku: str,
        key: str = Form(...),
        value: str = Form(...),
        access_email: str | None = Header(default=None, alias=ACCESS_EMAIL_HEADER),
    ) -> Response:
        # A human correction: write a manual override that wins on read and survives re-enrichment.
        # The value is validated against the curated vocabulary, so an out-of-vocabulary correction
        # is a 400 rather than a stored value the catalog could never have produced itself.
        if not item_exists(conn, sku):
            return JSONResponse({"detail": f"unknown SKU {sku}"}, status_code=404)
        try:
            set_classifier_override(conn, sku, key, value)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        # Count the accepted correction, tagged by which Classifier — a rising rate is direct
        # evidence the AI reads are not trusted. Recorded after the override lands so a rejected
        # value (the 400 above) or an unknown SKU (the 404) never inflates the signal.
        observability.record_enrichment_override(classifier=key)
        # Audit the mutation: the Actor, SKU, and the corrected Classifier ride as indexed fields,
        # so a human correction joins the same actor query as uploads and re-enrichments.
        _log.info(
            "Classifier %s overridden for %s",
            key,
            sku,
            extra={
                "actor": actor_from_header(access_email),
                "sku": sku,
                "classifier": key,
                "value": value,
            },
        )
        # Post/redirect/get back to the catalog so the form lands on the refreshed grid — the card
        # now shows the manual badge — without re-posting on reload, and works with no JS.
        return RedirectResponse(url="/", status_code=303)

    def _pick_list_state(actor: str) -> tuple[list, Decimal]:
        # The Actor's lines plus their running total — the one read both the view and every mutating
        # route render from, so the total is summed in exactly one place.
        lines = pick_list_for(conn, actor)
        return lines, sum((line.line_total for line in lines), Decimal("0"))

    @app.post("/pick-list/{sku}", response_class=HTMLResponse)
    def pick_list_add(
        sku: str,
        access_email: str | None = Header(default=None, alias=ACCESS_EMAIL_HEADER),
        hx_request: str | None = Header(default=None),
    ) -> Response:
        # Gather an Item onto the current Actor's Pick list. The list is keyed by the Access email —
        # off the edge the neutral placeholder Actor owns it, the same fallback the rest of the app
        # uses. A repeated add is idempotent in the store, so a double-click never duplicates it.
        if not item_exists(conn, sku):
            return JSONResponse({"detail": f"unknown SKU {sku}"}, status_code=404)
        add_to_pick_list(conn, actor_from_header(access_email), sku)
        if hx_request:
            # Swap the card's button for the non-actionable "on pick list" marker.
            return HTMLResponse(render_pick_button(sku, on_list=True))
        # Post/redirect/get back to the catalog so a no-JS add lands on a full page, not a fragment.
        return RedirectResponse(url="/", status_code=303)

    @app.post("/pick-list/{sku}/quantity", response_class=HTMLResponse)
    def pick_list_set_quantity(
        sku: str,
        quantity: int = Form(...),
        access_email: str | None = Header(default=None, alias=ACCESS_EMAIL_HEADER),
        hx_request: str | None = Header(default=None),
    ) -> Response:
        actor = actor_from_header(access_email)
        set_pick_list_quantity(conn, actor, sku, quantity)
        if hx_request:
            # Swap the whole list fragment so the changed line and the running total stay in step.
            return HTMLResponse(render_pick_list_fragment(*_pick_list_state(actor)))
        return RedirectResponse(url="/pick-list", status_code=303)

    @app.post("/pick-list/{sku}/remove", response_class=HTMLResponse)
    def pick_list_remove(
        sku: str,
        access_email: str | None = Header(default=None, alias=ACCESS_EMAIL_HEADER),
        hx_request: str | None = Header(default=None),
    ) -> Response:
        actor = actor_from_header(access_email)
        remove_from_pick_list(conn, actor, sku)
        if hx_request:
            return HTMLResponse(render_pick_list_fragment(*_pick_list_state(actor)))
        return RedirectResponse(url="/pick-list", status_code=303)

    @app.get("/pick-list", response_class=HTMLResponse)
    def pick_list_view(
        access_email: str | None = Header(default=None, alias=ACCESS_EMAIL_HEADER),
        hx_request: str | None = Header(default=None),
    ) -> HTMLResponse:
        lines, total = _pick_list_state(actor_from_header(access_email))
        if hx_request:
            return HTMLResponse(render_pick_list_fragment(lines, total))
        return HTMLResponse(render_pick_list(lines, total))

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.post("/enrich/{sku}")
    def reenrich(
        sku: str,
        access_email: str | None = Header(default=None, alias=ACCESS_EMAIL_HEADER),
    ) -> JSONResponse:
        # On-demand re-enrich: clear the SKU's AI row so it falls back into the un-enriched queue,
        # where the background drainer refills it. Only the enrichment row goes — a human's manual
        # override lives in a separate table and is left intact, so a correction survives a re-run.
        if not item_exists(conn, sku):
            return JSONResponse({"detail": f"unknown SKU {sku}"}, status_code=404)
        clear_enrichment(conn, sku)
        # Audit the mutation: the Actor and SKU ride as indexed fields, so an on-demand re-enrich
        # joins the same actor query as uploads and overrides.
        _log.info(
            "Re-enrich queued for %s",
            sku,
            extra={"actor": actor_from_header(access_email), "sku": sku},
        )
        return JSONResponse({"sku": sku, "status": "queued"})

    def _filtered_cards(
        items: list[Item],
        selected_classifiers: dict[str, set[str]],
        latest_date: date | None,
    ) -> list:
        # Resolve every visible Item's Classifiers from one batch read each, then apply the
        # Classifier facets on the *resolved* values — so a manual override is what a chip filters
        # on, exactly as it is what a badge shows. latest_date drives the new-this-week badge.
        cards = build_cards(
            items,
            enrichments=all_enrichments(conn),
            images=all_images(conn),
            overrides=all_classifier_overrides(conn),
            latest_date=latest_date,
        )
        return filter_cards_by_classifiers(cards, selected_classifiers)

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        include_out_of_stock: bool = False,
        category: str | None = None,
        size: str | None = None,
        on_special: bool = False,
        new_only: bool = False,
        search: str = "",
        sort: str = "",
        page: int = 1,
        difficulty: list[str] = Query(default=[]),
        temperament: list[str] = Query(default=[]),
        plant_safe: list[str] = Query(default=[]),
        hx_request: str | None = Header(default=None),
    ) -> HTMLResponse:
        # Load the whole catalog once: the dropdown lists every category regardless of the
        # active filters, so narrowing to In stock in SQL would force a second read for the
        # vocabulary. Both view filters are applied in process instead.
        items = all_items(conn, include_out_of_stock=True)
        categories = sorted({item.category for item in items})
        # The latest Stocklist date drives "this week" — the same MAX(last_seen) ingestion keeps
        # monotonic — and is read once for both the new-only filter and the per-card badge.
        latest_date = latest_stocklist_date(conn)
        if not include_out_of_stock:
            items = [item for item in items if item.qty_avail > 0]
        items = browse(
            items,
            category=category,
            size=size,
            on_special=on_special,
            new_only=new_only,
            latest_date=latest_date,
            search=search,
            sort=sort,
        )
        selected_classifiers = {
            "difficulty": set(difficulty),
            "temperament": set(temperament),
            "plant_safe": set(plant_safe),
        }
        cards = _filtered_cards(items, selected_classifiers, latest_date)
        # Window the filtered cards so even the full ~900-Item set (include out-of-stock on) renders
        # one bounded page of DOM, not all of it. The trailing sentinel points at the next page with
        # the active filters preserved, so a load-more continues the same filtered view.
        page = max(page, 1)
        start = (page - 1) * page_size
        window = cards[start : start + page_size]
        has_more = start + page_size < len(cards)
        next_link = request.url.include_query_params(page=page + 1)
        next_url = next_link.path + ("?" + next_link.query if next_link.query else "")
        # One route, header-sniffed: an HTMX filter change swaps the whole grid in place (a fresh
        # first page), an HTMX load-more appends just the next page's cards, and a hard navigation
        # to the same URL renders the whole page. The pushed URL and the reloadable URL are
        # identical because both go through here.
        images_enabled = image_store is not None
        if hx_request and page > 1:
            return HTMLResponse(
                render_cards(
                    window, images_enabled=images_enabled, has_more=has_more, next_url=next_url
                )
            )
        if hx_request:
            return HTMLResponse(
                render_grid(
                    window,
                    images_enabled=images_enabled,
                    has_more=has_more,
                    next_url=next_url,
                    total=len(cards),
                    oob=True,
                )
            )
        return HTMLResponse(
            render_catalog(
                window,
                total=len(cards),
                has_more=has_more,
                next_url=next_url,
                include_out_of_stock=include_out_of_stock,
                categories=categories,
                selected_category=category,
                sizes=list(SIZE_GRADES),
                selected_size=size,
                on_special=on_special,
                new_only=new_only,
                search=search,
                sort=sort,
                selected_classifiers=selected_classifiers,
                images_enabled=images_enabled,
            )
        )

    return app
