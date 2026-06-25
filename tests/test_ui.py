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


def test_a_card_with_a_stored_image_points_at_the_proxy_route():
    html = render_grid([ORNATE_M], image_skus={"110042"})

    # The card's image is served from the app's own proxy route — never a public bucket URL — so it
    # stays behind the Access edge. The placeholder is replaced for an Item that has an image.
    assert 'src="/items/110042/image"' in html
    assert "placeholder.svg" not in html


def test_a_card_without_an_image_falls_back_to_the_placeholder():
    html = render_grid([ORNATE_M])

    # An un-enriched, image-less Item still renders — the placeholder stands in until one is added.
    assert "placeholder.svg" in html
    assert "/items/110042/image" not in html


def test_cards_offer_a_manual_upload_form_when_images_are_enabled():
    html = render_grid([ORNATE_M], images_enabled=True)

    # The manual upload path: a multipart form per card posting an image file to the SKU's route,
    # working without JavaScript (plain action/method, not HTMX-only).
    assert 'action="/items/110042/image"' in html
    assert 'method="post"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'type="file"' in html


def test_cards_hide_the_upload_form_when_images_are_disabled():
    html = render_grid([ORNATE_M])

    # With no image bucket configured the form would only 503, so it is not offered at all.
    assert 'action="/items/110042/image"' not in html


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


def test_htmx_runtime_is_vendored_on_the_static_mount(tmp_path):
    resp = _client(tmp_path).get("/static/htmx.min.js")

    # The HTMX runtime is carried by the app itself on the existing /static mount, not pulled from
    # a CDN — the app runs behind a tunnel with no public origin, so the UI must be self-contained
    # and testable offline. It is the htmx 4 beta the catalog is built against.
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert "4.0.0-beta4" in resp.text


def test_index_returns_only_the_grid_partial_for_an_htmx_request(tmp_path):
    resp = _client(tmp_path).get("/", headers={"HX-Request": "true"})

    # An HTMX request to the one catalog route gets back just the grid fragment to swap in — the
    # cards, with none of the surrounding page shell or filter form.
    html = resp.text
    assert 'data-sku="110042"' in html
    assert "<!doctype" not in html.lower()
    assert "<form" not in html


def test_index_returns_the_full_page_without_the_htmx_header(tmp_path):
    html = _client(tmp_path).get("/").text

    # A normal browser navigation (no HX-Request header) still renders the whole page: the document
    # shell and the filter form, with the grid inside it.
    assert "<!doctype" in html.lower()
    assert "<form" in html
    assert 'data-sku="110042"' in html


def test_htmx_grid_partial_reflects_the_active_filters(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    leaf = Item("110092", "-", "Leaf Fish", Decimal("5.99"), None, 30)
    reconcile(conn, [ORNATE_M, leaf], JUN19)
    client = TestClient(create_app(conn))

    resp = client.get("/", params={"search": "leaf"}, headers={"HX-Request": "true"})

    # The swapped-in fragment is filtered, not the whole catalog: the same query string that
    # narrows the full page narrows the partial, so the bookmarkable URL and the live swap agree.
    html = resp.text
    assert 'data-sku="110092"' in html
    assert 'data-sku="110042"' not in html


def test_filter_form_is_wired_to_swap_the_grid_and_push_the_url(tmp_path):
    html = _client(tmp_path).get("/").text
    form = html[html.index("<form") : html.index("</form>")]

    # Changing a filter issues an HTMX GET to the same canonical route, swaps just the grid
    # fragment in place, and pushes the matching URL into the address bar so a reload or the back
    # button reproduces the filtered view.
    assert 'hx-get="/"' in form
    assert 'hx-target=".catalog-grid"' in form
    assert 'hx-push-url="true"' in form


def test_filter_form_still_filters_with_javascript_disabled(tmp_path):
    html = _client(tmp_path).get("/").text
    form = html[html.index("<form") : html.index("</form>")]

    # With no JS, HTMX never loads — so the form must work as a plain HTML control: a native GET
    # to the same canonical route, submittable without script. The old inline onchange-submit is
    # gone (it both needed JS and would double-fire against HTMX); a real submit button replaces it.
    assert 'method="get"' in form
    assert 'action="/"' in form
    assert 'type="submit"' in form
    assert "this.form.submit()" not in form


def test_catalog_loads_htmx_from_the_static_mount_not_a_cdn(tmp_path):
    html = _client(tmp_path).get("/").text

    # The base layout pulls the runtime from the vendored copy, exactly once, and nothing on the
    # page reaches out to a CDN — a stray external script would defeat the no-public-origin design.
    assert html.count('<script src="/static/htmx.min.js"') == 1
    assert "unpkg.com" not in html
    assert "jsdelivr" not in html
    assert "cdn." not in html


def test_upload_page_links_the_same_stylesheet(tmp_path):
    html = _client(tmp_path).get("/upload").text

    # The upload page extends the same base layout, so it pulls in the one stylesheet too —
    # styling is shared, not re-declared per page.
    assert html.count('<link rel="stylesheet" href="/static/app.css">') == 1
