"""The image bytes store: an injectable bucket the app puts images into and serves them back from.

Image bytes live in a separate ``fishpage-images`` R2 bucket, kept out of the bucket the Litestream
restore reasons about. The app proxies images through itself rather than exposing a public bucket
URL, so they stay behind the Cloudflare Access edge exactly like the wholesale prices. The store is
opt-in and default-off and is dependency-injected: the test suite exercises the routes against an
in-memory fake with no bucket and no credentials, the way the enricher is faked.
"""

import io
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from PIL import Image, UnidentifiedImageError

from fishpage import observability
from fishpage.config import Settings
from fishpage.models import Provenance
from fishpage.store import attach_image

_log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class StoredImage:
    """The bytes of one stored image plus the content type to serve it under."""

    data: bytes
    content_type: str


class ImageDecodeError(ValueError):
    """Raised when input bytes are not a decodable image, so nothing gets stored."""


def optimize_image(raw_bytes: bytes, *, max_dimension: int) -> StoredImage:
    """Transcode any image to downscaled WebP — the single seam every stored image flows through.

    Re-encodes to WebP at a sensible quality and shrinks the long edge to ``max_dimension`` so a
    huge phone original can't bloat the bucket or a card render; an already-small image is left at
    its size rather than upscaled. Pure in-memory compute with no network. Non-image or corrupt
    input raises :class:`ImageDecodeError` rather than yielding a file that can't be served.
    """
    try:
        image = Image.open(io.BytesIO(raw_bytes))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageDecodeError(str(exc)) from exc
    # thumbnail() scales the long edge down to the cap in place and is a no-op when the image
    # already fits, so a small original is never upscaled.
    image.thumbnail((max_dimension, max_dimension))
    buf = io.BytesIO()
    image.save(buf, format="WEBP", quality=80)
    return StoredImage(data=buf.getvalue(), content_type="image/webp")


@runtime_checkable
class ImageStore(Protocol):
    """The injectable bucket: put image bytes under a key, and read them back to proxy."""

    def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    def get(self, key: str) -> StoredImage | None: ...


def store_image(
    image_store: ImageStore,
    conn: sqlite3.Connection,
    sku: str,
    raw_bytes: bytes,
    *,
    provenance: Provenance,
    license: str | None = None,
    attribution: str | None = None,
    source_url: str | None = None,
    uploaded_by: str | None = None,
    now: Callable[[], datetime] = _utcnow,
    max_dimension: int,
) -> None:
    """Optimize raw bytes to WebP, put them in the bucket, and record the metadata — the one write
    path every stored image takes.

    Both sources call this: the manual upload route with ``provenance=MANUAL`` and an
    ``uploaded_by`` Uploader, and the future auto-source drainer with ``provenance=WIKIMEDIA`` plus
    the source's license/attribution. Routing every write through here is what keeps the two paths
    from diverging on optimization. The SKU is the object key, so one Item has one image and a
    re-store overwrites in place. Optimization runs first and raises :class:`ImageDecodeError` on a
    bad input *before* any write, so a corrupt image leaves nothing behind in the bucket or the DB.

    ``uploaded_by`` is the Uploader to credit; when present the moment it landed is stamped as
    ``uploaded_at``. The auto-source path passes no Uploader and so leaves both unset.
    """
    try:
        optimized = optimize_image(raw_bytes, max_dimension=max_dimension)
    except ImageDecodeError:
        observability.record_image_optimize_error(provenance=provenance)
        # The counter is the dashboard signal; the detail of which upload failed rides this log so
        # the SKU, Uploader, and decode exception stay off the metric. Same indexed fields as the
        # success event, so both paths narrate alike. Re-raise — the caller turns it into a 400.
        _log.warning(
            "Failed to optimize %s image for %s",
            provenance.value,
            sku,
            extra={"uploader": uploaded_by, "sku": sku, "provenance": provenance.value},
            exc_info=True,
        )
        raise
    observability.record_image_optimized(len(raw_bytes), len(optimized.data), provenance=provenance)
    image_store.put(sku, optimized.data, content_type=optimized.content_type)
    uploaded_at = None if uploaded_by is None else now()
    attach_image(
        conn,
        sku,
        object_key=sku,
        license=license,
        attribution=attribution,
        source_url=source_url,
        provenance=provenance,
        uploaded_by=uploaded_by,
        uploaded_at=uploaded_at,
    )
    # One structured event per stored image, emitted at the shared seam so both sources narrate
    # alike. The Uploader, SKU, and Provenance ride as indexed fields, not text baked into the
    # message, so the recent-uploads view filters on who attached what within log retention.
    _log.info(
        "Stored %s image for %s",
        provenance.value,
        sku,
        extra={"uploader": uploaded_by, "sku": sku, "provenance": provenance.value},
    )


class R2ImageStore:
    """An :class:`ImageStore` backed by the ``fishpage-images`` R2 bucket over its S3 API.

    The client is injected so the put/get logic is exercised by a fake with no network; production
    wires in a boto3 S3 client pointed at the R2 endpoint (typed ``Any`` — boto3 ships no stubs).
    """

    def __init__(self, client: Any, bucket: str):
        self._client = client
        self._bucket = bucket

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> StoredImage | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception:
            # A missing key (and any other read miss) is "no image" to the caller, which 404s; the
            # proxy route never distinguishes an absent object from a transient read failure.
            return None
        return StoredImage(
            data=response["Body"].read(),
            content_type=response.get("ContentType") or "application/octet-stream",
        )


def select_image_store(settings: Settings) -> ImageStore | None:
    """The configured image store, or ``None`` when the image bucket is off.

    Opt-in and default-off, the same pattern as the enricher: it takes both the flag and the R2
    credentials, so ``just run`` and the test suite need no bucket and construct no client.
    """
    if not (settings.images_enabled and settings.r2_images_bucket and settings.r2_images_endpoint):
        return None
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=settings.r2_images_endpoint,
        aws_access_key_id=settings.r2_images_access_key_id,
        aws_secret_access_key=settings.r2_images_secret_access_key,
    )
    return R2ImageStore(client, settings.r2_images_bucket)
