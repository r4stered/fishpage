# Instrument with OpenTelemetry, export to Grafana Cloud, alert on a silently stale catalog

Observability is a first-class goal of the cloud rework, not an afterthought:
[ADR 0007](0007-deploy-to-flyio-cloud-not-unraid.md) frames the whole deploy as a learning vehicle
for "cloud, web, and **observability** work." So the app is instrumented with **OpenTelemetry** —
structured logs, traces, and metrics — exported over **OTLP/HTTP** to the **Grafana Cloud** free
tier. The guiding constraint is the same as everywhere else: learning-maximal but near-free, and
credential-free off the cloud path.

## The exporter is opt-in and a no-op without an endpoint

Telemetry export switches on only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. With no endpoint — a
bare `just run`, a test run, CI — the providers are installed with no exporters: the domain
instruments still record in-process (so the code path is exercised) but nothing leaves the box and
no credential is needed. This mirrors how every other cloud dependency defaults off (Litestream,
cloud ingestion). The instruments are always live, so the domain code records unconditionally
without guarding on whether export is on.

A single module, `fishpage/observability.py`, is the **only** place that imports OpenTelemetry.
Everything else records through narrow domain-language helpers (`record_rows_parsed`,
`record_reuse_flag`, `record_monotonicity_skip`, `track_catalog_freshness`) and a `span()` context
manager. That module owns its own tracer and meter providers rather than the process-global ones,
which keeps the wiring testable: the suite installs in-memory readers through the same path the real
wiring uses and asserts the telemetry the code actually recorded, instead of mocking it.

## Beyond request spans: the domain signals that matter here

FastAPI requests are auto-instrumented, and parse and ingest carry manual spans. But the signals
worth watching are domain-specific, drawn from the reconciliation model of
[ADR 0001](0001-sku-permanent-key-upsert-never-delete.md):

- **rows parsed vs skipped** — a Stocklist that suddenly sheds rows (a layout change, a truncated
  drop) shows up as a skip spike instead of silently shrinking the catalog.
- **reuse-guard flags** — how often a SKU reappears under a materially different name.
- **monotonicity skips** — how often a drop is held back for not being newer than the catalog, the
  symptom of a misdated supplier export.
- **days since last successful ingest** — an observable gauge read from the store's `MAX(last_seen)`
  at collection time.

## The one alert: a silently stale catalog

The failure that quietly defeats the whole tool is nightly ingestion stopping without anyone
noticing — the catalog keeps serving yesterday's stock indefinitely. So the one monitor that earns
its keep is **"no successful ingest in N days"** (N = 2; nightly cadence means one missed night is
not yet alarming, two is). It keys on the freshness gauge and is provisioned as code in
`grafana/alerting/stale-catalog.yaml`. The gauge reports **nothing** for a never-ingested catalog,
and a dead exporter also yields no data — both are real staleness, so the rule treats no-data as
alerting rather than healthy.

## `/healthz` for the Machine health check

A trivial `/healthz` returning `{"status": "ok"}` is wired to a Fly **machine-level** health check
(`[checks]` in `fly.toml`). The Machine publishes no public service
([ADR 0007](0007-deploy-to-flyio-cloud-not-unraid.md)), so there is no `[[services]]` block to hang
a check on; the top-level `[checks]` block probes the app directly on its internal port so Fly
restarts a wedged Machine.

## Consequences

- A new runtime dependency surface (the OpenTelemetry SDK, OTLP/HTTP exporters, FastAPI
  instrumentation). They are pure-Python wheels and stay dormant without an endpoint.
- OpenTelemetry's **logs** SDK for Python is still experimental (the import path is underscored).
  We accept it for log export rather than run a second logging pipeline; the API may shift.
- Metric names cross an OTLP→Prometheus boundary in Grafana Cloud, which rewrites dots to
  underscores and appends units. The alert rule documents the resulting name; a mapping change
  there means editing the rule, not the code.
- "Reaches Grafana Cloud" and "the alert fires when stale" are validated against the live stack, not
  the test suite — the suite covers the recording and gating behaviour, which is what it can hold.
