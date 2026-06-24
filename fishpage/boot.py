"""Process-startup orchestration: bring a SQLite catalog up before the app serves from it.

The boot sequence is non-destructive. The database file is never deleted; an empty catalog is
seeded once from the sample Stocklist, and a catalog that already holds Items is left untouched
so local data persists across a restart.
"""

import sqlite3
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path

from fishpage.config import Settings
from fishpage.ingest import stocklist_date
from fishpage.parser import parse_stocklist
from fishpage.store import latest_stocklist_date, reconcile

# The Litestream config baked into the deploy image. It resolves the replica's bucket, R2 endpoint,
# and credentials from the environment, keeping those (and the secret keys) out of the argv.
LITESTREAM_CONFIG = Path("/etc/litestream.yml")


def litestream_restore_command(settings: Settings) -> list[str]:
    """Build the ``litestream restore`` invocation that pulls the database back before serving.

    ``-if-replica-exists`` makes the first-ever boot a no-op rather than a failure: the replica is
    still empty, so there is nothing to restore and the boot falls through to seeding from the
    sample PDF. On every later boot it restores the latest snapshot in place at the configured path.
    """
    return [
        "litestream",
        "restore",
        "-if-replica-exists",
        "-config",
        str(LITESTREAM_CONFIG),
        str(settings.db_path),
    ]


def restore_database(
    settings: Settings,
    run: Callable[..., object] = subprocess.run,
) -> bool:
    """Restore the SQLite file from object storage before serving, when replication is on.

    Returns ``True`` when a restore was performed. With no replica configured — bare ``just run``
    and CI — this is a no-op returning ``False`` and the app runs on the plain local file. When a
    replica *is* configured it shells out to ``litestream restore`` and fails loudly if that
    command fails, rather than serve a database that could not be recovered.

    Runs ahead of ``litestream replicate`` (the image entrypoint sequences it first), so the two
    Litestream operations never contend for the same file.
    """
    if settings.litestream_replica_url is None:
        return False
    run(litestream_restore_command(settings), check=True)
    return True


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
