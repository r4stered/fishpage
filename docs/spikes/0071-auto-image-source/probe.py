"""Spike #71 research tooling (throwaway, not wired into the app).

For each SKU in sample.csv, query Wikimedia, iNaturalist, and GBIF for an image of
the resolved species and classify whether that image is USABLE (a photo of the right
species exists) and LICENSABLE (its licence permits store-and-re-serve with
attribution). Writes results.csv + results.json and prints a summary.

Run: uv run python docs/spikes/0071-auto-image-source/probe.py
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
UA = "fishpage-image-spike/1.0 (https://github.com/r4stered/fishpage; williams.r.drew@gmail.com)"

# Licence buckets, from the fishpage perspective: an internal, non-commercial buying
# tool that stores the bytes in R2 and re-serves them behind Cloudflare Access with
# attribution. "as-is" re-serving means no-derivatives is fine.
COMMERCIAL_FREE = {"cc0", "cc-by", "cc-by-sa", "pd", "public domain"}  # safest
NONCOMMERCIAL_FREE = {"cc-by-nc", "cc-by-nc-sa", "cc-by-nc-nd"}  # ok for internal use
NODERIV_FREE = {"cc-by-nd"}  # storable + attributable as-is
NOT_LICENSABLE = {"all rights reserved", "c", "", None}

# Any of these buckets means we may store + re-serve the image with attribution.
LICENSABLE = ("commercial-free", "noncommercial-free", "noderiv-free")


def is_licensable(licences: dict) -> bool:
    return any(b in LICENSABLE for b in licences)


def classify_licence(code: str | None) -> str:
    if code is None:
        return "unknown"
    c = code.strip().lower().replace("_", "-")
    if c in COMMERCIAL_FREE:
        return "commercial-free"
    if c in NONCOMMERCIAL_FREE:
        return "noncommercial-free"
    if c in NODERIV_FREE:
        return "noderiv-free"
    if "by-nc" in c:
        return "noncommercial-free"
    if c.startswith("cc-by") or c == "cc0":
        return "commercial-free"
    if "public" in c or c == "pd":
        return "commercial-free"
    return "not-licensable"


def get(url: str, *, timeout: int = 20, retries: int = 4) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(2**attempt)  # backoff on rate limit
                continue
            print(f"  ! {url[:80]}... -> HTTP {e.code}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"  ! {url[:80]}... -> {type(e).__name__}: {e}", file=sys.stderr)
            return None
    return None


# --- GBIF: occurrence StillImage media, with per-media licence ---------------
def probe_gbif(species: str) -> dict:
    m = get(f"https://api.gbif.org/v1/species/match?name={urllib.parse.quote(species)}")
    if not m or not m.get("usageKey"):
        return {
            "matched": False,
            "usable": False,
            "licensable": False,
            "n_images": 0,
            "licences": {},
        }
    key = m["usageKey"]
    occ = get(
        f"https://api.gbif.org/v1/occurrence/search?taxonKey={key}&mediaType=StillImage&limit=20"
    )
    licences: dict[str, int] = {}
    n = 0
    if occ:
        for o in occ.get("results", []):
            for media in o.get("media", []):
                if media.get("type") != "StillImage":
                    continue
                n += 1
                lic = media.get("license") or o.get("license")
                bucket = classify_licence(_gbif_lic_short(lic))
                licences[bucket] = licences.get(bucket, 0) + 1
    usable = n > 0
    return {
        "matched": True,
        "match_type": m.get("matchType"),
        "usable": usable,
        "licensable": is_licensable(licences),
        "n_images": n,
        "total_with_media": (occ or {}).get("count", 0),
        "licences": licences,
    }


def _gbif_lic_short(url: str | None) -> str | None:
    if not url:
        return None
    u = url.lower()
    if "publicdomain" in u or "cc0" in u or "/zero/" in u:
        return "cc0"
    for tag in ("by-nc-sa", "by-nc-nd", "by-nc", "by-sa", "by-nd", "by"):
        if f"/{tag}/" in u:
            return f"cc-{tag}"
    if "all rights" in u or url == "All rights reserved":
        return "all rights reserved"
    return url


# --- iNaturalist: taxon photos, each with a licence_code ---------------------
def probe_inat(species: str) -> dict:
    data = get(
        "https://api.inaturalist.org/v1/taxa?"
        + urllib.parse.urlencode({"q": species, "rank": "species", "per_page": 1})
    )
    if not data or not data.get("results"):
        return {
            "matched": False,
            "usable": False,
            "licensable": False,
            "n_photos": 0,
            "licences": {},
        }
    hit = data["results"][0]
    matched_name = hit.get("name", "")
    # The search endpoint does NOT populate taxon_photos; the detail endpoint does.
    detail = get(f"https://api.inaturalist.org/v1/taxa/{hit['id']}")
    taxon = ((detail or {}).get("results") or [hit])[0]
    default_photo = taxon.get("default_photo") or {}
    photos = taxon.get("taxon_photos") or []
    licences: dict[str, int] = {}
    for tp in photos:
        code = (tp.get("photo") or {}).get("license_code")
        bucket = classify_licence(code)
        licences[bucket] = licences.get(bucket, 0) + 1
    usable = len(photos) > 0 or bool(default_photo)
    return {
        "matched": matched_name.lower() == species.lower(),
        "matched_name": matched_name,
        "usable": usable,
        "licensable": is_licensable(licences),
        "n_photos": len(photos),
        "default_photo_licence": classify_licence(default_photo.get("license_code")),
        "licences": licences,
    }


# --- Wikimedia: Wikipedia lead image (always on Commons, free by policy) ------
def probe_wikimedia(species: str) -> dict:
    # 1) find the best article title for the species
    search = get(
        "https://en.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": species,
                "srlimit": 1,
                "format": "json",
            }
        )
    )
    hits = ((search or {}).get("query") or {}).get("search") or []
    if not hits:
        return {"matched": False, "usable": False, "licensable": False, "title": None}
    title = hits[0]["title"]
    # 2) lead image filename via pageimages
    pi = get(
        "https://en.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode(
            {
                "action": "query",
                "titles": title,
                "prop": "pageimages",
                "piprop": "name",
                "format": "json",
            }
        )
    )
    pages = ((pi or {}).get("query") or {}).get("pages") or {}
    page = next(iter(pages.values()), {})
    fname = page.get("pageimage")
    if not fname:
        return {"matched": True, "title": title, "usable": False, "licensable": False}
    # 3) licence of that file from Commons extmetadata
    ii = get(
        "https://commons.wikimedia.org/w/api.php?"
        + urllib.parse.urlencode(
            {
                "action": "query",
                "titles": f"File:{fname}",
                "prop": "imageinfo",
                "iiprop": "extmetadata",
                "format": "json",
            }
        )
    )
    cpages = ((ii or {}).get("query") or {}).get("pages") or {}
    cpage = next(iter(cpages.values()), {})
    meta = (cpage.get("imageinfo") or [{}])[0].get("extmetadata") or {}
    lic = (meta.get("LicenseShortName") or {}).get("value") or (meta.get("License") or {}).get(
        "value"
    )
    bucket = classify_licence(_wiki_lic_short(lic))
    return {
        "matched": True,
        "title": title,
        "usable": True,
        "licence_raw": lic,
        "licence_bucket": bucket,
        "licensable": bucket in ("commercial-free", "noncommercial-free", "noderiv-free"),
    }


def _wiki_lic_short(lic: str | None) -> str | None:
    if not lic:
        return None
    u = lic.lower()
    if "public domain" in u or "cc0" in u or u == "pd":
        return "cc0"
    for tag in ("by-nc-sa", "by-nc-nd", "by-nc", "by-sa", "by-nd", "by"):
        if tag in u.replace(" ", "-"):
            return f"cc-{tag}"
    return lic


def main() -> None:
    rows = list(csv.DictReader((HERE / "sample.csv").open()))
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows, 1):
        sp = row["resolved_species"]
        print(f"[{i}/{len(rows)}] {row['sku']} {row['name']!r} -> {sp}")
        rec: dict[str, Any] = {**row}
        rec["gbif"] = probe_gbif(sp)
        rec["inat"] = probe_inat(sp)
        rec["wikimedia"] = probe_wikimedia(sp)
        out.append(rec)
        time.sleep(1.0)  # be polite to the free APIs

    (HERE / "results.json").write_text(json.dumps(out, indent=2))

    # flat CSV
    with (HERE / "results.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "sku",
                "name",
                "resolved_species",
                "resolution_confidence",
                "kind",
                "wiki_usable",
                "wiki_licensable",
                "wiki_licence",
                "inat_usable",
                "inat_licensable",
                "inat_default_licence",
                "inat_n_photos",
                "gbif_usable",
                "gbif_licensable",
                "gbif_n_images",
            ]
        )
        for r in out:
            w.writerow(
                [
                    r["sku"],
                    r["name"],
                    r["resolved_species"],
                    r["resolution_confidence"],
                    r["kind"],
                    r["wikimedia"].get("usable"),
                    r["wikimedia"].get("licensable"),
                    r["wikimedia"].get("licence_bucket"),
                    r["inat"].get("usable"),
                    r["inat"].get("licensable"),
                    r["inat"].get("default_photo_licence"),
                    r["inat"].get("n_photos"),
                    r["gbif"].get("usable"),
                    r["gbif"].get("licensable"),
                    r["gbif"].get("n_images"),
                ]
            )

    # summary
    n = len(out)

    def rate(src: str, field: str) -> str:
        c = sum(1 for r in out if r[src].get(field))
        return f"{c}/{n} ({100 * c // n}%)"

    print(f"\n=== HIT-RATE SUMMARY (n={n}) ===")
    print(f"{'source':<12} {'usable':>14} {'licensable':>14}")
    for src in ("wikimedia", "inat", "gbif"):
        print(f"{src:<12} {rate(src, 'usable'):>14} {rate(src, 'licensable'):>14}")

    # union: at least one source usable+licensable
    union = sum(
        1 for r in out if any(r[s].get("licensable") for s in ("wikimedia", "inat", "gbif"))
    )
    print(f"\nUnion (any source usable+licensable): {union}/{n} ({100 * union // n}%)")


if __name__ == "__main__":
    main()
