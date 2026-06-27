"""The Wikimedia image source: resolve a species to a commercial-free lead image.

These tests never touch the network. The licence filter is a pure function, and the source's
choreography is driven through an injected fake transport, so the store-confident-only image path
is exercised with no outbound request — the same opt-in, default-off posture as the enricher.
"""

import json

from fishpage.config import load_settings
from fishpage.imagesource import (
    SourcedImage,
    WikimediaImageSource,
    is_commercial_free,
    select_image_source,
)

IMAGE_BYTES = b"\xff\xd8\xff\xe0 pretend-jpeg-bytes"


class _Resp:
    """A stand-in for the context-manager urlopen returns: yields canned bytes from ``read()``."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


class FakeOpener:
    """Routes the Wikimedia choreography's URLs to canned responses — the network seam, no socket.

    A request is dispatched by the API operation in its query string (search, pageimages,
    imageinfo) or, for the final download, by matching the file URL. Every request's User-Agent
    header is recorded so a test can assert the descriptive UA the policy requires.
    """

    def __init__(
        self,
        *,
        title="Pterophyllum scalare",
        filename="Pterophyllum_scalare.jpg",
        file_url="https://upload.wikimedia.org/Pterophyllum_scalare.jpg",
        description_url="https://commons.wikimedia.org/wiki/File:Pterophyllum_scalare.jpg",
        license="CC BY-SA 4.0",
        artist='<a href="//commons.wikimedia.org/wiki/User:Jdoe">Jane Doe</a>',
        image_bytes=IMAGE_BYTES,
    ):
        self.title = title
        self.filename = filename
        self.file_url = file_url
        self.description_url = description_url
        self.license = license
        self.artist = artist
        self.image_bytes = image_bytes
        self.user_agents: list[str | None] = []

    def __call__(self, req, *, timeout=None):
        self.user_agents.append(req.get_header("User-agent"))
        url = req.full_url
        if url == self.file_url:
            return _Resp(self.image_bytes)
        return _Resp(json.dumps(self._json_for(url)).encode())

    def _json_for(self, url: str) -> dict:
        if "list=search" in url:
            hits = [{"title": self.title}] if self.title else []
            return {"query": {"search": hits}}
        if "pageimages" in url:
            page = {"pageimage": self.filename} if self.filename else {}
            return {"query": {"pages": {"123": page}}}
        if "imageinfo" in url:
            extmetadata = {}
            if self.license is not None:
                extmetadata["LicenseShortName"] = {"value": self.license}
            if self.artist is not None:
                extmetadata["Artist"] = {"value": self.artist}
            info = {
                "url": self.file_url,
                "descriptionurl": self.description_url,
                "extmetadata": extmetadata,
            }
            return {"query": {"pages": {"123": {"imageinfo": [info]}}}}
        raise AssertionError(f"unexpected URL {url}")


def _source(opener: FakeOpener) -> WikimediaImageSource:
    return WikimediaImageSource(opener)


def test_fetch_returns_a_commercial_free_lead_image_with_its_licence_and_attribution():
    source = _source(FakeOpener())

    sourced = source.fetch("Pterophyllum scalare")

    # The resolved species' Wikipedia lead image, downloaded as bytes, with the licence, the
    # photographer credit (HTML stripped to plain text), and the Commons file page as source URL.
    assert sourced == SourcedImage(
        data=IMAGE_BYTES,
        license="CC BY-SA 4.0",
        attribution="Jane Doe",
        source_url="https://commons.wikimedia.org/wiki/File:Pterophyllum_scalare.jpg",
    )


def test_fetch_skips_a_candidate_whose_licence_is_not_commercial_free():
    # The lead image exists but is CC BY-NC — storable only for a non-commercial tool. We refuse it
    # and report the honest gap rather than store an image we cannot safely re-serve.
    source = _source(FakeOpener(license="CC BY-NC 4.0"))

    assert source.fetch("Pterophyllum scalare") is None


def test_fetch_sends_the_descriptive_user_agent_on_every_request():
    opener = FakeOpener()

    _source(opener).fetch("Pterophyllum scalare")

    # Every outbound request — the three API calls and the image download — carries the descriptive
    # User-Agent Wikimedia's policy requires; none goes out anonymous.
    assert opener.user_agents
    assert all(ua and ua.startswith("fishpage/") for ua in opener.user_agents)
    assert "github.com/r4stered/fishpage" in (opener.user_agents[0] or "")


def test_fetch_returns_none_when_the_species_has_no_article():
    # No search hit — the unresolved/oddball tail. No query downstream, no image.
    assert _source(FakeOpener(title=None)).fetch("Nonexistent species") is None


def test_fetch_returns_none_when_the_article_has_no_lead_image():
    assert _source(FakeOpener(filename=None)).fetch("Pterophyllum scalare") is None


def test_fetch_returns_none_when_the_file_records_no_licence():
    # A Commons file with no LicenseShortName is unstorable — we can't honour an obligation we can't
    # read — so it resolves to the honest gap rather than a stored image with an unknown licence.
    assert _source(FakeOpener(license=None)).fetch("Pterophyllum scalare") is None


def test_fetch_swallows_a_transport_failure_and_returns_none():
    # A source fetch is best-effort: a reset connection, a 404, or malformed JSON is "no image",
    # never an exception that would propagate and take the Item's enrichment down with it.
    def boom(req, *, timeout=None):
        raise OSError("connection reset by peer")

    assert WikimediaImageSource(boom).fetch("Pterophyllum scalare") is None


def test_fetch_falls_back_to_the_licence_as_credit_when_no_artist_is_recorded():
    # Some Commons files carry a licence but no Artist; the card must still show *something*, so the
    # licence stands in as the credit rather than leaving an empty attribution.
    sourced = _source(FakeOpener(artist=None)).fetch("Pterophyllum scalare")

    assert sourced is not None and sourced.attribution == "CC BY-SA 4.0"


def test_the_auto_image_source_is_off_by_default():
    # With an empty environment — the `just run` / CI case — there is no source, no outbound call.
    assert select_image_source(load_settings({})) is None


def test_enabling_images_selects_a_wikimedia_source():
    # The auto-image source rides the image flag: when images are on, the Wikimedia source is wired
    # so the drainer can fill a resolved, non-strain Item's image. It needs no credential.
    source = select_image_source(load_settings({"FISHPAGE_IMAGES_ENABLED": "1"}))
    assert isinstance(source, WikimediaImageSource)


def test_only_cc0_cc_by_cc_by_sa_and_public_domain_are_commercial_free():
    # The exact set the spike cleared us to store-and-re-serve: CC0, CC-BY, CC-BY-SA, public domain.
    assert is_commercial_free("CC0")
    assert is_commercial_free("CC BY 4.0")
    assert is_commercial_free("CC BY-SA 3.0")
    assert is_commercial_free("Public domain")


def test_noncommercial_noderivative_and_all_rights_reserved_are_not_commercial_free():
    # NC and ND carry use-context risk on a tool that may turn commercial, and an unknown/ARR
    # licence is not storable at all — none of these clears the bar, so the candidate is skipped.
    assert not is_commercial_free("CC BY-NC 2.0")
    assert not is_commercial_free("CC BY-NC-SA 4.0")
    assert not is_commercial_free("CC BY-ND 4.0")
    assert not is_commercial_free("All rights reserved")
    assert not is_commercial_free(None)
    assert not is_commercial_free("")
