# Spike #71 — auto-image-source hit-rate (usable AND licensable)

**Question (issue [#71](https://github.com/r4stered/fishpage/issues/71), under [ADR 0011](../../adr/0011-enrichment-ai-classifiers-sourced-images.md)):**
over a real SKU sample, what fraction of Items can get an image that is **both usable**
(a clear photo of the right species) **and licensable** (storable + re-servable with
attribution) from each free source — Wikimedia, iNaturalist, GBIF — and is automatic image
acquisition therefore worth building on top of the manual-upload baseline?

**Recommendation: GO — but Wikimedia-only, species-level, as an opportunistic fill, not a
guarantee.** Coverage is far better than the ADR's pessimistic prior assumed. The real
limitation is not coverage or licensing; it is **strain fidelity** and **species
resolution**, addressed below.

---

## Method

- **Sample:** 40 real SKUs drawn from the live `fishpage.db` (969 Items), stratified across
  Derived Categories and deliberately loaded with the hard cases ADR 0011 flags — line-bred
  strains, L-number plecos, oddball/monster species, and plants. See [`sample.csv`](sample.csv).
- **Resolution:** each trade name was resolved by hand to a scientific species with a
  confidence grade (`high`/`medium`/`low`). In production this step is the LLM's
  constrained-schema call; doing it manually here isolates the *image-source* question from
  LLM-resolution quality, while still recording where resolution is shaky.
- **Probe:** [`probe.py`](probe.py) (throwaway research tooling, **not** wired into the app)
  queries each source's public API for the resolved species and records, per source, whether
  an image is **usable** and the **licence of every candidate photo** — not just that one
  exists. Raw output in [`results.json`](results.json) / [`results.csv`](results.csv).
- **Licence classification** (from fishpage's posture: an internal, non-commercial buying
  tool that stores bytes in R2 and re-serves them as-is behind Cloudflare Access, with
  attribution):
  - `commercial-free` — CC0 / CC-BY / CC-BY-SA / public domain. Safest; no use-context risk.
  - `noncommercial-free` — CC-BY-NC(-SA/-ND). Acceptable *for our internal use* but not if the
    tool ever turns commercial.
  - `not-licensable` — all-rights-reserved / unknown. **Present but unusable** by us.
- **Reproduce:** `uv run python docs/spikes/0071-auto-image-source/probe.py`

---

## Results

### Per-source hit-rate (n = 40)

| Source | Usable (photo of right species exists) | Licensable (any free incl. NC) | Licensable **commercial-free only** |
|---|---|---|---|
| **Wikimedia** (Wikipedia lead image) | **39/40 (97%)** | **39/40 (97%)** | **39/40 (97%)** |
| **iNaturalist** | 37/40 (92%) | 37/40 (92%) | 31/40 (77%) |
| **GBIF** (occurrence StillImage) | 39/40 (97%) | 37/40 (92%) | 29/40 (72%) |
| **Union (any source)** | — | **39/40 (97%)** | — |

The only total miss is `752074 Red Flame` → *Echinodorus 'Red Flame'*, a plant **cultivar**
trade name with no clean species — i.e. a **resolution** failure, not a coverage failure.

### Licence mix per source (every candidate photo, not just the chosen one)

| Source | commercial-free | noncommercial-free | not-licensable / unknown |
|---|---|---|---|
| Wikimedia lead images | 39 | 0 | 1 (no lead image) |
| iNaturalist taxon photos | 97 | 142 | 47 |
| GBIF occurrence media | 240 | 791 | 47 |

This table is the heart of the spike. "An image exists" and "we may store it" are different
questions, and they diverge most exactly where you'd reach first:

- **iNaturalist's *default* photo is frequently all-rights-reserved.** The naive "grab the
  taxon's default photo" path returns a non-licensable image for many species (the cardinal
  tetra default photo is `(c) … all rights reserved`). You **must** iterate `taxon_photos` and
  filter on `license_code`, skipping `null`/ARR. The 92% iNat licensable rate assumes you do.
- **GBIF occurrence media skews heavily CC-BY-NC** (791 NC vs 240 commercial-free). The photos
  are in-situ research/observation shots — variable quality, frequent wrong life-stage or
  preserved specimens — and the worst fit for a "decide what to order" card.
- **Wikimedia lead images are free by Commons policy** — overwhelmingly CC-BY-SA / CC0 / PD,
  and they are curated, representative photos. It is the single best source on every axis.

### Attribution / licensing obligations per source

- **Wikimedia / Commons** — CC-BY-SA or CC-BY: must show author + licence name + link, and (for
  SA) note that the photo is under a share-alike licence. CC0/PD: no obligation, but crediting
  is courteous. Requires a descriptive `User-Agent` on requests (already noted in ADR 0011).
  Storable and re-servable.
- **iNaturalist** — only photos with an explicit CC `license_code` are reusable; **default /
  ARR photos are not**. CC-BY(-NC/-SA) requires observer attribution + licence link. Needs the
  same descriptive `User-Agent`. Storable when CC-licensed.
- **GBIF** — GBIF is an index; the licence and attribution belong to the **underlying
  publisher** carried per-media (often the same iNat/observation photos). Must honour the
  per-record `license` and `rightsHolder`; much of it is **NC**, so it is acceptable only while
  fishpage stays non-commercial.

---

## The two findings that actually decide the design

### 1. The limiting factor is strain fidelity, not coverage

Every hit-rate above is at the **species** level. For a **line-bred strain**, the right-species
photo is the **wrong fish**: a wild *Poecilia reticulata* is not a "Guppy Tequila Sunrise", and
a wild *Pterophyllum scalare* is not a "Gold Marble Angel". On a tool whose stated value is an
*image-rich view to decide what to order*, a generic wild-type photo for a fancy strain is at
best unhelpful and at worst misleading.

This is not a fringe case. ~**23% of the live catalog (230/969 Items)** sits in
predominantly-line-bred categories — Guppy, Goldfish, Angelfish, Platy, GloFish, Molly, Betta,
Swordtail, Discus, Koi — and more line-bred/locality variants hide inside "wild-type"
categories (Plecos, Shrimp, Cichlids). For these, auto-images should be treated as a *genus/
species illustration*, clearly distinct from a true product photo, and **manual upload remains
the primary path**. The spike's by-kind split: wild-type 27/27 licensable, line-bred 12/13 — the
number says "hit", but the *fidelity* is what's missing.

### 2. Species resolution is the real gate

Auto-acquisition can only fire when resolution produces a clean species. The one hard miss was a
cultivar that doesn't resolve; low-confidence resolutions (plant cultivars, ambiguous trade
names like "Lipstick Goby", "Baby Whale Fish") are the risky tail. This is exactly ADR 0011's
`unknown`/`null` honesty guardrail doing its job: **no resolution → no query → no image**, which
is the correct, safe outcome. The image source is only as good as the upstream LLM resolution,
and that is a separate failure mode from coverage.

