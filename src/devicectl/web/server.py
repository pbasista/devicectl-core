"""The local web server: routing, guards, the event stream and the static app.

Deliberately the standard library and nothing else.  Everything behind it is
synchronous -- one connection on one worker thread -- so a thread-per-
connection server is the shape that fits: an event stream is a handler
thread blocking on a queue, and there is no async bridge to build.

What the transport layer has to get right, since there is no framework
doing it for us:

* **Streaming.** SSE responses carry no ``Content-Length``, so they close
  the connection when they end and say so up front.
* **Who may connect.** Off loopback the server requires a token, which
  arrives once in the URL and is then kept in a ``SameSite=Strict`` cookie.
* **DNS rebinding.** A page on the open internet can point a name it
  controls at ``127.0.0.1`` and drive a local server from the victim's
  browser.  Names are therefore refused outright: the ``Host`` header must
  be an IP literal, ``localhost``, or a name the user allowed explicitly.
* **Cross-site POSTs.** Cookie auth alone would let another origin submit a
  form here, so every write also needs the :data:`UI_HEADER`, which a
  cross-origin form cannot set without a preflight we never answer.

A local server that can reconfigure a device and flash its firmware is
worth attacking, which is why all four are here rather than only the first.

Everything a program has to say for itself is in :class:`Branding`; nothing
else here knows what kind of device is on the other end.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import os
import secrets
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from devicectl.cli.exits import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_OK
from devicectl.errors import DeviceError, format_traceback
from devicectl.web.agents import name_watchers
from devicectl.web.events import HEARTBEAT_INTERVAL_S, Broadcaster
from devicectl.web.http import (
    HTTP_BAD_REQUEST,
    HTTP_FORBIDDEN,
    HTTP_METHOD_NOT_ALLOWED,
    HTTP_NOT_FOUND,
    HTTP_OK,
    HTTP_PAYLOAD_TOO_LARGE,
    HTTP_SEE_OTHER,
    HTTP_SERVER_ERROR,
    HTTP_UNAVAILABLE,
    ApiError,
    Request,
    Response,
    Route,
    parse_query,
)

DEFAULT_HOST = "127.0.0.1"

# The header a same-origin fetch sets and a cross-origin form cannot.  It is
# the same in every program on purpose: its value is that a non-simple header
# forces a preflight nothing here answers, and a per-app spelling would buy
# nothing while reaching into the JS.
UI_HEADER = "X-UI-Request"

# The token, for a caller that has no cookie jar -- a script, or `curl`.
TOKEN_HEADER = "X-Device-Token"

# Concurrent event streams we will hold open.  Each costs a thread; a
# handful of browsers with a tab each stays far below this, and the cap
# keeps a client that reconnects in a loop from exhausting the process.
MAX_STREAMS = 24

# How long a browser waits before reconnecting a stream that dropped.  Kept
# short deliberately: a tab left open from an earlier run is how a restart
# finds out there is already a window to raise instead of opening another.
STREAM_RETRY_MS = 1000

# How long a starting server waits for such a tab to come back before it
# gives up and opens a new one.
BROWSER_GRACE_S = 2.0

# How often that wait looks to see whether a tab has arrived.
BROWSER_POLL_S = 0.05

# Largest request body we will read at all.  Uploads are capped lower still
# by the API layer; this is only the guard against a body that never ends,
# so a program that takes larger images raises it in its `Branding`.
DEFAULT_MAX_BODY_BYTES = 64 * 1024 * 1024

# How often the accept loop looks up to see whether it has been shut down;
# this is the floor on how long Ctrl+C takes to reach the prompt.
SHUTDOWN_POLL_S = 0.05

# How long the token cookie lives: a week, so a shared link keeps working
# across a few days without the token going back into the address bar.
COOKIE_MAX_AGE_S = 604800

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json",
}

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
ANY_HOSTS = ("0.0.0.0", "::", "")

# Where the shared frontend is served from, so an app's own static tree and
# this package's cannot collide however either grows.
CORE_PREFIX = "/core/"
CORE_STATIC = Path(__file__).with_name("static")


@dataclass(frozen=True)
class Branding:
    """Everything the shared server has to say in a program's own voice."""

    name: str
    """Printed on start-up, and the app id ``/api/focus`` answers with."""

    version: str

    token_cookie: str
    """Per app, and it must stay that way: cookies are scoped by host, not
    by port, so two programs served on ``localhost`` would otherwise
    overwrite each other's token the moment both were open."""

    default_port: int

    static_dir: Path
    """The program's own ``web/static``, served at the root."""

    read_only_note: str
    """What ``--read-only`` refuses, in this program's nouns."""

    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES

    links: Mapping[str, str] = field(default_factory=dict)
    """The project's own URLs, by loose label -- ``homepage``, ``releases``,
    ``license``.  Read out of the packaging metadata by
    :func:`devicectl.meta.project_links` rather than written down again here,
    and answered to the page at ``GET /api/about``, which is where the
    wordmark, the version beside it and the licence footer get their hrefs.
    A program that is running from a checkout nobody installed has none, and
    the page then draws the same three things without links."""


