# fishpage

[![codecov](https://codecov.io/github/r4stered/fishpage/graph/badge.svg?token=GCF1B15WGZ)](https://codecov.io/github/r4stered/fishpage)

An internal browsing tool that turns Sea Dwelling Creatures' (SDC) freshwater wholesale
**Stocklist** PDF into a searchable, filterable, sortable catalog — used to decide what
livestock to order. Not a public storefront.

## What it does (v1)

- Parses the SDC Stocklist PDF into structured records (SKU, size, name, retail/special
  price, quantity) and stores them in SQLite. Columns are located from the page header
  rather than fixed coordinates, so a shifted layout still parses and a misaligned price is
  flagged instead of silently recorded; see
  [ADR 0006](docs/adr/0006-header-derived-column-boundaries.md).
- Ingestion is **upsert-by-SKU and never deletes**: an item missing from the latest
  Stocklist goes to quantity 0 (with a `last_seen` date) rather than disappearing, so any
  data attached to it survives temporary stock-outs. A **reuse guard** flags any SKU that
  reappears under a materially different name for human review. See
  [ADR 0001](docs/adr/0001-sku-permanent-key-upsert-never-delete.md).
- Serves a grid of Item cards — defaulting to in-stock only, with a toggle for the rest —
  with **fuzzy name search** (approximate, order-independent matching ranked by relevance;
  see [ADR 0004](docs/adr/0004-fuzzy-name-search-scorers.md)) and **Derived Category**,
  **Size**, and **on-special** filters, plus an **effective-price sort** (special price if
  present, else retail). The Size filter matches the raw stocklist token against the grade
  set `-`/`S`/`M`/`L`/`Jumbo`; per
  [ADR 0002](docs/adr/0002-size-column-is-overloaded.md) packaging-unit rows (e.g. `POTTED`)
  match no grade. Every control combines.
- **Persists the catalog across restarts**: the SQLite file is never deleted on boot. An empty
  catalog is seeded once from the configured sample Stocklist PDF; a catalog that already holds
  Items is reused as-is. It then **watches an incoming
  folder**: a Stocklist PDF dropped into it is parsed and reconciled into the live catalog and
  moved aside. A polling trigger over a trigger-agnostic core keeps the drop reliable on a
  mounted volume; the cloud deployment drives the same core from an authenticated upload page
  instead (see [ADR 0005](docs/adr/0005-watched-folder-polling-trigger.md)).

## Stack

Python (managed with [uv](https://docs.astral.sh/uv/)) · FastAPI · pdfplumber · SQLite.
Deploys as a single Docker image to [Fly.io](https://fly.io/), auto-deployed from `main`. SQLite is
kept durable by [Litestream](https://litestream.io/) streaming the database to Cloudflare R2 and
restoring it on boot (see [ADR 0008](docs/adr/0008-sqlite-litestream-object-storage.md)); a Cloudflare
Tunnel + Access login fronts the app so the wholesale prices are not public (see
[ADR 0007](docs/adr/0007-deploy-to-flyio-cloud-not-unraid.md)). In the cloud a new Stocklist is
ingested through an authenticated upload page rather than the local watched folder.

## Run it

```sh
uv run just run        # serves a local SQLite catalog, seeding it from the sample Stocklist if empty
```

Then open <http://127.0.0.1:8000/> for the catalog grid, or `GET /catalog` for JSON.

The catalog lives in a local SQLite file (`fishpage.db`) that persists across restarts. The first
run seeds it from the committed sample PDF (`fishpage/data/Freshwater_Stocklist_6-19-26.pdf`),
which ships inside the package so the deploy image can seed from it too; later runs reuse whatever
is already there. Seed from another PDF into an empty catalog with
`STOCKLIST_PDF=/path/to.pdf uv run just run`, or point at a different database file with
`FISHPAGE_DB=/path/to.db`.

While it runs, drop a Stocklist PDF into `data/incoming/` (named `..._M-D-YY.pdf` so its date
is read from the filename) and the catalog reconciles it within one poll, moving the file to
`data/processed/`. Override the locations and cadence with `INCOMING_DIR`, `PROCESSED_DIR`, and
`INGEST_POLL_SECONDS`.

Every cloud dependency is opt-in and defaults off, so the commands above need no cloud
credentials. The cloud deploy switches them on through the environment: `LITESTREAM_REPLICA_URL`
(Litestream replication to Cloudflare R2), `CLOUDFLARE_TUNNEL_TOKEN` (run the Cloudflare Tunnel
that fronts the app), `OTEL_EXPORTER_OTLP_ENDPOINT` (telemetry export), and `FISHPAGE_CLOUD_INGEST`
(drive ingestion from the authenticated upload page instead of the local watched folder).

## Deploy

The deploy unit is a single multi-stage Docker image: a builder stage `uv build`s the wheel, and a
slim final stage installs only that wheel — no source tree, no dev dependencies. Build and run it
locally with one command each:

```sh
docker build -t fishpage .
docker run --rm -p 8080:8080 fishpage     # then open http://127.0.0.1:8080/
```

In the cloud the image runs on a single always-on [Fly.io](https://fly.io/) Machine (`fly.toml`).
No public IP is allocated and no service ports are published, so there is no internet-reachable
origin. Administration goes over `fly proxy` on Fly's private network; the public reaches the app
only through the Cloudflare Tunnel below.

```sh
fly deploy                          # build and release the image to the Machine
fly ips list                        # expect no addresses allocated
fly proxy 8080:8080 -a fishpage     # private admin path: open http://localhost:8080/
```

### Continuous deployment (push to `main`)

A merge to `main` ships itself — no manual `fly deploy`. The CI workflow runs `lint`, `types`, and
`test`, and only when all three pass do two further jobs run (and only on `main`, never on a PR):

- **deploy** builds the image once and pushes it to GHCR tagged `:<git-sha>` and `:main`, and to
  Fly's registry, then `fly deploy --image`s that exact SHA-tagged image to the Machine — no rebuild.
  GHCR is private: the image bakes in the sample Stocklist, so its wholesale prices stay unpublished.
- **release** builds the wheel and attaches it to a GitHub Release tagged `v<version>-<short-sha>`, a
  versioned audit trail only — the rollback lever is the image, not the wheel.

One secret makes this work: a Fly deploy token in the repo's GitHub Actions secrets as
`FLY_API_TOKEN` (GHCR uses the built-in `GITHUB_TOKEN`). Mint and set it with:

```sh
fly tokens create deploy -a fishpage     # paste the output as the FLY_API_TOKEN GitHub secret
```

### Rollback

A bad deploy is reverted by redeploying a prior image — seconds, no rebuild — because every `main`
commit is pushed to the registry under its git SHA. Find the SHA to return to (any earlier `main`
commit, or `fly releases -a fishpage`), then redeploy it:

```sh
fly deploy --app fishpage --image registry.fly.io/fishpage:<prior-git-sha>
```

The same image is mirrored at `ghcr.io/r4stered/fishpage:<prior-git-sha>` as the durable audit
trail. Rolling back is a deploy like any other, so the running Machine swaps to the prior image
without touching the database — Litestream restores it on boot exactly as on a forward deploy.

### Edge access (Cloudflare Tunnel + Access)

The catalog shows the supplier's wholesale prices, so it must be reachable by you from any browser
but not by the public. The Machine has no public origin; instead `cloudflared` (baked into the
image) dials out to Cloudflare's edge and forwards requests to the local app, and **Cloudflare
Access** enforces a login + allowlist on the hostname before any request reaches the tunnel. There
is deliberately no `fly.dev` URL to bypass it. One-time setup per environment:

1. **Create a tunnel** in the Cloudflare Zero Trust dashboard (Networks → Tunnels → *Create a
   tunnel* → **Cloudflared**). Name it `fishpage` and copy the tunnel **token** it shows.
2. **Route a public hostname to the local app.** On the tunnel's *Public Hostname* tab add a
   hostname on a Cloudflare-managed domain (e.g. `fishpage.example.com`) with service
   `HTTP` → `[::1]:8080`. The IPv6 loopback is deliberate: the app binds `::` (so `fly proxy`
   over Fly's IPv6 private network works), and that socket does not accept a literal IPv4
   `127.0.0.1` connection — `localhost` would resolve to IPv4 and the tunnel would 502. The tunnel
   is remotely managed, so this routing lives in the dashboard, not in the image.
3. **Give the Machine the token as a Fly secret** — never commit it. Its presence is what starts
   the tunnel on boot:

   ```sh
   fly secrets set CLOUDFLARE_TUNNEL_TOKEN="<tunnel-token>" -a fishpage
   ```

4. **Protect the hostname with Cloudflare Access** (Zero Trust → Access → Applications →
   *Add an application* → **Self-hosted**). Set the application domain to the same hostname, then
   add a policy with action **Allow** whose include rule is an emails allowlist (just your
   address). Everyone else is denied at the edge.

Confirm the four acceptance criteria once deployed:

```sh
fly ips list                                   # no v4/v6 addresses — no public Fly origin to bypass
curl -sI https://fishpage.example.com/         # un-logged-in: 302 to the Cloudflare Access login
```

Then open `https://fishpage.example.com/` in a browser: an allowlisted login reaches the catalog;
an un-allowlisted account is denied. The tunnel is opt-in via `CLOUDFLARE_TUNNEL_TOKEN`, which only
the cloud deploy sets, so `just run`, `docker run`, and the test suite serve the app directly with
no tunnel.

### Durability (Litestream → R2)

The Fly Machine's disk is ephemeral — it starts blank on every boot. Durability comes from
[Litestream](https://litestream.io/): it is the image entrypoint, restores the database from a
Cloudflare R2 bucket before serving, and then streams the write-ahead log back to R2 for as long
as the app runs. One-time setup per environment:

1. **Create the R2 bucket** and an API token scoped to it (Cloudflare dashboard → R2, or
   `wrangler r2 bucket create fishpage-db`). The bucket name and prefix must match
   `LITESTREAM_REPLICA_URL` in `fly.toml` (`s3://fishpage-db/catalog`).
2. **Set the R2 endpoint and credentials as Fly secrets** — never commit them. The endpoint is
   `https://<account-id>.r2.cloudflarestorage.com`:

   ```sh
   fly secrets set \
     LITESTREAM_R2_ENDPOINT="https://<account-id>.r2.cloudflarestorage.com" \
     LITESTREAM_ACCESS_KEY_ID="<r2-token-id>" \
     LITESTREAM_SECRET_ACCESS_KEY="<r2-token-secret>" \
     -a fishpage
   ```

On the first deploy the bucket is empty, so the restore is a no-op and the app seeds from the
sample Stocklist as usual; from then on each boot restores the latest snapshot. Confirm the
round-trip survives a redeploy:

```sh
fly proxy 8080:8080 -a fishpage     # ingest a Stocklist through the running app
fly deploy                          # redeploy onto a fresh, blank disk
fly proxy 8080:8080 -a fishpage     # the ingested data is still there
```

Replication is opt-in via `LITESTREAM_REPLICA_URL`, which only the cloud deploy sets, so
`just run`, `docker run`, and the test suite operate on a plain local SQLite file with no R2.

### Observability (OpenTelemetry → Grafana Cloud)

The app is instrumented with OpenTelemetry — structured logs, traces, and metrics — exported over
OTLP/HTTP to the Grafana Cloud free tier. Export is opt-in via `OTEL_EXPORTER_OTLP_ENDPOINT` (set
as a Fly secret alongside the standard OTLP auth headers in `OTEL_EXPORTER_OTLP_HEADERS`); with no
endpoint, `just run`, `docker run`, and the test suite record telemetry in-process but export
nothing, so they stay credential-free.

FastAPI requests are auto-instrumented, and parse and ingest carry manual spans. Beyond request
latency, the app emits the domain signals that actually de-risk the catalog: rows parsed vs
skipped, reuse-guard flags, monotonicity skips, and `days_since_last_ingest`. The one alert that
matters — **no successful ingest in 2 days**, the catalog silently going stale — is provisioned as
code in [`grafana/alerting/stale-catalog.yaml`](grafana/alerting/stale-catalog.yaml), keyed on that
last gauge. A `/healthz` endpoint returns `{"status": "ok"}` and is wired to the Fly Machine health
check in `fly.toml`. See [ADR 0009](docs/adr/0009-opentelemetry-grafana-cloud-stale-catalog-alert.md).

## Checks

[`just`](https://just.systems/) recipes are the single source of truth for the CI gate —
the same commands run locally, in CI, and in pre-commit. `just` itself ships as a dev
dependency (`rust-just`), so `uv sync` is all the setup you need.

```sh
uv run just check       # the full gate: lint + typecheck + test (what CI runs)

uv run just lint        # ruff check + ruff format --check
uv run just format      # ruff check --fix + ruff format (writes)
uv run just typecheck   # ty check
uv run just test        # pytest
uv run just --list      # show all recipes
```

CI runs `lint`, `types`, and `test` as three required checks on every PR to `main`, so run
`uv run just check` before pushing. Optionally install the pre-commit hooks (ruff + ty; tests
stay in CI only) to catch issues before each commit:

```sh
uvx pre-commit install
```

## Project docs

- [`CONTEXT.md`](CONTEXT.md) — domain glossary. Use this vocabulary throughout.
- [`docs/adr/`](docs/adr/) — architectural decision records.
- [`docs/agents/`](docs/agents/) — issue-tracker, triage-label, and domain-doc conventions
  for AI agents working in this repo.

## Status

Active development against PRD [#1](https://github.com/r4stered/fishpage/issues/1). Landed
so far: the walking skeleton ([#2](https://github.com/r4stered/fishpage/issues/2)),
upsert-by-SKU reconciliation with `last_seen` and zeroed absentees
([#3](https://github.com/r4stered/fishpage/issues/3)), the reuse guard
([#4](https://github.com/r4stered/fishpage/issues/4)), the in-stock default + out-of-stock
toggle ([#5](https://github.com/r4stered/fishpage/issues/5)), the Derived Category filter
([#6](https://github.com/r4stered/fishpage/issues/6)), fuzzy name search
([#7](https://github.com/r4stered/fishpage/issues/7)), the Size/on-special filters and
effective-price sort ([#8](https://github.com/r4stered/fishpage/issues/8)), the
CI / dev-tooling gate ([#14](https://github.com/r4stered/fishpage/issues/14)),
watched-folder ingestion ([#9](https://github.com/r4stered/fishpage/issues/9)), parser
row resilience — skip-and-log malformed rows + SKU-shape validation
([#12](https://github.com/r4stered/fishpage/issues/12)), and header-derived column detection
for varied Stocklist layouts ([#13](https://github.com/r4stered/fishpage/issues/13)).

Remaining v1 slices: cloud deployment to Fly.io — a Litestream-backed image, an authenticated
upload page, a gated push-to-`main` pipeline, and OpenTelemetry wiring
([#10](https://github.com/r4stered/fishpage/issues/10), to be rewritten from its original
Unraid framing; see [ADR 0007](docs/adr/0007-deploy-to-flyio-cloud-not-unraid.md) and
[ADR 0008](docs/adr/0008-sqlite-litestream-object-storage.md)).

## Out of scope (deferred to phase 2)

Images and care **Classifiers** (difficulty of care, plant needs, …), the AI-assisted
**Enrichment** that would populate them, the approval admin surface, and the nightly
email-poller. The v1 schema does not pre-build support for these.
