# Deploy to Fly.io cloud with a login gate, not a self-hosted Unraid container

The original plan was a single Docker container hand-installed on an Unraid NAS, served on the
home network with no authentication because the network itself was the access gate. We deliberately
reverse that and deploy to **Fly.io**, auto-deployed from `main`, behind an edge login.

Two reasons drive the change. First, this app is also a deliberate **learning vehicle** for cloud,
web, and observability work — the ordering tool is the excuse, real cloud infrastructure is the
point. Second, the goal is now "**push to `main` updates the live site**," which wants a cloud target
with a continuous-deployment pipeline, not a container someone copies onto a NAS by hand. The guiding
constraint is learning-maximal but near-free, favouring FOSS and real free tiers.

## Platform: an always-on Fly Machine

Fly runs the container as a long-lived micro-VM. We rejected the otherwise-tempting scale-to-zero
serverless class (Cloud Run, Lambda): the app needs a **continuously running process** — Litestream
must stream the database WAL without interruption (see
[ADR 0008](0008-sqlite-litestream-object-storage.md)) — and a function that freezes between requests
cannot do that without paying for a pinned warm instance, which loses both the free and the
serverless point.

## Access control moves to the edge

The Unraid design's "no authentication" was only safe because the home network gated access. The
cloud has no such gate, and the catalog displays the supplier's **wholesale** pricing — a public
`*.fly.dev` URL would republish it to anyone who finds it. So access control moves into the
deployment: a **Cloudflare Tunnel** carries traffic to a Fly Machine that has **no public origin**,
and **Cloudflare Access** enforces a login + allowlist at the edge. Putting the app behind Cloudflare
on a custom domain alone would not suffice — the bare `fly.dev` hostname would bypass the login — so
the origin is private by construction, reachable only through the tunnel. This supersedes Issue #1's
"No authentication (internal network tool)."

## Artifact and rollback

The deploy unit is **one multi-stage Docker image** (the app wheel built in a builder stage,
Litestream copied in as the entrypoint), pushed to **GHCR** and deployed by a GitHub Actions pipeline
that runs only after the existing `lint`/`types`/`test` gate is green. Images are tagged by git SHA,
so **rollback is redeploying a prior image** — seconds, no rebuild. Publishing the package to PyPI was
rejected: nobody `pip install`s a private single-user app. A wheel is attached to GitHub Releases as a
versioned audit trail only, not as the rollback lever (the image is).

## Consequences

- Cost is no longer strictly zero — a tiny always-on Machine runs a few dollars a month — but stays
  near-free, within the stated budget.
- The app now has a public attack surface where it had none. The Cloudflare Tunnel + Access front is
  the mitigation; there is deliberately no public Fly origin to fall back to.
- Deployment now depends on external services (Fly, Cloudflare, GHCR, and the R2 bucket of
  [ADR 0008](0008-sqlite-litestream-object-storage.md)). Their credentials are secrets, held in Fly
  secrets (runtime) and GitHub Actions secrets (CI), never committed.
- A single production environment is the whole topology; the image-SHA rollback is the safety net.
  Per-PR preview apps were considered and deferred as their own exercise.
- This rework supersedes the Unraid framing of Issue #10 ("Containerize + persistent volume for
  Unraid"); that issue needs rewriting against this target.