# Told about every API request this server serves: once when it arrives,
# with ``status`` of ``None``, and once when it has been answered.  It is
# how a program with a recording in it gets the half of a fault report the
# wire cannot carry -- which button was pressed, and what came back to the
# page.  Whatever it raises is swallowed: watching must never break a
# request.
Watcher = Callable[[Request, int | None, str], None]


def _said(exc: BaseException) -> str:
    """Render a failure as its own sentence, or as the kind of failure it is."""
    return str(exc).strip() or exc.__class__.__name__


class Stoppable(Protocol):
    """The one thing the server needs of a worker: that it can be told to stop."""

    def stop(self, timeout: float | None = None) -> None:
        """Stand down, without necessarily waiting for it to finish."""


@dataclass
class Settings:
    """How this server was started, as opposed to what it is serving."""

    read_only: bool = False
    debug: bool = False


class UIServer(ThreadingHTTPServer):
    """The HTTP server, plus everything the handlers need to reach."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        branding: Branding,
        routes: Mapping[tuple[str, str], Route[Any]],
        context: Any,
        events: Broadcaster,
        settings: Settings,
        token: str,
        allowed_hosts: frozenset[str],
        watch: Watcher | None = None,
    ) -> None:
        """Bind the server and record its shared state."""
        self.branding = branding
        self.routes = routes
        self.context = context
        self.events = events
        self.settings = settings
        self.watch = watch
        self.token = token
        self.allowed_hosts = allowed_hosts
        self.stopping = threading.Event()
        self.streams = 0
        self._stream_lock = threading.Lock()
        super().__init__(address, UIHandler)

    def claim_stream(self) -> bool:
        """Take one of the event-stream slots, if any is left."""
        with self._stream_lock:
            if self.streams >= MAX_STREAMS:
                return False
            self.streams += 1
            return True

    def release_stream(self) -> None:
        """Give an event-stream slot back."""
        with self._stream_lock:
            self.streams = max(0, self.streams - 1)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Swallow the noise a browser makes when it walks away mid-request.

        A closed tab, a cancelled fetch or a reload all show up here as a
        broken pipe or a reset; the default handler prints a full traceback
        per occurrence, which would bury the one line the user wants.

        This belongs on the server, not on the handler: socketserver calls
        it on whatever accepted the connection.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        if self.settings.debug:
            super().handle_error(request, client_address)


class UIHandler(BaseHTTPRequestHandler):
    """One request (or one long-lived event stream)."""

    protocol_version = "HTTP/1.1"
    sys_version = ""

    @property
    def ui(self) -> UIServer:
        """The server, typed: socketserver types this as the base class."""
        return cast(UIServer, self.server)

    def version_string(self) -> str:
        """Name the program in ``Server:``, rather than the Python version."""
        return self.ui.branding.name

    def log_message(self, format: str, *args: Any) -> None:  # stdlib's own name
        """Keep request logging off the terminal unless --debug asked for it.

        The parameter keeps the base class's name, builtin or not, so a
        caller passing it by keyword still reaches this override.
        """
        if self.ui.settings.debug:
            sys.stderr.write(f"[debug] -- ui {self.address_string()} {format % args}\n")

    # --- guards ------------------------------------------------------------------

    def _host_allowed(self) -> bool:
        """Refuse a Host header that is a name we were not told to expect."""
        host = urlsplit(f"//{self.headers.get('Host', '')}").hostname or ""
        host = host.strip("[]").lower()
        if not host:
            return False
        if host in self.ui.allowed_hosts:
            return True
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return False
        return True  # an IP literal cannot be re-pointed by DNS

    def _token_ok(self) -> tuple[bool, str | None]:
        """Check the access token; returns (allowed, token to set as a cookie)."""
        if not self.ui.token:
            return True, None
        supplied = parse_query(urlsplit(self.path).query).get("token")
        if supplied and secrets.compare_digest(supplied, self.ui.token):
            return True, supplied
        cookies = self.headers.get("Cookie", "")
        for part in cookies.split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.ui.branding.token_cookie and secrets.compare_digest(
                value, self.ui.token
            ):
                return True, None
        if secrets.compare_digest(self.headers.get(TOKEN_HEADER, ""), self.ui.token):
            return True, None
        return False, None

    # --- dispatch ----------------------------------------------------------------

    def do_GET(self) -> None:  # BaseHTTPRequestHandler's spelling
        """Serve the app, the event stream, or a read endpoint."""
        self._dispatch("GET")

    def do_POST(self) -> None:
        """Serve a write endpoint."""
        self._dispatch("POST")

    def do_HEAD(self) -> None:
        """Answer a HEAD like a GET without the body."""
        self._dispatch("GET", head_only=True)

    def _dispatch(self, method: str, *, head_only: bool = False) -> None:
        split = urlsplit(self.path)
        path = split.path.rstrip("/") or "/"
        if not self._host_allowed():
            self._send_error_page(
                HTTP_FORBIDDEN,
                "This server only answers to an IP address or 'localhost'. "
                "Start it with --allow-host NAME to use a hostname.",
            )
            return
        allowed, set_cookie = self._token_ok()
        if not allowed:
            self._send_error_page(
                HTTP_FORBIDDEN,
                "Missing or wrong access token. Open the link the server printed.",
            )
            return
        if set_cookie is not None and not path.startswith("/api/"):
            # The token arrived in the URL: stash it and reload without it,
            # so it stops appearing in the address bar and in referrers.
            self._redirect(split.path or "/", set_cookie)
            return
        try:
            if path == "/api/events":
                self._serve_events()
                return
            if path.startswith("/api/"):
                self._serve_api(method, path, split.query)
                return
            if method != "GET":
                self._send_error_page(HTTP_METHOD_NOT_ALLOWED, "method not allowed")
                return
            self._serve_static(path, head_only=head_only)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser went away mid-reply; nothing to report

    # --- API ---------------------------------------------------------------------

    def _serve_api(self, method: str, path: str, query: str) -> None:
        if method == "POST" and not self.headers.get(UI_HEADER):
            self._send_json(HTTP_FORBIDDEN, {"error": f"missing {UI_HEADER} header"})
            return
        # These three are about the server rather than the device, and the
        # first has to answer before a second copy of the program has any
        # context to route with -- that is how it finds the tab to raise.
        if (method, path) == ("POST", "/api/focus"):
            self._serve_focus()
            return
        if (method, path) == ("GET", "/api/clients"):
            self._serve_clients()
            return
        if (method, path) == ("GET", "/api/about"):
            self._serve_about()
            return
        request = Request(method=method, path=path, query=parse_query(query))
        route = self.ui.routes.get((method, path))
        if route is None:
            self._refuse(request, HTTP_NOT_FOUND, f"no such endpoint: {method} {path}")
            return
        if route.write and self.ui.settings.read_only:
            self._refuse(
                request,
                HTTP_FORBIDDEN,
                "this server is running read-only; " + self.ui.branding.read_only_note,
            )
            return
        body = self._read_body()
        if body is None:
            return
        request.body = body
        self._watched(request, None)
        self._run_route(route, request)

    def _refuse(self, request: Request, status: int, message: str) -> None:
        """Turn a request down before it reaches a handler, and record that."""
        self._watched(request, status, message)
        self._send_json(status, {"error": message})

    def _watched(self, request: Request, status: int | None, error: str = "") -> None:
        """Tell the program's watcher what just happened, if it has one.

        Before the reply goes out rather than after it, so that whatever is
        watching has been told by the time the client has its answer.  A
        browser that fetches a recording immediately after the request it is
        interested in would otherwise be racing the handler that serves it.
        """
        watch = self.ui.watch
        if watch is None:
            return
        try:
            watch(request, status, error)
        except Exception:  # noqa: BLE001 - watching must never break a request
            pass

    def _run_route(self, route: Route, request: Request) -> None:
        """Run a handler, and turn whatever it raises into a reply.

        This is the only place a failure becomes a status code, so no handler
        has to translate its own.
        """
        try:
            response = route.handler(self.ui.context, request)
        except ApiError as exc:
            self._watched(request, exc.status, exc.message)
            self._send_json(exc.status, {"error": exc.message})
        except DeviceError as exc:
            # The device or an input said no.  That is the client's problem
            # to fix, not a server fault, so it does not deserve a 500.
            self._watched(request, status_of(exc), str(exc))
            said: dict[str, Any] = {"error": str(exc)}
            if exc.traceable:
                said["traceable"] = True
            self._send_json(status_of(exc), said)
        except OSError as exc:
            # A port that vanished, a socket that will not open: the same
            # kind of answer, from a layer that raises the stdlib's error.
            self._watched(request, HTTP_BAD_REQUEST, str(exc))
            self._send_json(HTTP_BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - every failure becomes a reply
            self._watched(request, HTTP_SERVER_ERROR, _said(exc))
            self._report_failure(exc)
        else:
            self._watched(request, response.status)
            self._send_response(response)

    def _serve_focus(self) -> None:
        """Ask every open tab to bring the page it already has to the front.

        This is how a second ``<program> ui`` avoids opening a second tab:
        it finds the port taken, asks whoever holds it to raise the page,
        and stops.  Nothing on the device changes, so a read-only server
        answers it too -- and the reply names the app, which is how the
        caller knows it reached another copy of itself rather than
        something else listening on that port.
        """
        events = self.ui.events
        events.publish("focus", {"at": time.time()})
        self._send_json(
            HTTP_OK,
            {
                "app": self.ui.branding.name,
                "version": self.ui.branding.version,
                "clients": events.subscriber_count,
                # The caller is a second copy with no stream of its own, so
                # it can only name the tabs it just raised if we name them.
                "watching": name_watchers(events.clients()),
            },
        )

    def _serve_about(self) -> None:
        """Answer what this program is, and where its own pages live.

        The name, the version and the project's URLs, in one answer the
        shared header asks for once per page.  It is about the program and
        not about the device, so it is served here rather than from a route
        every program would have had to declare -- and it is the same three
        facts in both, which is exactly what stops being true the moment
        each page carries its own copy.
        """
        branding = self.ui.branding
        self._send_json(
            HTTP_OK,
            {
                "app": branding.name,
                "version": branding.version,
                "links": dict(branding.links),
            },
        )

    def _serve_clients(self) -> None:
        """Who is watching: one row per open event stream."""
        from devicectl.web.agents import client_json

        rows = [client_json(c) for c in self.ui.events.clients()]
        self._send_json(HTTP_OK, {"clients": rows})

    def _report_failure(self, exc: BaseException) -> None:
        """Turn an unexpected failure into a 500 (and a traceback in debug)."""
        if self.ui.settings.debug:
            sys.stderr.write(f"[debug] -- ui {format_traceback(exc)}\n")
        message = _said(exc)
        # A bug, but one met while doing something to a device -- which is
        # when a recording of what went over the wire shows where it was.
        self._send_json(
            HTTP_SERVER_ERROR,
            {"error": message, "kind": exc.__class__.__name__, "traceable": True},
        )

    def _read_body(self) -> bytes | None:
        """Read the request body, or answer an error and return None."""
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self._send_json(HTTP_BAD_REQUEST, {"error": "bad Content-Length"})
            return None
        if length < 0 or length > self.ui.branding.max_body_bytes:
            self._send_json(HTTP_PAYLOAD_TOO_LARGE, {"error": "request body too large"})
            return None
        return self.rfile.read(length) if length else b""

    # --- event stream ------------------------------------------------------------

    def _serve_events(self) -> None:
        """Hold one Server-Sent Events connection open until it goes away."""
        if not self.ui.claim_stream():
            self._send_json(HTTP_UNAVAILABLE, {"error": "too many open event streams"})
            return
        self.close_connection = True  # no Content-Length: the body ends at close
        self.send_response(HTTP_OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            # Reconnect quickly: this is what lets a tab from an earlier run
            # be found and raised, rather than a second one being opened.
            self._write_chunk(f"retry: {STREAM_RETRY_MS}\n\n".encode())
            with self.ui.events.subscribe(self._describe_client()) as subscription:
                # The stream's first event tells this browser which of the
                # watchers on /api/clients is itself; nothing else can, since
                # several tabs share one address and one user agent.
                payload = json.dumps({"clientId": subscription.client.get("id")})
                self._write_chunk(f"event: hello\ndata: {payload}\n\n".encode())
                while not self.ui.stopping.is_set():
                    event = subscription.get(HEARTBEAT_INTERVAL_S)
                    if subscription.closed:
                        break
                    if event is None:
                        self._write_chunk(b": ping\n\n")  # prove the socket is alive
                        continue
                    payload = json.dumps(event.data, default=str)
                    self._write_chunk(
                        f"event: {event.name}\nid: {event.seq}\n"
                        f"data: {payload}\n\n".encode()
                    )
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # the tab was closed
        finally:
            self.ui.release_stream()

    def _describe_client(self) -> dict[str, Any]:
        """Who is opening this stream, as far as the request can say."""
        # Annotated wide on purpose: `client_address` is a pair for the
        # sockets this server listens on, but the base class does not promise
        # one, so the shape is checked rather than assumed.
        peer: tuple[Any, ...] = (
            self.client_address if isinstance(self.client_address, tuple) else ()
        )
        return {
            "address": str(peer[0]) if len(peer) > 0 else "",
            "port": peer[1] if len(peer) > 1 else None,
            "agent": self.headers.get("User-Agent", "") or "",
            "since": time.time(),
        }

    def _write_chunk(self, data: bytes) -> None:
        """Write one piece of the stream and push it out immediately."""
        self.wfile.write(data)
        self.wfile.flush()

    # --- static files ------------------------------------------------------------

    def _serve_static(self, path: str, *, head_only: bool = False) -> None:
        """Serve the app, falling back to index.html for the app's own routes.

        Anything under ``/core/`` comes from this package rather than from
        the program: that is the one prefix the shared frontend owns, so a
        program's own tree can grow without ever colliding with it.
        """
        if path.startswith(CORE_PREFIX):
            root, relative = CORE_STATIC, path[len(CORE_PREFIX) :]
            fallback = None
        else:
            root, relative = self.ui.branding.static_dir, path.lstrip("/")
            fallback = root / "index.html"
        target = (root / (relative or "index.html")).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            self._send_error_page(HTTP_FORBIDDEN, "forbidden")
            return
        if not target.is_file():
            if fallback is None:
                self._send_error_page(HTTP_NOT_FOUND, f"no such file: {path}")
                return
            target = fallback
        if not target.is_file():
            self._send_error_page(
                HTTP_SERVER_ERROR,
                "The web UI files are missing from this installation "
                f"(expected them in {root}).",
            )
            return
        body = target.read_bytes()
        self._send_response(
            Response(
                status=HTTP_OK,
                body=b"" if head_only else body,
                content_type=CONTENT_TYPES.get(
                    target.suffix, "application/octet-stream"
                ),
                headers={"Cache-Control": "no-cache", "Content-Length": str(len(body))},
            )
        )

    # --- replies -----------------------------------------------------------------

    def _redirect(self, location: str, token: str) -> None:
        """Send the token to a cookie and reload the page without it."""
        cookie = self.ui.branding.token_cookie
        self.send_response(HTTP_SEE_OTHER)
        self.send_header("Location", location or "/")
        self.send_header(
            "Set-Cookie",
            f"{cookie}={token}; Path=/; SameSite=Strict; Max-Age={COOKIE_MAX_AGE_S}",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        """Send one JSON reply."""
        self._send_response(
            Response(status=status, body=json.dumps(payload).encode("utf-8"))
        )

    def _send_error_page(self, status: int, message: str) -> None:
        """Send a refusal a person can read in a browser tab."""
        self._send_response(
            Response(
                status=status,
                body=f"{self.ui.branding.name} ui: {message}\n".encode(),
                content_type="text/plain; charset=utf-8",
            )
        )

    def _send_response(self, response: Response) -> None:
        """Write a complete reply."""
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        if "Content-Length" not in response.headers:
            self.send_header("Content-Length", str(len(response.body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if response.body and self.command != "HEAD":
            self.wfile.write(response.body)


def status_of(exc: DeviceError) -> int:
    """Return the status a program's own error answers with.

    An error that carries one -- a worker refusing because it is busy says
    409 -- keeps it; everything else is the client's input to fix, and 400.
    """
    status = getattr(exc, "status", None)
    return status if isinstance(status, int) else HTTP_BAD_REQUEST


def parse_listen(text: str, *, default_host: str, default_port: int) -> tuple[str, int]:
    """Split a ``--listen`` argument into a host and a port.

    Takes ``HOST``, ``HOST:PORT``, ``[v6]:PORT`` or a bare ``PORT``, so
    ``--listen 9000`` and ``--listen 0.0.0.0`` both mean what they look like.
    The program passes its own defaults, since the port a page opens on is
    the one thing about the server each program picks for itself.
    """
    raw = text.strip()
    if not raw:
        return default_host, default_port
    if raw.isdigit():
        return default_host, int(raw)
    if raw.startswith("["):  # [::1] or [::1]:PORT
        host, _, rest = raw[1:].partition("]")
        port = rest.lstrip(":")
        return host, int(port) if port else default_port
    host, sep, port = raw.rpartition(":")
    if not sep or not port.isdigit():
        return raw, default_port
    return host or default_host, int(port)


def local_addresses() -> list[str]:
    """Best guess at the addresses others could reach this machine on."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.2)
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: routed nowhere
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    return found


