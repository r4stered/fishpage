from pathlib import Path

import fishpage
from fishpage.config import (
    DEFAULT_DB,
    DEFAULT_INCOMING,
    DEFAULT_PDF,
    DEFAULT_PROCESSED,
    load_settings,
)


def test_the_seed_stocklist_ships_inside_the_installed_package():
    # The deploy image installs only the wheel, with no source tree, and seeds the catalog from
    # DEFAULT_PDF on boot. So the sample Stocklist must live under the package directory (like the
    # templates and static assets) rather than alongside the tests, or it is absent in the wheel.
    package_dir = Path(fishpage.__file__).resolve().parent

    assert DEFAULT_PDF.is_file()
    assert DEFAULT_PDF.resolve().is_relative_to(package_dir)


def test_cloud_dependencies_default_off_with_an_empty_environment():
    settings = load_settings({})

    assert settings.litestream_replica_url is None
    assert settings.otel_endpoint is None
    assert settings.cloud_ingestion is False
    assert settings.enrichment_enabled is False
    assert settings.anthropic_api_key is None
    assert settings.images_enabled is False
    assert settings.r2_images_bucket is None
    assert settings.r2_images_endpoint is None


def test_local_defaults_apply_when_the_environment_is_empty():
    settings = load_settings({})

    assert settings.pdf_path == DEFAULT_PDF
    assert settings.db_path == DEFAULT_DB
    assert settings.incoming_dir == DEFAULT_INCOMING
    assert settings.processed_dir == DEFAULT_PROCESSED
    assert settings.poll_interval == 30.0
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


def test_environment_overrides_each_local_value():
    settings = load_settings(
        {
            "STOCKLIST_PDF": "/data/sl.pdf",
            "FISHPAGE_DB": "/data/fishpage.db",
            "INCOMING_DIR": "/data/in",
            "PROCESSED_DIR": "/data/out",
            "INGEST_POLL_SECONDS": "5",
            "HOST": "0.0.0.0",
            "PORT": "9000",
        }
    )

    assert settings.pdf_path == Path("/data/sl.pdf")
    assert settings.db_path == Path("/data/fishpage.db")
    assert settings.incoming_dir == Path("/data/in")
    assert settings.processed_dir == Path("/data/out")
    assert settings.poll_interval == 5.0
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000


def test_ingest_sanity_thresholds_default_and_override():
    defaults = load_settings({})
    assert defaults.ingest_min_row_fraction == 0.8
    assert defaults.ingest_max_zeroed_fraction == 0.5
    assert defaults.ingest_max_retail_price == 100000

    tuned = load_settings(
        {
            "INGEST_MIN_ROW_FRACTION": "0.6",
            "INGEST_MAX_ZEROED_FRACTION": "0.3",
            "INGEST_MAX_RETAIL_PRICE": "5000",
        }
    )
    assert tuned.ingest_min_row_fraction == 0.6
    assert tuned.ingest_max_zeroed_fraction == 0.3
    assert tuned.ingest_max_retail_price == 5000


def test_poll_interval_is_floored_so_a_low_override_cannot_busy_loop_the_watcher():
    assert load_settings({"INGEST_POLL_SECONDS": "0"}).poll_interval == 1.0
    assert load_settings({"INGEST_POLL_SECONDS": "-5"}).poll_interval == 1.0
    assert load_settings({"INGEST_POLL_SECONDS": "0.25"}).poll_interval == 1.0


def test_each_cloud_dependency_switches_on_from_its_environment_variable():
    settings = load_settings(
        {
            "LITESTREAM_REPLICA_URL": "s3://fishpage/db",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example:4317",
            "FISHPAGE_CLOUD_INGEST": "1",
        }
    )

    assert settings.litestream_replica_url == "s3://fishpage/db"
    assert settings.otel_endpoint == "https://otel.example:4317"
    assert settings.cloud_ingestion is True


def test_enrichment_switches_on_from_its_environment_variables():
    settings = load_settings(
        {
            "ENRICHMENT_ENABLED": "1",
            "ANTHROPIC_API_KEY": "sk-ant-test",
        }
    )

    assert settings.enrichment_enabled is True
    assert settings.anthropic_api_key == "sk-ant-test"


def test_cloud_ingestion_flag_reads_common_truthy_and_falsy_spellings():
    assert load_settings({"FISHPAGE_CLOUD_INGEST": "true"}).cloud_ingestion is True
    assert load_settings({"FISHPAGE_CLOUD_INGEST": "YES"}).cloud_ingestion is True
    assert load_settings({"FISHPAGE_CLOUD_INGEST": "0"}).cloud_ingestion is False
    assert load_settings({"FISHPAGE_CLOUD_INGEST": ""}).cloud_ingestion is False
