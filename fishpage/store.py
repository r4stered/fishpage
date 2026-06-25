"""Persist Item records in SQLite, keyed by SKU.

Ingestion is reconciliation, not mirroring: :func:`reconcile` upserts each
present SKU and advances its ``last_seen``, zeroes the quantity of any SKU absent from the
current Stocklist, and never deletes a row.
"""

import logging
import re
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from fishpage import observability
from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament
from fishpage.migrations import migrate
from fishpage.models import Item

_log = logging.getLogger(__name__)


def open_store(path: str | Path) -> sqlite3.Connection:
    # check_same_thread=False: this one connection is shared across threads — FastAPI handler
    # threads read from it while a background ingestion thread writes through it. The writer
    # runs one reconcile transaction per dropped Stocklist (a nightly cadence), so a reader can
    # briefly observe a half-reconciled state in the window between the upsert and the
    # absentee-zeroing UPDATE. For a low-traffic internal tool that window is acceptable;
    # closing it would mean a write lock around reconcile or a per-reader WAL snapshot.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Litestream replicates the write-ahead log, so the store must be in WAL mode for the cloud
    # deploy to have anything to stream. WAL is also a fine default for the local file. The pragma
    # is durable — it is recorded in the database header — so it holds across later reopens too.
    conn.execute("PRAGMA journal_mode=WAL")
    # The schema is owned by the migration runner: a fresh database is brought up to the latest
    # version, an already-current one is left untouched. The store no longer defines tables itself.
    migrate(conn)
    return conn


def reconcile(conn: sqlite3.Connection, items: list[Item], stocklist_date: date) -> None:
    """Reconcile the store against one Stocklist.

    Each ``item`` present in this Stocklist is upserted by SKU and stamped with
    ``stocklist_date`` as its ``last_seen``. No row is ever deleted.

    The reuse guard runs here: if a present SKU already exists under a materially
    different name, its row is still updated (the catalog stays current) but the Item is
    flagged for human review and the rename is logged. The flag is sticky — once raised it
    stays raised across later runs, since v1 has no way to clear it after review.
    """
    stored_names = {row["sku"]: row["name"] for row in conn.execute("SELECT sku, name FROM items")}
    params = []
    for item in items:
        prior_name = stored_names.get(item.sku)
        reuse = _is_reuse(prior_name, item.name)
        if reuse:
            _log.warning(
                "Reuse guard: SKU %s reappeared as %r (was %r); flagged for review.",
                item.sku,
                item.name,
                prior_name,
            )
            observability.record_reuse_flag()
        params.append(
            {
                "sku": item.sku,
                "size": item.size,
                "name": item.name,
                "retail": str(item.retail_price),
                "special": None if item.special_price is None else str(item.special_price),
                "qty": item.qty_avail,
                "last_seen": stocklist_date.isoformat(),
                "reuse": int(reuse),
            }
        )
    conn.executemany(
        "INSERT INTO items (sku, size, name, retail_price, special_price, qty_avail, last_seen, "
        "reuse_flagged) "
        "VALUES (:sku, :size, :name, :retail, :special, :qty, :last_seen, :reuse) "
        "ON CONFLICT(sku) DO UPDATE SET "
        "size = excluded.size, name = excluded.name, retail_price = excluded.retail_price, "
        "special_price = excluded.special_price, qty_avail = excluded.qty_avail, "
        "last_seen = excluded.last_seen, "
        "reuse_flagged = MAX(items.reuse_flagged, excluded.reuse_flagged)",
        params,
    )
    # An absentee is exactly a row the upsert above did NOT just stamp with this run's
    # date, so "absent" is "last_seen is not stocklist_date" — one bound parameter rather
    # than one per present SKU, which keeps us clear of SQLITE_MAX_VARIABLE_NUMBER (999 on
    # SQLite builds before 3.32) no matter how large the Stocklist grows. Absent SKUs are
    # zeroed, never deleted, and keep their last_seen.
    #
    # Tradeoff: this defines "absent" by date, not set membership, so re-running reconcile
    # twice with the *same* stocklist_date will not re-zero the first run's absentees (they
    # already carry that date). A degenerate case — real runs use a distinct date each night.
    conn.execute(
        "UPDATE items SET qty_avail = 0 WHERE last_seen IS NOT ?",
        (stocklist_date.isoformat(),),
    )
    conn.commit()


def _normalize_name(name: str) -> str:
    """Fold a name to its comparison form, collapsing differences the guard ignores.

    Case, surrounding/internal whitespace runs, and punctuation are all normalized away,
    so ``"Bichir Ornate"``, ``"bichir  ornate"`` and ``"Bichir, Ornate."`` compare equal.
    """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def _is_reuse(stored_name: str | None, incoming_name: str) -> bool:
    """True when an existing SKU's name has materially changed.

    ``stored_name`` is ``None`` for a SKU seen for the first time, which is never a reuse.
    """
    if stored_name is None:
        return False
    return _normalize_name(stored_name) != _normalize_name(incoming_name)


