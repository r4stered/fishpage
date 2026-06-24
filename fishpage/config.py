"""Read the process environment once into a typed settings object.

Every cloud dependency is opt-in and defaults off: with an empty environment the app runs on a
plain local SQLite file with no replication, no telemetry export, and the local watched-folder
ingestion trigger. The cloud deploy sets the environment that switches each one on.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF = _REPO_ROOT / "tests" / "fixtures" / "Freshwater_Stocklist_6-19-26.pdf"
DEFAULT_DB = _REPO_ROOT / "fishpage.db"
DEFAULT_INCOMING = _REPO_ROOT / "data" / "incoming"
DEFAULT_PROCESSED = _REPO_ROOT / "data" / "processed"


@dataclass(frozen=True)
class Settings:
    pdf_path: Path
    db_path: Path
    incoming_dir: Path
    processed_dir: Path
    poll_interval: float
    host: str
    port: int
    litestream_replica_url: str | None
    otel_endpoint: str | None
    cloud_ingestion: bool


def load_settings(env: Mapping[str, str]) -> Settings:
    return Settings(
        pdf_path=Path(env.get("STOCKLIST_PDF", DEFAULT_PDF)),
        db_path=Path(env.get("FISHPAGE_DB", DEFAULT_DB)),
        incoming_dir=Path(env.get("INCOMING_DIR", DEFAULT_INCOMING)),
        processed_dir=Path(env.get("PROCESSED_DIR", DEFAULT_PROCESSED)),
        # Floor the poll interval: a zero or negative override would busy-loop the watcher.
        poll_interval=max(1.0, float(env.get("INGEST_POLL_SECONDS", "30"))),
        host=env.get("HOST", "127.0.0.1"),
        port=int(env.get("PORT", "8000")),
        litestream_replica_url=env.get("LITESTREAM_REPLICA_URL"),
        otel_endpoint=env.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        cloud_ingestion=_flag(env.get("FISHPAGE_CLOUD_INGEST")),
    )


def _flag(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}
