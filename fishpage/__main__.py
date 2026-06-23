"""Local entrypoint: parse the sample Stocklist into a fresh SQLite catalog and serve it.

Run with ``uv run fishpage`` (or ``uv run python -m fishpage``). This is the walking-skeleton
wiring — it rebuilds the catalog from the committed sample PDF on every start. Ingestion goes
through :func:`reconcile`, the single upsert-by-SKU path (ADR-0001); the watched-folder trigger
is a later slice.
"""

import os
import re
from datetime import date
from pathlib import Path

import uvicorn

from fishpage.app import create_app
from fishpage.parser import parse_stocklist
from fishpage.store import open_store, reconcile

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PDF = _REPO_ROOT / "tests" / "fixtures" / "Freshwater_Stocklist_6-19-26.pdf"
_DEFAULT_DB = _REPO_ROOT / "fishpage.db"


def build_app():
    pdf_path = Path(os.environ.get("STOCKLIST_PDF", _DEFAULT_PDF))
    db_path = Path(os.environ.get("FISHPAGE_DB", _DEFAULT_DB))

    db_path.unlink(missing_ok=True)  # fresh DB each start
    conn = open_store(db_path)
    items = parse_stocklist(pdf_path)
    reconcile(conn, items, _stocklist_date(pdf_path))
    print(f"Loaded {len(items)} Items from {pdf_path.name}")
    return create_app(conn)


def _stocklist_date(pdf_path: Path) -> date:
    """Derive the Stocklist date from a ``..._M-D-YY.pdf`` filename, else fall back to today."""
    match = re.search(r"(\d{1,2})-(\d{1,2})-(\d{2})\b", pdf_path.stem)
    if match is None:
        return date.today()
    month, day, year = (int(part) for part in match.groups())
    return date(2000 + year, month, day)


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
