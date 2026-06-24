# infra — one-command cloud bring-up

OpenTofu owns the declarative cloud resources; a thin `flyctl` wrapper (`just bootstrap`) owns the
imperative Fly bits. Fill in the gitignored `terraform.tfvars` + `backend.hcl`, export a handful of
credentials, and run one command to get a verified, Access-gated, running site — with no secret
copy-pasted between dashboards.

## What OpenTofu manages

- **Cloudflare** — the R2 bucket `fishpage-litestream` and an R2-write-scoped API token; the Tunnel
  and its ingress to `http://[::1]:8080`; a proxied DNS record; an Access application with an
  email-allowlist policy.
- **GitHub** — the `FLY_API_TOKEN` Actions secret CD deploys with.
- **Grafana Cloud** — an OTLP write token and the stale-catalog alert, into the existing stack.

Fly itself (`apps create`, `secrets set`, the first `deploy`) is driven by `flyctl`, not a provider.

## Secrets are wired, never copy-pasted

Most of these resources mint secrets that other resources consume. They flow machine-to-machine: the
GitHub provider sets the Actions secret inside `tofu apply`, and `just bootstrap` pipes
`tofu output -json fly_secrets` straight into `flyctl secrets import`. The R2 S3 keys, the tunnel
token, and the OTLP credentials reach the Fly Machine without a human seeing them. Because these
derived secrets transit state, state is encrypted (below).

## Remote state

State lives in a dedicated R2 bucket reached over its S3-compatible API, with OpenTofu **native state
encryption** (a passphrase-derived key). The state bucket is the one hand-created bootstrap step:

```sh
wrangler r2 bucket create fishpage-tfstate     # once, by hand
```

`bucket` and the R2 `endpoints.s3` URL go in `backend.hcl` (copy from `backend.hcl.example`); the
state bucket's S3 credentials come from `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in the
environment.

## One-time inputs

1. **Non-secret values** → `terraform.tfvars` (copy from `terraform.tfvars.example`): account/zone
   IDs, the hostname, the Access allowlist, the Grafana stack slug + URL.
2. **Backend** → `backend.hcl` (copy from `backend.hcl.example`).
3. **Secrets via the environment** — never written to a file:

   ```sh
   export CLOUDFLARE_API_TOKEN=...               # R2 + Tunnel + DNS + Access scopes
   export GITHUB_TOKEN=...                        # repo scope, to set the Actions secret
   export GRAFANA_CLOUD_ACCESS_POLICY_TOKEN=...   # mints the OTLP token
   export GRAFANA_AUTH=...                        # stack token, to provision the alert
   export AWS_ACCESS_KEY_ID=...                   # R2 state-bucket S3 keys
   export AWS_SECRET_ACCESS_KEY=...
   export TF_VAR_fly_deploy_token="$(fly tokens create deploy -a fishpage)"
   export TF_VAR_state_encryption_passphrase=...  # >= 16 chars; guard it — it unlocks state
   ```

   Plus an authenticated `flyctl` (`FLY_API_TOKEN` or `fly auth login`).

## Run it

```sh
just bootstrap          # create app → tofu apply → wire Fly secrets → first deploy → verify
just bootstrap-plan     # dry run: prove a re-apply is a clean no-op
just bootstrap-verify   # re-run just the acceptance checks
```

`just bootstrap` is idempotent: a second run is a no-op on unchanged config.

## Bring-up order

`flyctl apps create` → `tofu apply` (mints the R2/tunnel/OTLP secrets and wires the Cloudflare edge)
→ `flyctl secrets import --stage` (the derived secrets) → first `flyctl deploy` (the Machine boots
with its secrets, Litestream restores, cloudflared connects) → verify. OpenTofu runs before the first
deploy because the Machine cannot boot healthy without the secrets that `tofu apply` mints.

## Acceptance criteria

`just bootstrap-verify` checks the first two; confirm the rest by hand:

- `fly ips list` shows no v4/v6 addresses — no public origin to bypass.
- `curl -sI https://<hostname>/` returns a 302 to the Cloudflare Access login, not the app.
- No secret is committed and none is hand-copied between dashboards.
- Re-running `just bootstrap` is idempotent (`just bootstrap-plan` shows no changes).
