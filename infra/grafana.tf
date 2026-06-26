# Grafana Cloud wiring: an OTLP push token the Machine exports telemetry with, and the one alert that
# de-risks the catalog silently going stale. Both land in the pre-existing free-tier stack.

data "grafana_cloud_stack" "this" {
  slug = var.grafana_cloud_stack_slug
}

# --- OTLP push token (cloud-scoped) ---

# A token scoped to write-only ingestion against just this stack. Paired with the stack's OTLP
# instance id it becomes the Basic-auth header the OTel exporter sends; both are assembled in
# outputs.tf and pushed to Fly as secrets.
resource "grafana_cloud_access_policy" "otlp" {
  name   = "${var.fly_app}-otlp-write"
  region = data.grafana_cloud_stack.this.region_slug
  scopes = ["metrics:write", "logs:write", "traces:write"]

  realm {
    type       = "stack"
    identifier = data.grafana_cloud_stack.this.id
  }
}

resource "grafana_cloud_access_policy_token" "otlp" {
  access_policy_id = grafana_cloud_access_policy.otlp.policy_id
  region           = grafana_cloud_access_policy.otlp.region
  name             = "${var.fly_app}-otlp-write"
}

# --- Stale-catalog alert (stack-scoped) ---

resource "grafana_folder" "fishpage" {
  provider = grafana.stack
  title    = "Fishpage"
}

# Pages when no Stocklist has been ingested in two days, or when the freshness gauge reports nothing
# at all — an empty/never-ingested catalog and a dead exporter both look like no-data, hence
# no_data_state Alerting.
#
# The gauge `fishpage.catalog.days_since_last_ingest` reaches Grafana Cloud's Prometheus store with
# its dots rewritten to underscores and no unit appended, so it is queried bare as
# `fishpage_catalog_days_since_last_ingest`. The threshold is `gte 2`: ingestion is nightly, so one
# missed night reads under 2 and does not page on a single late supplier drop; two missed nights
# reads exactly 2. `gte` (not `gt`) is what makes the two-nights-stale case fire.
resource "grafana_rule_group" "stale_catalog" {
  provider         = grafana.stack
  name             = "fishpage-catalog"
  folder_uid       = grafana_folder.fishpage.uid
  interval_seconds = 300

  rule {
    name           = "Catalog stale — no successful ingest in 2 days"
    condition      = "is_stale"
    for            = "0m"
    no_data_state  = "Alerting"
    exec_err_state = "Alerting"

    annotations = {
      summary = "No Stocklist has been ingested in over 2 days (or the app is not reporting freshness). The catalog is silently stale — check the watcher, the nightly drop, and the exporter."
    }
    labels = {
      severity = "page"
    }

    data {
      ref_id         = "days"
      datasource_uid = "grafanacloud-prom"
      relative_time_range {
        from = 600
        to   = 0
      }
      model = jsonencode({
        refId      = "days"
        instant    = true
        expr       = "max(fishpage_catalog_days_since_last_ingest)"
        datasource = { type = "prometheus", uid = "grafanacloud-prom" }
      })
    }

    data {
      ref_id         = "is_stale"
      datasource_uid = "__expr__"
      relative_time_range {
        from = 600
        to   = 0
      }
      model = jsonencode({
        refId      = "is_stale"
        type       = "threshold"
        expression = "days"
        datasource = { type = "__expr__", uid = "__expr__" }
        conditions = [{
          evaluator = { type = "gte", params = [2] }
        }]
      })
    }
  }
}

# --- Site overview dashboard (stack-scoped) ---

