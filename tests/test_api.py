import logging
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from fishpage.app import create_app
from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament
from fishpage.models import Item
from fishpage.store import (
    classifier_overrides_for,
    enrichment_for,
    open_store,
    persist_enrichment,
    reconcile,
    unenriched_items,
)

JUN19 = date(2026, 6, 19)

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
AN_ENRICHMENT = EnrichmentResult(
    scientific_name="Polypterus ornatipinnis",
    common_name="Ornate Bichir",
    difficulty=Difficulty.INTERMEDIATE,
    temperament=Temperament.SEMI_AGGRESSIVE,
    plant_safe=PlantSafe.SAFE,
)
LEAF = Item("110092", "-", "Leaf Fish Leopard Ctenopoma", Decimal("5.99"), Decimal("4.99"), 30)
SOLD_OUT = Item("110200", "L", "Datnoid Indo", Decimal("89.99"), None, 0)

# Items spanning distinct Derived Categories, for the category-filter tests.
# ORNATE_M and LEAF are both block-11 Monster/Oddball.
ANGEL = Item("120091", "S", "Angelfish Full Black", Decimal("9.99"), None, 12)
ANGEL_KOI = Item("120093", "M", "Angelfish Koi", Decimal("12.99"), None, 8)
BARB = Item("170011", "-", "Barb Cherry", Decimal("3.99"), None, 40)


# A spread of Angelfish varying in size, special price, and stock — plus an off-category
# Barb — for exercising the browse controls in combination.
ANGEL_M_SPECIAL = Item("120095", "M", "Angelfish Marble", Decimal("20.00"), Decimal("7.00"), 5)
ANGEL_M_OOS = Item("120096", "M", "Angelfish Zebra", Decimal("11.00"), Decimal("6.00"), 0)


def client(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF, SOLD_OUT], JUN19)
    return TestClient(create_app(conn))


def combo_client(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ANGEL, ANGEL_KOI, ANGEL_M_SPECIAL, ANGEL_M_OOS, BARB], JUN19)
    return TestClient(create_app(conn))


def categorized_client(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF, ANGEL, BARB], JUN19)
    return TestClient(create_app(conn))


def searchable_client(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ANGEL, ANGEL_KOI, BARB], JUN19)
    return TestClient(create_app(conn))


