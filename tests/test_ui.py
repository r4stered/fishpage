"""The catalog UI foundation: a shared base layout, an includable grid partial, and one
hand-rolled stylesheet served from /static.

These drive the rendered HTML the way a browser sees it — link tags, the shared head, the
served stylesheet — rather than asserting against template internals.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from fishpage.app import create_app
from fishpage.catalog import build_cards
from fishpage.models import ImageRecord, Item, Provenance
from fishpage.render import render_grid
from fishpage.store import open_store, reconcile

JUN19 = date(2026, 6, 19)
ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)


def _cards(items, *, images=None, enrichments=None, overrides=None):
    """Build the Card bundles render_grid now takes, from raw Items plus optional enrichment,
    image, and override maps — the same join the route does, so a unit test drives the real path."""
    return build_cards(
        items,
        enrichments=enrichments or {},
        images=images or {},
        overrides=overrides or {},
    )


def _image_record(
    object_key: str = "img/x.webp",
    *,
    attribution: str | None = None,
    provenance: Provenance = Provenance.MANUAL,
) -> ImageRecord:
    return ImageRecord(
        object_key=object_key,
        license=None,
        attribution=attribution,
        source_url=None,
        provenance=provenance,
    )


def _client(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    return TestClient(create_app(conn))


def _windowed_client(tmp_path, *, count, page_size):
    """A catalog of ``count`` in-stock Items behind a tiny ``page_size``, so a handful of Items is
    enough to exercise windowing without standing up the full ~900-Item Stocklist."""
    conn = open_store(tmp_path / "fishpage.db")
    items = [
        Item(f"1100{n:02d}", "M", f"Tetra {n:02d}", Decimal("4.99"), None, 5) for n in range(count)
    ]
    reconcile(conn, items, JUN19)
    return TestClient(create_app(conn, page_size=page_size))


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

    html = render_grid(_cards([ORNATE_M, leaf]))

    # The partial is just the grid of cards — one per Item, with the special-price badge where
    # there is one — so the HTMX path can swap it in on its own.
    assert html.count('data-sku="110042"') == 1
    assert html.count('data-sku="110092"') == 1
    assert '<span class="special-price">special $4.99</span>' in html
    # It carries none of the surrounding page: no document shell, no filter form. (Per-card
    # override forms are part of a card and so do belong in the partial.)
    assert "<!doctype" not in html.lower()
    assert 'class="filters"' not in html


def test_a_card_with_a_stored_image_points_at_the_proxy_route():
    html = render_grid(_cards([ORNATE_M], images={"110042": _image_record()}))

    # The card's image is served from the app's own proxy route — never a public bucket URL — so it
    # stays behind the Access edge. The placeholder is replaced for an Item that has an image.
    assert 'src="/items/110042/image"' in html
    assert "placeholder.svg" not in html


def test_a_card_without_an_image_falls_back_to_the_placeholder():
    html = render_grid(_cards([ORNATE_M]))

    # An un-enriched, image-less Item still renders — the placeholder stands in until one is added.
    assert "placeholder.svg" in html
    assert "/items/110042/image" not in html


def test_cards_offer_a_manual_upload_form_when_images_are_enabled():
    html = render_grid(_cards([ORNATE_M]), images_enabled=True)

    # The manual upload path: a multipart form per card posting an image file to the SKU's route,
    # working without JavaScript (plain action/method, not HTMX-only).
    assert 'action="/items/110042/image"' in html
    assert 'method="post"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'type="file"' in html


def test_cards_hide_the_upload_form_when_images_are_disabled():
    html = render_grid(_cards([ORNATE_M]))

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
    assert 'class="filters"' not in html


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


def test_cards_render_classifier_badges_marked_by_provenance():
    from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament

    enrichment = EnrichmentResult(
        scientific_name=None,
        common_name=None,
        difficulty=Difficulty.ADVANCED,  # AI read, overridden below
        temperament=Temperament.PEACEFUL,  # AI read, stands
        plant_safe=PlantSafe.UNKNOWN,  # honest gap, no badge
        strain_specific=False,
    )
    html = render_grid(
        _cards(
            [ORNATE_M],
            enrichments={"110042": enrichment},
            overrides={"110042": {"difficulty": "beginner"}},
        )
    )

    # The overridden value shows as a manual badge; the un-overridden AI value as an ai-generated
    # one — visibly distinguished so a buyer reads human fact apart from best-effort guess.
    assert 'class="badge provenance-manual"' in html
    assert ">beginner<" in html
    assert 'class="badge provenance-ai-generated"' in html
    assert ">peaceful<" in html
    # The honest gap (unknown plant-safe) gets no badge — the card degrades, never shows "unknown".
    assert "unknown" not in html
    # The two provenances carry a visible, distinct marker, not just a CSS hook a sighted buyer
    # can't see.
    assert "ai-generated" in html and "manual" in html
    assert html.count("provenance-ai-generated") >= 1


def test_a_sourced_image_shows_its_attribution_credit():
    sourced = _image_record(attribution="A. Photographer", provenance=Provenance.WIKIMEDIA)
    html = render_grid(_cards([ORNATE_M], images={"110042": sourced}))

    # A sourced image carries a licensing obligation: the photographer must be credited on the card.
    assert "A. Photographer" in html
    assert "credit" in html.lower()


def test_a_manual_image_shows_no_attribution_credit():
    html = render_grid(_cards([ORNATE_M], images={"110042": _image_record()}))

    # A human-uploaded image has no external photographer to credit, so no credit line is drawn.
    assert "credit" not in html.lower()


def test_each_card_offers_an_inline_override_form_per_classifier():
    from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament

    enrichment = EnrichmentResult(
        None, None, Difficulty.ADVANCED, Temperament.PEACEFUL, PlantSafe.UNKNOWN, False
    )
    html = render_grid(_cards([ORNATE_M], enrichments={"110042": enrichment}))

    # A human can correct any Classifier inline: one no-JS form per Classifier, posting the chosen
    # value to the SKU's override route. The forms exist even for the unknown plant-safe attribute,
    # so a buyer can fill a gap the model left.
    assert html.count('action="/items/110042/classifier" method="post"') == 3
    for key in ("difficulty", "temperament", "plant_safe"):
        assert f'<input type="hidden" name="key" value="{key}">' in html
    assert '<select name="value">' in html
    # The select offers the curated vocabulary minus the unknown hatch — a human picks a real value.
    for choice in ("beginner", "intermediate", "advanced"):
        assert f'<option value="{choice}"' in html
    assert '<option value="unknown"' not in html


def test_the_override_select_preselects_the_current_resolved_value():
    from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament

    enrichment = EnrichmentResult(
        None, None, Difficulty.ADVANCED, Temperament.PEACEFUL, PlantSafe.SAFE, False
    )
    html = render_grid(_cards([ORNATE_M], enrichments={"110042": enrichment}))

    # The currently-resolved value is the selected option, so opening the control shows the buyer
    # what is in force before they change it.
    assert '<option value="advanced" selected' in html


def test_each_card_carries_a_stable_id_for_targeted_swaps():
    html = render_grid(_cards([ORNATE_M]))

    # A stable per-card id leaves the seam the live-update poll and lazy/pagination work build on:
    # an individually-addressable card for an out-of-band swap, without a whole-grid re-render.
    assert 'id="card-110042"' in html


def test_card_images_defer_their_fetch_until_near_the_viewport():
    sourced = _image_record(attribution="A. Photographer", provenance=Provenance.WIKIMEDIA)
    with_image = render_grid(_cards([ORNATE_M], images={"110042": sourced}))
    without_image = render_grid(_cards([ORNATE_M]))

    # Every card image is lazy: a full ~900-Item grid must not fire ~900 image requests on load, so
    # an <img> off-screen waits to fetch until it nears the viewport. The placeholder defers too —
    # it is still a request per card otherwise.
    assert 'loading="lazy"' in with_image
    assert 'loading="lazy"' in without_image


def test_the_first_page_renders_a_bounded_window_and_a_sentinel_for_the_rest(tmp_path):
    html = _windowed_client(tmp_path, count=5, page_size=2).get("/").text

    # The grid renders only the first window of cards — not all 5 — so a ~900-Item catalog never
    # builds ~900 cards of DOM at once. A single sentinel marks where the rest will load.
    assert html.count('class="card"') == 2
    assert html.count('class="load-more"') == 1


def test_no_sentinel_when_the_whole_catalog_fits_in_one_window(tmp_path):
    html = _windowed_client(tmp_path, count=2, page_size=2).get("/").text

    # With nothing past the first window there is no next page, so no sentinel is drawn — a
    # load-more that fetched an empty page would loop forever on scroll.
    assert html.count('class="card"') == 2
    assert "load-more" not in html


def test_the_sentinel_carries_the_active_filters_into_the_next_page(tmp_path):
    html = (
        _windowed_client(tmp_path, count=5, page_size=2).get("/", params={"search": "Tetra"}).text
    )
    sentinel = html[html.index('class="load-more"') :]

    # The next window must continue the same filtered view, not the whole catalog: the sentinel's
    # URL carries the active filter alongside the bumped page, so load-more stays in the filter.
    assert "search=Tetra" in sentinel
    assert "page=2" in sentinel


def test_a_load_more_request_returns_only_the_next_window_not_the_grid_shell(tmp_path):
    resp = _windowed_client(tmp_path, count=5, page_size=2).get(
        "/", params={"page": 2}, headers={"HX-Request": "true"}
    )
    html = resp.text

    # A load-more swaps the spent sentinel for the next window's cards, so the fragment is the cards
    # alone — no <ul> shell to nest, no page chrome — plus a fresh sentinel pointing one page on.
    assert html.count('class="card"') == 2
    assert "catalog-grid" not in html
    assert "<!doctype" not in html.lower()
    assert "page=3" in html[html.index('class="load-more"') :]


def test_a_page_url_is_a_reloadable_full_page_for_no_javascript(tmp_path):
    html = _windowed_client(tmp_path, count=5, page_size=2).get("/", params={"page": 2}).text

    # With JS off the sentinel is a plain link to ?page=N, so that URL must render a whole, working
    # page on its own — the document shell and filter form, showing the *second* window of cards.
    assert "<!doctype" in html.lower()
    assert 'class="filters"' in html
    assert 'data-sku="110002"' in html and 'data-sku="110003"' in html
    assert 'data-sku="110000"' not in html


def test_the_sentinel_loads_on_scroll_and_degrades_to_a_plain_link():
    html = render_grid(_cards([ORNATE_M]), has_more=True, next_url="/?page=2")
    sentinel = html[html.index('class="load-more"') :]

    # The sentinel fetches the next window when it nears the viewport (infinite scroll) and replaces
    # itself with that window in place. It carries a real link to the same URL, so with JS off the
    # rest of the catalog is one plain click away rather than hidden.
    assert 'hx-trigger="revealed"' in sentinel
    assert 'hx-swap="outerHTML"' in sentinel
    assert '<a href="/?page=2">' in sentinel
    # It does not push ?page into the address bar: infinite scroll accumulates, so the canonical URL
    # stays the filter view and a reload restarts cleanly at the first window.
    assert "hx-push-url" not in sentinel
