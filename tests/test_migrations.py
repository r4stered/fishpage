import sqlite3

import pytest

from fishpage.migrations import migrate, schema_version

# The exact v1 schema as it existed before the runner — created ad hoc by an earlier boot,
# leaving the database at user_version 0. This is what the runner meets on the live database.
PRE_RUNNER_SCHEMA = """
CREATE TABLE items (
    sku           TEXT PRIMARY KEY,
    size          TEXT NOT NULL,
    name          TEXT NOT NULL,
    retail_price  TEXT NOT NULL,
    special_price TEXT,
    qty_avail     INTEGER NOT NULL,
    last_seen     TEXT,
    reuse_flagged INTEGER NOT NULL DEFAULT 0
)
"""


def fresh_conn():
    return sqlite3.connect(":memory:")


def test_a_fresh_database_is_brought_up_to_the_baseline_schema(tmp_path):
    conn = fresh_conn()

    version = migrate(conn)

    # The baseline migration creates the v1 items table and stamps the database with its version.
    assert version == 1
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    assert "sku" in columns and "reuse_flagged" in columns


def test_re_running_on_an_up_to_date_database_is_a_noop(tmp_path):
    conn = fresh_conn()
    migrate(conn)

    # A second boot must not re-apply or error; the version stays put.
    assert migrate(conn) == 1


def test_a_populated_pre_runner_database_keeps_its_rows_and_is_stamped(tmp_path):
    conn = fresh_conn()
    conn.executescript(PRE_RUNNER_SCHEMA)
    conn.execute(
        "INSERT INTO items (sku, size, name, retail_price, qty_avail) VALUES (?, ?, ?, ?, ?)",
        ("110042", "M", "Bichir Ornate", "28.99", 15),
    )
    conn.commit()

    version = migrate(conn)

    # The baseline meets an existing table as a no-op: the row survives and the database is
    # stamped to v1 so later migrations build on it.
    assert version == 1
    rows = conn.execute("SELECT sku, name FROM items").fetchall()
    assert rows == [("110042", "Bichir Ornate")]


def test_pending_migrations_apply_in_version_order_to_the_highest(tmp_path):
    conn = fresh_conn()
    steps = [
        (1, "CREATE TABLE widget (id INTEGER PRIMARY KEY);"),
        # Depends on the table from step 1, so this only succeeds if step 1 ran first.
        (2, "ALTER TABLE widget ADD COLUMN label TEXT;"),
    ]

    version = migrate(conn, steps)

    assert version == 2
    columns = {row[1] for row in conn.execute("PRAGMA table_info(widget)")}
    assert columns == {"id", "label"}


def test_already_applied_migrations_are_skipped(tmp_path):
    conn = fresh_conn()
    steps = [
        (1, "CREATE TABLE widget (id INTEGER PRIMARY KEY);"),
        (2, "ALTER TABLE widget ADD COLUMN label TEXT;"),
    ]
    migrate(conn, steps[:1])  # only step 1 applied; database now at version 1

    # Re-running the full list must apply step 2 only — re-running step 1 would raise
    # "table widget already exists", so a clean run proves step 1 was skipped.
    assert migrate(conn, steps) == 2


def test_a_failing_migration_rolls_back_atomically(tmp_path):
    conn = fresh_conn()
    steps = [
        (1, "CREATE TABLE widget (id INTEGER PRIMARY KEY);"),
        # Multi-statement: the first statement would succeed, the second is invalid. The whole
        # migration must roll back together so the half-applied table never persists.
        (2, "CREATE TABLE half (id INTEGER);\nINSERT INTO missing VALUES (1);"),
    ]

    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, steps)

    # The database is left cleanly at the last fully-applied version, with no debris from step 2.
    assert schema_version(conn) == 1
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'half'").fetchone() is None
