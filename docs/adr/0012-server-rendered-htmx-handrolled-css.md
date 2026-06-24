# Server-rendered Jinja with HTMX and a hand-rolled stylesheet, not a JS SPA

The catalog UI grows real weight in Phase 2 — image-rich cards, Classifier filter chips, Provenance
markers, and inline manual overrides (Issue #74). The reflex for "a filterable, image-rich catalog"
is a JavaScript single-page app (React/Vue/Svelte) talking to the existing `/catalog` JSON. We
deliberately do **not** do that. The UI stays **server-rendered FastAPI + Jinja2**, made interactive
with **HTMX**, and styled with **one hand-written stylesheet**. No SPA, no node toolchain, no build
step — the Docker image stays a single Python stage.

The guiding constraints: this is an **internal buying tool** for one or two people, not a public
storefront, and the wanted aesthetic is deliberately minimal ("just enough styling to make it look
nice"). Both point away from a client-side framework and its machinery.

## Interactivity: HTMX over a SPA

Filter chips and inline overrides want to feel snappy without a full-page reload, but not at the cost
of a client-side framework, a bundler, and a second language runtime. **HTMX** buys exactly that: the
filter controls and override forms swap a server-rendered fragment in place. It is **vendored into
`/static`**, not loaded from a CDN — the app runs behind a Cloudflare Tunnel with no public origin
(see [ADR 0007](0007-deploy-to-flyio-cloud-not-unraid.md)), so a self-contained asset keeps the UI
free of an external runtime dependency and testable offline.

The trade-off accepted is a **second rendering path**: alongside whole pages, the app now renders
fragments. `catalog.html` splits into a shared `base.html` layout, the page, and an includable
`_grid.html` partial rendered by both the full page and the HTMX response.

## One route, header-sniffed, with the URL as the source of truth

Filter state already lives entirely in the query string (`/?category=Discus&on_special=true`), which
makes filtered views bookmarkable, shareable, reloadable, and back-button-friendly. HTMX must not
quietly destroy that. So there is **one `/` route**: it inspects the `HX-Request` header and returns
the `_grid.html` partial for HTMX requests, the full page otherwise. Chips set `hx-push-url` so the
address bar stays a real, reloadable URL. The consequence worth stating: with JavaScript disabled the
same route still serves a working full page and the chips fall back to plain links/reloads — graceful
degradation comes for free from keeping the URL canonical, rather than being bolted on.

Dedicated `/partials/*` fragment routes were rejected as a second URL shape to keep in sync; one route
that branches on a header is less to maintain and keeps the pushed URL identical to the one that
renders on a hard refresh.

## Styling: a hand-rolled stylesheet, not a CSS framework

The aesthetic target is a minimal, near-classless look (the "motherfuckingwebsite" family) with **no
fancy colors**. A classless framework (Pico/Simple/Water) would style forms and typography for free
but cannot style the card grid, chips, or Provenance markers — and brings its own opinions and palette.
A utility framework (Tailwind) would reintroduce the node build step we just refused. So the UI ships
**one hand-written `static/app.css`** the project owns entirely, driven by `:root` CSS variables:

- Greyscale with a **single muted accent**, spent only on links and the active filter chip; everything
  else is neutral.
- **Auto light/dark** via `prefers-color-scheme` — variables only, no toggle UI.
- System font stack (no web fonts to fetch). Card grid via `repeat(auto-fill, minmax(220px, 1fr))`
  with a fixed image aspect ratio and `object-fit: cover`, so mismatched source images do not break
  the layout.

Because color is scarce, Provenance and attribution read **without** relying on it: an `ai-generated`
Classifier value gets a dotted underline and an "AI-generated guess" tooltip while a `manual` value
renders plain, and a sourced image's required credit is a small muted caption beneath the photo. This
keeps the `ai-generated` / `manual` distinction legible in both themes.

## Consequences

- The UI ecosystem is intentionally tiny: **FastAPI + Jinja + one CSS file + one vendored JS file**,
  no `package.json` anywhere in the repo, no build step in CI or the Dockerfile.
- There are now two rendering paths (page and partial). New interactive UI is expected to render a
  fragment behind the `HX-Request` branch, and partials are testable by sending that header.
- Reversing to a SPA later is costly: routes, templates, and the URL-as-state contract are all built
  around server rendering. That is the accepted price of not carrying SPA machinery for an internal
  two-person tool.
- This decision is implementation, not domain language, so [CONTEXT.md](../../CONTEXT.md) is untouched.