def raise_open_tab(
    app: str, host: str, port: int, *, timeout: float = 1.0
) -> dict[str, Any] | None:
    """Ask a copy already serving this port to raise its browser tab.

    Returns the reply -- which names the app and the browsers it reached --
    or ``None`` if nobody answered as ``app``.  The reply has to name it:
    something else entirely may be listening on that port, and a stranger's
    200 is not a reason to say the UI is already open.
    """
    import http.client

    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request(
                "POST",
                "/api/focus",
                body=b"{}",
                headers={"Content-Type": "application/json", UI_HEADER: "1"},
            )
            response = conn.getresponse()
            doc = json.loads(response.read() or b"{}")
        finally:
            conn.close()
    except (OSError, ValueError, http.client.HTTPException):
        return None
    if response.status == HTTP_OK and doc.get("app") == app:
        return doc
    return None


def show_the_page(
    url: str, events: Broadcaster, *, grace: float = BROWSER_GRACE_S
) -> bool:
    """Raise the tab that already has this page open, or open a new one.

    No browser can be told from the outside to switch to a tab it already
    has, so the page is told to raise itself instead: a tab left over from an
    earlier run reconnects to the stream within :data:`STREAM_RETRY_MS`, and
    a ``focus`` event reaches it there.  Only if nothing has reconnected by
    the end of the grace period is a new tab opened.  Returns whether an
    existing tab was found.
    """
    import webbrowser

    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if events.subscriber_count:
            events.publish("focus", {"at": time.time()})
            print(
                "  a browser already has this page open -- raising that tab", flush=True
            )
            for name in name_watchers(events.clients()):
                print(f"    {name}", flush=True)
            return True
        time.sleep(BROWSER_POLL_S)
    webbrowser.open(url)
    return False


