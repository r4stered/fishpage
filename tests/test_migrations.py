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

    # The baseline migration creates the v1 items table; later migrations then carry the fresh
    # database forward to the latest version.
    assert version == 8
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    assert "sku" in columns and "reuse_flagged" in columns


def test_re_running_on_an_up_to_date_database_is_a_noop(tmp_path):
    conn = fresh_conn()
    migrate(conn)

    # A second boot must not re-apply or error; the version stays put.
    assert migrate(conn) == 8


def test_a_populated_pre_runner_database_keeps_its_rows_and_is_stamped(tmp_path):
    conn = fresh_conn()
    conn.executescript(PRE_RUNNER_SCHEMA)
    conn.execute(
        "INSERT INTO items (sku, size, name, retail_price, qty_avail) VALUES (?, ?, ?, ?, ?)",
        ("110042", "M", "Bichir Ornate", "28.99", 15),
    )
    conn.commit()

    version = migrate(conn)

    # The baseline meets an existing table as a no-op: the row survives while the database is
    # carried forward to the latest version so later migrations build on it.
    assert version == 8
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


def table_names(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_the_enrichment_schema_migration_creates_both_enrichment_tables(tmp_path):
    conn = fresh_conn()

    version = migrate(conn)

    # The phase-2 Enrichment schema lands as the migration after the v1 baseline; the runner then
    # carries the database on to the latest version.
    assert version == 8
    assert {"enrichment", "classifier_override"} <= table_names(conn)


def test_the_pick_list_migration_creates_the_per_actor_table(tmp_path):
    conn = fresh_conn()

    migrate(conn)

    # The Pick-list table lands via the runner, keyed by (actor, sku) so a repeated add is
    # idempotent and one Actor's list is isolated from another's.
    assert "pick_list" in table_names(conn)
    info = conn.execute("PRAGMA table_info(pick_list)").fetchall()
    assert {row[1] for row in info} >= {"actor", "sku", "quantity"}
    assert {row[1] for row in info if row[5]} == {"actor", "sku"}


def enrichment_columns(conn):
    return {row[1] for row in conn.execute("PRAGMA table_info(enrichment)")}


def test_enrichment_holds_the_species_and_enum_care_classifiers(tmp_path):
    conn = fresh_conn()

    migrate(conn)

    # The AI care block: a resolved species plus one column per enum Classifier.
    assert {
        "scientific_name",
        "common_name",
        "difficulty",
        "temperament",
        "plant_safe",
    } <= enrichment_columns(conn)


def test_enum_care_columns_reject_out_of_vocabulary_values(tmp_path):
    conn = fresh_conn()
    migrate(conn)

    # A fabricated grade the model could never legitimately emit is refused at the DB level.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO enrichment (sku, difficulty) VALUES ('1', 'wizard')")


def test_enum_care_columns_accept_a_valid_value_the_unknown_hatch_and_null(tmp_path):
    conn = fresh_conn()
    migrate(conn)

    # In-vocabulary values, the honesty hatch, and an unset (NULL) attribute are all stored.
    conn.execute(
        "INSERT INTO enrichment (sku, difficulty, temperament, plant_safe) "
        "VALUES ('1', 'beginner', 'unknown', NULL)"
    )

    row = conn.execute(
        "SELECT difficulty, temperament, plant_safe FROM enrichment WHERE sku = '1'"
    ).fetchone()
    assert row == ("beginner", "unknown", None)


def image_columns(conn):
    return {row[1] for row in conn.execute("PRAGMA table_info(image)")}


def test_migration_3_moves_image_metadata_into_its_own_table(tmp_path):
    conn = fresh_conn()

    migrate(conn)

    # The manual image follows the override pattern, not the wholesale-overwritten enrichment row,
    # so its metadata lives in a dedicated image table — keyed by SKU, carrying only the R2 object
    # key plus license/attribution/source and its Provenance, never the bytes.
    assert "image" in table_names(conn)
    assert {
        "sku",
        "object_key",
        "license",
        "attribution",
        "source_url",
        "provenance",
    } <= image_columns(conn)
    # And the vestigial image columns the phase-2 schema put in enrichment are gone.
    assert not (
        {"image_object_key", "image_license", "image_attribution", "image_source_url"}
        & enrichment_columns(conn)
    )


def test_migration_4_adds_nullable_uploader_audit_columns_to_image(tmp_path):
    conn = fresh_conn()

    migrate(conn)

    # The Uploader and an upload timestamp join the image row: who attached a manual image and
    # when, durable in the same catalog. Both are nullable — the auto-source path has no human
    # Uploader and leaves them unset, the way it already leaves license/attribution unset.
    assert {"uploaded_by", "uploaded_at"} <= image_columns(conn)
    conn.execute(
        "INSERT INTO image (sku, object_key, provenance, uploaded_by, uploaded_at) "
        "VALUES ('110042', 'k', 'manual', 'a@example.com', '2026-06-25T12:00:00+00:00')"
    )
    # A sourced row leaves both NULL, proving the columns are nullable, not defaulted.
    conn.execute(
        "INSERT INTO image (sku, object_key, provenance) VALUES ('110092', 'k2', 'wikimedia')"
    )
    rows = {
        sku: (uploaded_by, uploaded_at)
        for sku, uploaded_by, uploaded_at in conn.execute(
            "SELECT sku, uploaded_by, uploaded_at FROM image"
        )
    }
    assert rows == {
        "110042": ("a@example.com", "2026-06-25T12:00:00+00:00"),
        "110092": (None, None),
    }


def test_classifier_override_stores_one_correction_per_sku_and_key(tmp_path):
    conn = fresh_conn()
    migrate(conn)

    conn.execute(
        "INSERT INTO classifier_override (sku, key, value) "
        "VALUES ('110042', 'difficulty', 'advanced')"
    )

    # A row's presence is the manual Provenance; the correction reads back verbatim.
    row = conn.execute(
        "SELECT sku, key, value FROM classifier_override WHERE sku = '110042'"
    ).fetchone()
    assert row == ("110042", "difficulty", "advanced")

    # One correction per (sku, key): a second override of the same attribute collides.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO classifier_override (sku, key, value) "
            "VALUES ('110042', 'difficulty', 'beginner')"
        )


