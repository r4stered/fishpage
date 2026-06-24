# Task runner for fishpage. The dev recipes are the single source of truth shared by CI,
# pre-commit, humans, and agents; the ops recipes are human-run shortcuts for administering the
# deployed Fly Machine. Run any recipe with `uv run just <recipe>`.

# The Fly app these ops recipes administer.
app := "fishpage"

# List available recipes.
default:
    @just --list

# Sync the environment (project + dev dependencies) from uv.lock.
[group('dev')]
install:
    uv sync

# Serve the catalog at http://127.0.0.1:8000/ (override source with STOCKLIST_PDF=/path).
[group('dev')]
run:
    fishpage

# Lint and check formatting (no writes) — mirrors the CI `lint` job.
[group('dev')]
lint:
    ruff check
    ruff format --check

# Auto-fix lint findings and reformat in place.
[group('dev')]
format:
    ruff check --fix
    ruff format

# Type-check with ty — mirrors the CI `types` job.
[group('dev')]
typecheck:
    ty check

# Run the test suite — mirrors the CI `test` job.
[group('dev')]
test:
    pytest

# Full local gate: everything CI runs, in one command.
[group('dev')]
check: lint typecheck test

# Tail the live Machine's logs.
[group('ops')]
logs:
    fly logs -a {{app}}

# Open the private admin path — browse http://localhost:8080/ over Fly's WireGuard network.
[group('ops')]
proxy:
    fly proxy 8080:8080 -a {{app}}

# Show the Machine's status.
[group('ops')]
status:
    fly status -a {{app}}

# List allocated IPs — expect none; a public address would mean a bypassable origin.
[group('ops')]
ips:
    fly ips list -a {{app}}

# List past releases, newest first — use this to find the SHA to roll back to.
[group('ops')]
releases:
    fly releases -a {{app}}

# A merge to `main` ships itself, so there is no forward `deploy` recipe; rolling back to a prior
# SHA-tagged image is the one sanctioned manual deploy, the deliberate exception.
# Roll back to a prior SHA-tagged image — the only manual deploy (find the SHA with `just releases`).
[group('ops')]
rollback sha:
    fly deploy --app {{app}} --image registry.fly.io/{{app}}:{{sha}}
