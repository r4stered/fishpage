# The Cloudflare edge: object storage for the catalog's Litestream replica, the Tunnel that is the
# Machine's only ingress, the DNS that points at it, and the Access app that gates it. Together these
# are why the Fly Machine needs no public IP.

# --- R2: durable object storage for the Litestream replica ---

resource "cloudflare_r2_bucket" "litestream" {
  account_id = var.cloudflare_account_id
  name       = var.r2_bucket_name
}

# The permission group granting read+write to R2 storage. Looked up rather than hard-coded because
# the group's UUID is opaque; the lookup fails loudly if the name ever changes upstream.
data "cloudflare_account_api_token_permission_groups_list" "r2" {
  account_id = var.cloudflare_account_id
  max_items  = 1000
}

locals {
  r2_write_permission_group = one([
    for g in data.cloudflare_account_api_token_permission_groups_list.r2.result :
    g.id if can(regex("R2 Storage Write", g.name))
  ])
}

# An account-owned token scoped to just R2 storage write — nothing else on the account, and not tied
# to a person. Litestream speaks R2's S3-compatible API, whose Access Key ID is the token's id and
# whose Secret Access Key is the SHA-256 of the token's value; both are derived in outputs.tf and
# pushed to Fly as secrets.
resource "cloudflare_account_token" "r2" {
  account_id = var.cloudflare_account_id
  name       = "${var.fly_app}-r2-litestream"
  policies = [{
    effect            = "allow"
    permission_groups = [{ id = local.r2_write_permission_group }]
    resources = jsonencode({
      "com.cloudflare.api.account.${var.cloudflare_account_id}" = "*"
    })
  }]
}

# --- R2: a second bucket for enrichment image bytes ---

# Image bytes live in their own bucket, separate from the Litestream replica, so they never enter the
# database the restore reasons about. The app reads and writes it over R2's S3-compatible API and
# proxies the bytes through itself — the bucket is never public — so images stay behind Access.
resource "cloudflare_r2_bucket" "images" {
  account_id = var.cloudflare_account_id
  name       = var.r2_images_bucket_name
}

# A second account-owned token scoped to R2 storage write, distinct from the Litestream token so the
# image and replica credentials can be rotated independently. Its id/secret derive the S3 keys in
# outputs.tf and are pushed to Fly as the R2_IMAGES_* secrets.
resource "cloudflare_account_token" "r2_images" {
  account_id = var.cloudflare_account_id
  name       = "${var.fly_app}-r2-images"
  policies = [{
    effect            = "allow"
    permission_groups = [{ id = local.r2_write_permission_group }]
    resources = jsonencode({
      "com.cloudflare.api.account.${var.cloudflare_account_id}" = "*"
    })
  }]
}

# --- Tunnel: the Machine's only ingress ---

# A remotely-configured tunnel (config_src = cloudflare) so its ingress is declared below rather than
# in a connector config file baked into the image.
resource "cloudflare_zero_trust_tunnel_cloudflared" "fishpage" {
  account_id = var.cloudflare_account_id
  name       = var.fly_app
  config_src = "cloudflare"
}

# Forward everything for the hostname to the app on the IPv6 loopback. The app binds `::`, and that
# socket does not accept a literal IPv4 127.0.0.1 connection, so the origin must be [::1], not
# localhost — localhost would resolve to IPv4 and the tunnel would 502. The trailing catch-all is
# required: any request not matching a rule above returns 404 instead of erroring the connector.
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "fishpage" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.fishpage.id

  config = {
    ingress = [
      {
        hostname = var.hostname
        service  = "http://[::1]:8080"
      },
      {
        service = "http_status:404"
      },
    ]
  }
}

# The connector token cloudflared dials out with, set as the CLOUDFLARE_TUNNEL_TOKEN Fly secret.
data "cloudflare_zero_trust_tunnel_cloudflared_token" "fishpage" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.fishpage.id
}

# --- DNS: point the hostname at the tunnel ---

# A proxied CNAME to the tunnel's routable address. Proxying is what lets Access intercept the
# request at the edge before it reaches the connector; an unproxied record would bypass the gate.
resource "cloudflare_dns_record" "fishpage" {
  zone_id = var.cloudflare_zone_id
  name    = var.hostname
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.fishpage.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

# --- Access: gate the hostname on a login + allowlist ---

resource "cloudflare_zero_trust_access_application" "fishpage" {
  account_id       = var.cloudflare_account_id
  name             = var.fly_app
  type             = "self_hosted"
  domain           = var.hostname
  session_duration = var.access_session_duration

  policies = [{
    id         = cloudflare_zero_trust_access_policy.allowlist.id
    precedence = 1
  }]
}

# Allow exactly the listed emails; everyone else is denied at the edge. The reusable policy is
# defined standalone and referenced by the application above.
resource "cloudflare_zero_trust_access_policy" "allowlist" {
  account_id = var.cloudflare_account_id
  name       = "${var.fly_app}-allowlist"
  decision   = "allow"

  include = [
    for email in [for e in split(",", var.access_allowed_emails) : trimspace(e)] :
    { email = { email = email } }
  ]
}
