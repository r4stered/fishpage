# Every provider authenticates from the ambient environment, so no secret is written to a file:
#   CLOUDFLARE_API_TOKEN              — Cloudflare API token (R2 + Tunnel + DNS + Access scopes)
#   GITHUB_TOKEN                      — GitHub PAT with repo scope, to set Actions secrets
#   GRAFANA_CLOUD_ACCESS_POLICY_TOKEN — Grafana Cloud access-policy token (cloud-level API)
#   GRAFANA_AUTH                      — Grafana stack service-account token, to provision the alert

provider "cloudflare" {}

provider "github" {
  owner = var.github_owner
}

# Cloud-scoped: mints the OTLP push token against the Grafana Cloud API.
provider "grafana" {}

# Stack-scoped: provisions the folder and alert rule into the running stack instance.
provider "grafana" {
  alias = "stack"
  url   = var.grafana_url
}
