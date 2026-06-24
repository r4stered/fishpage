# The Cloudflare edge: object storage for the catalog's Litestream replica, the Tunnel that is the
# Machine's only ingress, the DNS that points at it, and the Access app that gates it. Together these
# are why the Fly Machine needs no public IP.

# --- R2: durable object storage for the Litestream replica ---

resource "cloudflare_r2_bucket" "litestream" {
  account_id = var.cloudflare_account_id
  name       = var.r2_bucket_name
}

# The permission group granting read+write to R2 storage. Looked up rather than hard-coded because
# the group's UUID is account-opaque; the lookup fails loudly if the name ever changes upstream.
data "cloudflare_account_api_token_permission_groups" "r2" {
  account_id = var.cloudflare_account_id
  scope      = "com.cloudflare.api.account"
}

locals {
  r2_write_permission_group = one([
    for g in data.cloudflare_account_api_token_permission_groups.r2.permission_groups :
    g.id if g.name == "Workers R2 Storage Write"
  ])
}

# A token scoped to just R2 storage write — nothing else on the account. Litestream speaks R2's
# S3-compatible API, whose Access Key ID is the token's id and whose Secret Access Key is the
# SHA-256 of the token's value; both are derived in outputs.tf and pushed to Fly as secrets.
resource "cloudflare_api_token" "r2" {
  name = "${var.fly_app}-r2-litestream"
  policies = [{
    effect            = "allow"
    permission_groups = [{ id = local.r2_write_permission_group }]
    resources = {
      "com.cloudflare.api.account.${var.cloudflare_account_id}" = "*"
    }
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