def test_healthz_reports_ok(tmp_path):
    resp = client(tmp_path).get("/healthz")

    # Fly's Machine health check pings this; a 200 with a tiny body is all it needs.
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_reenrich_route_clears_the_row_and_requeues_the_sku(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    persist_enrichment(conn, "110042", AN_ENRICHMENT)
    app_client = TestClient(create_app(conn))
    assert "110042" not in {item.sku for item in unenriched_items(conn)}

    resp = app_client.post("/enrich/110042")

    # The on-demand route clears just that SKU's AI row, dropping it back into the drainer's queue
    # for a fresh pass — the background drainer takes it from there.
    assert resp.status_code == 200
    assert enrichment_for(conn, "110042") is None
    assert "110042" in {item.sku for item in unenriched_items(conn)}


def test_reenrich_route_404s_an_unknown_sku(tmp_path):
    resp = client(tmp_path).post("/enrich/999999")

    # Re-queuing presupposes a real Item; an unknown SKU is a 404, not a silent no-op that would
    # read as success.
    assert resp.status_code == 404


def test_reenrich_emits_an_audit_event_crediting_the_access_actor(tmp_path, caplog):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    persist_enrichment(conn, "110042", AN_ENRICHMENT)
    app_client = TestClient(create_app(conn))

    with caplog.at_level(logging.INFO, logger="fishpage"):
        app_client.post(
            "/enrich/110042",
            headers={"Cf-Access-Authenticated-User-Email": "alice@example.com"},
        )

    # The on-demand re-enrich narrates itself as one INFO event carrying the SKU under the uniform
    # actor field, so it joins the same "everything this person did" query as uploads and overrides.
    events = [r for r in caplog.records if getattr(r, "sku", None) == "110042"]
    assert len(events) == 1
    assert events[0].actor == "alice@example.com"


def test_reenrich_off_the_access_edge_credits_the_neutral_placeholder(tmp_path, caplog):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    persist_enrichment(conn, "110042", AN_ENRICHMENT)
    app_client = TestClient(create_app(conn))

    # No Access header — a local run or the test suite. The mutation still succeeds and the audit
    # event credits the neutral placeholder rather than going anonymous.
    with caplog.at_level(logging.INFO, logger="fishpage"):
        app_client.post("/enrich/110042")

    events = [r for r in caplog.records if getattr(r, "sku", None) == "110042"]
    assert len(events) == 1
    assert events[0].actor == "unknown"


def test_browsing_gets_emit_no_per_request_actor_log(tmp_path, caplog):
    app_client = client(tmp_path)

    # Browsing is deliberately not attributed per request: high-volume actor logging on GETs
    # duplicates what Cloudflare Access's own edge audit logs capture better. Even with an Access
    # header present, a catalog read and an index render emit no actor-bearing event.
    with caplog.at_level(logging.INFO, logger="fishpage"):
        app_client.get("/", headers={"Cf-Access-Authenticated-User-Email": "alice@example.com"})
        app_client.get(
            "/catalog", headers={"Cf-Access-Authenticated-User-Email": "alice@example.com"}
        )

    assert [r for r in caplog.records if getattr(r, "actor", None) is not None] == []


def test_requests_are_auto_instrumented_with_a_server_span(tmp_path, telemetry):
    client(tmp_path).get("/catalog")

    # FastAPI is auto-instrumented, so every request produces a server span carrying the route —
    # the spine each manual parse/ingest span hangs off in a trace.
    assert any("/catalog" in name for name in telemetry.span_names())


def test_catalog_defaults_to_in_stock_with_shape(tmp_path):
    resp = client(tmp_path).get("/catalog")

    assert resp.status_code == 200
    items = {item["sku"]: item for item in resp.json()}
    # Default view is In stock only: the qty-0 Datnoid is absent.
    assert set(items) == {"110042", "110092"}

    ornate = items["110042"]
    assert ornate == {
        "sku": "110042",
        "size": "M",
        "name": "Bichir Ornate",
        "retail_price": "28.99",
        "special_price": None,
        "qty_avail": 15,
        "category": "Monster/Oddball",
    }
    assert items["110092"]["special_price"] == "4.99"


def test_catalog_includes_out_of_stock_when_toggled(tmp_path):
    resp = client(tmp_path).get("/catalog", params={"include_out_of_stock": "true"})

    items = {item["sku"]: item for item in resp.json()}
    assert set(items) == {"110042", "110092", "110200"}
    assert items["110200"]["qty_avail"] == 0


def test_catalog_reflects_reconciled_state_after_reingest(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    # A second Stocklist: ORNATE_M is cheaper and scarcer, LEAF is gone.
    repriced = Item("110042", "M", "Bichir Ornate", Decimal("24.99"), None, 2)
    reconcile(conn, [repriced], date(2026, 6, 26))

    # The zeroed absentee only surfaces with the out-of-stock view turned on.
    resp = TestClient(create_app(conn)).get("/catalog", params={"include_out_of_stock": "true"})
    items = {item["sku"]: item for item in resp.json()}

    assert items["110042"]["retail_price"] == "24.99"
    assert items["110042"]["qty_avail"] == 2
    assert items["110092"]["qty_avail"] == 0  # absentee zeroed, still served


def test_index_links_to_the_upload_page(tmp_path):
    html = client(tmp_path).get("/").text

    # The upload page is otherwise reachable only by URL; the catalog links to it so a new
    # Stocklist can be ingested in the cloud, where there is no watched folder to drop into.
    assert 'href="/upload"' in html


def test_index_defaults_to_in_stock_cards(tmp_path):
    html = client(tmp_path).get("/").text

    # The qty-0 Datnoid has no card by default; the in-stock Items do.
    assert 'data-sku="110200"' not in html
    assert html.count('data-sku="110042"') == 1
    assert html.count('data-sku="110092"') == 1


def test_index_shows_out_of_stock_cards_when_toggled(tmp_path):
    html = client(tmp_path).get("/", params={"include_out_of_stock": "true"}).text

    assert 'data-sku="110200"' in html


def test_index_stock_toggle_filters_on_change_unchecked_by_default(tmp_path):
    html = client(tmp_path).get("/").text

    # A checkbox bound to the query param, inside the change-triggered filter form, so toggling it
    # re-filters the grid without a manual submit.
    assert 'name="include_out_of_stock"' in html
    assert 'hx-trigger="change, submit"' in html
    # Default view is In stock only, so the control is not checked.
    assert " checked" not in html


def test_index_toggle_is_checked_in_out_of_stock_view(tmp_path):
    html = client(tmp_path).get("/", params={"include_out_of_stock": "true"}).text

    assert " checked" in html


def test_index_renders_one_card_per_item(tmp_path):
    resp = client(tmp_path).get("/")
    html = resp.text

    assert resp.status_code == 200
    # One card per Item, tagged by SKU.
    assert html.count('data-sku="110042"') == 1
    assert html.count('data-sku="110092"') == 1

    # Each card shows name, size, retail price, quantity, and a placeholder image.
    # Assert against the tagged spans so a value can't be satisfied by an unrelated
    # number elsewhere in the page.
    assert "Bichir Ornate" in html
    assert "Leaf Fish Leopard Ctenopoma" in html
    assert '<span class="retail-price">$28.99</span>' in html
    assert '<span class="size">M</span>' in html
    assert '<span class="qty">15 available</span>' in html
    assert "placeholder" in html and "<img" in html

    # The special price appears only on the row that has one.
    assert '<span class="special-price">special $4.99</span>' in html


def test_catalog_json_carries_each_items_category(tmp_path):
    items = {i["sku"]: i for i in categorized_client(tmp_path).get("/catalog").json()}

    assert items["120091"]["category"] == "Angelfish"
    assert items["110042"]["category"] == "Monster/Oddball"


def test_catalog_filters_by_category(tmp_path):
    resp = categorized_client(tmp_path).get("/catalog", params={"category": "Monster/Oddball"})

    # Only the two block-11 oddballs come back; the Angelfish and Barb are excluded.
    assert {i["sku"] for i in resp.json()} == {"110042", "110092"}


def test_catalog_fuzzy_searches_by_name(tmp_path):
    resp = searchable_client(tmp_path).get("/catalog", params={"search": "angel koi"})

    # "angel koi" finds the Angelfish Koi by partial, order-free token match; the other
    # Angelfish and the Barb are left out.
    assert {i["sku"] for i in resp.json()} == {"120093"}


def test_catalog_search_orders_by_relevance(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    exact = Item("120094", "M", "Angel Koi", Decimal("14.99"), None, 5)
    reconcile(conn, [ANGEL_KOI, exact], JUN19)
    resp = TestClient(create_app(conn)).get("/catalog", params={"search": "angel koi"})

    # The exact "Angel Koi" outranks the partial "Angelfish Koi" in the JSON order.
    assert [i["sku"] for i in resp.json()] == ["120094", "120093"]


def test_catalog_without_search_returns_everything(tmp_path):
    resp = searchable_client(tmp_path).get("/catalog")

    assert {i["sku"] for i in resp.json()} == {"120091", "120093", "170011"}


def test_index_has_search_box_reflecting_the_active_term(tmp_path):
    html = searchable_client(tmp_path).get("/", params={"search": "angel koi"}).text

    # A text input bound to the query param, holding the active term so the box keeps its
    # text across submits.
    assert 'name="search"' in html
    assert 'value="angel koi"' in html


def test_index_filters_grid_by_search(tmp_path):
    html = searchable_client(tmp_path).get("/", params={"search": "angel koi"}).text

    assert 'data-sku="120093"' in html  # Angelfish Koi
    assert 'data-sku="120091"' not in html  # the other Angelfish excluded
    assert 'data-sku="170011"' not in html  # Barb excluded


def test_index_category_dropdown_filters_on_change(tmp_path):
    html = categorized_client(tmp_path).get("/").text

    # A select bound to the query param, inside the change-triggered filter form, with an option
    # per present category plus an empty "all categories" default.
    assert '<select name="category"' in html
    assert 'hx-trigger="change, submit"' in html
    assert '<option value="">' in html
    assert '<option value="Angelfish"' in html
    assert '<option value="Barb"' in html
    assert '<option value="Monster/Oddball"' in html


def test_index_category_and_stock_controls_share_one_form(tmp_path):
    # Both controls live in a single filter form, so changing one preserves the other's state.
    html = categorized_client(tmp_path).get("/").text

    assert html.count('class="filters"') == 1
    assert 'name="include_out_of_stock"' in html
    assert 'name="category"' in html


def test_index_filters_grid_by_category(tmp_path):
    html = categorized_client(tmp_path).get("/", params={"category": "Barb"}).text

    assert 'data-sku="170011"' in html  # the Barb
    assert 'data-sku="120091"' not in html  # Angelfish excluded
    assert 'data-sku="110042"' not in html  # oddball excluded
    # The chosen category is reflected as the selected option.
    assert '<option value="Barb" selected' in html


def test_catalog_filters_by_size(tmp_path):
    resp = client(tmp_path).get("/catalog", params={"size": "M"})

    # Only the M-grade Bichir comes back; the "-" Leaf and the L Datnoid are excluded.
    assert {i["sku"] for i in resp.json()} == {"110042"}


def test_catalog_filters_to_on_special_only(tmp_path):
    resp = client(tmp_path).get("/catalog", params={"on_special": "true"})

    # Only the Leaf carries a special price; the retail-only Bichir drops out.
    assert {i["sku"] for i in resp.json()} == {"110092"}


def test_catalog_sorts_by_effective_price_ascending(tmp_path):
    resp = client(tmp_path).get("/catalog", params={"sort": "price_asc"})

    # The Leaf's special (4.99) is below the Bichir's retail (28.99), so it leads.
    assert [i["sku"] for i in resp.json()] == ["110092", "110042"]


def test_catalog_sorts_by_effective_price_descending(tmp_path):
    resp = client(tmp_path).get("/catalog", params={"sort": "price_desc"})

    assert [i["sku"] for i in resp.json()] == ["110042", "110092"]


def test_catalog_combines_category_size_and_on_special(tmp_path):
    resp = combo_client(tmp_path).get(
        "/catalog", params={"category": "Angelfish", "size": "M", "on_special": "true"}
    )

    # Of the M Angelfish, only the in-stock one with a special survives all three filters:
    # the S Angelfish, the non-special M Koi, the off-category Barb, and the out-of-stock
    # M (zeroed, hidden by the default in-stock view) all drop out.
    assert {i["sku"] for i in resp.json()} == {"120095"}


def test_catalog_combines_category_filter_with_effective_price_sort(tmp_path):
    resp = combo_client(tmp_path).get(
        "/catalog", params={"category": "Angelfish", "sort": "price_asc"}
    )

    # Barb excluded by category; the three in-stock Angelfish come back cheapest-first by
    # effective price: Marble's special 7.00, then Full Black 9.99, then Koi 12.99.
    assert [i["sku"] for i in resp.json()] == ["120095", "120091", "120093"]


def test_catalog_combines_out_of_stock_toggle_with_on_special(tmp_path):
    resp = combo_client(tmp_path).get(
        "/catalog", params={"include_out_of_stock": "true", "on_special": "true"}
    )

    # With out-of-stock included, both special-priced Angelfish surface — including the
    # zeroed Zebra that the default view would hide.
    assert {i["sku"] for i in resp.json()} == {"120095", "120096"}


def test_index_size_dropdown_filters_on_change(tmp_path):
    html = client(tmp_path).get("/").text

    # A select bound to the query param, inside the change-triggered filter form, with the fixed
    # grade set plus an empty "all sizes" default.
    assert '<select name="size"' in html
    assert 'hx-trigger="change, submit"' in html
    for grade in ("-", "S", "M", "L", "Jumbo"):
        assert f'<option value="{grade}"' in html


def test_index_size_dropdown_reflects_selected_grade(tmp_path):
    html = client(tmp_path).get("/", params={"size": "M"}).text

    assert '<option value="M" selected' in html


def _on_special_input(html):
    """The on-special checkbox's own <input> tag, so assertions scope to it alone and a
    checked state on some unrelated control can't satisfy (or break) them."""
    start = html.index('<input type="checkbox" name="on_special"')
    return html[start : html.index(">", start) + 1]


def test_index_on_special_toggle_filters_on_change_unchecked_by_default(tmp_path):
    html = client(tmp_path).get("/").text

    # The on-special input is bound and off by default; the enclosing change-triggered filter form
    # re-filters the grid the moment it is ticked.
    assert "checked" not in _on_special_input(html)
    assert 'hx-trigger="change, submit"' in html


def test_index_on_special_toggle_is_checked_when_active(tmp_path):
    tag = _on_special_input(client(tmp_path).get("/", params={"on_special": "true"}).text)

    assert "checked" in tag


def test_index_sort_dropdown_filters_on_change(tmp_path):
    html = client(tmp_path).get("/").text

    # A select bound to the query param, inside the change-triggered filter form, offering both
    # effective price directions plus a default order.
    assert '<select name="sort"' in html
    assert 'hx-trigger="change, submit"' in html
    assert '<option value="price_asc"' in html
    assert '<option value="price_desc"' in html


def test_index_sort_dropdown_reflects_selected_order(tmp_path):
    html = client(tmp_path).get("/", params={"sort": "price_desc"}).text

    assert '<option value="price_desc" selected' in html


def test_index_all_browse_controls_share_one_form(tmp_path):
    # Every control lives in a single filter form, so changing one preserves the others' state.
    html = combo_client(tmp_path).get("/").text

    assert html.count('class="filters"') == 1
    for control in (
        'name="search"',
        'name="include_out_of_stock"',
        'name="category"',
        'name="size"',
        'name="on_special"',
        'name="sort"',
    ):
        assert control in html


def test_index_filters_grid_by_size(tmp_path):
    html = combo_client(tmp_path).get("/", params={"size": "S"}).text

    assert 'data-sku="120091"' in html  # the only S Angelfish
    assert 'data-sku="120093"' not in html  # an M Angelfish, excluded


def test_index_filters_grid_to_on_special(tmp_path):
    html = combo_client(tmp_path).get("/", params={"on_special": "true"}).text

    assert 'data-sku="120095"' in html  # in-stock special
    assert 'data-sku="120091"' not in html  # no special, excluded
    assert 'data-sku="120096"' not in html  # special but out of stock, hidden by default


def test_index_sorts_grid_by_effective_price(tmp_path):
    html = (
        combo_client(tmp_path).get("/", params={"category": "Angelfish", "sort": "price_asc"}).text
    )

    # The cheapest-by-effective-price card renders before the priciest.
    assert html.index('data-sku="120095"') < html.index('data-sku="120093"')


def test_index_dropdown_lists_categories_independent_of_stock_filter(tmp_path):
    # A category whose only Item is out of stock must still be selectable in the
    # dropdown, even in the default in-stock-only view — otherwise it could never be
    # browsed to. Most of the stocklist is out of stock at any time.
    conn = open_store(tmp_path / "fishpage.db")
    oos_eel = Item("150013", "Jumbo", "Eel Fire", Decimal("19.99"), None, 0)
    reconcile(conn, [ANGEL, oos_eel], JUN19)

    html = TestClient(create_app(conn)).get("/").text  # default: in-stock only

    assert '<option value="Eel"' in html  # offered despite having no in-stock Item
    assert 'data-sku="150013"' not in html  # but its card stays hidden until toggled


def test_classifier_override_route_records_a_manual_correction(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    app_client = TestClient(create_app(conn))

    resp = app_client.post(
        "/items/110042/classifier",
        data={"key": "difficulty", "value": "beginner"},
        follow_redirects=False,
    )

    # A human correction is written as a manual override that wins on read, then the form lands back
    # on the refreshed catalog via post/redirect/get so a reload does not re-submit.
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert classifier_overrides_for(conn, "110042") == {"difficulty": "beginner"}


def test_classifier_override_shows_on_the_card_as_manual(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    app_client = TestClient(create_app(conn))
    app_client.post("/items/110042/classifier", data={"key": "difficulty", "value": "beginner"})

    html = app_client.get("/").text

    # The correction is now a manual badge on the card — the round-trip a buyer actually sees.
    assert 'class="badge provenance-manual"' in html
    assert ">beginner<" in html


def test_classifier_override_route_404s_an_unknown_sku(tmp_path):
    resp = client(tmp_path).post(
        "/items/999999/classifier", data={"key": "difficulty", "value": "beginner"}
    )

    # Correcting presupposes a real Item; an unknown SKU is a 404, not a silent write.
    assert resp.status_code == 404


def test_classifier_override_emits_an_audit_event_crediting_the_access_actor(tmp_path, caplog):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    app_client = TestClient(create_app(conn))

    with caplog.at_level(logging.INFO, logger="fishpage"):
        app_client.post(
            "/items/110042/classifier",
            data={"key": "difficulty", "value": "beginner"},
            headers={"Cf-Access-Authenticated-User-Email": "alice@example.com"},
        )

    # A human correction narrates itself as one INFO event carrying the SKU and the corrected
    # Classifier under the uniform actor field, so it joins the same actor query as the others.
    events = [r for r in caplog.records if getattr(r, "sku", None) == "110042"]
    assert len(events) == 1
    event = events[0]
    assert event.actor == "alice@example.com"
    assert event.classifier == "difficulty"


def test_classifier_override_off_the_access_edge_credits_the_neutral_placeholder(tmp_path, caplog):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    app_client = TestClient(create_app(conn))

    # No Access header — a local run or the test suite. The correction still lands and the audit
    # event credits the neutral placeholder rather than going anonymous.
    with caplog.at_level(logging.INFO, logger="fishpage"):
        app_client.post("/items/110042/classifier", data={"key": "difficulty", "value": "beginner"})

    events = [r for r in caplog.records if getattr(r, "sku", None) == "110042"]
    assert len(events) == 1
    assert events[0].actor == "unknown"


def test_classifier_override_route_400s_a_value_outside_the_vocabulary(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    app_client = TestClient(create_app(conn))

    resp = app_client.post(
        "/items/110042/classifier", data={"key": "difficulty", "value": "trivial"}
    )

    # An out-of-vocabulary value is rejected at the door, never persisted — the catalog can only
    # ever hold curated Classifier values.
    assert resp.status_code == 400
    assert classifier_overrides_for(conn, "110042") == {}


def _enriched_client(tmp_path):
    """A catalog where the two oddballs carry contrasting care reads, for the Classifier facets."""
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    persist_enrichment(
        conn,
        "110042",
        EnrichmentResult(None, None, Difficulty.BEGINNER, Temperament.PEACEFUL, PlantSafe.SAFE),
    )
    persist_enrichment(
        conn,
        "110092",
        EnrichmentResult(None, None, Difficulty.ADVANCED, Temperament.AGGRESSIVE, PlantSafe.UNSAFE),
    )
    return conn, TestClient(create_app(conn))


def test_catalog_filters_by_a_classifier_facet(tmp_path):
    _conn, app_client = _enriched_client(tmp_path)

    resp = app_client.get("/catalog", params={"difficulty": "beginner"})

    # Only the beginner Item survives the Classifier facet; the advanced one drops out.
    assert {i["sku"] for i in resp.json()} == {"110042"}


def test_catalog_classifier_facet_respects_a_manual_override(tmp_path):
    _conn, app_client = _enriched_client(tmp_path)
    # A human corrects the advanced Item down to beginner.
    app_client.post("/items/110092/classifier", data={"key": "difficulty", "value": "beginner"})

    resp = app_client.get("/catalog", params={"difficulty": "beginner"})

    # The override is what the facet matches now: both Items read beginner on resolve.
    assert {i["sku"] for i in resp.json()} == {"110042", "110092"}


def test_index_filters_grid_by_a_classifier_facet(tmp_path):
    _conn, app_client = _enriched_client(tmp_path)

    html = app_client.get("/", params={"temperament": "aggressive"}).text

    assert 'data-sku="110092"' in html  # the aggressive oddball
    assert 'data-sku="110042"' not in html  # the peaceful one excluded


def test_index_renders_classifier_filter_chips_marking_the_active_one(tmp_path):
    _conn, app_client = _enriched_client(tmp_path)

    html = app_client.get("/", params={"difficulty": "beginner"}).text

    # Each enum Classifier value is a chip in the filter form, bound to its query param; the active
    # value is marked so the buyer sees the live facet. A chip toggles via the same change-triggered
    # form as the other controls.
    assert 'name="difficulty" value="beginner"' in html
    assert 'name="temperament" value="peaceful"' in html
    assert 'name="plant_safe" value="safe"' in html
    # The active chip is checked; an inactive one is not.
    active = html[
        html.index('name="difficulty" value="beginner"') - 60 : html.index(
            'name="difficulty" value="beginner"'
        )
        + 60
    ]
    assert "checked" in active
    # No chip ever offers the unknown hatch.
    assert 'value="unknown"' not in html
