import io
import json
import logging

import pytest

from fishpage import observability
from fishpage.config import load_settings


@pytest.fixture
def restore_logging():
    """Snapshot and restore the ``fishpage`` logger so configure_logging can't leak global state."""
    logger = logging.getLogger("fishpage")
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    yield
    logger.handlers[:] = saved_handlers
    logger.setLevel(saved_level)


def test_info_logs_surface_after_configuration(restore_logging):
    stream = io.StringIO()
    observability.configure_logging(load_settings({}), stream=stream)

    # The root logger defaults to WARNING, dropping every fishpage INFO line before it can be
    # rendered. Configuration raises the fishpage logger to INFO so the narrative surfaces.
    logging.getLogger("fishpage.drainer").info("Enriched 3 Item(s)")

    assert "Enriched 3 Item(s)" in stream.getvalue()


def test_log_level_env_var_raises_the_threshold(restore_logging):
    stream = io.StringIO()
    observability.configure_logging(load_settings({"LOG_LEVEL": "WARNING"}), stream=stream)

    # LOG_LEVEL overrides the INFO default; a deployment can quiet the narrative back to warnings.
    logging.getLogger("fishpage.drainer").info("Enriched 3 Item(s)")

    assert stream.getvalue() == ""


def test_console_output_is_json_with_message_level_and_logger(restore_logging):
    stream = io.StringIO()
    observability.configure_logging(load_settings({}), stream=stream)

    logging.getLogger("fishpage.drainer").info("Enriched 3 Item(s)")

    # Structured lines, so a log consumer indexes fields instead of regexing a message string.
    line = json.loads(stream.getvalue())
    assert line["message"] == "Enriched 3 Item(s)"
    assert line["level"] == "INFO"
    assert line["logger"] == "fishpage.drainer"


def test_extra_fields_surface_as_structured_json_keys(restore_logging):
    stream = io.StringIO()
    observability.configure_logging(load_settings({}), stream=stream)

    # Structured fields ride along via the stdlib `extra=` mechanism, so downstream consumers
    # index the SKU rather than parsing it back out of the message.
    logging.getLogger("fishpage.drainer").info("Enriched Item", extra={"sku": "110042", "count": 3})

    line = json.loads(stream.getvalue())
    assert line["sku"] == "110042"
    assert line["count"] == 3


def test_info_records_reach_a_root_handler_once_the_level_is_raised(restore_logging):
    # The OTLP log handler is attached to the root logger on the export path; a fishpage INFO
    # record only reaches it if the fishpage logger's level admits it and it propagates upward.
    received: list[logging.LogRecord] = []

    class Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            received.append(record)

    root = logging.getLogger()
    sink = Sink()
    root.addHandler(sink)
    try:
        observability.configure_logging(load_settings({}), stream=io.StringIO())
        logging.getLogger("fishpage.drainer").info("Enriched 3 Item(s)")
    finally:
        root.removeHandler(sink)

    assert [r.getMessage() for r in received] == ["Enriched 3 Item(s)"]


def test_exception_logs_carry_the_traceback(restore_logging):
    stream = io.StringIO()
    observability.configure_logging(load_settings({}), stream=stream)

    # The drainer and ingest loops survive failures with `_log.exception(...)`; the traceback has
    # to ride along in the structured line or those errors lose their diagnosis.
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("fishpage.drainer").exception("Drain pass failed")

    line = json.loads(stream.getvalue())
    assert line["message"] == "Drain pass failed"
    assert "ValueError: boom" in line["exception"]
