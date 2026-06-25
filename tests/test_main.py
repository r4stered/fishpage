import socket
import threading

import pytest

import fishpage.__main__ as main
from fishpage.__main__ import listening_socket, start_drainer
from fishpage.config import load_settings
from fishpage.store import open_store


@pytest.mark.skipif(not socket.has_ipv6, reason="IPv6 stack unavailable")
def test_ipv6_host_serves_the_ipv4_loopback_too():
    # The cloud binds HOST=::; the Fly Machine health check probes /healthz over IPv4 loopback.
    # The socket must answer IPv4 as well, or the check fails even though the app is up on IPv6.
    sock = listening_socket("::", 0)
    try:
        port = sock.getsockname()[1]
        socket.create_connection(("127.0.0.1", port), timeout=2).close()  # IPv4 reaches it
    finally:
        sock.close()


def test_ipv4_host_binds_a_plain_socket():
    sock = listening_socket("127.0.0.1", 0)
    try:
        port = sock.getsockname()[1]
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    finally:
        sock.close()


def test_drainer_stays_off_without_enrichment_configured(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    spawned = []

    result = start_drainer(conn, load_settings({}), spawn=lambda *a: spawned.append(a))

    # Opt-in and default-off: an empty environment yields no enricher, so no drainer is spawned —
    # `just run` and the test suite start no background enrichment and need no credential.
    assert result is None
    assert spawned == []


def test_drainer_starts_when_enrichment_is_enabled_and_keyed(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    spawned = []

    start_drainer(
        conn,
        load_settings({"ENRICHMENT_ENABLED": "1", "ANTHROPIC_API_KEY": "sk-ant-test"}),
        spawn=lambda *a: spawned.append(a) or "thread",
    )

    # With the flag and a key the drainer is wired up exactly once, against the same connection the
    # app serves from — so it drains the live catalog's queue.
    assert len(spawned) == 1
    assert spawned[0][0] is conn


def test_start_drainer_launches_a_daemon_thread_by_default(tmp_path, monkeypatch):
    conn = open_store(tmp_path / "fishpage.db")
    ran = threading.Event()
    # Stub the forever-loop so the spawned thread runs once and exits instead of polling forever.
    monkeypatch.setattr(main, "run_drainer", lambda *a, **k: ran.set())
    settings = load_settings({"ENRICHMENT_ENABLED": "1", "ANTHROPIC_API_KEY": "sk-ant-test"})

    thread = start_drainer(conn, settings)

    # The default spawn runs the drain loop on a background daemon thread — daemon so it never
    # blocks process shutdown — against the live connection.
    assert isinstance(thread, threading.Thread)
    assert thread.daemon
    thread.join(timeout=2)
    assert ran.is_set()
