# Keep SQLite in the cloud, made durable by Litestream replication to object storage

Moving off the NAS onto an ephemeral Fly Machine (see
[ADR 0007](0007-deploy-to-flyio-cloud-not-unraid.md)) breaks the quiet assumption that the SQLite
file survives on local disk. It does not: a deploy or a Machine restart starts from a blank disk, and
continuous deployment makes restarts frequent. That matters because [ADR 0001](0001-sku-permanent-key-upsert-never-delete.md)'s
never-delete design — accumulating `last_seen` and, in phase 2, per-Item images and Classifiers —
only has value if the database **persists**. The obvious cloud answer is a managed Postgres. We
deliberately don't.

We keep **SQLite** and make it durable with **[Litestream](https://litestream.io/)**: the database
WAL is streamed continuously to a **Cloudflare R2** bucket, and on boot the Machine **restores** the
latest snapshot before serving. Litestream runs as the container entrypoint and supervises the app
(`litestream replicate -exec`), so "always replicating" and "run the app" are one process.

## Why not Postgres, why not a plain volume

- **Postgres** would force a driver, SQL-dialect, and schema-access rewrite, dragging in an ORM the
  codebase deliberately avoids and landing in exactly the plugin-heavy territory where `ty` is weak
  (see [ADR 0003](0003-ty-as-typecheck-gate.md)) — for no benefit at single-user scale.
- **A Fly volume** would keep SQLite with less wiring, but pins the app to one Machine in one region,
  makes backups a manual chore, and teaches nothing transferable. Litestream gives continuous backup,
  point-in-time restore, and platform independence, and exercises object storage directly — a stated
  learning goal. The accepted cost is one more moving part (the Litestream process) and an R2 bucket
  with credentials.

## Boot sequence inverts

The local walking skeleton deletes the database and rebuilds from the sample PDF on every start. In
the cloud that is replaced by: **restore from R2 → seed from the sample PDF only if the restored
database is empty → never delete.** The destructive `unlink`-on-boot is removed.

## Consequences

- One writer only. A single Machine owns the database; horizontal scaling is off the table while
  Litestream is the durability mechanism. Fine for a single-user tool.
- Litestream and R2 are **opt-in via environment**, defaulting off, so bare `just run` and the CI
  test suite need no cloud credentials and operate on a plain local SQLite file. The cloud deploy
  sets the env that turns replication on. R2 credentials are a secret held in Fly secrets.
- Now that the database persists, **schema migrations become a real concern** — a schema change would
  meet a live, populated database rather than a fresh one. v1's schema is stable, so create-if-not-exists
  on boot suffices for this rework; a lightweight raw-SQL migration runner (not Alembic, which carries
  the SQLAlchemy weight ADR 0003 avoids) is the intended next step before the phase-2 schema lands.
- Restore-on-boot adds startup latency proportional to the database size. At ~1k Items this is
  negligible; a much larger catalog would make it noticeable.
