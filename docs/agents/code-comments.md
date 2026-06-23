# Code Comments

**A code comment describes the current state of the code — nothing else.**

## No self-referential pointers in code

Code comments and docstrings **must not** point at issues, ADRs, PRs, or other
project artifacts. Never write:

- `# see ADR-0001`, `(ADR-0002)`, `# per ADR 3`
- `# see #21`, `# fixes #14`, `# as discussed in PR #19`
- `# see CONTEXT.md`, `# per the glossary`
- `# TODO(#42): ...`, `# was a bug, see issue 7`

These references rot. Issues get closed and renumbered, ADRs get superseded, PRs
vanish into history — and the comment is left lying about a thing that no longer
says what it claimed. A reader in the code should never have to leave the code to
understand the code.

## Explain the *what* and *why*, not the paper trail

If a constraint is worth a comment, **state the constraint** — the actual rule,
invariant, or reason — directly in the comment. The substance is what matters, not
the document that happens to record it.

```python
# BAD — points at an artifact that will rot
size: str  # raw supplier token (see ADR-0002)

# GOOD — states the fact
size: str  # raw supplier grade/unit token, stored verbatim
```

```python
# BAD
# ON CONFLICT would silently keep the last row (ADR-0001 keys on SKU; #21)

# GOOD
# ON CONFLICT would silently keep only the last row, since SKU is the permanent key
```

## Comments reflect the present, not the timeline

Don't narrate history or planned work in code comments: no "used to be X", "changed
in the SKU refactor", "temporary until the watched-folder slice lands tied to issue
N." Describe what the code does *now*. If something is genuinely provisional, say
what the current behaviour is and why it's provisional — without citing a ticket.

## Where the paper trail *does* belong

The decisions, history, and rationale live in their proper homes:

- **`CONTEXT.md`** — the domain language and glossary.
- **`docs/adr/`** — the durable record of *why* a decision was made.
- **GitHub Issues / PRs** — the work and the discussion around it.

Cross-linking **between those documents** (an ADR citing another ADR, CONTEXT.md
pointing at an ADR) is fine — that's their job. The rule is one-directional: **docs
may reference docs; code may not reference either.** Code stands on its own.
