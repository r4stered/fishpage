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
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS enrichment (
            sku             TEXT PRIMARY KEY,
            scientific_name TEXT,
            common_name     TEXT,
            difficulty      TEXT CHECK (
                difficulty IN ('beginner', 'intermediate', 'advanced', 'unknown')
            ),
            temperament     TEXT CHECK (
                temperament IN ('peaceful', 'semi_aggressive', 'aggressive', 'unknown')
            ),
            plant_safe      TEXT CHECK (
                plant_safe IN ('safe', 'unsafe', 'unknown')
            ),
            image_object_key  TEXT,
            image_license     TEXT,
            image_attribution TEXT,
            image_source_url  TEXT
        );
        CREATE TABLE IF NOT EXISTS classifier_override (
            sku   TEXT NOT NULL,
            key   TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (sku, key)
        );
        """,
    ),
    (
        3,
        # A manual image is the same kind of value as a manual Classifier — human-authored,
        # outranks any sourced value, must survive re-enrichment — so its metadata lives in its own
        # table, not the wholesale-overwritten enrichment row. Re-enrichment clears only non-manual
        # rows here, leaving a manual upload structurally un-clobberable. The four image columns the
        # phase-2 schema put in enrichment move here; the bytes themselves never touch the database.
        """
        CREATE TABLE IF NOT EXISTS image (
            sku         TEXT PRIMARY KEY,
            object_key  TEXT NOT NULL,
            license     TEXT,
            attribution TEXT,
            source_url  TEXT,
            provenance  TEXT NOT NULL CHECK (
                provenance IN ('manual', 'wikimedia', 'ai-generated')
            )
        );
        ALTER TABLE enrichment DROP COLUMN image_object_key;
        ALTER TABLE enrichment DROP COLUMN image_license;
        ALTER TABLE enrichment DROP COLUMN image_attribution;
        ALTER TABLE enrichment DROP COLUMN image_source_url;
        """,
    ),
    (
        4,
        # Who attached a manual image, and when. The Uploader is the Cloudflare Access identity
        # credited with a manual upload; the timestamp is when it landed. Both are nullable and
        # meaningful only for manual images — the auto-source path has no human Uploader and leaves
        # them unset, the way it already leaves license/attribution unset on a manual upload.
        """
        ALTER TABLE image ADD COLUMN uploaded_by TEXT;
        ALTER TABLE image ADD COLUMN uploaded_at TEXT;
        """,
    ),
    (
        5,
        # The Pick list: the Items an Actor has gathered to order, held per Actor and keyed by the
        # Cloudflare Access email. This is the app's first owned per-Actor persisted state. The
        # composite (actor, sku) primary key is what makes a repeated add idempotent — a second add
        # of the same SKU collides on the key rather than duplicating the line — and what isolates
        # one Actor's list from another's. Quantity defaults to 1: gathering an Item means wanting
        # one of it until the buyer says otherwise.
        """
        CREATE TABLE IF NOT EXISTS pick_list (
            actor    TEXT NOT NULL,
            sku      TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (actor, sku)
        );
        """,
    ),
    (
        6,
        # The date a SKU was first seen, stamped once on insert and never advanced, so a
        # first-ever sighting stays distinguishable from a SKU that went out of stock and
        # returned (which advances last_seen but not this). Nullable: rows inserted before
        # this column carry no first-sight date and read as not-new.
        """
        ALTER TABLE items ADD COLUMN first_seen TEXT;
        """,
    ),
    (
        7,
        # An immutable per-SKU snapshot for one Stocklist date: the retail and special price and
        # quantity as that Stocklist printed them. Append-only — ingestion inserts one row per SKU
        # per Stocklist date and never updates or deletes it, so the live items row stays the fast
        # current-state read while this ledger keeps the week-over-week history the upsert destroys.
        # The (sku, stocklist_date) primary key makes a re-run of the same Stocklist a no-op append
        # rather than a duplicate row. Prices are TEXT, the same str(Decimal) form items stores.
        """
        CREATE TABLE IF NOT EXISTS stocklist_history (
            sku            TEXT NOT NULL,
            stocklist_date TEXT NOT NULL,
            retail_price   TEXT NOT NULL,
            special_price  TEXT,
            qty            INTEGER NOT NULL,
            PRIMARY KEY (sku, stocklist_date)
        );
        """,
    ),
    (
        8,
        # Whether the Item is a line-bred or fancy variant whose wild-type species photo would be
        # the wrong fish — the gate that keeps automatic image acquisition from storing a misleading
        # sourced photo for a strain. SQLite has no boolean, so it is an INTEGER (0/1). Existing
        # enrichment rows predate the column and default to 0 (wild-type) until a re-enrich sets it.
        """
        ALTER TABLE enrichment ADD COLUMN strain_specific INTEGER NOT NULL DEFAULT 0;
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
