# The human residue of the bring-up: the handful of inputs automation cannot mint for itself.
# Non-secret values are filled into a gitignored terraform.tfvars (template: terraform.tfvars.example);
# secrets arrive through the environment (provider auth tokens, the state passphrase) and are never
# written to a file.

# --- Cloudflare ---

variable "cloudflare_account_id" {
  type        = string
  description = "Cloudflare account ID that owns the R2 bucket, tunnel, and Access app."
}

variable "cloudflare_zone_id" {
  type        = string
  description = "Zone ID of the Cloudflare-managed domain the hostname lives under."
}

variable "hostname" {
  type        = string
  description = "Public hostname the catalog is served at, e.g. fishpage.example.com. Must be inside the zone above."
}

# --- Fly (consumed by the flyctl wrapper, not a provider; surfaced here so one tfvars drives everything) ---

variable "fly_app" {
  type        = string
  description = "Fly app name. Must match `app` in fly.toml and LITESTREAM_REPLICA_URL's bucket prefix."
  default     = "fishpage"
}

variable "fly_primary_region" {
  type        = string
  description = "Fly region for `flyctl apps create`. Must match primary_region in fly.toml."
  default     = "sjc"
}

# --- Storage ---

variable "r2_bucket_name" {
  type        = string
  description = "R2 bucket Litestream replicates the catalog into. Must match the bucket in LITESTREAM_REPLICA_URL (fly.toml)."
  default     = "fishpage-litestream"
}

# --- Access allowlist ---

variable "access_allowed_emails" {
  type        = string
  description = "Comma-separated emails Cloudflare Access lets through. Everyone else is denied at the edge."
}

variable "access_session_duration" {
  type        = string
  description = "How long an Access login stays valid before re-auth."
  default     = "24h"
}

# --- GitHub ---

variable "github_owner" {
  type        = string
  description = "GitHub owner of the repo whose Actions secrets are managed."
  default     = "r4stered"
}

variable "github_repository" {
  type        = string
  description = "GitHub repo (name only) whose Actions secrets are managed."
  default     = "fishpage"
}

variable "fly_deploy_token" {
  type        = string
  sensitive   = true
  description = "Fly deploy token set as the FLY_API_TOKEN GitHub Actions secret so CD can deploy. Mint with `fly tokens create deploy`."
}

# --- Grafana Cloud ---

variable "grafana_cloud_stack_slug" {
  type        = string
  description = "Slug of the existing Grafana Cloud stack (the subdomain of <slug>.grafana.net)."
}

variable "grafana_url" {
  type        = string
  description = "Base URL of the Grafana stack instance, e.g. https://<slug>.grafana.net, used to provision the alert rule."
}

# --- State encryption ---

variable "state_encryption_passphrase" {
  type        = string
  sensitive   = true
  description = "Passphrase that derives the key encrypting remote state. Supplied via TF_VAR_state_encryption_passphrase; never written to a file. Min 16 chars (pbkdf2 requirement)."
}
