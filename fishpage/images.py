"""The image bytes store: an injectable bucket the app puts images into and serves them back from.

Image bytes live in a separate ``fishpage-images`` R2 bucket, kept out of the bucket the Litestream
restore reasons about. The app proxies images through itself rather than exposing a public bucket
URL, so they stay behind the Cloudflare Access edge exactly like the wholesale prices. The store is
opt-in and default-off and is dependency-injected: the test suite exercises the routes against an
in-memory fake with no bucket and no credentials, the way the enricher is faked.
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from fishpage.config import Settings


@dataclass(frozen=True)
class StoredImage:
    """The bytes of one stored image plus the content type to serve it under."""

    data: bytes
    content_type: str


@runtime_checkable
class ImageStore(Protocol):
    """The injectable bucket: put image bytes under a key, and read them back to proxy."""

    def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    def get(self, key: str) -> StoredImage | None: ...


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
