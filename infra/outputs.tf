# The derived secrets the bring-up wrapper pipes into `flyctl secrets set` — the machine-to-machine
# half of "no secret is hand-copied". `fly_secrets` is a single map so the wrapper can set them in one
# `flyctl secrets set` call; the rest are conveniences for verification. Everything here is sensitive
# because it is built from minted tokens.

# R2's S3-compatible API: the Access Key ID is the token's id; the Secret Access Key is the SHA-256 of
# the token's value. Both are deterministic from the token, so they never appear in a dashboard.
locals {
  r2_endpoint          = "https://${var.cloudflare_account_id}.r2.cloudflarestorage.com"
  r2_access_key_id     = cloudflare_account_token.r2.id
  r2_secret_access_key = sha256(cloudflare_account_token.r2.value)

  # The image bucket shares the account R2 endpoint but uses its own token's derived S3 keys.
  r2_images_access_key_id     = cloudflare_account_token.r2_images.id
  r2_images_secret_access_key = sha256(cloudflare_account_token.r2_images.value)

  # Grafana Cloud's OTLP gateway takes Basic auth of "<instance id>:<token>". The stack attribute is
  # the gateway base without the "/otlp" path the OTLP/HTTP exporter posts under, so append it (and
  # guard against a double suffix if the upstream value ever starts including it).
  otlp_endpoint = "${trimsuffix(data.grafana_cloud_stack.this.otlp_url, "/otlp")}/otlp"
  otlp_headers  = "Authorization=Basic ${base64encode("${data.grafana_cloud_stack.this.id}:${grafana_cloud_access_policy_token.otlp.token}")}"
}

output "fly_secrets" {
  description = "Runtime secrets to push to the Fly Machine (consumed by `just bootstrap`)."
  sensitive   = true
  value = {
    LITESTREAM_R2_ENDPOINT       = local.r2_endpoint
    LITESTREAM_ACCESS_KEY_ID     = local.r2_access_key_id
    LITESTREAM_SECRET_ACCESS_KEY = local.r2_secret_access_key
    R2_IMAGES_ENDPOINT           = local.r2_endpoint
    R2_IMAGES_ACCESS_KEY_ID      = local.r2_images_access_key_id
    R2_IMAGES_SECRET_ACCESS_KEY  = local.r2_images_secret_access_key
    CLOUDFLARE_TUNNEL_TOKEN      = data.cloudflare_zero_trust_tunnel_cloudflared_token.fishpage.token
    OTEL_EXPORTER_OTLP_ENDPOINT  = local.otlp_endpoint
    OTEL_EXPORTER_OTLP_HEADERS   = local.otlp_headers
  }
}

output "hostname" {
  description = "The gated public hostname, for the verification step."
  value       = var.hostname
}

output "tunnel_id" {
  description = "ID of the Cloudflare Tunnel the Machine dials out to."
  value       = cloudflare_zero_trust_tunnel_cloudflared.fishpage.id
}
