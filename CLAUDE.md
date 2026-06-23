# Fishpage

See `CONTEXT.md` for the domain language.

## Agent skills

### Issue tracker

Issues and PRDs live in this repo's GitHub Issues (`r4stered/fishpage`), via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Branch & PR workflow

One branch per issue, merged via a PR that closes the issue with a `Closes #<n>` keyword. `main` stays green. See `docs/agents/branch-workflow.md`.

### Triage labels

Canonical triage label strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), the default assignee (`r4stered`), and the one-category + one-state labelling rule. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Code comments

Code comments and docstrings describe the **current state of the code only**. **Never** point a comment at an issue, ADR, PR, or `CONTEXT.md` (`see ADR-0001`, `# fixes #21`, `(see CONTEXT.md)`) — those references rot. State the constraint or reason directly instead. The paper trail lives in ADRs, `CONTEXT.md`, and Issues; docs may cross-link docs, but code references neither. See `docs/agents/code-comments.md`.
