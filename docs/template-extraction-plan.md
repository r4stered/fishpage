# Template extraction plan

A future-work note, not a fishpage decision. Fishpage stays an internal, Cloudflare-Access-gated
tool. This captures the plan to lift fishpage's reusable core into a starter template for **public,
read-heavy side projects** — built from a grilling session, kept here until the template repo exists
to own it.

## Goal

A GitHub template repo that ships the annoying-to-rebuild infrastructure already wired — logging,
metrics, dashboards, IaC, deploy, backup — so a new public site idea gets off the ground in an
afternoon. Low cost per app; expand only on success.

## Resolved decisions

| Decision | Landed on | Why |
|---|---|---|
| Template's target | Public, **read-heavy** sites with optional login | Not internal tools — the original framing. The audience is the open web, mostly browsing. |
| Data spine | Keep single-writer SQLite + Litestream → R2 | Read-heavy + edge-cached makes one writer ideal, not a compromise. Reads scale enormously on one box; the origin barely gets hit. |
| Auth | **Off by default**; hosted provider (Supabase/Clerk) when a feature earns it | A mostly-read site needs no login to be useful or to make money. Don't build session security before a feature demands it. Never hand-roll auth across N projects. |
| Cloudflare Access | **Dropped** for the template | Access is all-or-nothing gating — it cannot do anonymous browsing + optional login. It only ever fit fishpage's internal model. |
| Topology | Published Fly service behind Cloudflare-as-CDN | The opposite of fishpage's no-public-origin tunnel-only model. The "no public IP" property only made sense for an internal tool. |
| Reuse mechanism | **GitHub template repo**, frozen-at-fork | Zero machinery, handles infra files (which can't live in a package), and matches a fleet of ~5 personal projects. No Copier, no published library. |
| Shape | **Runnable tracer-bullet slice** you gut | Boots, renders a page, logs to Grafana, backs up to R2 on first deploy — proving the infra works *before* any domain code. Gut the example to start. |
| UI | Keep htmx server-rendering; swap hand-rolled CSS for **Pico.css** (classless) | SSR HTML stays cacheable/SEO-friendly — the asset that lets one SQLite writer serve a public read site cheaply. A JS SPA framework would forfeit that. Pico = zero build, zero Node, semantic HTML styles itself. Ceiling: family look, limited custom components — a project that needs a distinct design system graduates to Tailwind on its own. |
| Idle cost | Accept ~$3/mo always-on per app | Below the threshold worth engineering scale-to-zero for, which would also break the clean topology. Add a `just teardown` to kill dead experiments cheaply. |

## Core carried over (auth- and scale-agnostic)

- OpenTelemetry → Grafana Cloud wiring (`observability.py`)
- OpenTofu bootstrap — already one-command (`just bootstrap`) and parameterized via `terraform.tfvars`
- SQLite + Litestream backup/restore
- htmx render layer + base template (keep base/layout, drop domain fragments); **Pico.css replaces the hand-rolled CSS** — diverges from fishpage's ADR-0012 on the CSS half, keeps the htmx server-rendered half
- `images.py` — private image upload → optimize → serve-behind-app; broadly reusable
- `config.py` env→Settings pattern; `migrations.py` / `store.py` connection + migration scaffolding (split: keep scaffolding, drop the catalog/pick-list queries)
- CI gates

## Fleet model

One Cloudflare account, one Grafana Cloud stack, one GitHub owner — each project provisions its own
folder / buckets / tunnel-or-service into the shared accounts. Provider tokens are fill-once and
reused across all projects; only `terraform.tfvars` is per-project.

## When to extract

**Not now.** Building the template before a first real idea is speculative — it guesses at what's
reusable instead of knowing. Path: fork fishpage → gut the domain → flip topology to public-read →
ship the first real idea. That project reveals which lines were truly reusable; *then* lift the
proven core into the template repo. Extraction by first-use, not by speculation.
