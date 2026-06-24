"""Process-startup orchestration: bring a SQLite catalog up before the app serves from it.

The boot sequence is non-destructive. The database file is never deleted; an empty catalog is
seeded once from the sample Stocklist, and a catalog that already holds Items is left untouched
so local data persists across a restart.
"""

import sqlite3
from datetime import date
from pathlib import Path

from fishpage.config import Settings
from fishpage.ingest import stocklist_date
from fishpage.parser import parse_stocklist
from fishpage.store import latest_stocklist_date, reconcile


def restore_database(settings: Settings) -> bool:
    """Restore the SQLite file from object storage before serving, when replication is on.

    Returns ``True`` when a restore was performed. With no replica configured — bare ``just run``
    and CI — this is a no-op returning ``False`` and the app runs on the plain local file. When a
    replica *is* configured it refuses to continue rather than serve an unreplicated database: the
    restore mechanism is filled in by the Litestream slice this seam exists for.
    """
    if settings.litestream_replica_url is None:
        return False
    raise NotImplementedError("Litestream restore is configured but not yet implemented")


def init_observability(settings: Settings) -> bool:
    """Start the OpenTelemetry exporter when an OTLP endpoint is configured.

    Returns ``True`` when telemetry export was started. With no endpoint — bare ``just run`` and
    CI — this is a no-op returning ``False`` and the app emits no telemetry. When an endpoint *is*
    configured it refuses to continue rather than silently drop telemetry: the exporter wiring is
    filled in by the observability slice this seam exists for.
    """
    if settings.otel_endpoint is None:
        return False
    raise NotImplementedError("OTel exporter is configured but not yet implemented")


def seed_if_empty(conn: sqlite3.Connection, pdf_path: Path) -> int:
    """Seed the catalog from ``pdf_path`` only when it holds no Items; otherwise do nothing.

    Returns the number of Items loaded — zero when the catalog was already populated, so a
    restart reuses the existing data rather than rebuilding it. The startup PDF may be an ad-hoc
    path without the ``M-D-YY`` name convention; into an empty catalog there are no absentees to
    mis-zero, so dating it today is a harmless convenience here.
    """
    if latest_stocklist_date(conn) is not None:
        return 0
    items = parse_stocklist(pdf_path)
    try:
        startup_date = stocklist_date(pdf_path)
    except ValueError:
        startup_date = date.today()
    reconcile(conn, items, startup_date)
    return len(items)
