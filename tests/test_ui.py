"""The catalog UI foundation: a shared base layout, an includable grid partial, and one
hand-rolled stylesheet served from /static.

These drive the rendered HTML the way a browser sees it — link tags, the shared head, the
served stylesheet — rather than asserting against template internals.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from fishpage.app import create_app
from fishpage.models import Item
from fishpage.render import render_grid
from fishpage.store import open_store, reconcile

JUN19 = date(2026, 6, 19)
ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)


def _client(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    return TestClient(create_app(conn))


def test_catalog_links_the_stylesheet_once(tmp_path):
    html = _client(tmp_path).get("/").text

    # The page pulls its styling from the single hand-rolled sheet on the static mount, linked
    # exactly once via the shared base layout — not inlined and not duplicated.
    assert html.count('<link rel="stylesheet" href="/static/app.css">') == 1


def test_both_pages_carry_the_responsive_viewport_from_the_shared_head(tmp_path):
    client = _client(tmp_path)

    # The shared base head supplies the viewport meta to every page, so responsive layout works
    # on a phone — the catalog page carried no viewport before it adopted the base layout.
    for path in ("/", "/upload"):
        assert '<meta name="viewport"' in client.get(path).text


def test_grid_partial_renders_cards_without_page_chrome():
    leaf = Item("110092", "-", "Leaf Fish", Decimal("5.99"), Decimal("4.99"), 30)

    html = render_grid([ORNATE_M, leaf])

    # The partial is just the grid of cards — one per Item, with the special-price badge where
    # there is one — so the HTMX path can swap it in on its own.
    assert html.count('data-sku="110042"') == 1
    assert html.count('data-sku="110092"') == 1
    assert '<span class="special-price">special $4.99</span>' in html
    # It carries none of the surrounding page: no document shell, no filter form.
    assert "<!doctype" not in html.lower()
    assert "<form" not in html


def test_stylesheet_is_served_from_the_static_mount(tmp_path):
    resp = _client(tmp_path).get("/static/app.css")

    # The hand-rolled sheet the page links must actually resolve on the existing /static mount,
    # as a non-empty CSS document — a broken link would leave every page unstyled.
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]
    assert resp.text.strip()


def test_stylesheet_adapts_to_dark_mode_with_a_single_accent(tmp_path):
    css = _client(tmp_path).get("/static/app.css").text

    # Auto light/dark with no toggle UI: the sheet re-themes under a dark preference. Colour is
    # scarce — a single named accent variable carries the only non-greyscale hue.
    assert "prefers-color-scheme: dark" in css
    assert "--accent" in css


def test_upload_page_links_the_same_stylesheet(tmp_path):
    html = _client(tmp_path).get("/upload").text

    # The upload page extends the same base layout, so it pulls in the one stylesheet too —
    # styling is shared, not re-declared per page.
    assert html.count('<link rel="stylesheet" href="/static/app.css">') == 1
