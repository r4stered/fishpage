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
import threading

import uvicorn

from fishpage.app import create_app
from fishpage.boot import init_observability, seed_if_empty
from fishpage.config import Settings, load_settings
from fishpage.ingest import watch_incoming
from fishpage.store import open_store


def build_app(settings: Settings):
    conn = open_store(settings.db_path)
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

    return create_app(conn)


def main():
    settings = load_settings(os.environ)
    init_observability(settings)
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
