"""The Pick-list routes: gather from a card, view the list, adjust a quantity, remove a line.

These drive the HTTP routes the way the deployment does — post to the routes Access fronts and
read the rendered HTML back — rather than reaching into the store. The Actor is the Cloudflare
Access email header; off the edge (no header) the neutral placeholder Actor owns the list, the
same fallback the rest of the app uses.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from fishpage.access import ACCESS_EMAIL_HEADER
from fishpage.app import create_app
from fishpage.models import Item
from fishpage.store import open_store, reconcile

JUN19 = date(2026, 6, 19)
JUN26 = date(2026, 6, 26)
ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
LEAF = Item("110092", "-", "Leaf Fish Leopard Ctenopoma", Decimal("5.99"), Decimal("4.99"), 30)

ALICE = {ACCESS_EMAIL_HEADER: "alice@sdc.test"}
BOB = {ACCESS_EMAIL_HEADER: "bob@sdc.test"}
HX = {"HX-Request": "true"}


def _store(tmp_path, *, seed=(ORNATE_M, LEAF)):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, list(seed), JUN19)
    return conn


def _client(tmp_path, *, seed=(ORNATE_M, LEAF)):
    return TestClient(create_app(_store(tmp_path, seed=seed)))


def test_add_from_a_card_puts_the_item_on_the_actors_pick_list(tmp_path):
    client = _client(tmp_path)

    # The card's button is an hx-post; the HTMX response swaps it for the "on pick list" marker.
    added = client.post("/pick-list/110042", headers={**ALICE, **HX})
    assert added.status_code == 200
    assert "On Pick list" in added.text

    # The Item is now on Alice's list — the view renders it.
    view = client.get("/pick-list", headers=ALICE).text
    assert 'data-sku="110042"' in view


def test_adding_an_unknown_sku_is_rejected(tmp_path):
    client = _client(tmp_path)
    assert client.post("/pick-list/does-not-exist", headers=ALICE).status_code == 404


def test_adding_the_same_item_twice_does_not_duplicate_the_line(tmp_path):
    client = _client(tmp_path)
    client.post("/pick-list/110042", headers=ALICE)
    client.post("/pick-list/110042", headers=ALICE)

    view = client.get("/pick-list", headers=ALICE).text
    assert view.count('class="pick-line"') == 1


def test_the_pick_list_view_shows_sku_name_effective_price_quantity_and_total(tmp_path):
    client = _client(tmp_path)
    client.post("/pick-list/110092", headers=ALICE)  # LEAF, Special price 4.99
    client.post("/pick-list/110092/quantity", data={"quantity": "3"}, headers=ALICE)

    view = client.get("/pick-list", headers=ALICE).text
    assert "110092" in view
    assert "Leaf Fish Leopard Ctenopoma" in view
    assert "4.99" in view  # effective (Special) price, not the 5.99 retail
    assert "5.99" not in view
    # quantity field and a running total reflecting 3 x 4.99.
    assert 'value="3"' in view
    assert "14.97" in view


def test_changing_a_quantity_is_reflected_immediately_via_htmx(tmp_path):
    client = _client(tmp_path)
    client.post("/pick-list/110092", headers=ALICE)

    resp = client.post(
        "/pick-list/110092/quantity",
        data={"quantity": "4"},
        headers={**ALICE, **HX},
    )

    # The HTMX response is the re-rendered list fragment carrying the new quantity and total.
    assert resp.status_code == 200
    assert 'value="4"' in resp.text
    assert "19.96" in resp.text  # 4 x 4.99


def test_removing_a_line_is_reflected_immediately_via_htmx(tmp_path):
    client = _client(tmp_path)
    client.post("/pick-list/110042", headers=ALICE)
    client.post("/pick-list/110092", headers=ALICE)

    resp = client.post("/pick-list/110042/remove", headers={**ALICE, **HX})

    # The fragment comes back without the removed line and still carrying the one that remains.
    assert resp.status_code == 200
    assert 'data-sku="110042"' not in resp.text
    assert 'data-sku="110092"' in resp.text


def test_off_the_access_edge_the_placeholder_actor_owns_the_list(tmp_path):
    client = _client(tmp_path)

    # No Access header: the add still succeeds and the view shows it — the neutral placeholder Actor
    # owns the list, the same fallback the rest of the app uses.
    client.post("/pick-list/110042")
    assert 'data-sku="110042"' in client.get("/pick-list").text


def test_each_card_carries_an_add_to_pick_list_button(tmp_path):
    client = _client(tmp_path)

    html = client.get("/").text

    # A card gains the "Add to Pick list" action — one per Item — that hx-posts the gather.
    assert html.count("add-to-pick-list") == 2
    assert 'hx-post="/pick-list/110042"' in html


def test_the_catalog_links_to_the_pick_list_view(tmp_path):
    client = _client(tmp_path)
    assert 'href="/pick-list"' in client.get("/").text


def test_one_actor_never_sees_anothers_list_over_http(tmp_path):
    client = _client(tmp_path)
    client.post("/pick-list/110042", headers=ALICE)

    # Bob's view is empty — the list is keyed by the Access email, so Alice's gather is hers alone.
    assert 'data-sku="110042"' not in client.get("/pick-list", headers=BOB).text


def test_the_view_offers_an_export_button_only_when_the_list_has_lines(tmp_path):
    client = _client(tmp_path)
    assert 'action="/pick-list/export"' not in client.get("/pick-list", headers=ALICE).text

    client.post("/pick-list/110042", headers=ALICE)
    assert 'action="/pick-list/export"' in client.get("/pick-list", headers=ALICE).text


def test_export_returns_an_order_ready_download_of_sku_name_quantity(tmp_path):
    client = _client(tmp_path)
    client.post("/pick-list/110092", headers=ALICE)  # LEAF
    client.post("/pick-list/110092/quantity", data={"quantity": "3"}, headers=ALICE)

    resp = client.post("/pick-list/export", headers=ALICE)

    # The artifact is a plain-text attachment the buyer can paste into the order or save to disk.
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "attachment" in resp.headers["content-disposition"]
    # One line carrying SKU, name, and the chosen quantity.
    assert resp.text == "110092\tLeaf Fish Leopard Ctenopoma\t3"


def test_export_clears_the_actors_list(tmp_path):
    client = _client(tmp_path)
    client.post("/pick-list/110042", headers=ALICE)

    client.post("/pick-list/export", headers=ALICE)

    # Export is terminal: the served list is wiped so it can't bleed into next week's order.
    assert 'data-sku="110042"' not in client.get("/pick-list", headers=ALICE).text


def test_export_and_clear_are_scoped_to_the_current_actor(tmp_path):
    client = _client(tmp_path)
    client.post("/pick-list/110042", headers=ALICE)
    client.post("/pick-list/110092", headers=BOB)

    # Alice exports; only her line lands in the file and only her list is cleared.
    resp = client.post("/pick-list/export", headers=ALICE)
    assert "110042" in resp.text
    assert "110092" not in resp.text
    assert 'data-sku="110042"' not in client.get("/pick-list", headers=ALICE).text
    assert 'data-sku="110092"' in client.get("/pick-list", headers=BOB).text


def test_exporting_an_empty_list_is_rejected_and_clears_nothing(tmp_path):
    client = _client(tmp_path)
    assert client.post("/pick-list/export", headers=ALICE).status_code == 400


def test_an_out_of_stock_line_is_flagged_on_export_not_dropped(tmp_path):
    conn = _store(tmp_path)
    client = TestClient(create_app(conn))
    client.post("/pick-list/110042", headers=ALICE)
    # A newer Stocklist omitting the gathered SKU zeroes its availability — out of stock, kept.
    reconcile(conn, [LEAF], JUN26)

    resp = client.post("/pick-list/export", headers=ALICE)

    # The stale line survives the export and carries a visible out-of-stock flag with its last-seen
    # date, so the buyer notices before placing the order.
    assert "110042" in resp.text
    assert "OUT OF STOCK" in resp.text
    assert "2026-06-19" in resp.text
