"""The trusted Cloudflare Access identity — the app's only notion of who a request is.

Access fronts every route and injects the authenticated user's email as a request header. The app
reads it and trusts it **without verifying the signed JWT**: the origin publishes no public service,
so the only path to it runs through Access, which strips any client-supplied copy of the header and
sets its own. There is no route on which a forged header survives, so JWT verification would buy
nothing the topology has not already eliminated.

The identity is read-only and request-scoped: there is no session, no user table, no login the app
owns. This is the one seam that turns a request into an Actor — the *who* on every mutating audit
event — and where JWT verification would land if the origin ever stopped being private.
"""

# The header Cloudflare Access sets to the authenticated user's email on every proxied request.
ACCESS_EMAIL_HEADER = "Cf-Access-Authenticated-User-Email"

# Recorded as the Actor off the Access edge — local ``just run`` and the test suite, where the
# header is absent. A missing identity must never fail a working mutation, and an off-edge run has
# no real human to credit, so it is attributed to no one rather than rejected.
UNKNOWN_ACTOR = "unknown"


def actor_from_header(value: str | None) -> str:
    """The Actor to credit, given the raw Access email header (or ``None`` when it is absent).

    A present email is taken at face value. An absent or blank header — every request off the Access
    edge — falls back to :data:`UNKNOWN_ACTOR` so the mutation still succeeds.
    """
    if value is None or not value.strip():
        return UNKNOWN_ACTOR
    return value.strip()