def latest_stocklist_date(conn: sqlite3.Connection) -> date | None:
    """The most recent Stocklist date reconciled into the store, or ``None`` if it is empty.

    This is ``MAX(last_seen)``: every reconcile stamps its present SKUs with the run's date, so
    the maximum is the newest Stocklist ever applied. Callers use it to keep ingestion monotonic
    — refusing to apply a Stocklist older than one already reconciled.
    """
    row = conn.execute("SELECT MAX(last_seen) AS latest FROM items").fetchone()
    return None if row["latest"] is None else date.fromisoformat(row["latest"])


def all_items(conn: sqlite3.Connection, *, include_out_of_stock: bool = True) -> list[Item]:
    """Read every stored Item, newest schema columns included.

    With ``include_out_of_stock=False`` the result is narrowed to In stock Items
    (``qty_avail > 0``); the filter runs in SQL so zeroed rows are never loaded.
    """
    query = (
        "SELECT sku, size, name, retail_price, special_price, qty_avail, last_seen, "
        "reuse_flagged FROM items"
    )
    if not include_out_of_stock:
        query += " WHERE qty_avail > 0"
    rows = conn.execute(query).fetchall()
    return [_row_to_item(row) for row in rows]


def unenriched_items(conn: sqlite3.Connection) -> list[Item]:
    """Every Item that has no enrichment row yet — the drainer's work queue.

    Ingestion writes only to ``items``; the *absence* of an ``enrichment`` row is exactly what
    marks a SKU un-enriched, so the queue is a left anti-join and needs no separate flag column.
    Clearing a SKU's enrichment row (an on-demand re-enrich) drops it straight back into this set.
    """
    rows = conn.execute(
        "SELECT i.sku, i.size, i.name, i.retail_price, i.special_price, i.qty_avail, "
        "i.last_seen, i.reuse_flagged "
        "FROM items i LEFT JOIN enrichment e ON e.sku = i.sku "
        "WHERE e.sku IS NULL"
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def persist_enrichment(conn: sqlite3.Connection, sku: str, result: EnrichmentResult) -> None:
    """Write one Item's AI-read species and care Classifiers into the ``enrichment`` table.

    Upsert by SKU so a re-enrich overwrites the prior AI row in place. Only the AI-owned columns
    are touched — the image columns are left as they are (the image pipeline owns those), and the
    ``classifier_override`` table is never written here, so a human's ``manual`` value, which lives
    there, is never clobbered by a re-enrich.
    """
    conn.execute(
        "INSERT INTO enrichment "
        "(sku, scientific_name, common_name, difficulty, temperament, plant_safe) "
        "VALUES (:sku, :scientific_name, :common_name, :difficulty, :temperament, :plant_safe) "
        "ON CONFLICT(sku) DO UPDATE SET "
        "scientific_name = excluded.scientific_name, common_name = excluded.common_name, "
        "difficulty = excluded.difficulty, temperament = excluded.temperament, "
        "plant_safe = excluded.plant_safe",
        {
            "sku": sku,
            "scientific_name": result.scientific_name,
            "common_name": result.common_name,
            "difficulty": result.difficulty.value,
            "temperament": result.temperament.value,
            "plant_safe": result.plant_safe.value,
        },
    )
    conn.commit()


def enrichment_for(conn: sqlite3.Connection, sku: str) -> EnrichmentResult | None:
    """The persisted enrichment for one SKU, or ``None`` when it is still un-enriched."""
    row = conn.execute(
        "SELECT scientific_name, common_name, difficulty, temperament, plant_safe "
        "FROM enrichment WHERE sku = ?",
        (sku,),
    ).fetchone()
    if row is None:
        return None
    return EnrichmentResult(
        scientific_name=row["scientific_name"],
        common_name=row["common_name"],
        difficulty=Difficulty(row["difficulty"]),
        temperament=Temperament(row["temperament"]),
        plant_safe=PlantSafe(row["plant_safe"]),
    )


def item_exists(conn: sqlite3.Connection, sku: str) -> bool:
    """Whether ``sku`` names a stored Item — the guard the on-demand re-enrich route checks."""
    return conn.execute("SELECT 1 FROM items WHERE sku = ?", (sku,)).fetchone() is not None


def clear_enrichment(conn: sqlite3.Connection, sku: str) -> None:
    """Drop a SKU's enrichment row, returning it to the un-enriched queue for a re-enrich.

    Only the AI ``enrichment`` row goes; any ``manual`` ``classifier_override`` for the SKU stays,
    so an on-demand re-enrich re-runs the AI pass without discarding a human correction.
    """
    conn.execute("DELETE FROM enrichment WHERE sku = ?", (sku,))
    conn.commit()


def _row_to_item(row: sqlite3.Row) -> Item:
    special: Decimal | None = (
        None if row["special_price"] is None else Decimal(row["special_price"])
    )
    last_seen: date | None = (
        None if row["last_seen"] is None else date.fromisoformat(row["last_seen"])
    )
    return Item(
        sku=row["sku"],
        size=row["size"],
        name=row["name"],
        retail_price=Decimal(row["retail_price"]),
        special_price=special,
        qty_avail=row["qty_avail"],
        last_seen=last_seen,
        reuse_flagged=bool(row["reuse_flagged"]),
    )
