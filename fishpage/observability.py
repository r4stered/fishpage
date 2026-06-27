"""The one place that knows about OpenTelemetry.

Every other module records telemetry through the narrow domain-language helpers here
(:func:`record_rows_parsed`, :func:`record_reuse_flag`, …) and never imports OpenTelemetry
directly. This module owns the tracer and meter providers and the domain instruments built from
them; on the export path it also forwards stdlib logs through an OTLP handler.

Export is opt-in. :func:`configure` attaches OTLP/HTTP exporters only when an OTLP endpoint is
set in the environment; with no endpoint — bare ``just run`` and CI — it installs providers with
no exporters, so the instruments still record in-process but nothing leaves the box and the app
stays credential-free. The instruments are always live, so callers record unconditionally without
guarding on whether export is on.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, TextIO

from opentelemetry.metrics import CallbackOptions, Counter, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

from fishpage.config import Settings

if TYPE_CHECKING:
    from fishpage.models import Provenance

# Stamped on every signal so the app is identifiable as one service in Grafana, where traces,
# metrics, and logs from many sources land together.
_INSTRUMENTING_SCOPE = "fishpage"
_RESOURCE = Resource.create({SERVICE_NAME: "fishpage"})


@dataclass(frozen=True)
class _Instruments:
    rows_parsed: Counter
    rows_skipped: Counter
    reuse_flags: Counter
    monotonicity_skips: Counter
    ingest_report: Counter
    ingest_held: Counter
    images_optimized: Counter
    image_original_bytes: Counter
    image_optimized_bytes: Counter
    image_optimize_errors: Counter
    image_acquired: Counter
    enrichment_tokens: Counter
    enrichment_calls: Counter
    enrichment_species_unresolved: Counter
    enrichment_classifier_unknown: Counter
    enrichment_overrides: Counter


_meter_provider: MeterProvider
_tracer_provider: TracerProvider
_instruments: _Instruments


def configure(settings: Settings) -> bool:
    """Install the telemetry providers, wiring OTLP export when an endpoint is configured.

    Returns ``True`` when an exporter was started (an OTLP endpoint is set) and ``False`` when
    export is off. The OTLP exporter connects lazily on first flush, so configuring against an
    unreachable endpoint neither blocks nor raises here.
    """
    if settings.otel_endpoint is None:
        _install(metric_readers=[], span_processors=[])
        return False
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    _install(
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
        span_processors=[BatchSpanProcessor(OTLPSpanExporter())],
    )
    _export_logs_via_otlp()
    return True


def _export_logs_via_otlp() -> None:
    """Ship the stdlib logging the app already emits to the OTLP endpoint.

    The modules log through ``logging`` (the ingest warnings, the reuse-guard line); attaching an
    OTLP handler to the root logger forwards those records as OTel logs without rewriting any call
    site. Kept off the no-endpoint path so local runs and CI still log only to the console.
    """
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    provider = LoggerProvider(resource=_RESOURCE)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    logging.getLogger().addHandler(LoggingHandler(logger_provider=provider))


def configure_logging(settings: Settings, *, stream: TextIO | None = None) -> None:
    """Raise the ``fishpage`` logger to INFO and render its records to ``stream`` as JSON.

    The root logger defaults to WARNING, so every ``_log.info(...)`` the domain code emits is
    dropped at the source before any handler — console or OTLP — can see it. Lifting the
    ``fishpage`` logger's own level to INFO (overridable via ``LOG_LEVEL``) lets those records
    through; raising it on the package logger rather than root keeps third-party noise quiet.
    """
    logger = logging.getLogger(_INSTRUMENTING_SCOPE)
    logger.setLevel(settings.log_level)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(_JsonFormatter())
    logger.handlers[:] = [handler]


# The attributes the stdlib stamps on every record; anything else in a record's __dict__ arrived
# through `extra={...}` at the call site and is promoted to a top-level JSON field.
_STANDARD_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({})))


class _JsonFormatter(logging.Formatter):
    """Render a record as a single JSON line: the standard fields, the message, and any extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            {k: v for k, v in record.__dict__.items() if k not in _STANDARD_RECORD_ATTRS}
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


