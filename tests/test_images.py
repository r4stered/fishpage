"""The manual image pipeline: upload an image for an Item, then serve it back proxied by the app.

These tests drive the HTTP routes the way a human would — post an image for a SKU, then fetch it —
through an injected in-memory :class:`~fishpage.images.ImageStore`, so the route orchestration is
exercised with no R2 bucket and no credentials. Images are opt-in and default-off; the suite never
reaches for a real bucket.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from fishpage.app import create_app
from fishpage.config import load_settings
from fishpage.images import R2ImageStore, StoredImage, select_image_store
from fishpage.models import Item, Provenance
from fishpage.store import image_for, open_store, reconcile

_R2_ENV = {
    "FISHPAGE_IMAGES_ENABLED": "1",
    "R2_IMAGES_BUCKET": "fishpage-images",
    "R2_IMAGES_ENDPOINT": "https://acct.r2.cloudflarestorage.com",
    "R2_IMAGES_ACCESS_KEY_ID": "key-id",
    "R2_IMAGES_SECRET_ACCESS_KEY": "secret",
}

JUN19 = date(2026, 6, 19)
ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF fake jpeg bytes"


class FakeImageStore:
    """An in-memory :class:`ImageStore` — the bucket a test serves from, no network."""

    def __init__(self):
        self.objects: dict[str, StoredImage] = {}

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self.objects[key] = StoredImage(data=data, content_type=content_type)

    def get(self, key: str) -> StoredImage | None:
        return self.objects.get(key)


def _client(tmp_path, *, image_store=None, seed=(ORNATE_M,)):
    conn = open_store(tmp_path / "fishpage.db")
    if seed:
        reconcile(conn, list(seed), JUN19)
    app = create_app(
        conn,
        incoming_dir=tmp_path / "incoming",
        processed_dir=tmp_path / "processed",
        image_store=image_store,
    )
    return conn, TestClient(app)


def _post_image(client, sku, name="ornate.jpg", data=JPEG, content_type="image/jpeg"):
    return client.post(f"/items/{sku}/image", files={"file": (name, data, content_type)})


def test_uploading_an_image_stores_the_bytes_and_records_manual_provenance(tmp_path):
    store = FakeImageStore()
    conn, client = _client(tmp_path, image_store=store)

    resp = _post_image(client, "110042")

    assert resp.status_code == 200
    # The bytes land in the bucket, and the DB records only the key + manual Provenance — never the
    # bytes. The recorded key is exactly what was stored.
    record = image_for(conn, "110042")
    assert record is not None
    assert record.provenance is Provenance.MANUAL
    assert store.objects[record.object_key].data == JPEG


def test_an_uploaded_image_is_served_back_proxied_through_the_app(tmp_path):
    store = FakeImageStore()
    _, client = _client(tmp_path, image_store=store)
    _post_image(client, "110042")

    resp = client.get("/items/110042/image")

    # The app proxies the bytes from R2 itself — no redirect to a public bucket URL — so the image
    # stays behind the Access edge. Content type is preserved from the upload.
    assert resp.status_code == 200
    assert resp.content == JPEG
    assert resp.headers["content-type"] == "image/jpeg"


def test_an_uploaded_image_shows_on_the_catalog_card(tmp_path):
    _, client = _client(tmp_path, image_store=FakeImageStore())
    _post_image(client, "110042")

    html = client.get("/").text

    # Once an image is stored, its card swaps the placeholder for the proxied image.
    assert 'src="/items/110042/image"' in html


def test_serving_an_image_for_an_item_with_none_is_404(tmp_path):
    _, client = _client(tmp_path, image_store=FakeImageStore())

    assert client.get("/items/110042/image").status_code == 404


def test_uploading_an_image_for_an_unknown_sku_is_404(tmp_path):
    store = FakeImageStore()
    conn, client = _client(tmp_path, image_store=store)

    resp = _post_image(client, "999999")

    assert resp.status_code == 404
    # Nothing was stored for a SKU the catalog does not know — neither bytes nor a DB row.
    assert image_for(conn, "999999") is None
    assert store.objects == {}


def test_uploading_with_images_disabled_is_rejected_not_silently_dropped(tmp_path):
    conn, client = _client(tmp_path, image_store=None)  # images opt-in, default off

    resp = _post_image(client, "110042")

    # With no bucket configured the upload cannot store bytes, so it fails loudly rather than
    # recording a key that points at nothing.
    assert resp.status_code == 503
    assert image_for(conn, "110042") is None


def test_images_are_off_by_default_so_no_bucket_is_needed():
    assert select_image_store(load_settings({})) is None


def test_images_stay_off_unless_the_flag_bucket_and_endpoint_are_all_present():
    # Each piece alone leaves the store off — no half-configured bucket that fails at first upload.
    assert select_image_store(load_settings({"FISHPAGE_IMAGES_ENABLED": "1"})) is None
    assert select_image_store(load_settings({k: _R2_ENV[k] for k in ["R2_IMAGES_BUCKET"]})) is None
    assert select_image_store(load_settings({**_R2_ENV, "R2_IMAGES_ENDPOINT": ""})) is None


def test_a_fully_configured_environment_selects_an_r2_image_store():
    assert isinstance(select_image_store(load_settings(_R2_ENV)), R2ImageStore)