def _begin_teardown(server: UIServer, worker: Stoppable) -> None:
    """Start everything that has to end, without waiting for any of it.

    Runs off the signal handler rather than in it: each of these takes a
    lock some other thread may be holding, and a handler runs on the main
    thread, where blocking on one is how a process hangs on Ctrl+C instead
    of stopping on it.
    """
    worker.stop(timeout=0)  # tell it to stand down; do not wait here
    server.events.shutdown()  # end the event streams, waking their threads
    server.shutdown()  # stop accepting, and wait for the loop to notice


def _install_stop_handlers(server: UIServer, worker: Stoppable) -> Callable[[], None]:
    """Make Ctrl+C (and SIGTERM) stop the server; returns a restore callback.

    ``serve_forever`` waits in ``selectors.select``, and an interrupt there is
    not guaranteed to surface as ``KeyboardInterrupt`` -- on some platforms it
    does not, and the server keeps the port.  So the signal is handled
    explicitly instead.  ``shutdown`` blocks until the loop has stopped and
    would deadlock if called from the loop's own thread, which is exactly where
    a signal handler runs, hence the throwaway thread.

    Everything that can be started here is started here rather than in
    ``serve``'s ``finally``, because they are independent and the slow one
    should not be waiting on its turn: the event streams end, the accept
    loop stops and the worker is told to stand down all at once.  A second
    Ctrl+C is taken to mean the first one was not fast enough and leaves
    immediately, which is the usual contract for one.
    """
    stopping = threading.Event()

    def handler(signum: int, frame: Any) -> None:
        if stopping.is_set():
            # Asked twice: stop asking politely.  Nothing here writes to
            # disk, so there is nothing a second pass could corrupt.
            os._exit(EXIT_INTERRUPTED)
        stopping.set()
        print("\nStopping...", flush=True)
        server.stopping.set()
        threading.Thread(target=_begin_teardown, args=(server, worker)).start()

    previous: list[tuple[int, Any]] = []
    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous.append((signum, signal.signal(signum, handler)))
        except ValueError:
            # Not the main thread -- the embedding process owns the signals.
            pass

    def restore() -> None:
        for signum, old in previous:
            try:
                signal.signal(signum, old)
            except (ValueError, TypeError):
                pass

    return restore


