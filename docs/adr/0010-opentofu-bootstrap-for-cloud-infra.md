# Stand up the cloud infrastructure with OpenTofu, not a setup script

Bringing the deploy of [ADR 0007](0007-deploy-to-flyio-cloud-not-unraid.md) up from nothing has been a
manual chore spread across four providers — Fly.io, Cloudflare, Grafana Cloud, and GitHub — most of it
copy-pasting generated secrets between dashboards. We replace that with a single re-runnable bring-up:
**OpenTofu** owns the declarative cloud resources, a thin `flyctl` wrapper owns the imperative Fly bits,
and the secrets are wired machine-to-machine so a human never copies one between dashboards.

The guiding constraint is the one from ADR 0007 — learning-maximal but near-free, favouring FOSS — which
is why this is real infrastructure-as-code rather than a bash script: the bring-up is itself a learning
vehicle for IaC, remote state, and object storage, and a declarative source-of-truth re-applies cleanly
where a script would need hand-written idempotency guards.

## OpenTofu, not Terraform

OpenTofu (MPL-2.0) over Terraform (Business Source License) for the same reason ADR 0007 favours FOSS
everywhere else. Same HCL, same Cloudflare/Grafana/GitHub providers. OpenTofu also brings **native state
encryption**, which Terraform lacks and which this design relies on (see below).

## Ownership split: OpenTofu for the declarative, flyctl for Fly

- **OpenTofu owns** the providers with first-class support: Cloudflare (the R2 bucket `fishpage-litestream`
  and its scoped token, the Tunnel and its ingress to `[::1]:8080`, the DNS record, and the Access
  application + allowlist policy), the GitHub Actions secrets, and Grafana (the OTLP push token and the
  stale-catalog alert of [ADR 0009](0009-opentelemetry-grafana-cloud-stale-catalog-alert.md), provisioned
  into the pre-existing free-tier stack).
- **`flyctl` owns** `apps create`, `secrets set`, and the initial `deploy`. Fly has no official provider;
  the community ones lag the platform and are the most likely thing to break a re-apply — the opposite of
  the goal. Fly is already driven by `flyctl` through the `just` ops recipes and the CD pipeline, so this
  reuses a known-good path rather than betting bring-up on an immature provider.

The accepted cost is two tools in play and Fly living outside the declarative state.

## Secrets are wired, never copy-pasted

Most of the manual toil was secret-shuffling, and most of those secrets are minted by the very resources
being created. They flow directly to where they are consumed: GitHub Actions secrets are set by the GitHub
provider inside `tofu apply`; Fly runtime secrets are set by the wrapper piping `tofu output` into
`flyctl secrets set`. The R2 token, the tunnel token, the Grafana OTLP credentials, and the Fly deploy
token all reach their destinations without a human seeing them. The cost is that these derived secrets
transit OpenTofu state — which forces the state decision below.

## Remote state in R2, encrypted

State lives in a dedicated Cloudflare R2 bucket with OpenTofu state encryption enabled. Remote state is
durable (losing a laptop does not orphan live cloud resources), exercises object storage and remote-state
locking directly — a stated learning goal, the same one [ADR 0008](0008-sqlite-litestream-object-storage.md)
cites for keeping the catalog in R2 — and encryption protects the secrets that now sit in state. Local
state was rejected as too fragile for resources that cost real money to orphan; a managed backend
(Terraform Cloud, Scalr) was rejected as another non-FOSS external account against the project's
minimalism. The one chicken-and-egg cost: the state bucket must be created by hand before the first apply.

## The irreducible human residue

Automation cannot mint the credentials it authenticates with, so bring-up still requires a human, once, to
supply: a Cloudflare API token + account ID, a Cloudflare-managed domain and the chosen hostname, a Fly
deploy token (or ambient `fly auth`), a Grafana access-policy token, the Access allowlist, and the
state-encryption passphrase. Non-secret inputs live in a gitignored `terraform.tfvars` (with a committed
`.example` template); secrets come from ambient CLI auth and the environment. After that it is one command.

## Consequences

- Bring-up is re-runnable and verifiable in a single pass: the wrapper creates the app, sets secrets, does
  the first deploy so a Machine is live, then OpenTofu wires the Cloudflare edge and the run can assert the
  acceptance criteria (the hostname returns the Access login redirect; `fly ips list` is empty).
- Continuous deployment is unchanged — every subsequent ship is still a push to `main`. The bootstrap only
  closes the gap CD assumes away: that the app, its secrets, and one running image already exist.
- State now holds live secrets. It is encrypted at rest and single-operator, but the state bucket and its
  encryption passphrase are themselves sensitive and must be guarded accordingly.
- This is infrastructure, not domain language: `CONTEXT.md` is deliberately untouched.
