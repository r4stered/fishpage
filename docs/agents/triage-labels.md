# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Default assignee

The maintainer is **`r4stered`** (Drew Williams). Assign **issues and PRs** to them by default —
when creating an issue, when triaging one, and when opening a PR — unless explicitly told otherwise:

```sh
gh issue create ... --assignee r4stered      # or --assignee @me when the maintainer runs it
gh issue edit <number> --add-assignee r4stered
gh pr create   ... --assignee r4stered
gh pr edit   <number> --add-assignee r4stered
```

## Applying labels

Every triaged issue carries **exactly one category role and one state role** — never zero, never
two of either:

- **Category** (one of): `bug`, `enhancement`.
- **State** (one of): `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`.

Apply both together:

```sh
gh issue edit <number> --add-label enhancement --add-label ready-for-agent
```

On a **state transition**, remove the old state label in the same call so the two don't coexist
(category rarely changes; state moves through the machine):

```sh
gh issue edit <number> --add-label ready-for-agent --remove-label needs-triage
```

**PRs** carry the **category** label only — the `bug` / `enhancement` of the issue they close.
State roles are an issue-triage concept and don't belong on a PR.

```sh
gh pr edit <number> --add-label enhancement
```

These labels don't exist in the GitHub repo yet. Create them on first use with, e.g.:

```sh
gh label create needs-triage --description "Maintainer needs to evaluate this issue"
gh label create needs-info --description "Waiting on reporter for more information"
gh label create ready-for-agent --description "Fully specified, ready for an AFK agent"
gh label create ready-for-human --description "Requires human implementation"
gh label create wontfix --description "Will not be actioned"
```
