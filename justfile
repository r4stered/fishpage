# Task runner for fishpage — the single source of truth shared by CI,
# pre-commit, humans, and agents. Run any recipe with `uv run just <recipe>`.

# List available recipes.
default:
    @just --list

# Sync the environment (project + dev dependencies) from uv.lock.
install:
    uv sync

# Lint and check formatting (no writes) — mirrors the CI `lint` job.
lint:
    ruff check
    ruff format --check

# Auto-fix lint findings and reformat in place.
format:
    ruff check --fix
    ruff format

# Type-check with ty — mirrors the CI `types` job.
typecheck:
    ty check

# Run the test suite — mirrors the CI `test` job.
test:
    pytest

# Full local gate: everything CI runs, in one command.
check: lint typecheck test