@dataclass
class Serving:
    """What a program hands the shared server to run."""

    branding: Branding
    routes: Mapping[tuple[str, str], Route[Any]]
    context: Any
    events: Broadcaster
    worker: Stoppable
    notes: Sequence[str] = field(default_factory=tuple)
    """Extra lines printed under the URL, in the program's own words."""

    watch: Watcher | None = None
    """Told about every API request, before and after it is served."""


def _cannot_listen(
    exc: OSError,
    name: str,
    *,
    host: str,
    shown_host: str,
    port: int,
    open_browser: bool,
) -> int:
    """Report a port we could not have, raising the tab already on it if we can.

    The usual reason the port is taken is that this is the second
    ``<program> ui``, so before failing, ask the first one to bring its
    browser tab forward -- which is what the user wanted anyway.
    """
    taken = getattr(exc, "errno", None) == errno.EADDRINUSE
    reply = raise_open_tab(name, shown_host, port) if taken and open_browser else None
    if reply is None:
        print(f"Cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"{name} ui is already serving on http://{shown_host}:{port}/")
    print("  raised the browser tab it had already opened")
    # Named by the other process, which is the only one that can see who is
    # on its stream.
    for watching in reply.get("watching") or []:
        print(f"    {watching}")
    return EXIT_OK


def _announce(
    serving: Serving,
    url: str,
    port: int,
    *,
    read_only: bool,
    loopback: bool,
    token: str,
) -> None:
    """Print where the server can be reached, and on what terms."""
    print(f"{serving.branding.name} ui is serving on {url}", flush=True)
    if read_only:
        print(f"  read-only: {serving.branding.read_only_note}")
    for note in serving.notes:
        print(f"  {note}")
    if not loopback:
        suffix = f"?token={token}" if token else ""
        for address in local_addresses():
            print(f"  shareable: http://{address}:{port}/{suffix}")
        if token:
            print("  the link includes an access token -- share it deliberately")
    print("  press Ctrl+C to stop", flush=True)


def serve(
    serving: Serving,
    *,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    token: str | None = None,
    read_only: bool = False,
    open_browser: bool = True,
    allow_hosts: Sequence[str] = (),
    debug: bool = False,
) -> int:
    """Run the web UI until interrupted; returns a process exit code."""
    branding = serving.branding
    port = branding.default_port if port is None else port
    loopback = host in LOOPBACK_HOSTS
    if token is None:
        token = "" if loopback else secrets.token_urlsafe(16)

    allowed = frozenset(
        {"localhost", *(h.strip().lower() for h in allow_hosts if h.strip())}
    )
    shown_host = DEFAULT_HOST if host in ANY_HOSTS else host
    try:
        server = UIServer(
            (host, port),
            branding=branding,
            routes=serving.routes,
            context=serving.context,
            events=serving.events,
            settings=Settings(read_only=read_only, debug=debug),
            token=token,
            allowed_hosts=allowed,
            watch=serving.watch,
        )
    except OSError as exc:
        serving.worker.stop()
        return _cannot_listen(
            exc,
            branding.name,
            host=host,
            shown_host=shown_host,
            port=port,
            open_browser=open_browser,
        )

    suffix = f"?token={token}" if token else ""
    url = f"http://{shown_host}:{server.server_port}/{suffix}"
    _announce(
        serving,
        url,
        server.server_port,
        read_only=read_only,
        loopback=loopback,
        token=token,
    )

    if open_browser:
        threading.Thread(
            target=show_the_page, args=(url, serving.events), daemon=True
        ).start()

    stop = _install_stop_handlers(server, serving.worker)
    try:
        server.serve_forever(poll_interval=SHUTDOWN_POLL_S)
    except KeyboardInterrupt:
        print("\nStopping...", flush=True)
    finally:
        stop()
        server.stopping.set()
        serving.events.shutdown()
        server.shutdown()
        server.server_close()
        serving.worker.stop()
    return EXIT_OK


__all__ = [
    "Branding",
    "CORE_PREFIX",
    "DEFAULT_HOST",
    "Serving",
    "Settings",
    "Stoppable",
    "TOKEN_HEADER",
    "UIServer",
    "UI_HEADER",
    "Watcher",
    "local_addresses",
    "parse_listen",
    "raise_open_tab",
    "serve",
    "show_the_page",
    "status_of",
]