# One dashboard answering "is the system healthy right now" at a glance, sat in the same Fishpage
# folder as the alert and provisioned the same way. Four panels: who uploaded what lately (Loki),
# the optimization metrics and their derived savings (Prometheus), anything that logged at WARNING
# or worse (Loki), and the catalog-freshness gauge the alert already keys on.
#
# Two datasource boundaries are wired here, both stack built-ins shared with the alert above:
#   - grafanacloud-prom — the Prometheus store the OTLP metrics land in.
#   - grafanacloud-logs — the Loki store the OTLP log records land in, selected by the resource's
#     service.name (`{service_name="fishpage"}`).
#
# OTLP→Prometheus name rewriting (the same boundary the alert documents): Grafana Cloud rewrites
# dots to underscores and appends `_total` to every counter (monotonic sum), but does not append
# unit suffixes. The two halves are independent and the gauges confirm both: a gauge never takes
# `_total`, so `fishpage_catalog_days_since_last_ingest` arriving bare (also no `_days` unit tail)
# shows only that unit suffixing is off — it says nothing about counters. The counters land with a
# `_total` tail and so are queried with it: `fishpage_image_optimized_total`,
# `fishpage_image_original_bytes_total`, `fishpage_image_optimized_bytes_total`,
# `fishpage_image_optimize_errors_total`. The single `provenance` attribute carries through as a
# label; gauges are queried bare, counters with the `_total` suffix.
#
# The upload event and the decode-failure warning are the two log records `store_image` emits; both
# carry `actor`, `sku`, and `provenance` as record attributes. The INFO upload event is the one
# whose message reads "Stored <provenance> image for <sku>"; the WARNING reads "Failed to optimize…".
resource "grafana_dashboard" "overview" {
  provider = grafana.stack
  folder   = grafana_folder.fishpage.uid

  config_json = jsonencode({
    uid           = "fishpage-overview"
    title         = "Fishpage — site overview"
    tags          = ["fishpage"]
    editable      = true
    schemaVersion = 39
    timezone      = "browser"
    time          = { from = "now-24h", to = "now" }
    refresh       = "1m"
    annotations   = { list = [] }

    # Enrichment dollars are derived on the dashboard from a price-per-million-tokens variable, never
    # from a price table in the app — a reprice is editing these defaults, not shipping code. Two
    # textboxes (input/output) feed the estimated-cost stat below; defaults are a Sonnet-class rate
    # and are meant to be overridden to the enricher's actual model price.
    templating = { list = [
      {
        name    = "price_in"
        type    = "textbox"
        label   = "Input $/Mtok"
        query   = "3"
        current = { text = "3", value = "3" }
      },
      {
        name    = "price_out"
        type    = "textbox"
        label   = "Output $/Mtok"
        query   = "15"
        current = { text = "15", value = "15" }
      },
    ] }

    panels = [
      # 1 — Recent image uploads: the INFO upload event, newest first. The "when + by who" view —
      # time, uploader (the `actor` attribute), sku, and provenance read off the event's attributes.
      # `|= "Stored"` keeps this to the success event and excludes the "Failed to optimize" warning.
      {
        id         = 1
        type       = "logs"
        title      = "Recent image uploads"
        datasource = { type = "loki", uid = "grafanacloud-logs" }
        gridPos    = { h = 8, w = 24, x = 0, y = 0 }
        targets = [{
          refId      = "A"
          datasource = { type = "loki", uid = "grafanacloud-logs" }
          queryType  = "range"
          expr       = "{service_name=\"fishpage\"} |= `Stored` |= `image for`"
        }]
        options = {
          showTime         = true
          showLabels       = true
          wrapLogMessage   = true
          enableLogDetails = true
          sortOrder        = "Descending"
        }
      },

      # 2 — Metrics: uploads by provenance, the bytes-in/bytes-out flow with a derived "space saved"
      # series, and the optimize-error count — all over the dashboard window. Bytes are the panel's
      # default unit; the count series (uploads, errors) are overridden onto the right axis as plain
      # counts. "Space saved" is derived here (in − out), never stored, because a per-image saving
      # can be negative and a monotonic counter cannot carry that.
      {
        id         = 2
        type       = "timeseries"
        title      = "Image optimization"
        datasource = { type = "prometheus", uid = "grafanacloud-prom" }
        gridPos    = { h = 9, w = 16, x = 0, y = 8 }
        targets = [
          {
            refId        = "uploads"
            datasource   = { type = "prometheus", uid = "grafanacloud-prom" }
            expr         = "sum by (provenance) (increase(fishpage_image_optimized_total[$__rate_interval]))"
            legendFormat = "uploads {{provenance}}"
            range        = true
          },
          {
            refId        = "bytes_in"
            datasource   = { type = "prometheus", uid = "grafanacloud-prom" }
            expr         = "sum(increase(fishpage_image_original_bytes_total[$__rate_interval]))"
            legendFormat = "bytes in"
            range        = true
          },
          {
            refId        = "bytes_out"
            datasource   = { type = "prometheus", uid = "grafanacloud-prom" }
            expr         = "sum(increase(fishpage_image_optimized_bytes_total[$__rate_interval]))"
            legendFormat = "bytes out"
            range        = true
          },
          {
            refId        = "space_saved"
            datasource   = { type = "prometheus", uid = "grafanacloud-prom" }
            expr         = "sum(increase(fishpage_image_original_bytes_total[$__rate_interval])) - sum(increase(fishpage_image_optimized_bytes_total[$__rate_interval]))"
            legendFormat = "space saved"
            range        = true
          },
          {
            refId        = "errors"
            datasource   = { type = "prometheus", uid = "grafanacloud-prom" }
            expr         = "sum(increase(fishpage_image_optimize_errors_total[$__rate_interval]))"
            legendFormat = "optimize errors"
            range        = true
          },
        ]
        fieldConfig = {
          defaults = {
            unit   = "bytes"
            custom = { drawStyle = "line", fillOpacity = 10, showPoints = "auto" }
          }
          overrides = [{
            matcher = { id = "byRegexp", options = "/^(uploads|optimize errors).*/" }
            properties = [
              { id = "unit", value = "short" },
              { id = "custom.axisPlacement", value = "right" },
              { id = "custom.drawStyle", value = "bars" },
            ]
          }]
        }
        options = {
          legend  = { displayMode = "table", placement = "bottom", calcs = ["sum"] }
          tooltip = { mode = "multi", sort = "desc" }
        }
      },

      # 4 — Catalog freshness: the same gauge and threshold the stale-catalog alert keys on, shown as
      # a number that goes green under two days and red at two-or-more (one missed nightly ingest is
      # not yet alarming, two is). No value at all means a never-ingested catalog or a dead exporter.
      {
        id         = 3
        type       = "stat"
        title      = "Catalog freshness (days since last ingest)"
        datasource = { type = "prometheus", uid = "grafanacloud-prom" }
        gridPos    = { h = 9, w = 8, x = 16, y = 8 }
        targets = [{
          refId      = "A"
          datasource = { type = "prometheus", uid = "grafanacloud-prom" }
          expr       = "max(fishpage_catalog_days_since_last_ingest)"
          instant    = true
        }]
        fieldConfig = {
          defaults = {
            unit     = "d"
            decimals = 1
            thresholds = {
              mode = "absolute"
              steps = [
                { color = "green", value = null },
                { color = "red", value = 2 },
              ]
            }
          }
          overrides = []
        }
        options = {
          colorMode     = "value"
          graphMode     = "area"
          textMode      = "value"
          reduceOptions = { calcs = ["lastNotNull"] }
        }
      },

      # 3 — Warnings & errors: everything the app logged at WARNING or worse — decode failures, the
      # reuse-guard flag line, drainer exceptions. Filtered on the level Grafana Cloud derives for the
      # OTLP records (`detected_level`), newest first.
      {
        id         = 4
        type       = "logs"
        title      = "Warnings & errors"
        datasource = { type = "loki", uid = "grafanacloud-logs" }
        gridPos    = { h = 9, w = 24, x = 0, y = 17 }
        targets = [{
          refId      = "A"
          datasource = { type = "loki", uid = "grafanacloud-logs" }
          queryType  = "range"
          expr       = "{service_name=\"fishpage\"} | detected_level =~ `warn|error|critical|fatal`"
        }]
        options = {
          showTime         = true
          showLabels       = true
          wrapLogMessage   = true
          enableLogDetails = true
          sortOrder        = "Descending"
        }
      },

      # --- Enrichment section: the cost, throughput, drainer-health, and quality signals the
      # enrichment slices instrument. All Prometheus, all bare names across the OTLP boundary.
      {
        id      = 10
        type    = "row"
        title   = "Enrichment"
        gridPos = { h = 1, w = 24, x = 0, y = 26 }
      },

      # Drainer health: Items still awaiting enrichment, over time. A line that climbs and never
      # falls is the drainer wedged or the upstream call failing; a true 0 is the drainer caught up.
      # No value at all is a never-populated catalog (the gauge reports nothing rather than a
      # misleading zero), so connect-nulls is left off.
      {
        id         = 11
        type       = "timeseries"
        title      = "Un-enriched queue depth"
        datasource = { type = "prometheus", uid = "grafanacloud-prom" }
        gridPos    = { h = 8, w = 8, x = 0, y = 27 }
        targets = [{
          refId        = "A"
          datasource   = { type = "prometheus", uid = "grafanacloud-prom" }
          expr         = "max(fishpage_enrichment_queue_depth)"
          legendFormat = "queue depth"
          range        = true
        }]
        fieldConfig = {
          defaults = {
            unit   = "short"
            custom = { drawStyle = "line", fillOpacity = 10, showPoints = "auto" }
          }
          overrides = []
        }
        options = {
          legend  = { displayMode = "list", placement = "bottom" }
          tooltip = { mode = "single" }
        }
      },

      # Throughput: drainer calls split ok vs failed, by outcome. A failing upstream shows as the
      # failed series climbing while ok flatlines; the failure rate reads straight off the two.
      {
        id         = 12
        type       = "timeseries"
        title      = "Enrichment calls by outcome"
        datasource = { type = "prometheus", uid = "grafanacloud-prom" }
        gridPos    = { h = 8, w = 8, x = 8, y = 27 }
        targets = [{
          refId        = "A"
          datasource   = { type = "prometheus", uid = "grafanacloud-prom" }
          expr         = "sum by (outcome) (increase(fishpage_enrichment_calls_total[$__rate_interval]))"
          legendFormat = "{{outcome}}"
          range        = true
        }]
        fieldConfig = {
          defaults = {
            unit   = "short"
            custom = { drawStyle = "bars", fillOpacity = 60, stacking = { mode = "normal" } }
          }
          overrides = [
            { matcher = { id = "byName", options = "ok" }, properties = [{ id = "color", value = { mode = "fixed", fixedColor = "green" } }] },
            { matcher = { id = "byName", options = "failed" }, properties = [{ id = "color", value = { mode = "fixed", fixedColor = "red" } }] },
          ]
        }
        options = {
          legend  = { displayMode = "table", placement = "bottom", calcs = ["sum"] }
          tooltip = { mode = "multi", sort = "desc" }
        }
      },

      # Cost driver: tokens spent split by direction. The dollar figure is derived from these two
      # counters and the price variables in the estimated-cost stat, never stored as money here.
      {
        id         = 13
        type       = "timeseries"
        title      = "Enrichment tokens by direction"
        datasource = { type = "prometheus", uid = "grafanacloud-prom" }
        gridPos    = { h = 8, w = 8, x = 16, y = 27 }
        targets = [{
          refId        = "A"
          datasource   = { type = "prometheus", uid = "grafanacloud-prom" }
          expr         = "sum by (direction) (increase(fishpage_enrichment_tokens_total[$__rate_interval]))"
          legendFormat = "{{direction}}"
          range        = true
        }]
        fieldConfig = {
          defaults = {
            unit   = "short"
            custom = { drawStyle = "line", fillOpacity = 10, showPoints = "auto" }
          }
          overrides = []
        }
        options = {
          legend  = { displayMode = "table", placement = "bottom", calcs = ["sum"] }
          tooltip = { mode = "multi", sort = "desc" }
        }
      },

      # Derived spend over the dashboard window: (input tokens × $price_in + output tokens ×
      # $price_out) / 1e6. The price variables are the only place a model rate lives.
      {
        id         = 14
        type       = "stat"
        title      = "Estimated enrichment cost (window)"
        datasource = { type = "prometheus", uid = "grafanacloud-prom" }
        gridPos    = { h = 8, w = 8, x = 0, y = 35 }
        targets = [{
          refId      = "A"
          datasource = { type = "prometheus", uid = "grafanacloud-prom" }
          expr       = "sum(increase(fishpage_enrichment_tokens_total{direction=\"input\"}[$__range])) * $price_in / 1e6 + sum(increase(fishpage_enrichment_tokens_total{direction=\"output\"}[$__range])) * $price_out / 1e6"
          instant    = true
        }]
        fieldConfig = {
          defaults = {
            unit       = "currencyUSD"
            decimals   = 2
            thresholds = { mode = "absolute", steps = [{ color = "green", value = null }] }
          }
          overrides = []
        }
        options = {
          colorMode     = "none"
          graphMode     = "none"
          textMode      = "value"
          reduceOptions = { calcs = ["lastNotNull"] }
        }
      },

      # Quality / honesty signals: a rising rate of species that won't resolve, Classifiers coming
      # back unknown, or humans overriding the AI is the early evidence enrichment is degrading or
      # not trusted. The latter two are split by which Classifier so a single attribute degrading
      # shows up rather than hiding in an aggregate.
      {
        id         = 15
        type       = "timeseries"
        title      = "Enrichment quality signals"
        datasource = { type = "prometheus", uid = "grafanacloud-prom" }
        gridPos    = { h = 8, w = 16, x = 8, y = 35 }
        targets = [
          {
            refId        = "species_unresolved"
            datasource   = { type = "prometheus", uid = "grafanacloud-prom" }
            expr         = "sum(increase(fishpage_enrichment_species_unresolved_total[$__rate_interval]))"
            legendFormat = "species unresolved"
            range        = true
          },
          {
            refId        = "classifier_unknown"
            datasource   = { type = "prometheus", uid = "grafanacloud-prom" }
            expr         = "sum by (classifier) (increase(fishpage_enrichment_classifier_unknown_total[$__rate_interval]))"
            legendFormat = "unknown {{classifier}}"
            range        = true
          },
          {
            refId        = "overrides"
            datasource   = { type = "prometheus", uid = "grafanacloud-prom" }
            expr         = "sum by (classifier) (increase(fishpage_enrichment_overrides_total[$__rate_interval]))"
            legendFormat = "override {{classifier}}"
            range        = true
          },
        ]
        fieldConfig = {
          defaults = {
            unit   = "short"
            custom = { drawStyle = "line", fillOpacity = 10, showPoints = "auto" }
          }
          overrides = []
        }
        options = {
          legend  = { displayMode = "table", placement = "bottom", calcs = ["sum"] }
          tooltip = { mode = "multi", sort = "desc" }
        }
      },
    ]
  })
}
