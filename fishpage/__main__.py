"""Local entrypoint: serve the catalog from a persistent SQLite file, seeding it from the sample
Stocklist the first time, then watch the incoming directory so a dropped Stocklist updates it.

Run with ``uv run fishpage`` (or ``uv run python -m fishpage``). Configuration is read once from
the environment into a :class:`~fishpage.config.Settings` object; with no environment set the app
runs on a plain local SQLite file with every cloud dependency off. The catalog persists across
restarts: it is seeded from the committed sample PDF only when empty and is never deleted. A
background watcher then reconciles any Stocklist PDF dropped into the incoming directory into the
same connection the app serves from, unless cloud ingestion is configured to drive that instead.
"""

import os
import socket
import sqlite3
import threading
from collections.abc import Callable

import uvicorn

from fishpage import observability
from fishpage.app import create_app
from fishpage.boot import seed_if_empty
from fishpage.config import Settings, load_settings
from fishpage.drainer import run_drainer
from fishpage.enricher import Enricher, select_enricher
from fishpage.images import select_image_store
from fishpage.ingest import watch_incoming
from fishpage.store import open_store


def build_app(settings: Settings):
    conn = open_store(settings.db_path)
    observability.track_catalog_freshness(conn)
    loaded = seed_if_empty(conn, settings.pdf_path)
    if loaded:
        print(f"Seeded {loaded} Items from {settings.pdf_path.name}")
    else:
        print(f"Reusing existing catalog at {settings.db_path}")

    if not settings.cloud_ingestion:
        watcher = threading.Thread(
            target=watch_incoming,
            args=(conn, settings.incoming_dir, settings.processed_dir),
            kwargs={"interval": settings.poll_interval},
            daemon=True,
        )
        watcher.start()
        print(
            f"Watching {settings.incoming_dir} for dropped Stocklists "
            f"(every {settings.poll_interval:g}s)"
        )

    if start_drainer(conn, settings) is not None:
        print("Enrichment drainer running — filling un-enriched Items in the background")

    image_store = select_image_store(settings)
    if image_store is not None:
        print("Image bucket configured — manual uploads stored in R2 and proxied through the app")

    return create_app(
        conn,
        incoming_dir=settings.incoming_dir,
        processed_dir=settings.processed_dir,
        image_store=image_store,
        image_max_dimension=settings.image_max_dimension,
    )


def start_drainer(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    spawn: Callable[[sqlite3.Connection, Enricher], object] | None = None,
) -> object | None:
    """Start the background enrichment drainer when Enrichment is configured; otherwise do nothing.

    Opt-in and default-off: with no flag or no key :func:`select_enricher` returns ``None``, so no
    drainer thread is started — ``just run`` and the test suite start no background enrichment and
    need no credential. ``spawn`` is injected so a test can assert the wiring decision without
    launching a real thread; in production it defaults to a daemon thread running the drain loop.
    """
    enricher = select_enricher(settings)
    if enricher is None:
        return None
    return (spawn or _spawn_drainer)(conn, enricher)


def _spawn_drainer(conn: sqlite3.Connection, enricher: Enricher) -> threading.Thread:
    thread = threading.Thread(target=run_drainer, args=(conn, enricher), daemon=True)
    thread.start()
    return thread


def listening_socket(host: str, port: int) -> socket.socket:
    """Bind the server socket the app serves on, dual-stack for an IPv6 host.

    An IPv6 host (``::`` in the cloud) is bound with ``IPV6_V6ONLY`` cleared so the one socket
    answers both stacks. The app must reach the IPv6 private network (``fly proxy``, the Cloudflare
    Tunnel's ``[::1]``) *and* the IPv4 loopback that Fly's Machine health check probes; left to
    uvicorn, asyncio binds an IPv6 host ``V6ONLY`` and the app goes deaf on IPv4, failing the
    check. An IPv4 host (local dev) needs none of this.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind((host, port))
    sock.listen()
    return sock


def main():
    settings = load_settings(os.environ)
    # Raise the fishpage logger to INFO and JSON-format the console before anything logs, so the
    # startup narrative surfaces locally and is shaped for the OTLP handler configure() may attach.
    observability.configure_logging(settings)
    # Configure telemetry before building the app so the providers and the catalog-freshness
    # gauge are installed when the app and its connection register against them.
    observability.configure(settings)
    sock = listening_socket(settings.host, settings.port)
    uvicorn.run(build_app(settings), fd=sock.fileno())


if __name__ == "__main__":
    main()
