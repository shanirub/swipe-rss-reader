"""The SSRF guard and the guarded fetcher (swipe_rss/safe_fetch.py)."""

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpcore
import pytest

from swipe_rss import safe_fetch
from swipe_rss.safe_fetch import BlockedAddress, FetchError, GuardedBackend, check_address, fetch_html

# --- the address check ---


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "0.0.0.0",  # unspecified
        "10.0.0.1",  # private
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # link-local, cloud metadata endpoint
        "100.64.0.1",  # Tailscale / CGNAT
        "100.73.33.21",  # this server's own tailnet address
        "224.0.0.1",  # multicast
        "239.1.1.1",  # multicast
        "240.0.0.1",  # reserved
        "ff02::1",  # IPv6 multicast
        "ff0e::1",  # IPv6 global-scope multicast
        "255.255.255.255",
        "::1",
        "fe80::1",  # link-local
        "fe80::1%eth0",  # with zone index
        "fd7a:115c:a1e0::1",  # Tailscale IPv6
        "fc00::1",  # unique local
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "64:ff9b::a00:1",  # NAT64 of 10.0.0.1
        "2002:a00:1::1",  # 6to4 of 10.0.0.1
    ],
)
def test_non_public_addresses_are_blocked(address):
    with pytest.raises(BlockedAddress):
        check_address(address)


@pytest.mark.parametrize("address", ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111", "::ffff:1.1.1.1"])
def test_public_addresses_are_allowed(address):
    check_address(address)


# --- the guarded connection ---


def fake_dns(monkeypatch, *addresses):
    def getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def no_real_connections(monkeypatch) -> list:
    """Record connect attempts instead of opening sockets, so no test can reach the network."""
    seen = []
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda self, host, port, *a, **k: seen.append(host))
    return seen


def test_any_bad_address_blocks_the_connection(monkeypatch):
    fake_dns(monkeypatch, "93.184.216.34", "10.0.0.1")  # one public, one private record
    seen = no_real_connections(monkeypatch)
    with pytest.raises(BlockedAddress):
        GuardedBackend().connect_tcp("mixed.example", 80)
    assert seen == []  # refused before any connection attempt


def test_connection_goes_to_the_checked_address(monkeypatch):
    # No second DNS lookup: the socket is opened to the IP that passed the check.
    fake_dns(monkeypatch, "93.184.216.34")
    seen = no_real_connections(monkeypatch)
    GuardedBackend().connect_tcp("public.example", 443)
    assert seen == ["93.184.216.34"]


def test_unresolvable_host_is_a_temporary_error(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(FetchError) as e:
        GuardedBackend().connect_tcp("nowhere.example", 80)
    assert not e.value.permanent


# --- end to end against a local HTTP server ---


class Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    requests: list = []  # headers of every request received
    drip: float = 0.0  # if set, the body is sent in 10 pieces with this pause in between

    def do_GET(self):
        self.requests.append(dict(self.headers))
        status, headers, body, delay = self.routes.get(self.path, (404, {}, b"", 0))
        time.sleep(delay)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value.replace("{port}", str(self.server.server_port)))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not self.drip:
            self.wfile.write(body)
            return
        step = max(1, len(body) // 10)
        for i in range(0, len(body), step):
            self.wfile.write(body[i : i + step])
            self.wfile.flush()
            time.sleep(self.drip)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Handler.routes, Handler.requests, Handler.drip = {}, [], 0.0
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


HTML = {"Content-Type": "text/html; charset=utf-8"}
PAGE = b"<html><body><article><p>Hello</p></article></body></html>"


def url(server, path="/"):
    return f"http://127.0.0.1:{server.server_port}{path}"


def allow_loopback_only(address):
    # Test check: lets the test reach 127.0.0.1, blocks everything else (e.g. 127.0.0.2).
    if address != "127.0.0.1":
        raise BlockedAddress(f"blocked address {address}")


def test_real_guard_refuses_a_local_server(server):
    # The default fetch path must really go through the guard: a local server is unreachable.
    Handler.routes = {"/": (200, HTML, PAGE, 0)}
    with pytest.raises(BlockedAddress):
        fetch_html(url(server))
    with pytest.raises(BlockedAddress):
        fetch_html(f"http://localhost:{server.server_port}/")


def test_fetches_html(server):
    Handler.routes = {"/": (200, HTML, PAGE, 0)}
    page = fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))
    assert page.body == PAGE and page.url == url(server)


