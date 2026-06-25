"""The manual image pipeline: upload an image for an Item, then serve it back proxied by the app.

These tests drive the HTTP routes the way a human would — post an image for a SKU, then fetch it —
through an injected in-memory :class:`~fishpage.images.ImageStore`, so the route orchestration is
exercised with no R2 bucket and no credentials. Images are opt-in and default-off; the suite never
reaches for a real bucket.
"""

import io
import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from fishpage.app import create_app
from fishpage.config import load_settings
from fishpage.images import (
    ImageDecodeError,
    R2ImageStore,
    StoredImage,
    select_image_store,
    store_image,
)
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


def _real_jpeg(width=64, height=48, color=(20, 120, 200)) -> bytes:
    """A genuine JPEG the optimization seam can actually decode — the fake magic-byte string a
    route test would otherwise post can't be transcoded."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG")
    return buf.getvalue()


JPEG = _real_jpeg()


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


def _post_image(client, sku, name="ornate.jpg", data=JPEG, content_type="image/jpeg", headers=None):
    return client.post(
        f"/items/{sku}/image", files={"file": (name, data, content_type)}, headers=headers
    )


def _seeded_conn(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    return conn


def test_store_image_optimizes_to_webp_and_records_provenance(tmp_path):
    store = FakeImageStore()
    conn = _seeded_conn(tmp_path)

    store_image(store, conn, "110042", JPEG, provenance=Provenance.MANUAL, max_dimension=1024)

    # The single seam every source flows through: the bucket holds WebP under the WebP content
    # type, and the DB records the key + provenance — never the bytes.
    stored = store.objects["110042"]
    assert stored.content_type == "image/webp"
    assert Image.open(io.BytesIO(stored.data)).format == "WEBP"
    record = image_for(conn, "110042")
    assert record is not None and record.provenance is Provenance.MANUAL


def test_store_image_records_the_uploader_and_when_for_a_manual_upload(tmp_path):
    store = FakeImageStore()
    conn = _seeded_conn(tmp_path)
    uploaded_at = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)

    store_image(
        store,
        conn,
        "110042",
        JPEG,
        provenance=Provenance.MANUAL,
        uploaded_by="alice@example.com",
        now=lambda: uploaded_at,
        max_dimension=1024,
    )

    # The Uploader and the moment it landed are stamped on the image row, durable in the catalog —
    # so the catalog can answer who attached this image, and when, straight from the DB.
    record = image_for(conn, "110042")
    assert record is not None
    assert record.uploaded_by == "alice@example.com"
    assert record.uploaded_at == uploaded_at


def test_store_image_carries_source_license_for_the_auto_source_path(tmp_path):
    store = FakeImageStore()
    conn = _seeded_conn(tmp_path)

    # The same seam the future Wikimedia drainer calls: provenance plus the source's attribution
    # ride through unchanged, proving optimization isn't bolted onto the manual upload route.
    store_image(
        store,
        conn,
        "110042",
        JPEG,
        provenance=Provenance.WIKIMEDIA,
        license="CC BY-SA 4.0",
        attribution="A. Photographer",
        source_url="https://commons.wikimedia.org/wiki/File:Fish.jpg",
        max_dimension=1024,
    )

    record = image_for(conn, "110042")
    assert record is not None
    assert record.provenance is Provenance.WIKIMEDIA
    assert record.license == "CC BY-SA 4.0"
    assert record.attribution == "A. Photographer"
    assert record.source_url == "https://commons.wikimedia.org/wiki/File:Fish.jpg"
    # The auto-source path has no human Uploader, so it leaves both unset — the credited
    # photographer rides in `attribution`, a different "who" than the Uploader.
    assert record.uploaded_by is None
    assert record.uploaded_at is None


def test_store_image_writes_nothing_when_the_input_cannot_be_decoded(tmp_path):
    store = FakeImageStore()
    conn = _seeded_conn(tmp_path)

    with pytest.raises(ImageDecodeError):
        store_image(
            store, conn, "110042", b"not an image", provenance=Provenance.MANUAL, max_dimension=1024
        )

    # Optimization fails before any write, so a corrupt input leaves neither a bucket object nor a
    # DB row pointing at bytes that can't be served.
    assert store.objects == {}
    assert image_for(conn, "110042") is None


def test_uploading_an_image_stores_the_bytes_and_records_manual_provenance(tmp_path):
    store = FakeImageStore()
    conn, client = _client(tmp_path, image_store=store)

    resp = _post_image(client, "110042")

    assert resp.status_code == 200
    # The bytes land in the bucket optimized to WebP, and the DB records only the key + manual
    # Provenance — never the bytes. The recorded key is exactly what was stored.
    record = image_for(conn, "110042")
    assert record is not None
    assert record.provenance is Provenance.MANUAL
    assert Image.open(io.BytesIO(store.objects[record.object_key].data)).format == "WEBP"


def test_uploading_an_image_credits_the_access_authenticated_user_as_uploader(tmp_path):
    store = FakeImageStore()
    conn, client = _client(tmp_path, image_store=store)

    resp = _post_image(
        client, "110042", headers={"Cf-Access-Authenticated-User-Email": "alice@example.com"}
    )

    assert resp.status_code == 200
    # Access authenticated the human at the edge and injected their email; the route credits it as
    # the Uploader and stamps when it landed, so the catalog knows who attached this image and when.
    record = image_for(conn, "110042")
    assert record is not None
    assert record.uploaded_by == "alice@example.com"
    assert record.uploaded_at is not None


def test_uploading_an_image_off_the_access_edge_records_a_neutral_uploader(tmp_path):
    store = FakeImageStore()
    conn, client = _client(tmp_path, image_store=store)

    # No Access header — a local run or the test suite. A missing identity must never fail a working
    # upload, so it succeeds and is credited to a neutral placeholder rather than rejected.
    resp = _post_image(client, "110042")

    assert resp.status_code == 200
    record = image_for(conn, "110042")
    assert record is not None
    assert record.uploaded_by == "unknown"


def test_a_successful_upload_emits_a_structured_event(tmp_path, caplog):
    _, client = _client(tmp_path, image_store=FakeImageStore())

    with caplog.at_level(logging.INFO, logger="fishpage"):
        _post_image(
            client, "110042", headers={"Cf-Access-Authenticated-User-Email": "alice@example.com"}
        )

    # A successful upload narrates itself as one INFO event carrying the Uploader, SKU, and
    # Provenance as structured fields — so the recent-uploads view answers "who attached what" from
    # the logs (within retention) the way the DB answers it forever.
    events = [r for r in caplog.records if getattr(r, "uploader", None) is not None]
    assert len(events) == 1
    event = events[0]
    assert event.levelno == logging.INFO
    assert event.uploader == "alice@example.com"
    assert event.sku == "110042"
    assert event.provenance == "manual"


def test_an_uploaded_image_is_served_back_proxied_through_the_app(tmp_path):
    store = FakeImageStore()
    _, client = _client(tmp_path, image_store=store)
    _post_image(client, "110042")

    resp = client.get("/items/110042/image")

    # The app proxies the bytes from R2 itself — no redirect to a public bucket URL — so the image
    # stays behind the Access edge. It is served as the optimized WebP that was stored, regardless
    # of the upload's original content type.
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(resp.content)).format == "WEBP"


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


def test_uploading_a_non_image_is_rejected_and_stores_nothing(tmp_path):
    store = FakeImageStore()
    conn, client = _client(tmp_path, image_store=store)

    resp = _post_image(client, "110042", data=b"this is not an image", content_type="image/jpeg")

    # A corrupt or non-image upload can't be transcoded, so it fails at the door with a 400 rather
    # than storing a file the proxy could never serve. Nothing lands in the bucket or the DB.
    assert resp.status_code == 400
    assert store.objects == {}
    assert image_for(conn, "110042") is None


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


class _FakeS3:
    """A stand-in for the boto3 S3 client — the R2 contract R2ImageStore speaks, no network."""

    def __init__(self):
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = (Body, ContentType)

    def get_object(self, *, Bucket, Key):
        body, content_type = self.objects[(Bucket, Key)]  # KeyError on a miss
        return {"Body": io.BytesIO(body), "ContentType": content_type}


def test_r2_image_store_round_trips_the_bytes_and_content_type():
    store = R2ImageStore(_FakeS3(), "fishpage-images")

    store.put("110042", JPEG, content_type="image/jpeg")

    # The bytes and their content type survive the put/get round-trip through the S3 API.
    assert store.get("110042") == StoredImage(data=JPEG, content_type="image/jpeg")


def test_r2_image_store_treats_a_read_miss_as_no_image():
    # A missing key (or any read failure) is "no image" — the proxy route turns that into a 404.
    assert R2ImageStore(_FakeS3(), "fishpage-images").get("absent") is None


def test_serving_an_image_with_images_disabled_is_404(tmp_path):
    _, client = _client(tmp_path, image_store=None)  # images opt-in, default off

    # With no bucket configured there is nothing to proxy, so the route 404s rather than erroring.
    assert client.get("/items/110042/image").status_code == 404
