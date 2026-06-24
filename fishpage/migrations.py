"""Apply ordered, versioned schema migrations to the SQLite catalog on boot.

A deliberately small alternative to a migration framework: SQLite's own ``PRAGMA
user_version`` is the version counter. Each migration pairs the version it brings the
database *to* with the raw SQL that gets it there. On boot the runner applies, in order,
every migration whose version exceeds the database's current ``user_version`` and stamps
the version forward. Once the database is current, applying again is a no-op, so boot is
idempotent. Each migration runs in its own transaction with the version bump, so a crash
mid-run leaves the database at the last fully-applied version, never half-migrated.
"""

import sqlite3

# Ordered migrations: (version, raw SQL). To add one, append a tuple whose version is the
# next integer and whose SQL advances the schema to it; statements are terminated with `;`.
# Never edit or reorder a released migration — a database that already applied the old text
# would silently diverge from one migrating fresh.
MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS items (
            sku           TEXT PRIMARY KEY,
            size          TEXT NOT NULL,
            name          TEXT NOT NULL,
            retail_price  TEXT NOT NULL,
            special_price TEXT,
            qty_avail     INTEGER NOT NULL,
            last_seen     TEXT,
            reuse_flagged INTEGER NOT NULL DEFAULT 0
        );
        """,
    ),
]


def schema_version(conn: sqlite3.Connection) -> int:
    """The database's current schema version, as recorded in ``PRAGMA user_version``."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(
    conn: sqlite3.Connection,
    migrations: list[tuple[int, str]] = MIGRATIONS,
) -> int:
    """Apply every pending migration in order; return the resulting schema version."""
    current = schema_version(conn)
    for version, sql in migrations:
        if version <= current:
            continue
        _apply(conn, version, sql)
        current = version
    return current


def _apply(conn: sqlite3.Connection, version: int, sql: str) -> None:
    # The migration's SQL and the user_version bump commit together inside one explicit
    # transaction. user_version (an int the runner controls, not user input) is interpolated
    # because PRAGMA values can't be bound parameters.
    try:
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
