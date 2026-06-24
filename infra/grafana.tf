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
