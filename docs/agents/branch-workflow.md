# Branch & PR workflow: one branch per issue

Each issue is delivered on its own branch and merged through a pull request that closes the
issue. `main` stays green; work never lands directly on it.

A ruleset on `main` enforces this: every PR must pass three required checks — `lint`, `types`,
`test` — and `main` cannot be pushed to directly. Reproduce that gate locally with a single
command before you push:

```sh
uv run just check     # lint + typecheck + test — the same gate CI runs
```

See the [Checks section of the README](../../README.md#checks) for the individual recipes and
the optional pre-commit hooks.

## Steps

1. **Branch off `main`**, named for the issue:

   ```sh
   git switch main && git pull
   git switch -c issue-<number>-<short-slug>     # e.g. issue-2-walking-skeleton
   ```

2. **Do the work on that branch.** Keep the branch scoped to the one issue; unrelated fixes
   belong on their own branch and issue.

3. **Commit** with a message whose body ends by linking the issue:

   ```
   <type>: <summary>

   <what changed and why>

   Refs #<number>
   ```

   Use `Refs #<n>` on intermediate commits. Save the closing keyword for the PR (step 5) so
   the issue closes on merge, not on the first commit.

4. **Push** and set upstream. Run the gate first so CI doesn't bounce the PR:

   ```sh
   uv run just check                              # must be green before pushing
   git push -u origin issue-<number>-<short-slug>
   ```

5. **Open a PR** whose body contains a [closing keyword][closes] so GitHub auto-links it and
   closes the issue when the PR merges:

   ```sh
   gh pr create --base main --assignee r4stered --label <category> \
     --title "..." --body "$(cat <<'EOF'
   <summary>

   Closes #<number>

   <details / verification>
   EOF
   )"
   ```

   GitHub recognizes `Closes #<n>`, `Fixes #<n>`, and `Resolves #<n>` (case-insensitive). The
   PR view will show "Closes #<n>" and tag the issue; merging into `main` closes it.

   Assign and label the PR the same way as an issue: assignee defaults to the maintainer
   (`r4stered`), and the PR carries the **category** label (`bug` / `enhancement`) of the
   issue it closes. The triage **state** roles (`needs-triage`, `ready-for-agent`, …) live on
   issues, not PRs. See [`triage-labels.md`](triage-labels.md). Backfill an existing PR with
   `gh pr edit <n> --add-assignee r4stered --add-label <category>`.

6. **Merge** once checks/review pass; delete the branch.

## Notes

- One issue → one branch → one PR. If a PR grows to cover extra issues, list each:
  `Closes #2`, `Closes #3` (each keyword needs its own `#n`).
- A closing keyword only fires when the PR targets the **default branch** (`main`) and is
  merged — not on a draft, and not from a comment.

[closes]: https://docs.github.com/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue
