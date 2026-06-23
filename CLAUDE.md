# Fishpage

See `CONTEXT.md` for the domain language.

## Agent skills

### Issue tracker

Issues and PRDs live in this repo's GitHub Issues (`r4stered/fishpage`), via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Branch & PR workflow

One branch per issue, merged via a PR that closes the issue with a `Closes #<n>` keyword. `main` stays green. See `docs/agents/branch-workflow.md`.

### Triage labels

Canonical triage label strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