def test_follows_redirects(server):
    Handler.routes = {"/old": (301, {"Location": "/new"}, b"", 0), "/new": (200, HTML, PAGE, 0)}
    page = fetch_html(url(server, "/old"), backend=GuardedBackend(allow_loopback_only))
    assert page.url == url(server, "/new")


def test_redirect_to_a_blocked_address_is_refused(server):
    # Every redirect hop goes through the guard, not only the first URL.
    Handler.routes = {"/": (302, {"Location": "http://127.0.0.2:{port}/"}, b"", 0)}
    with pytest.raises(BlockedAddress):
        fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))


@pytest.mark.parametrize("location", ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/"])
def test_redirect_to_another_scheme_is_refused(server, location):
    Handler.routes = {"/": (302, {"Location": location}, b"", 0)}
    with pytest.raises(FetchError) as e:
        fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))
    assert e.value.permanent


def test_redirect_loop_is_cut(server):
    Handler.routes = {"/": (302, {"Location": "/"}, b"", 0)}
    with pytest.raises(FetchError, match="redirects") as e:
        fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))
    assert e.value.permanent


@pytest.mark.parametrize("scheme_url", ["file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)"])
def test_non_http_urls_are_refused(scheme_url):
    with pytest.raises(FetchError) as e:
        fetch_html(scheme_url)
    assert e.value.permanent


@pytest.mark.parametrize(
    ("status", "permanent"),
    [(400, True), (404, True), (403, True), (410, True), (408, False), (429, False), (500, False), (503, False)],
)
def test_http_errors(server, status, permanent):
    Handler.routes = {"/": (status, HTML, b"", 0)}
    with pytest.raises(FetchError) as e:
        fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))
    assert e.value.permanent is permanent


@pytest.mark.parametrize("content_type", ["application/pdf", "image/png", "application/json", ""])
def test_non_html_is_refused(server, content_type):
    Handler.routes = {"/": (200, {"Content-Type": content_type} if content_type else {}, b"x", 0)}
    with pytest.raises(FetchError, match="not an HTML page") as e:
        fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))
    assert e.value.permanent


def test_compressed_response_is_refused(server):
    Handler.routes = {"/": (200, HTML | {"Content-Encoding": "gzip"}, b"\x1f\x8b...", 0)}
    with pytest.raises(FetchError, match="compressed"):
        fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))


def test_oversized_page_is_refused(server, monkeypatch):
    monkeypatch.setattr(safe_fetch, "MAX_BYTES", 100)
    Handler.routes = {"/": (200, HTML, b"x" * 101, 0)}
    with pytest.raises(FetchError, match="larger than") as e:
        fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))
    assert e.value.permanent


def test_slow_server_times_out(server, monkeypatch):
    monkeypatch.setattr(safe_fetch, "READ_TIMEOUT", 0.2)
    Handler.routes = {"/": (200, HTML, PAGE, 1.0)}
    with pytest.raises(FetchError) as e:
        fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))
    assert not e.value.permanent


# --- gaps found by mutmut ---


def test_blocked_address_is_always_permanent():
    assert BlockedAddress("x").permanent


def test_request_identifies_the_reader_and_refuses_compression(server):
    Handler.routes = {"/": (200, HTML, PAGE, 0)}
    fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))
    [headers] = Handler.requests
    lower = {k.lower(): v for k, v in headers.items()}
    assert lower["user-agent"] == safe_fetch.USER_AGENT
    assert lower["accept-encoding"] == "identity"
    assert "text/html" in lower["accept"]


class Stop(Exception):
    pass


