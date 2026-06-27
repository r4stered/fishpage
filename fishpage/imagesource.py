"""The automatic image source: resolve a species to a Wikimedia lead image we may store + re-serve.

The drainer keys this off the species the enricher resolves, but only when it is safe to
store-and-show — a resolved, non-strain Item. The licence filter is the second guardrail: a
candidate is taken only when its licence is *commercial-free* (CC0 / CC-BY / CC-BY-SA / public
domain), the set the spike cleared for an internal tool that stores the bytes in R2 and re-serves
them behind Cloudflare Access with attribution. Anything NC, ND, or all-rights-reserved is skipped.

Like the enricher and the image store, the source is opt-in, default-off, and dependency-injected:
the transport is a seam, so the whole choreography is exercised by a fake with no outbound request.
"""

import json
import logging
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from html import unescape
from typing import Any, Protocol, runtime_checkable

from fishpage.config import Settings

_log = logging.getLogger(__name__)

# Wikimedia/Commons require a descriptive User-Agent identifying the app and a contact, per their
# API etiquette — an anonymous agent risks being throttled or blocked.
USER_AGENT = "fishpage/0.1 (https://github.com/r4stered/fishpage; williams.r.drew@gmail.com)"

_WIKI_API = "https://en.wikipedia.org/w/api.php"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"


@dataclass(frozen=True)
class SourcedImage:
    """One automatically-sourced image: the raw bytes plus the licence metadata that must ride with
    it. ``attribution`` is the photographer credit rendered on the card; ``source_url`` points at
    the file's description page. All three are stored beside the bytes so the licensing obligation
    is discharged on every render."""

    data: bytes
    license: str
    attribution: str
    source_url: str


@runtime_checkable
class ImageSource(Protocol):
    """The injectable image source: resolve a species to a storable lead image, or ``None``.

    ``None`` is the honest gap — no usable, commercial-free image for this species — which the
    drainer treats exactly like the manual-upload fallback, never as a failure.
    """

    def fetch(self, species: str) -> SourcedImage | None: ...


class WikimediaImageSource:
    """An :class:`ImageSource` backed by the Wikipedia lead image for a species, via Commons.

    The choreography is the spike's: search Wikipedia for the species' article, read its lead image
    filename, then pull that file's licence and download URL from Commons. A candidate is returned
    only when its licence is commercial-free; anything else resolves to ``None`` so the drainer
    falls back to manual upload. The opener is injected so the whole flow — including the
    descriptive User-Agent on every request — is exercised by a fake with no network.
    """

    def __init__(self, opener: Callable[..., Any] = urllib.request.urlopen, *, timeout: float = 20):
        self._opener = opener
        self._timeout = timeout

    def fetch(self, species: str) -> SourcedImage | None:
        title = self._search_title(species)
        if title is None:
            return None
        filename = self._lead_image_filename(title)
        if filename is None:
            return None
        info = self._file_info(filename)
        if info is None or not is_commercial_free(info["license"]):
            return None
        data = self._get_bytes(info["url"])
        if data is None:
            return None
        return SourcedImage(
            data=data,
            license=info["license"],
            attribution=info["attribution"],
            source_url=info["source_url"],
        )

    def _search_title(self, species: str) -> str | None:
        data = self._get_json(
            _WIKI_API,
            action="query",
            list="search",
            srsearch=species,
            srlimit=1,
            format="json",
        )
        hits = (((data or {}).get("query") or {}).get("search")) or []
        return hits[0]["title"] if hits else None

    def _lead_image_filename(self, title: str) -> str | None:
        data = self._get_json(
            _WIKI_API,
            action="query",
            titles=title,
            prop="pageimages",
            piprop="name",
            format="json",
        )
        pages = (((data or {}).get("query") or {}).get("pages")) or {}
        page = next(iter(pages.values()), {})
        return page.get("pageimage")

    def _file_info(self, filename: str) -> dict[str, str] | None:
        data = self._get_json(
            _COMMONS_API,
            action="query",
            titles=f"File:{filename}",
            prop="imageinfo",
            iiprop="url|extmetadata",
            format="json",
        )
        pages = (((data or {}).get("query") or {}).get("pages")) or {}
        page = next(iter(pages.values()), {})
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("url")
        if not url:
            return None
        meta = info.get("extmetadata") or {}
        license = (meta.get("LicenseShortName") or {}).get("value")
        if not license:
            return None
        artist = (meta.get("Artist") or {}).get("value")
        return {
            "url": url,
            "license": license,
            "attribution": _plain_text(artist) or license,
            "source_url": info.get("descriptionurl")
            or f"https://commons.wikimedia.org/wiki/File:{filename}",
        }

    def _get_json(self, base: str, **params: Any) -> dict | None:
        body = self._get_bytes(f"{base}?{urllib.parse.urlencode(params)}")
        return None if body is None else json.loads(body.decode())

    def _get_bytes(self, url: str) -> bytes | None:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self._opener(req, timeout=self._timeout) as response:
                return response.read()
        except Exception:
            # A source fetch is best-effort: a transport error or a 404 is "no image", logged for
            # the dashboard, never a reason to fail the Item's enrichment. The drainer wraps the
            # whole acquisition besides, so even a malformed response leaves the Item on manual.
            _log.warning("Wikimedia fetch failed for %s", url, exc_info=True)
            return None


def select_image_source(settings: Settings) -> ImageSource | None:
    """The configured auto-image source, or ``None`` when images are off.

    Rides the image flag: it needs no credential of its own, so enabling images is enough to wire
    the Wikimedia source. The drainer only queries it when an image bucket is also configured, so a
    flag without a bucket still stores nothing.
    """
    if not settings.images_enabled:
        return None
    return WikimediaImageSource()


def _plain_text(html: str | None) -> str:
    """Strip the HTML Commons wraps an Artist credit in down to a plain attribution string."""
    if not html:
        return ""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def is_commercial_free(license: str | None) -> bool:
    """Whether a Wikimedia licence short name clears the store-and-re-serve bar.

    Commercial-free only: CC0, CC-BY, CC-BY-SA, and public domain. A non-commercial (NC) or
    no-derivatives (ND) clause, or an unknown / all-rights-reserved licence, is rejected — those
    carry a use-context or storability risk this internal tool refuses to take on.
    """
    if not license:
        return False
    text = license.strip().lower()
    if "cc0" in text or "public domain" in text or text == "pd":
        return True
    # Token-split so an NC/ND clause is matched as its own component, never as a substring of an
    # unrelated word: "CC BY-SA 4.0" -> ['cc', 'by', 'sa', '4.0'].
    tokens = re.split(r"[\s\-]+", text)
    if "nc" in tokens or "nd" in tokens:
        return False
    return "by" in tokens