def test_migration_6_adds_a_nullable_first_seen_to_items(tmp_path):
    conn = fresh_conn()
    conn.executescript(PRE_RUNNER_SCHEMA)
    conn.execute(
        "INSERT INTO items (sku, size, name, retail_price, qty_avail) VALUES (?, ?, ?, ?, ?)",
        ("110042", "M", "Bichir Ornate", "28.99", 15),
    )
    conn.commit()

    migrate(conn)

    # The column lands additively: the pre-existing row keeps its data and reads NULL first_seen —
    # an Item that predates first-sight tracking has no first-sight date, so it is never "new".
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    assert "first_seen" in columns
    assert conn.execute("SELECT first_seen FROM items WHERE sku = '110042'").fetchone()[0] is None


def test_migration_7_creates_the_append_only_stocklist_history_table(tmp_path):
    conn = fresh_conn()

    migrate(conn)

    # The append-only ledger: one row per SKU per Stocklist date, carrying the price and quantity
    # that Stocklist printed. Keyed by (sku, stocklist_date) so a re-run of the same Stocklist
    # cannot duplicate a row.
    assert "stocklist_history" in table_names(conn)
    info = conn.execute("PRAGMA table_info(stocklist_history)").fetchall()
    assert {row[1] for row in info} >= {
        "sku",
        "stocklist_date",
        "retail_price",
        "special_price",
        "qty",
    }
    assert {row[1] for row in info if row[5]} == {"sku", "stocklist_date"}


def test_migration_8_adds_a_not_null_default_zero_strain_specific_to_enrichment(tmp_path):
    conn = fresh_conn()

    migrate(conn)

    # The strain flag joins the AI care block as an INTEGER (SQLite has no boolean). It is NOT NULL
    # with a default of 0, so enrichment rows that predate it read as wild-type until a re-enrich.
    assert "strain_specific" in enrichment_columns(conn)
    conn.execute("INSERT INTO enrichment (sku) VALUES ('110042')")
    assert (
        conn.execute("SELECT strain_specific FROM enrichment WHERE sku = '110042'").fetchone()[0]
        == 0
    )


def test_the_enrichment_migration_is_additive_on_a_populated_database(tmp_path):
    conn = fresh_conn()
    conn.executescript(PRE_RUNNER_SCHEMA)
    conn.execute(
        "INSERT INTO items (sku, size, name, retail_price, qty_avail) VALUES (?, ?, ?, ?, ?)",
        ("110042", "M", "Bichir Ornate", "28.99", 15),
    )
    conn.commit()

    version = migrate(conn)

    # The first real migrations against the live, populated database: existing Items are untouched
    # and the new tables land empty alongside them — no data loss.
    assert version == 8
    assert conn.execute("SELECT sku, name FROM items").fetchall() == [("110042", "Bichir Ornate")]
    assert conn.execute("SELECT count(*) FROM enrichment").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM classifier_override").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM image").fetchone()[0] == 0