def recording_backend(calls: list) -> GuardedBackend:
    class Recording(GuardedBackend):
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            calls.append({"host": host, "port": port, "timeout": timeout})
            raise Stop

    return Recording()


def test_connect_timeout_is_applied():
    calls = []
    with pytest.raises(Stop):
        fetch_html("http://news.example/a", backend=recording_backend(calls))
    assert calls[0]["timeout"] == safe_fetch.CONNECT_TIMEOUT


def test_guarded_backend_passes_the_timeout_on(monkeypatch):
    fake_dns(monkeypatch, "93.184.216.34")
    seen = []

    def record(self, host, port, timeout=None, *args, **kwargs):
        seen.append(timeout)

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", record)
    GuardedBackend().connect_tcp("public.example", 443, timeout=7.0)
    assert seen == [7.0]


def test_https_urls_are_allowed():
    calls = []
    with pytest.raises(Stop):  # reached the connection step: the scheme check let https through
        fetch_html("https://news.example/a", backend=recording_backend(calls))
    assert (calls[0]["host"], calls[0]["port"]) == ("news.example", 443)


def test_whole_body_is_kept_across_chunks(server):
    body = b"<html><body>" + b"x" * 300_000 + b"</body></html>"
    Handler.routes = {"/": (200, HTML, body, 0)}
    assert fetch_html(url(server), backend=GuardedBackend(allow_loopback_only)).body == body


def test_body_of_exactly_max_bytes_is_allowed(server, monkeypatch):
    monkeypatch.setattr(safe_fetch, "MAX_BYTES", 100)
    Handler.routes = {"/": (200, HTML, b"x" * 100, 0)}
    assert len(fetch_html(url(server), backend=GuardedBackend(allow_loopback_only)).body) == 100


def test_slow_drip_hits_the_overall_deadline(server, monkeypatch):
    # Each piece arrives within the read timeout, but the whole page takes too long.
    monkeypatch.setattr(safe_fetch, "TOTAL_DEADLINE", 0.3)
    Handler.routes = {"/": (200, HTML, b"x" * 1000, 0)}
    Handler.drip = 0.1
    with pytest.raises(FetchError, match="took longer") as e:
        fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))
    assert not e.value.permanent


def chain(n: int) -> dict:
    routes = {f"/r{i}": (302, {"Location": f"/r{i + 1}"}, b"", 0) for i in range(n)}
    routes[f"/r{n}"] = (200, HTML, PAGE, 0)
    return routes


def test_exactly_max_redirects_is_fine(server):
    Handler.routes = chain(safe_fetch.MAX_REDIRECTS)
    page = fetch_html(url(server, "/r0"), backend=GuardedBackend(allow_loopback_only))
    assert page.url.endswith(f"/r{safe_fetch.MAX_REDIRECTS}")


def test_one_redirect_too_many_is_refused(server):
    Handler.routes = chain(safe_fetch.MAX_REDIRECTS + 1)
    with pytest.raises(FetchError, match="redirects"):
        fetch_html(url(server, "/r0"), backend=GuardedBackend(allow_loopback_only))


def test_redirect_without_location_is_permanent(server):
    Handler.routes = {"/": (302, {}, b"", 0)}
    with pytest.raises(FetchError, match="without Location") as e:
        fetch_html(url(server), backend=GuardedBackend(allow_loopback_only))
    assert e.value.permanent


@pytest.mark.parametrize("bad_url", ["http://ex\x00ample.com/", "http://[::1/"])
def test_invalid_url_is_permanent(bad_url):
    # Both fail when parsed, before any DNS lookup or connection.
    with pytest.raises(FetchError, match="invalid URL") as e:
        fetch_html(bad_url)
    assert e.value.permanent


def test_connection_refused_is_temporary():
    with socket.socket() as s:  # a port that is certainly closed
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(FetchError) as e:
        fetch_html(f"http://127.0.0.1:{port}/", backend=GuardedBackend(allow_loopback_only))
    assert not e.value.permanent
