"""Test support for telemetry assertions.

The ``telemetry`` fixture installs in-memory readers through the very wiring the app uses, so a
test exercises the real recording path — parse a Stocklist, then read back the metrics and spans
the domain code actually emitted — rather than asserting against a mock.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fishpage import observability


class Telemetry:
    def __init__(self, metric_reader: InMemoryMetricReader, spans: InMemorySpanExporter):
        self._metric_reader = metric_reader
        self._spans = spans

    def _metrics(self) -> Iterator[Any]:
        """Every recorded metric, flattening the resource/scope nesting the reader returns."""
        data = self._metric_reader.get_metrics_data()
        for resource in data.resource_metrics if data else []:
            for scope in resource.scope_metrics:
                yield from scope.metrics

    def counter(self, name: str) -> float:
        """The summed value recorded to the counter/gauge ``name`` across all attribute sets."""
        # Counters and gauges report NumberDataPoints; histogram points (no plain .value) are not
        # among the instruments here.
        return sum((value for _, value in self.points(name)), 0.0)

    def points(self, name: str) -> list[tuple[dict[str, object], float]]:
        """Every data point recorded to ``name`` as an (attributes, value) pair.

        Lets a test assert not just the recorded total but the attribute sets carrying it — the
        cardinality discipline that keeps high-cardinality keys off a counter.
        """
        return [
            (dict(point.attributes), getattr(point, "value", 0))
            for metric in self._metrics()
            if metric.name == name
            for point in metric.data.data_points
        ]

    def metric_names(self) -> set[str]:
        """The names of every metric that has at least one recorded data point."""
        return {metric.name for metric in self._metrics()}

    def span_names(self) -> list[str]:
        return [span.name for span in self._spans.get_finished_spans()]


@pytest.fixture
def telemetry():
    metric_reader = InMemoryMetricReader()
    spans = InMemorySpanExporter()
    observability._install(
        metric_readers=[metric_reader],
        span_processors=[SimpleSpanProcessor(spans)],
    )
    yield Telemetry(metric_reader, spans)
    observability._install(metric_readers=[], span_processors=[])