---

## Go / no-go

**GO — build automatic image acquisition, scoped as follows:**

1. **Wikimedia first and, initially, only.** 97% usable + commercial-free, curated lead images,
   cleanest licensing. It alone clears the bar the ADR worried we'd miss. iNaturalist is a
   reasonable second source *if* you filter to CC-licensed `taxon_photos` (never the default
   photo blindly). **Deprioritise GBIF**: mostly NC, poorest image quality for a buying tool,
   adds publisher-attribution complexity for little marginal coverage.
2. **Species-level, best-effort, never a guarantee.** Manual upload stays the baseline and the
   authoritative path for line-bred strains (~23% of catalog) — exactly ADR 0011's design.
   Store the auto-image with `wikimedia` Provenance so a human can override per ADR 0011's
   un-clobberable `manual` rule.
3. **Persist licence + attribution + source URL** with every stored image (the schema already
   provides for this) and render the attribution. Treat NC sources as internal-use-only and
   re-evaluate if the tool's use context ever changes.
4. **Watch resolution, not coverage.** The failure mode in production is bad/`null` species
   resolution, not missing images. Route low-confidence/`unknown` resolutions straight to manual.

**Why not no-go:** the ADR hedged that free-source coverage might be too thin to bother. The
evidence says the opposite at the species level (97% via Wikimedia alone). The honest scope is
narrower than "a photo for every SKU" — it is "a representative species photo for most Items,
manual upload for strains and the unresolved tail" — and that is worth building.

---

## Acceptance criteria

- [x] A documented sample of 30–50 real SKUs with resolved species names — [`sample.csv`](sample.csv) (40 SKUs)
- [x] Per-source hit-rate (usable AND licensable) for Wikimedia, iNaturalist, GBIF — table above
- [x] Licensing notes per source (what is/isn't storable + attribution obligation) — above
- [x] A written go/no-go recommendation — GO, Wikimedia-first, species-level best-effort
- [x] No production code change — all artifacts live under `docs/spikes/`; `probe.py` is not imported by the app
