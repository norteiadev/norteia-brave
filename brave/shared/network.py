"""Is this host online? A TCP connect to public anycast resolvers: no DNS, no HTTP.

Tells an internet outage (every request fails, waiting is the fix) from a request's own
transport error (one host reset us, retrying is the fix).
"""

import socket

_PROBES = (("1.1.1.1", 443), ("8.8.8.8", 443))


def is_online(timeout: float = 3.0) -> bool:
    """True when any probe accepts a TCP connection within ``timeout`` seconds."""
    for host, port in _PROBES:
        try:
            socket.create_connection((host, port), timeout=timeout).close()
        except OSError:
            continue
        return True
    return False
