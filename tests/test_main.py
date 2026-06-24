import socket

import pytest

from fishpage.__main__ import listening_socket


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
