# Build the order from a per-Actor Pick list, exported then cleared

*Status: proposed — decided in a design session, not yet built.*

The catalog's stated purpose is to decide what to order, but until now the decision left the app
entirely: the buyer re-keyed the chosen SKUs into SDC's order by hand. A **Pick list** closes that
last gap — the buyer gathers Items into a list, then exports it to place the order through SDC. The
app does **not** check out (there is no supplier integration), so a Pick list is a staging list, not
an order.

## Per-Actor, server-side, keyed by the Access email

The Pick list is stored server-side in the catalog DB, keyed by the Cloudflare Access email — the same
identity [ADR 0013](0013-trust-cloudflare-access-email-as-uploader.md) already trusts and durably
stores as the Uploader. This is the app's **first owned per-user *persisted* state**: until now the
only identity was request-scoped with no per-user data of its own.

A client-side cookie/`localStorage` list was the obvious lighter option — it keeps the app sessionless
and needs no table, no migration, and no backup. It was rejected because each buyer keeps their own
list and a server-side list is consistent across devices and with the existing per-Actor audit story.
The cost is real and worth naming: the Pick list now rides the Litestream-replicated DB, and the app
owns per-user rows it never did before — so the integrity caveat of ADR 0013 (values are only as
trustworthy as the origin staying private) now extends to Pick-list ownership too.

## Cleared on export

Export is the terminal action — the buyer takes the exported list to SDC — so the app's copy has
served its purpose and is wiped on export, preventing a stale list from bleeding into next week's
order. Whether a separate manual "clear" exists, and how a line whose SKU drops to qty 0 or is
discontinued before export is handled, are implementation details settled in the issue, not here.
