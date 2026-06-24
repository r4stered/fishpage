# Task runner for fishpage. The dev recipes are the single source of truth shared by CI,
# pre-commit, humans, and agents; the ops recipes are human-run shortcuts for administering the
# deployed Fly Machine. Run any recipe with `uv run just <recipe>`.

# The Fly app these ops recipes administer.
app := "fishpage"

# Auto-load a gitignored `.env` (copy from `.env.example`) so the bootstrap credentials are exported
# into every recipe — no manual `export` and no remembering which token is which. Optional: recipes
# that need no secrets ignore it, and it is silently skipped when absent.
set dotenv-load := true

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

# --- bootstrap: stand up the whole cloud deploy from nothing, in one command ---
#
# Prerequisites (one-time): run `just install-tools`, create the R2 state bucket by hand, copy
# infra/backend.hcl.example to infra/backend.hcl and infra/terraform.tfvars.example to
# infra/terraform.tfvars and fill both, then export the auth the providers and the encrypted state
# backend read from the environment:
#   CLOUDFLARE_API_TOKEN GITHUB_TOKEN GRAFANA_CLOUD_ACCESS_POLICY_TOKEN GRAFANA_AUTH
#   AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY            # R2 state-bucket S3 keys
#   TF_VAR_fly_deploy_token TF_VAR_state_encryption_passphrase
# plus an authenticated flyctl (FLY_API_TOKEN or `fly auth login`). See infra/README.md.

# macOS uses Homebrew; Linux uses the distro package manager for jq and the vendors' own installers
# for OpenTofu and flyctl (neither ships in default distro repos). wrangler is an npm global on both;
# `just` itself comes from `uv sync`. Idempotent: skips anything already on PATH.
# Install the non-Python binaries bootstrap needs (OpenTofu, flyctl, jq, wrangler).
[group('bootstrap')]
install-tools:
    #!/usr/bin/env bash
    set -euo pipefail
    have() { command -v "$1" >/dev/null 2>&1; }
    os="$(uname -s)"

    brew_install() { brew list "$1" >/dev/null 2>&1 || brew install "$1"; }
    linux_pkg_install() {
        if   have apt-get; then sudo apt-get update -qq && sudo apt-get install -y "$1"
        elif have dnf;     then sudo dnf install -y "$1"
        elif have yum;     then sudo yum install -y "$1"
        elif have zypper;  then sudo zypper --non-interactive install "$1"
        elif have pacman;  then sudo pacman -S --noconfirm "$1"
        elif have apk;     then sudo apk add "$1"
        else echo "no supported Linux package manager found; install $1 manually" >&2; return 1
        fi
    }

    if have jq; then echo "jq: present"
    elif [ "$os" = Darwin ]; then echo "jq: installing"; brew_install jq
    else echo "jq: installing"; linux_pkg_install jq
    fi

    if have tofu; then echo "tofu: present"
    elif [ "$os" = Darwin ]; then echo "tofu: installing"; brew_install opentofu
    else
        echo "tofu: installing via get.opentofu.org (standalone)"
        curl --proto '=https' --tlsv1.2 -fsSL https://get.opentofu.org/install-opentofu.sh -o /tmp/install-opentofu.sh
        chmod +x /tmp/install-opentofu.sh
        /tmp/install-opentofu.sh --install-method standalone
        rm -f /tmp/install-opentofu.sh
    fi

    if have flyctl; then echo "flyctl: present"
    elif [ "$os" = Darwin ]; then echo "flyctl: installing"; brew_install flyctl
    else echo "flyctl: installing via fly.io/install.sh"; curl -fsSL https://fly.io/install.sh | sh
    fi

    if have wrangler; then echo "wrangler: present"
    elif have npm; then echo "wrangler: installing"; npm install -g wrangler
    else echo "wrangler needs npm (install Node.js first): https://nodejs.org" >&2; exit 1
    fi

    echo "tools ready — restart your shell if a freshly installed tool isn't found (Linux installers add to ~/.fly or /usr/local/bin)."

# Create app, apply cloud infra, wire secrets into Fly, first deploy, verify — re-runnable.
[group('bootstrap')]
bootstrap: _bootstrap-preflight
    # Create the Fly app if it does not exist yet (idempotent).
    flyctl status -a {{app}} >/dev/null 2>&1 || flyctl apps create {{app}}
    # Apply the declarative cloud infra (Cloudflare edge, GitHub secret, Grafana token + alert).
    tofu -chdir=infra init -input=false -backend-config=backend.hcl
    tofu -chdir=infra apply -auto-approve
    # Pipe the derived runtime secrets straight into Fly — staged, so they apply on the next deploy.
    tofu -chdir=infra output -json fly_secrets | jq -r 'to_entries[] | "\(.key)=\(.value)"' | flyctl secrets import --stage -a {{app}}
    # First deploy: the Machine boots with its secrets, Litestream restores, cloudflared connects.
    flyctl deploy -a {{app}}
    @just bootstrap-verify

# Dry-run the declarative half: prove a re-apply is a clean no-op without changing anything.
[group('bootstrap')]
bootstrap-plan: _bootstrap-preflight
    tofu -chdir=infra init -input=false -backend-config=backend.hcl
    tofu -chdir=infra plan

# Assert the acceptance criteria against the live deploy: no public origin, gated hostname.
[group('bootstrap')]
bootstrap-verify:
    #!/usr/bin/env bash
    set -euo pipefail
    host="$(tofu -chdir=infra output -raw hostname)"
    echo "==> fly ips list (expect no v4/v6 addresses — no public origin)"
    flyctl ips list -a {{app}}
    echo "==> curl -sI https://${host}/ (expect 302 to the Cloudflare Access login)"
    curl -sI "https://${host}/" | head -n1

# Fail fast with a clear message if any required credential is missing from the environment.
[private]
_bootstrap-preflight:
    #!/usr/bin/env bash
    set -euo pipefail
    missing=()
    for c in tofu flyctl jq; do
        command -v "$c" >/dev/null 2>&1 || missing+=("$c binary (run: just install-tools)")
    done
    for v in CLOUDFLARE_API_TOKEN GITHUB_TOKEN GRAFANA_CLOUD_ACCESS_POLICY_TOKEN GRAFANA_AUTH \
             AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY TF_VAR_fly_deploy_token \
             TF_VAR_state_encryption_passphrase; do
        [[ -n "${!v:-}" ]] || missing+=("$v")
    done
    [[ -f infra/terraform.tfvars ]] || missing+=("infra/terraform.tfvars (copy from .example)")
    [[ -f infra/backend.hcl ]] || missing+=("infra/backend.hcl (copy from .example)")
    if (( ${#missing[@]} )); then
        printf 'bootstrap prerequisite missing:\n'; printf '  - %s\n' "${missing[@]}"
        echo 'Secrets go in .env (copy from .env.example); non-secret values in infra/terraform.tfvars. See infra/README.md.'
        exit 1
    fi
