"""Local entrypoint: parse the sample Stocklist into a fresh SQLite catalog and serve it,
then watch the incoming directory so a dropped Stocklist updates the live catalog.

Run with ``uv run fishpage`` (or ``uv run python -m fishpage``). The catalog is rebuilt from
the committed sample PDF on every start; a background watcher then reconciles any Stocklist
PDF dropped into the incoming directory into the same connection the app serves from.
"""

import os
import threading
from datetime import date
from pathlib import Path

import uvicorn

from fishpage.app import create_app
from fishpage.ingest import stocklist_date, watch_incoming
from fishpage.parser import parse_stocklist
from fishpage.store import open_store, reconcile

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PDF = _REPO_ROOT / "tests" / "fixtures" / "Freshwater_Stocklist_6-19-26.pdf"
_DEFAULT_DB = _REPO_ROOT / "fishpage.db"
_DEFAULT_INCOMING = _REPO_ROOT / "data" / "incoming"
_DEFAULT_PROCESSED = _REPO_ROOT / "data" / "processed"


def build_app():
    pdf_path = Path(os.environ.get("STOCKLIST_PDF", _DEFAULT_PDF))
    db_path = Path(os.environ.get("FISHPAGE_DB", _DEFAULT_DB))
    incoming = Path(os.environ.get("INCOMING_DIR", _DEFAULT_INCOMING))
    processed = Path(os.environ.get("PROCESSED_DIR", _DEFAULT_PROCESSED))
    # Floor the poll interval: a zero or negative override would busy-loop the watcher.
    interval = max(1.0, float(os.environ.get("INGEST_POLL_SECONDS", "30")))

    db_path.unlink(missing_ok=True)  # fresh DB each start
    conn = open_store(db_path)
    items = parse_stocklist(pdf_path)
    # The startup PDF may be an ad-hoc path without the M-D-YY name convention; into a fresh DB
    # there are no absentees to mis-zero, so dating it today is a harmless convenience here.
    try:
        startup_date = stocklist_date(pdf_path)
    except ValueError:
        startup_date = date.today()
    reconcile(conn, items, startup_date)
    print(f"Loaded {len(items)} Items from {pdf_path.name}")

    watcher = threading.Thread(
        target=watch_incoming,
        args=(conn, incoming, processed),
        kwargs={"interval": interval},
        daemon=True,
    )
    watcher.start()
    print(f"Watching {incoming} for dropped Stocklists (every {interval:g}s)")

    return create_app(conn)


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
