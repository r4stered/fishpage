# Pins the toolchain and the three declarative providers, points remote state at the hand-created
# R2 state bucket, and turns on native state encryption. Fly is deliberately absent: it is driven
# by flyctl from the justfile wrapper, not a provider.

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
    grafana = {
      source  = "grafana/grafana"
      version = "~> 3.0"
    }
  }

  # Remote state in the dedicated R2 bucket, reached over R2's S3-compatible API. The bucket name
  # and endpoint are environment-specific and so live in a -backend-config file passed at init time
  # (see infra/README.md), not here — a backend block cannot read input variables. State-bucket
  # credentials come from the AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment the wrapper sets
  # to the R2 token; the skip_* flags switch off the AWS-only preflight that R2 does not implement.
  backend "s3" {
    key                         = "fishpage/terraform.tfstate"
    region                      = "auto"
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_s3_checksum            = true
    use_path_style              = true
  }

  # Derived secrets (R2 keys, tunnel token, OTLP creds) transit state, so state is encrypted at
  # rest with a passphrase-derived key. The passphrase is read from the TF_VAR_state_encryption_
  # passphrase environment the wrapper exports; it is never written to disk.
  encryption {
    key_provider "pbkdf2" "state" {
      passphrase = var.state_encryption_passphrase
    }
    method "aes_gcm" "state" {
      keys = key_provider.pbkdf2.state
    }
    state {
      method = method.aes_gcm.state
    }
    plan {
      method = method.aes_gcm.state
    }
  }
}
