"""Fetching third-party URLs safely: the SSRF guard (PROJECT_PLAN.md §3 Content extraction).

Saved links come from feed content and from client snapshots, so they are untrusted. With
the default allow-all tailnet ACL this guard is the only barrier between the server and the
other tailnet devices.

How it works:
- Only http and https; redirects are followed by hand (at most MAX_REDIRECTS), and every hop
  goes through the same checks.
- The check happens where the socket is opened (GuardedBackend.connect_tcp): the hostname is
  resolved once, EVERY resolved address must be public, and the connection goes to that same
  checked address. There is no second DNS lookup, so a hostname can't pass the check with a
  public address and then connect to a private one (DNS rebinding). TLS still verifies the
  certificate against the hostname.
- No proxies: httpcore ignores proxy environment variables.
- Bounded: connect/read timeouts, an overall deadline and a maximum body size.

Never fetch saved links any other way (e.g. trafilatura.fetch_url): that would skip the guard.
"""

import ipaddress
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpcore
import httpx

from swipe_rss.fetcher import USER_AGENT

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 15.0
TOTAL_DEADLINE = 30.0
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
HTML_TYPES = {"text/html", "application/xhtml+xml"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}

# Not "global", but listed explicitly: the tailnet (required by the plan) and IPv6 prefixes
# that embed an IPv4 address (NAT64, 6to4), which could otherwise smuggle in a private one.
BLOCKED_NETWORKS = [
    ipaddress.ip_network("100.64.0.0/10"),  # Tailscale IPv4 (CGNAT range)
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),  # Tailscale IPv6
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64 well-known prefix
    ipaddress.ip_network("64:ff9b:1::/48"),  # NAT64 local-use prefix
    ipaddress.ip_network("2002::/16"),  # 6to4
]


class FetchError(Exception):
    """A fetch that failed. `permanent` means retrying the same URL won't help."""

    def __init__(self, message: str, *, permanent: bool) -> None:
        super().__init__(message)
        self.permanent = permanent


class BlockedAddress(FetchError):
    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


def check_address(address: str) -> None:
    """Raise BlockedAddress unless `address` is a public (globally routable) IP."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop an IPv6 zone index like %eth0
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped  # ::ffff:10.0.0.1 is 10.0.0.1
    # is_global alone is not enough: Python counts e.g. multicast 224.0.0.1 as global.
    not_public = (
        not ip.is_global
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_unspecified
    )
    if not_public or any(ip in net for net in BLOCKED_NETWORKS if net.version == ip.version):
        raise BlockedAddress(f"blocked address {ip}")


class GuardedBackend(httpcore.SyncBackend):
    """Opens connections only to checked public addresses (see module docstring)."""

    def __init__(self, check: Callable[[str], None] = check_address) -> None:
        self._check = check

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as e:
            raise FetchError(f"cannot resolve {host}: {e}", permanent=False) from e
        addresses = [info[4][0] for info in infos]
        for address in addresses:  # every address, so a mix of good and bad records is refused
            self._check(address)
        # Connect to the address we just checked, not to the hostname (no second lookup).
        return super().connect_tcp(addresses[0], port, timeout, local_address, socket_options)


@dataclass(frozen=True)
class Page:
    url: str  # final URL after redirects
    body: bytes


def fetch_html(url: str, *, backend: httpcore.SyncBackend | None = None) -> Page:
    """Fetch an HTML page through the SSRF guard. Raises FetchError."""
    deadline = time.monotonic() + TOTAL_DEADLINE
    timeouts = {"connect": CONNECT_TIMEOUT, "read": READ_TIMEOUT, "write": READ_TIMEOUT, "pool": CONNECT_TIMEOUT}
    headers = [
        (b"User-Agent", USER_AGENT.encode()),
        (b"Accept", b"text/html,application/xhtml+xml"),
        (b"Accept-Encoding", b"identity"),  # httpcore does not decompress
    ]
    try:
        current = httpx.URL(url)
        with httpcore.ConnectionPool(network_backend=backend or GuardedBackend()) as pool:
            for _ in range(MAX_REDIRECTS + 1):
                if current.scheme not in ("http", "https"):
                    raise FetchError(f"unsupported URL scheme {current.scheme!r}", permanent=True)
                with pool.stream("GET", str(current), headers=headers, extensions={"timeout": timeouts}) as response:
                    if response.status in REDIRECT_STATUSES:
                        location = _header(response, b"location")
                        if not location:
                            raise FetchError(f"HTTP {response.status} without Location", permanent=True)
                        current = current.join(location)
                        continue
                    if response.status != 200:
                        permanent = 400 <= response.status < 500 and response.status not in (408, 429)
                        raise FetchError(f"HTTP {response.status}", permanent=permanent)
                    content_type = (_header(response, b"content-type") or "").split(";")[0].strip().lower()
                    if content_type not in HTML_TYPES:
                        raise FetchError(f"not an HTML page ({content_type or 'no content type'})", permanent=True)
                    if (_header(response, b"content-encoding") or "identity").lower() != "identity":
                        raise FetchError("compressed response despite Accept-Encoding: identity", permanent=True)
                    body = bytearray()
                    for chunk in response.iter_stream():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise FetchError(f"page larger than {MAX_BYTES} bytes", permanent=True)
                        if time.monotonic() > deadline:
                            raise FetchError(f"took longer than {TOTAL_DEADLINE:.0f} s", permanent=False)
                    return Page(url=str(current), body=bytes(body))
            raise FetchError(f"more than {MAX_REDIRECTS} redirects", permanent=True)
    except FetchError:
        raise
    except httpx.InvalidURL as e:
        raise FetchError(f"invalid URL: {e}", permanent=True) from e
    except httpcore.UnsupportedProtocol as e:
        raise FetchError(f"unsupported protocol: {e}", permanent=True) from e
    except (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError) as e:
        raise FetchError(f"{type(e).__name__}: {e}", permanent=False) from e


def _header(response: httpcore.Response, name: bytes) -> str | None:
    for key, value in response.headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None
