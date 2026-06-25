# Attribute manual uploads to the trusted Cloudflare Access email

Recording *who* attached a `manual` image means the app needs an identity, and it has never had one
— until now there was no notion of an actor anywhere in the request path. The identity comes from
the Cloudflare Access edge that already fronts every route: Access authenticates the human and
injects their email as the `Cf-Access-Authenticated-User-Email` request header. We read that header
in the upload route, record it as the image's Uploader, and **trust it without verifying the signed
`Cf-Access-Jwt-Assertion` JWT**.

## Trust the header, don't verify the JWT

The header is trivially spoofable on a public origin — anyone who can reach the app directly can set
it to whatever they like. The reason we accept it here is the network model of
[ADR 0007](0007-deploy-to-flyio-cloud-not-unraid.md): the Fly Machine publishes **no public
service**. The only path to the app is through the Cloudflare Tunnel, and therefore through Access,
which strips any client-supplied copy of the header and sets its own. There is no route to the
origin that bypasses the edge, so there is no path on which a forged header survives.

Verifying the JWT against Access's public keys would close the gap on a *public* origin, but it buys
nothing against a threat the topology already eliminates, while adding a JWKS fetch, key caching, and
a crypto dependency. We take the header at face value and spend the complexity elsewhere.

## What gets recorded, and the fallback

The Uploader — and the moment it landed — is stored on the image row alongside its Provenance,
durable in the same Litestream-replicated catalog, and emitted with the SKU and Provenance as
attributes on the upload's structured log event. So the catalog answers *who* attached an image, and
*when*, straight from the DB forever, while the log answers the same within retention. Both are
meaningful only for `manual` images; the auto-source path has no human Uploader and leaves them
unset, the way it already leaves `license`/`attribution` unset on a manual upload.

Off the Access edge — local `just run`, the test suite — the header is absent. The route records a
neutral placeholder rather than failing: a missing identity must never turn a working upload into an
error, and a local run has no real human to credit anyway.

## Consequences

- The first actor identity enters the app. It is read-only and request-scoped — there is no session,
  no user table, no login the app itself owns. Access remains the sole authority on who a request is.
- The audit trail is only as trustworthy as the assumption that the origin stays private. If a public
  `[[services]]` block is ever added to `fly.toml`, this decision is void and the values written
  before that point become retroactively suspect — there is no way to re-verify an identity recorded
  under header-trust. Keeping the origin private is load-bearing for the integrity of the Uploader
  column, not merely a deployment detail.
- Moving to JWT verification later is additive — the recording seam is the same — but it does not
  recover the provenance of rows already written.
