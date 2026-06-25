# Enrichment: AI-generated Classifiers and sourced images, filled by a decoupled drainer

The catalog promises an "image-rich" view used "to decide what livestock to order," but v1 ships
neither images nor care attributes — every card is a row of text over a placeholder. Phase 2, the
Enrichment story foreshadowed by [ADR 0008](0008-sqlite-litestream-object-storage.md), closes that
gap: it populates each Item's care **Classifiers** and **image** from outside the Stocklist. This ADR
records the shape of that subsystem, because almost every choice in it deviates from the obvious one.

## Two sources, the LLM as the spine

Care attributes (difficulty, aggression, plant-safety, …) and a photograph are two different
acquisition problems with no shared best source, so we split them:

- **Classifiers are AI-generated.** Care attributes are hobbyist *judgments*, not biological facts:
  the clean academic databases don't carry them and the sites that do have no API and are
  scrape-hostile. They are exactly the structured judgments an LLM does well. One constrained-schema
  Claude call (Sonnet the default tier, Haiku the cost fallback) takes the trade name plus the Item's
  Derived Category and size, and returns a `scientific_name`, a `common_name`, and the enum
  Classifiers in a single validated payload. Name-normalisation and care extraction are the same call,
  and the species it resolves is what keys the image lookup.
- **Images are sourced, never generated.** An LLM cannot hand back a real photograph, and a fabricated
  one is actively harmful on a purchasing tool. The image comes from an external source keyed by the
  resolved species, with **manual upload** as the always-available fallback.

## The `unknown` escape hatch is the honesty guardrail

This is a buying tool: a confident-but-wrong "peaceful" causes a bad order. So the Classifier schema
lets the model return `unknown` for any attribute and `null` for the species when it cannot map the
name with confidence. An honest gap routes to manual entry; a fabricated value does not reach the
catalog. This is what makes `ai-generated` data safe to filter on — it is the model's *best honest*
read, not a fully-populated guess.

## Provenance, and manual values that cannot be clobbered

Every enriched value records its **Provenance** — `manual`, `wikimedia`, or `ai-generated` — carried
per attribute so a human can correct one field while the rest stay AI-read. `manual` is authoritative
and re-enrichment must never overwrite it, the same never-destroy-the-human's-work instinct as
[ADR 0001](0001-sku-permanent-key-upsert-never-delete.md)'s never-delete rule.

We enforce that with two tables rather than one, and deliberately **not** an EAV registry:

- A typed **`enrichment`** row per SKU holds the AI care block (enum columns with `CHECK`
  constraints). Re-enrichment overwrites this row wholesale — no merge logic. (Image metadata moved
  out of this row to its own table — see the 2026-06-25 amendment below.)
- A sparse **`classifier_override`** table holds only human corrections. A row's presence *is* `manual`
  Provenance and wins on read; re-enrichment never touches this table, so a correction is structurally
  un-clobberable. Provenance is therefore *derived* (override present → `manual`, else the enrichment
  value → `ai-generated`, else unset), which avoids a parallel `_provenance` column beside every value.

An open-ended EAV "classifier registry" was rejected: its only payoff is adding a Classifier without a
migration, and the lightweight runner of issue #51 already made migrations cheap. The Classifier set
is a small curated vocabulary that changes rarely, so typed columns — with real `CHECK` enums and no
SQL-side filtering pain (browse filters in memory) — are the better trade.

## Enrichment is decoupled from ingestion

A fresh Stocklist can introduce hundreds of new SKUs, and each enrichment is a network round-trip
(LLM + image fetch). Running that inside the upload request would hang it past the edge's request
ceiling. So ingestion stays fast and only marks new SKUs **un-enriched**; that set is the work queue.
An in-process **background drainer** on the always-on Machine (no scale-to-zero, per
[ADR 0008](0008-sqlite-litestream-object-storage.md)) fills them in, rate-limited, and "on demand"
re-enrichment simply clears a row back into the queue. A crash mid-batch is safe — the un-enriched set
survives and Provenance protects manual values. New Items are browseable un-enriched in the meantime:
price, qty, size and Derived Category — the data that actually drives an order — are present at once,
and image/Classifiers fill in behind them.

Per [ADR 0007](0007-deploy-to-flyio-cloud-not-unraid.md)/[0008](0008-sqlite-litestream-object-storage.md)'s
opt-in pattern, the drainer and the Anthropic key default **off**; the enricher is dependency-injected
so the test suite exercises parse/store/Provenance logic with a fake and never touches the network.

## Images live in their own bucket, proxied through the app

