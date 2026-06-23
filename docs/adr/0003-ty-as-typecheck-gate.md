# Gate CI on `ty`, a beta type checker, rather than the mature `mypy`

We type-check in CI as a required gate, and the obvious choice is `mypy` — the mature default,
stable diagnostics, the broadest ecosystem. We deliberately pick `ty` (Astral's checker) instead,
even though as of mid-2026 it is still **beta**, with a 1.0 targeted for "sometime in 2026" and no
firm date. A future reader will reasonably ask why `main` is blocked by a pre-1.0 tool; this records
the trade-off.

The pull toward `ty` is a single coherent toolchain. We already run `ruff` and `uv`, both Astral
tools; `ty` is the same vendor, Rust-based, and 10–60× faster than `mypy`/`pyright`. One toolchain
means one mental model and one place dependencies come from — `ty` rides into CI through `uv.lock`
exactly like `ruff` and `pytest`, so there is nothing extra to install or pin.

The standard objection to `ty` is its weak support for plugin-heavy ecosystems — Pydantic, Django,
SQLAlchemy — where `mypy` plugins still win. That objection mostly doesn't apply here: our domain
model is plain `@dataclass` (see `models.py`), not Pydantic `BaseModel`, and we render HTML through
Jinja rather than typed Pydantic response models. The FastAPI surface is thin. So the codebase sits
in `ty`'s comfortable zone, not its weak spot.

The real risk of gating on a beta tool is churn: a `ty` release could change its diagnostics and turn
`main` red without any code change of ours. We accept this knowingly. This is a small internal tool,
not enterprise-grade software — a little instability is an acceptable price for the faster, unified
toolchain, and `uv.lock` already bounds the blast radius (diagnostics only change when we run
`uv lock --upgrade`, on our schedule). If `ty` ever becomes a net drag, swapping to `mypy` or
`pyright` is possible but not free — a different checker reports different diagnostics, so the swap
means re-satisfying a new set of complaints across the codebase. That reversibility cost is why this
is a recorded decision and not an offhand config choice.

## Consequences

- The `types` CI job runs `ty check` and is a required status check on `main` alongside `lint` and
  `test`. A `ty` release can therefore break `main` independently of our code; the fix is to adapt to
  the new diagnostics or hold the version in `uv.lock`.
- We track `ty` latest rather than pinning a known-good version, accepting churn in exchange for
  staying current with a fast-moving beta. Reproducibility within a given commit still comes from
  `uv.lock`.
- This choice is sound only while the codebase avoids `ty`'s weak spots. If we later adopt Pydantic
  models or another plugin-dependent library, revisit whether `ty` still type-checks them adequately —
  that would be the trigger to reconsider, not a date.