@contextmanager
def span(name: str) -> Iterator[None]:
    """Open a manual span named ``name`` for the enclosed work.

    Lets parse and ingest carry their own spans without importing OpenTelemetry at the call site,
    and nests them under the auto-instrumented request span when one is active.
    """
    tracer = _tracer_provider.get_tracer(_INSTRUMENTING_SCOPE)
    with tracer.start_as_current_span(name):
        yield


def instrument_fastapi(app) -> None:
    """Auto-instrument a FastAPI app so every request emits a server span.

    Binds to this module's tracer provider rather than the global one, so request spans land in
    the same place as the manual parse/ingest spans — including the in-memory exporter tests read.
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=_tracer_provider)


def record_rows_parsed(count: int) -> None:
    """Record how many Stocklist rows a parse kept as Items."""
    _instruments.rows_parsed.add(count)


def record_rows_skipped(count: int) -> None:
    """Record how many Stocklist rows a parse dropped as unparseable."""
    _instruments.rows_skipped.add(count)


def record_reuse_flag() -> None:
    """Record that the reuse guard flagged one SKU reappearing under a different name."""
    _instruments.reuse_flags.add(1)


def record_monotonicity_skip() -> None:
    """Record that one dropped Stocklist was held back for being no newer than the catalog."""
    _instruments.monotonicity_skips.add(1)


def record_ingestion_report(
    *, new: int, returned: int, zeroed: int, price_changed: int, flagged: int
) -> None:
    """Record the per-ingest report counts, each tagged by which kind of change it is.

    One counter carries all five kinds on a ``kind`` attribute (low cardinality), so a dashboard
    reads new/returned/zeroed/price-changed/flagged off the same series. A sudden spike in
    ``zeroed`` or ``flagged`` is the early signal of a partial or column-shifted parse that slipped
    the hard hold.
    """
    _instruments.ingest_report.add(new, {"kind": "new"})
    _instruments.ingest_report.add(returned, {"kind": "returned"})
    _instruments.ingest_report.add(zeroed, {"kind": "zeroed"})
    _instruments.ingest_report.add(price_changed, {"kind": "price_changed"})
    _instruments.ingest_report.add(flagged, {"kind": "flagged"})


def record_ingest_held() -> None:
    """Record that one parse was held in incoming as structurally implausible, not reconciled.

    This is the signal the held-parse alert keys on: any increase means a Stocklist parsed to a
    shape implausible enough to suspect a partial or column-shifted extraction, so reconciling it
    would have corrupted the catalog and zeroed live SKUs.
    """
    _instruments.ingest_held.add(1)


def record_image_optimized(bytes_in: int, bytes_out: int, *, provenance: Provenance) -> None:
    """Record that one image flowed through the optimization seam.

    Two separate byte counters rather than one "bytes saved": WebP can occasionally re-encode a
    tiny source larger, and a monotonic counter can't carry a negative saving. Space saved and the
    compression ratio are derived downstream from the two totals.

    ``provenance`` (manual/wikimedia) is the only attribute; the SKU and Uploader are
    high-cardinality and ride the log event, never a counter.
    """
    attributes = {"provenance": provenance.value}
    _instruments.images_optimized.add(1, attributes)
    _instruments.image_original_bytes.add(bytes_in, attributes)
    _instruments.image_optimized_bytes.add(bytes_out, attributes)


def record_image_optimize_error(*, provenance: Provenance) -> None:
    """Record that one input failed to decode at the optimization seam.

    Dashboard-only signal — a human uploading a bad file is expected noise, not an alert. The
    detail of *which* upload failed rides the exception log; ``provenance`` is the only attribute.
    """
    _instruments.image_optimize_errors.add(1, {"provenance": provenance.value})


def record_image_acquired(*, outcome: str) -> None:
    """Record one auto-image acquisition attempt, tagged by its ``outcome``.

    Recorded only when the gate actually queries the source — a resolved, non-strain Item with no
    manual image — so by-design skips never reach it. ``stored`` is a landed image; ``none`` is the
    image honesty gap (a resolved species with no commercial-free photo), the early signal that
    source coverage is degrading or the licence filter is too strict; ``failed`` is a fetch that
    raised — the outbound source can fail and rate-limit. The dashboard derives the gap and failure
    rates from these outcomes. The SKU is high-cardinality and rides the per-image log, not this
    counter.
    """
    _instruments.image_acquired.add(1, {"outcome": outcome})


def record_enrichment_tokens(input_tokens: int, output_tokens: int, *, model: str) -> None:
    """Record the token spend of one Enrichment call, split by direction and tagged by model.

    Tokens are the durable primitive — dollars are derived downstream in the dashboard from a
    price variable, never from a price table in the app, so a reprice never touches this code. The
    ``model`` tag is stamped even though only the default tier is wired today, so the spend split is
    already in place if the cost-fallback model is ever added.

    The SKU is high-cardinality and rides the per-Item result log, never this counter.
    """
    _instruments.enrichment_tokens.add(input_tokens, {"direction": "input", "model": model})
    _instruments.enrichment_tokens.add(output_tokens, {"direction": "output", "model": model})


def record_enrichment_call(*, outcome: str, model: str) -> None:
    """Record that one Enrichment call finished, tagged by its ``outcome`` and ``model``.

    ``outcome`` is ``ok`` when the call returned a result and ``failed`` when it raised; split by
    model so the failure rate of each tier reads off the same counter. The dashboard derives the
    failure rate from the two outcomes rather than the app tracking it.
    """
    _instruments.enrichment_calls.add(1, {"outcome": outcome, "model": model})


def record_enrichment_species_unresolved() -> None:
    """Record that one call came back unable to resolve a species.

    A rising rate is the early signal that name-resolution is silently degrading — the honesty
    guardrail returning null where it once mapped a name.
    """
    _instruments.enrichment_species_unresolved.add(1)


def record_enrichment_classifier_unknown(*, classifier: str) -> None:
    """Record that one Classifier came back ``unknown``, tagged by which ``classifier`` it was.

    Tagging by Classifier makes the honesty guardrail observable per attribute, so a single
    Classifier degrading to mostly-``unknown`` shows up rather than hiding in an aggregate.
    """
    _instruments.enrichment_classifier_unknown.add(1, {"classifier": classifier})


def record_enrichment_override(*, classifier: str) -> None:
    """Record that a human accepted-corrected one Classifier, tagged by which ``classifier`` it was.

    A rising rate is direct evidence the ``ai-generated`` reads are not trusted — the quality
    signal that pairs with the honesty-gap counters. Recorded only when a correction is actually
    accepted, so a rejected or invalid override never inflates it.
    """
    _instruments.enrichment_overrides.add(1, {"classifier": classifier})


def track_catalog_freshness(
    conn: sqlite3.Connection,
    *,
    today: Callable[[], date] = date.today,
) -> None:
    """Register the observable gauge the staleness alert keys on.

    On every metric collection it reports how many days have passed since the newest Stocklist
    reconciled into ``conn`` — the signal that goes flat-then-climbing when nightly ingestion
    silently stops. An empty catalog (no ingest ever) reports no value, which Grafana reads as
    missing data and alerts on just the same.
    """
    # Imported here, not at module scope: store imports this module for its recorders, so a
    # top-level import back into store would close the cycle.
    from fishpage.store import latest_stocklist_date

    def observe(_options: CallbackOptions):
        latest = latest_stocklist_date(conn)
        if latest is None:
            return []
        return [Observation((today() - latest).days)]

    meter = _meter_provider.get_meter(_INSTRUMENTING_SCOPE)
    meter.create_observable_gauge(
        "fishpage.catalog.days_since_last_ingest",
        callbacks=[observe],
        unit="d",
        description="Days since the newest Stocklist was reconciled",
    )


def track_enrichment_queue_depth(conn: sqlite3.Connection) -> None:
    """Register the observable gauge that tracks how far behind the drainer is.

    On every metric collection it reports how many Items still have no enrichment row — the
    drainer's work queue. A queue that climbs and never drains is the signal that the drainer is
    wedged or the upstream call is failing. A never-populated catalog (no ingest ever) reports no
    value, which Grafana reads as missing data rather than a misleading zero; a populated catalog
    with nothing left to enrich reports a true 0, the drainer caught up.
    """
    # Imported here, not at module scope: store imports this module for its recorders, so a
    # top-level import back into store would close the cycle.
    from fishpage.store import catalog_is_empty, unenriched_count

    def observe(_options: CallbackOptions):
        if catalog_is_empty(conn):
            return []
        return [Observation(unenriched_count(conn))]

    meter = _meter_provider.get_meter(_INSTRUMENTING_SCOPE)
    meter.create_observable_gauge(
        "fishpage.enrichment.queue_depth",
        callbacks=[observe],
        unit="{item}",
        description="Items still awaiting enrichment",
    )


def _install(
    *,
    metric_readers: list[MetricReader],
    span_processors: list[SpanProcessor],
) -> None:
    """(Re)build the providers and the instruments hung off them.

    Kept separate from :func:`configure` so tests can install in-memory readers through the same
    path the real wiring uses, then assert the telemetry the domain code actually recorded.
    """
    global _meter_provider, _tracer_provider, _instruments
    _meter_provider = MeterProvider(resource=_RESOURCE, metric_readers=metric_readers)
    _tracer_provider = TracerProvider(resource=_RESOURCE)
    for processor in span_processors:
        _tracer_provider.add_span_processor(processor)

    meter = _meter_provider.get_meter(_INSTRUMENTING_SCOPE)
    _instruments = _Instruments(
        rows_parsed=meter.create_counter(
            "fishpage.stocklist.rows_parsed", unit="{row}", description="Stocklist rows kept"
        ),
        rows_skipped=meter.create_counter(
            "fishpage.stocklist.rows_skipped", unit="{row}", description="Stocklist rows dropped"
        ),
        reuse_flags=meter.create_counter(
            "fishpage.ingest.reuse_flags",
            unit="{flag}",
            description="SKUs flagged by the reuse guard",
        ),
        monotonicity_skips=meter.create_counter(
            "fishpage.ingest.monotonicity_skips",
            unit="{drop}",
            description="Stocklist drops held back for not being newer than the catalog",
        ),
        ingest_report=meter.create_counter(
            "fishpage.ingest.report",
            unit="{item}",
            description="Per-ingest change counts by kind: new, returned, zeroed, "
            "price_changed, flagged",
        ),
        ingest_held=meter.create_counter(
            "fishpage.ingest.held",
            unit="{drop}",
            description="Parses held in incoming as structurally implausible, not reconciled",
        ),
        images_optimized=meter.create_counter(
            "fishpage.image.optimized",
            unit="{image}",
            description="Images put through the optimization seam",
        ),
        image_original_bytes=meter.create_counter(
            "fishpage.image.original_bytes",
            unit="By",
            description="Total bytes of images before optimization",
        ),
        image_optimized_bytes=meter.create_counter(
            "fishpage.image.optimized_bytes",
            unit="By",
            description="Total bytes of images after optimization",
        ),
        image_optimize_errors=meter.create_counter(
            "fishpage.image.optimize_errors",
            unit="{error}",
            description="Images that failed to decode at the optimization seam",
        ),
        image_acquired=meter.create_counter(
            "fishpage.image.acquired",
            unit="{acquisition}",
            description="Auto-image source acquisitions, by outcome: stored, none, failed",
        ),
        enrichment_tokens=meter.create_counter(
            "fishpage.enrichment.tokens",
            unit="{token}",
            description="Tokens spent on Enrichment Claude calls, by direction and model",
        ),
        enrichment_calls=meter.create_counter(
            "fishpage.enrichment.calls",
            unit="{call}",
            description="Enrichment calls drained, by outcome and model",
        ),
        enrichment_species_unresolved=meter.create_counter(
            "fishpage.enrichment.species_unresolved",
            unit="{call}",
            description="Enrichment calls that resolved no species",
        ),
        enrichment_classifier_unknown=meter.create_counter(
            "fishpage.enrichment.classifier_unknown",
            unit="{classifier}",
            description="Classifiers that resolved to unknown, by classifier",
        ),
        enrichment_overrides=meter.create_counter(
            "fishpage.enrichment.overrides",
            unit="{override}",
            description="Human Classifier corrections accepted, by classifier",
        ),
    )


# Install no-export providers at import so the instruments are always live: domain code records
# unconditionally, and a process that never calls configure() (a test, a bare import) is a no-op
# rather than an AttributeError.
_install(metric_readers=[], span_processors=[])
