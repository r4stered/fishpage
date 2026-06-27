"""Read the process environment once into a typed settings object.

Every cloud dependency is opt-in and defaults off: with an empty environment the app runs on a
plain local SQLite file with no replication, no telemetry export, and the local watched-folder
ingestion trigger. The cloud deploy sets the environment that switches each one on.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_ROOT.parent
# Shipped as package data so the wheel-only deploy image, which has no source tree, can seed the
# catalog from it on boot.
DEFAULT_PDF = _PACKAGE_ROOT / "data" / "Freshwater_Stocklist_6-19-26.pdf"
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
    ingest_min_row_fraction: float
    ingest_max_zeroed_fraction: float
    ingest_max_retail_price: float
    log_level: str
    host: str
    port: int
    litestream_replica_url: str | None
    otel_endpoint: str | None
    cloud_ingestion: bool
    enrichment_enabled: bool
    anthropic_api_key: str | None
    images_enabled: bool
    image_max_dimension: int
    r2_images_bucket: str | None
    r2_images_endpoint: str | None
    r2_images_access_key_id: str | None
    r2_images_secret_access_key: str | None


def load_settings(env: Mapping[str, str]) -> Settings:
    return Settings(
        pdf_path=Path(env.get("STOCKLIST_PDF", DEFAULT_PDF)),
        db_path=Path(env.get("FISHPAGE_DB", DEFAULT_DB)),
        incoming_dir=Path(env.get("INCOMING_DIR", DEFAULT_INCOMING)),
        processed_dir=Path(env.get("PROCESSED_DIR", DEFAULT_PROCESSED)),
        # Floor the poll interval: a zero or negative override would busy-loop the watcher.
        poll_interval=max(1.0, float(env.get("INGEST_POLL_SECONDS", "30"))),
        # Ingestion sanity thresholds, measured against the previous Stocklist's in-stock SKUs. A
        # parse is held in incoming (not reconciled) when it keeps fewer than this fraction of the
        # prior in-stock rows, or when reconciling it would zero more than the zeroed fraction of
        # them — either is the shape of a partial or column-shifted parse, not a real restock swing.
        ingest_min_row_fraction=float(env.get("INGEST_MIN_ROW_FRACTION", "0.8")),
        ingest_max_zeroed_fraction=float(env.get("INGEST_MAX_ZEROED_FRACTION", "0.5")),
        # A retail price at or above this is absurd for livestock and flags the Item in the report.
        ingest_max_retail_price=float(env.get("INGEST_MAX_RETAIL_PRICE", "100000")),
        log_level=env.get("LOG_LEVEL", "INFO"),
        host=env.get("HOST", "127.0.0.1"),
        port=int(env.get("PORT", "8000")),
        litestream_replica_url=env.get("LITESTREAM_REPLICA_URL"),
        otel_endpoint=env.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        cloud_ingestion=_flag(env.get("FISHPAGE_CLOUD_INGEST")),
        enrichment_enabled=_flag(env.get("ENRICHMENT_ENABLED")),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY"),
        images_enabled=_flag(env.get("FISHPAGE_IMAGES_ENABLED")),
        # The long-edge cap optimization downscales every stored image to; 1024 px keeps a card's
        # ~400 px render crisp without holding the multi-megapixel phone original.
        image_max_dimension=int(env.get("IMAGE_MAX_DIMENSION", "1024")),
        r2_images_bucket=env.get("R2_IMAGES_BUCKET"),
        r2_images_endpoint=env.get("R2_IMAGES_ENDPOINT"),
        r2_images_access_key_id=env.get("R2_IMAGES_ACCESS_KEY_ID"),
        r2_images_secret_access_key=env.get("R2_IMAGES_SECRET_ACCESS_KEY"),
    )


def _flag(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}