Image bytes download to a **separate `fishpage-images` R2 bucket** (provisioned alongside the
Litestream bucket by the OpenTofu bring-up of [ADR 0010](0010-opentofu-bootstrap-for-cloud-infra.md)),
keeping them out of the bucket `fishpage-restore` reasons about. The database stores only the object
key plus license/attribution metadata in a dedicated `image` table (see the 2026-06-25 amendment
below) — never the bytes, which would bloat the WAL Litestream streams.
The app **proxies** images from R2 rather than exposing a public bucket URL, so they stay behind the
Cloudflare Access edge exactly like the wholesale prices ([ADR 0007](0007-deploy-to-flyio-cloud-not-unraid.md)'s
no-public-origin). The cost is image egress on the single Machine — negligible at single-user scale.

## The image *source* is spiked, not gated

Open aquarium-trade image coverage is doubtful: free, attributable sources (Wikimedia, iNaturalist,
GBIF) are thin on line-bred strains and oddballs — the very Items a photo helps most — while the
high-coverage sources (retailers, image search) cannot be stored and re-served without a licensing
liability. The tension is coverage vs. licensability, and it may not resolve well. So Enrichment
**ships as one bundle** — Classifiers + image storage/proxy + manual upload — and the *automatic*
image source is a separate spike that measures usable-and-licensable hit-rate over a real SKU sample.
Manual upload is the baseline that always works, so the bundle is never blocked on the spike; a
poor result simply means images stay manual-only. We refuse to gate the valuable, reliable half
(Classifiers) on the uncertain half (auto-images).

## Consequences

- Enrichment introduces the codebase's **first outbound-network production dependency** and first LLM
  call; everything prior was local compute. The OTel instrumentation of
  [ADR 0009](0009-opentelemetry-grafana-cloud-stale-catalog-alert.md) now covers a call that can fail,
  rate-limit, and cost money.
- The one-time `enrichment` + `classifier_override` schema lands via issue #51's migration runner — the
  first real exercise of it against the live, populated database.
- Wikimedia/iNaturalist need no API key but do require a descriptive `User-Agent`; the Anthropic key is
  a Fly secret. Both ride the existing opt-in-default-off config.
- Auto-image coverage is explicitly not guaranteed. Some Items will only ever have a manually-uploaded
  image, and that is an accepted outcome, not a defect.

## Amendment (2026-06-25): image Provenance follows the override pattern, not the `enrichment` row

The original text put image metadata in the typed `enrichment` row alongside the AI care block. That
could not coexist with two other rules this same ADR sets out:

- **Re-enrichment overwrites the `enrichment` row wholesale** (on-demand re-enrich deletes it), so a
  value living there *cannot* also be "never overwritten by re-enrichment" — which is exactly what a
  `manual` image must be.
- **A SKU is un-enriched precisely when it has no `enrichment` row**, so attaching an image to a
  not-yet-enriched Item would force that row into existence and silently drop the SKU out of the
  drainer's Classifier queue.

The resolution is the rule this ADR already applies to Classifiers: a value's **Provenance decides
where it lives**. A `manual` image is the same kind of value as a `manual` Classifier — human-authored,
outranks any best-effort sourced value, must survive re-enrichment — so it belongs in the same
un-clobberable layer, not in the wholesale-overwritten AI row.

Image metadata therefore moves to a dedicated sparse **`image`** table keyed by SKU (`object_key`,
`license`, `attribution`, `source_url`, `provenance`). The four `image_*` columns the phase-2 schema
added to `enrichment` are dropped. Re-enrichment clears only *non-`manual`* image rows
(`DELETE FROM image WHERE sku = ? AND provenance != 'manual'`); the `enrichment` delete, the
row-absent un-enriched queue, and `enrichment_for` are otherwise untouched. The sourced-image path,
when the spike ships, writes the same table with `provenance = 'wikimedia'` and *is* re-enrichable;
the manual upload writes `provenance = 'manual'` and is structurally safe — the image columns and the
two-table Provenance split are now uniform across Classifiers and images.

## Amendment (2026-06-25): enrichment cost is observed as tokens, dollars derived downstream

The "can fail, rate-limit, and cost money" consequence above is now instrumented, following the
[ADR 0009](0009-opentelemetry-grafana-cloud-stale-catalog-alert.md) pattern: per-Item result logs,
counters for call outcome, token spend, and the honesty-gap rate (how often a call returns a `null`
species or an `unknown` Classifier — the quality signal that catches enrichment silently degrading),
and an observable gauge for the un-enriched queue depth.

The one choice worth recording is how **cost** is observed: as **token counts tagged by model, never a
dollar figure in the app**. A per-model price table is exactly the kind of fact that goes stale, and
this repo keeps rotting facts out of code. Tokens are the durable primitive; dollars are computed in
Grafana from the token counters with a price variable, so a reprice edits a dashboard, not the app.
The `model` tag is stamped even though only the Sonnet default is wired today, so the spend split is
already in place if the Haiku cost fallback this ADR describes is ever implemented.
