"""The source-agnostic optimization seam: any image in, downscaled WebP out.

These tests feed tiny images generated in-memory and assert the result is valid WebP within the
dimension cap — pure compute, no network and no credentials. This is the single choke point every
stored image flows through, so both the manual upload and the future auto-source path inherit it.
"""

import io

import pytest
from PIL import Image

from fishpage.images import ImageDecodeError, optimize_image


def _png(width: int, height: int, color=(20, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def test_an_image_is_transcoded_to_webp():
    result = optimize_image(_png(64, 48), max_dimension=1024)

    # The bytes come back as WebP under the WebP content type, whatever the input format was.
    assert result.content_type == "image/webp"
    assert Image.open(io.BytesIO(result.data)).format == "WEBP"


def test_an_oversized_image_is_downscaled_to_the_max_dimension():
    result = optimize_image(_png(4000, 3000), max_dimension=1024)

    # A huge original is shrunk so its long edge fits the cap — the bucket and the card render only
    # ever hold pixels at the size they're shown, never the 4 MB phone original.
    width, height = Image.open(io.BytesIO(result.data)).size
    assert max(width, height) == 1024


def test_an_image_under_the_cap_keeps_its_dimensions():
    result = optimize_image(_png(64, 48), max_dimension=1024)

    # Optimization only ever shrinks: an already-small image is re-encoded at its own size, never
    # blown up to the cap.
    assert Image.open(io.BytesIO(result.data)).size == (64, 48)


def test_non_image_bytes_fail_the_decode_cleanly():
    # A corrupt or non-image upload can't be transcoded, so it raises rather than producing a file
    # that can't be served. The caller turns this into a 400 (manual) or a log + skip (auto-source).
    with pytest.raises(ImageDecodeError):
        optimize_image(b"this is not an image", max_dimension=1024)
