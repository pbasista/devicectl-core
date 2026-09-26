"""The HTTP server: what it serves, what it turns away, and who is watching.

Each test runs a real :class:`UIServer` on an ephemeral port and talks to it
over the loopback, because the parts under test -- the Host header check,
the token, the event stream -- live in the request path itself.  The
"program" behind it is three routes and a directory of two files.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from devicectl.errors import DeviceError
from devicectl.web import server as srv
from devicectl.web.events import Broadcaster
from devicectl.web.http import HTTP_CONFLICT, ApiError, Request, Response, Route, ok

PAGE = "<!doctype html><title>widgetctl</title><div id=root></div>"


class FakeWorker:
    def __init__(self) -> None:
        self.stopped: list[float | None] = []

    def stop(self, timeout: float | None = None) -> None:
        self.stopped.append(timeout)


class Refused(DeviceError):
    """What a program raises when the device says no."""


class Silent(DeviceError):
    """What a program raises when the device does not answer."""

    traceable = True


class Busy(DeviceError):
    """What a worker raises when it never got its turn."""

    status = HTTP_CONFLICT


class Context:
    def __init__(self) -> None:
        self.reads = 0
        self.writes = 0


def get_state(ctx: Context, req: Request) -> Response:
    ctx.reads += 1
    return ok({"ready": True, "n": ctx.reads})


def post_thing(ctx: Context, req: Request) -> Response:
    ctx.writes += 1
    return ok({"wrote": req.json()})


def post_refuse(ctx: Context, req: Request) -> Response:
    raise Refused("that value is out of range")


def post_silent(ctx: Context, req: Request) -> Response:
    raise Silent("no/short response")


def post_busy(ctx: Context, req: Request) -> Response:
    raise Busy("something else is in front of this")


def post_explode(ctx: Context, req: Request) -> Response:
    raise ZeroDivisionError("division by zero")


def post_teapot(ctx: Context, req: Request) -> Response:
    raise ApiError(418, "no coffee here")


ROUTES: dict[tuple[str, str], Route[Context]] = {
    ("GET", "/api/state"): Route(get_state),
    ("POST", "/api/thing"): Route(post_thing, write=True),
    ("POST", "/api/refuse"): Route(post_refuse, write=True),
    ("POST", "/api/busy"): Route(post_busy),
    ("POST", "/api/silent"): Route(post_silent),
    ("POST", "/api/explode"): Route(post_explode),
    ("POST", "/api/teapot"): Route(post_teapot),
}


@pytest.fixture
def static(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    (root / "js").mkdir(parents=True)
    (root / "index.html").write_text(PAGE)
    (root / "js" / "app.js").write_text("export const a = 1;\n")
    return root


@pytest.fixture
def branding(static: Path) -> srv.Branding:
    return srv.Branding(
        name="widgetctl",
        version="9.9.9",
        token_cookie="widgetctl_token",
        default_port=8099,
        static_dir=static,
        read_only_note="nothing on the widget can be changed from here",
        links={
            "homepage": "https://example.invalid/widgetctl",
            "releases": "https://example.invalid/widgetctl/releases",
            "license": "https://example.invalid/widgetctl/LICENSE",
        },
    )


def start(
    branding,
    *,
    token="",
    read_only=False,
    debug=False,
    hosts=("localhost",),
    watch=None,
):
    """Run a UIServer on an ephemeral port; the caller closes it."""
    events = Broadcaster()
    instance = srv.UIServer(
        ("127.0.0.1", 0),
        branding=branding,
        routes=ROUTES,
        context=Context(),
        events=events,
        settings=srv.Settings(read_only=read_only, debug=debug),
        token=token,
        allowed_hosts=frozenset(hosts),
        watch=watch,
    )
    thread = threading.Thread(
        target=instance.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    instance.base = f"http://127.0.0.1:{instance.server_port}"  # ty: ignore
    instance.thread = thread  # ty: ignore
    return instance


def shut(instance) -> None:
    instance.stopping.set()
    instance.events.shutdown()
    instance.shutdown()
    instance.server_close()
    instance.thread.join(timeout=3)


@pytest.fixture
def server(branding):
    instance = start(branding)
    yield instance
    shut(instance)


def fetch(url, *, method="GET", data=None, headers=None, timeout=5):
    """Make one request and return (status, body text)."""
    request = urllib.request.Request(
        url, data=data, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def post(server, path, **kwargs):
    headers = {srv.UI_HEADER: "1", **kwargs.pop("headers", {})}
    return fetch(server.base + path, method="POST", data=b"{}", headers=headers)


# --- what is served -------------------------------------------------------------------------


def test_the_app_is_served(server):
    status, body = fetch(server.base + "/")
    assert status == 200
    assert "widgetctl" in body


def test_an_unknown_path_falls_back_to_the_app(server):
    # The page routes on the hash, but a reload of a deep link must not 404.
    status, body = fetch(server.base + "/settings")
    assert (status, body) == (200, PAGE)


def test_a_file_that_exists_is_served_with_its_own_type(server):
    status, body = fetch(server.base + "/js/app.js")
    assert (status, body) == (200, "export const a = 1;\n")


def test_the_shared_frontend_is_served_under_its_own_prefix(server):
    status, body = fetch(server.base + "/core/vendor/preact-htm.module.js")
    assert status == 200
    assert "preact" in body.lower()


def test_a_missing_file_under_the_core_prefix_is_a_404_not_the_page(server):
    # Falling back to index.html there would answer an ES module import with
    # HTML, which the browser reports as a syntax error in the wrong file.
    status, _ = fetch(server.base + "/core/js/nope.js")
    assert status == 404


def test_a_path_climbing_out_of_the_static_root_is_refused(server):
    status, _ = fetch(server.base + "/../../etc/passwd")
    assert status in (403, 404)


def test_a_head_asks_the_same_question_without_the_answer(server):
    request = urllib.request.Request(server.base + "/", method="HEAD")
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
        assert response.read() == b""
        assert response.headers["Content-Length"] == str(len(PAGE))


# --- the guards -----------------------------------------------------------------------------


def test_a_hostname_in_the_host_header_is_refused(server):
    # DNS rebinding: a name someone else controls, pointed at 127.0.0.1.
    status, body = fetch(server.base + "/", headers={"Host": "evil.example"})
    assert status == 403
    assert "IP address" in body


def test_localhost_is_allowed(server):
    status, _ = fetch(server.base + "/", headers={"Host": "localhost"})
    assert status == 200


def test_a_hostname_the_user_allowed_is_let_through(branding):
    instance = start(branding, hosts=("localhost", "workshop"))
    try:
        status, _ = fetch(instance.base + "/", headers={"Host": "workshop"})
        assert status == 200
    finally:
        shut(instance)


def test_a_post_without_the_ui_header_is_refused(server):
    status, body = fetch(server.base + "/api/thing", method="POST", data=b"{}")
    assert status == 403
    assert srv.UI_HEADER in body


def test_a_get_needs_no_ui_header(server):
    status, _ = fetch(server.base + "/api/state")
    assert status == 200


def test_a_token_is_required_when_one_is_set(branding):
    instance = start(branding, token="s3cret")
    try:
        assert fetch(instance.base + "/api/state")[0] == 403
        ok_status, _ = fetch(
            instance.base + "/api/state", headers={srv.TOKEN_HEADER: "s3cret"}
        )
        assert ok_status == 200
    finally:
        shut(instance)


def test_the_token_in_the_url_is_moved_into_a_cookie(branding):
    instance = start(branding, token="s3cret")
    try:
        request = urllib.request.Request(instance.base + "/?token=s3cret")
        opener = urllib.request.build_opener(NoRedirect())
        try:
            opener.open(request, timeout=5)
            raise AssertionError("expected a redirect")
        except urllib.error.HTTPError as exc:
            assert exc.code == 303
            assert exc.headers["Location"] == "/"
            cookie = exc.headers["Set-Cookie"]
            assert cookie.startswith("widgetctl_token=s3cret")
            assert "SameSite=Strict" in cookie
    finally:
        shut(instance)


def test_the_cookie_is_named_per_program(branding):
    # Cookies are scoped by host, not by port, so two programs served on
    # localhost would otherwise overwrite each other's token.
    assert branding.token_cookie == "widgetctl_token"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def test_a_wrong_token_is_refused_however_it_arrives(branding):
    instance = start(branding, token="s3cret")
    try:
        assert fetch(instance.base + "/api/state?token=nope")[0] == 403
        assert (
            fetch(instance.base + "/api/state", headers={srv.TOKEN_HEADER: "nope"})[0]
            == 403
        )
        assert (
            fetch(instance.base + "/api/state", headers={"Cookie": "x=nope"})[0] == 403
        )
    finally:
        shut(instance)


def test_read_only_refuses_a_write(branding):
    instance = start(branding, read_only=True)
    try:
        status, body = post(instance, "/api/thing")
        assert status == 403
        assert "read-only" in body
        assert "nothing on the widget can be changed" in body
    finally:
        shut(instance)


def test_read_only_still_allows_reading(branding):
    instance = start(branding, read_only=True)
    try:
        assert fetch(instance.base + "/api/state")[0] == 200
    finally:
        shut(instance)


def test_a_body_larger_than_the_program_takes_is_refused(branding):
    small = srv.Branding(**{**vars(branding), "max_body_bytes": 16})
    instance = start(small)
    try:
        status, _ = fetch(
            instance.base + "/api/thing",
            method="POST",
            data=b"x" * 64,
            headers={srv.UI_HEADER: "1"},
        )
        assert status == 413
    finally:
        shut(instance)


# --- how failures are answered --------------------------------------------------------------


def test_an_unknown_endpoint_answers_json(server):
    status, body = fetch(server.base + "/api/nope")
    assert status == 404
    assert "no such endpoint" in json.loads(body)["error"]


def test_the_device_saying_no_is_a_400_not_a_500(server):
    status, body = post(server, "/api/refuse")
    assert status == 400
    assert json.loads(body)["error"] == "that value is out of range"


def test_a_refusal_of_the_input_is_not_offered_a_trace(server):
    _, body = post(server, "/api/refuse")
    assert "traceable" not in json.loads(body)


def test_a_device_that_did_not_answer_is_offered_a_trace(server):
    status, body = post(server, "/api/silent")
    assert status == 400
    assert json.loads(body)["traceable"] is True


def test_an_error_that_carries_its_own_status_keeps_it(server):
    # A worker refusing because it is busy is a 409, not a bad request.
    status, body = post(server, "/api/busy")
    assert status == 409
    assert "in front of this" in json.loads(body)["error"]


def test_an_api_error_answers_with_the_status_it_names(server):
    status, body = post(server, "/api/teapot")
    assert (status, json.loads(body)["error"]) == (418, "no coffee here")


def test_a_bug_is_a_500_that_still_names_itself(server):
    status, body = post(server, "/api/explode")
    doc = json.loads(body)
    assert status == 500
    assert doc["kind"] == "ZeroDivisionError"
    # A bug met while talking to a device is what a recording shows.
    assert doc["traceable"] is True


def test_a_browser_walking_away_is_not_a_traceback(server, capsys):
    # A closed tab arrives as a broken pipe; the default handler would print
    # a full traceback for each one and bury the line the user wants.
    server.handle_error(None, ("127.0.0.1", 1))  # no live exception at all
    try:
        raise BrokenPipeError
    except BrokenPipeError:
        server.handle_error(None, ("127.0.0.1", 1))
    assert capsys.readouterr().err == ""


# --- the event stream -----------------------------------------------------------------------


def test_the_event_stream_frames_events(server):
    request = urllib.request.Request(server.base + "/api/events")
    with urllib.request.urlopen(request, timeout=5) as stream:
        assert stream.headers["Content-Type"].startswith("text/event-stream")
        assert stream.readline() == b"retry: 1000\n"
        stream.readline()  # blank
        assert stream.readline() == b"event: hello\n"
        hello = stream.readline()
        assert json.loads(hello.decode().partition("data: ")[2])["clientId"] == "1"
        stream.readline()
        server.events.publish("link", {"state": "idle"})
        assert stream.readline() == b"event: link\n"
        assert stream.readline().startswith(b"id: ")
        assert json.loads(stream.readline().decode().partition("data: ")[2]) == {
            "state": "idle"
        }


def test_too_many_streams_are_refused(server, monkeypatch):
    monkeypatch.setattr(srv, "MAX_STREAMS", 0)
    status, body = fetch(server.base + "/api/events")
    assert status == 503
    assert "too many" in json.loads(body)["error"]


def test_the_clients_endpoint_names_the_open_streams(server):
    request = urllib.request.Request(
        server.base + "/api/events", headers={"User-Agent": "Mozilla/5.0 Firefox/1 X11"}
    )
    with urllib.request.urlopen(request, timeout=5) as stream:
        stream.readline()
        status, body = fetch(server.base + "/api/clients")
        rows = json.loads(body)["clients"]
        assert status == 200
        assert rows[0]["label"] == "Firefox on Linux"


def test_about_names_the_program_and_its_own_pages(server):
    # The header's wordmark, the version beside it and the licence footer all
    # get their hrefs from here, so that no URL is written into the browser
    # half of either program.
    status, body = fetch(server.base + "/api/about")
    doc = json.loads(body)
    assert status == 200
    assert doc["app"] == "widgetctl"
    assert doc["version"] == "9.9.9"
    assert doc["links"]["releases"] == "https://example.invalid/widgetctl/releases"


def test_about_is_a_read_a_read_only_server_still_answers(branding):
    instance = start(branding, read_only=True)
    try:
        status, body = fetch(instance.base + "/api/about")
    finally:
        shut(instance)
    assert status == 200
    assert json.loads(body)["app"] == "widgetctl"


def test_a_program_with_no_metadata_still_serves_its_page(static):
    # A checkout nobody installed has no `[project.urls]` to read, which is a
    # wordmark that does not click rather than a server that will not start.
    bare = srv.Branding(
        name="widgetctl",
        version="9.9.9",
        token_cookie="widgetctl_token",
        default_port=8099,
        static_dir=static,
        read_only_note="nothing can be changed from here",
    )
    instance = start(bare)
    try:
        status, body = fetch(instance.base + "/api/about")
    finally:
        shut(instance)
    assert status == 200
    assert json.loads(body)["links"] == {}


# --- one program finding another --------------------------------------------------------------


def test_a_second_copy_raises_the_tab_the_first_one_opened(server):
    reply = srv.raise_open_tab("widgetctl", "127.0.0.1", server.server_port)
    assert reply is not None
    assert reply["app"] == "widgetctl"
    assert reply["version"] == "9.9.9"


def test_a_stranger_on_that_port_is_not_mistaken_for_us(server):
    # Something else entirely may be listening; a 200 is not a reason to
    # say the UI is already open.
    assert srv.raise_open_tab("otherctl", "127.0.0.1", server.server_port) is None


def test_asking_a_port_nobody_is_listening_on_says_no(server):
    port = server.server_port + 1 if server.server_port < 65000 else 1
    assert srv.raise_open_tab("widgetctl", "127.0.0.1", port, timeout=0.2) is None


def test_focus_reaches_the_tabs_that_are_open(server):
    with server.events.subscribe({"agent": "Firefox/1 X11"}) as subscription:
        while subscription.get(timeout=0) is not None:
            pass
        reply = srv.raise_open_tab("widgetctl", "127.0.0.1", server.server_port)
        assert reply["watching"] == ["Firefox on Linux"]
        event = subscription.get(timeout=1)
        assert event is not None and event.name == "focus"


def test_a_tab_that_is_already_watching_is_raised_instead_of_a_new_one(monkeypatch):
    events = Broadcaster()
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    with events.subscribe({"agent": ""}):
        assert srv.show_the_page("http://x/", events, grace=0.5) is True
    assert opened == []


def test_with_nothing_watching_a_browser_is_opened(monkeypatch):
    events = Broadcaster()
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    assert srv.show_the_page("http://x/", events, grace=0.1) is False
    assert opened == ["http://x/"]


# --- who is watching the API ------------------------------------------------------------


@pytest.fixture
def watched(branding):
    """A server that writes down every API call it serves."""
    seen: list[tuple[str, int | None, str]] = []
    instance = start(
        branding,
        watch=lambda req, status, error: seen.append((req.path, status, error)),
    )
    instance.seen = seen  # ty: ignore
    yield instance
    shut(instance)


def test_a_request_is_watched_as_it_arrives_and_again_when_it_is_answered(watched):
    status, _ = post(watched, "/api/thing")
    assert status == 200
    assert watched.seen == [("/api/thing", None, ""), ("/api/thing", 200, "")]


def test_the_watcher_is_told_what_the_failure_was(watched):
    post(watched, "/api/refuse")
    assert watched.seen[-1] == ("/api/refuse", 400, "that value is out of range")


def test_an_endpoint_nobody_serves_is_watched_too(watched):
    fetch(watched.base + "/api/nowhere")
    assert watched.seen == [("/api/nowhere", 404, "no such endpoint: GET /api/nowhere")]


def test_a_write_refused_for_being_read_only_is_watched(branding):
    seen: list[tuple[str, int | None, str]] = []
    instance = start(
        branding,
        read_only=True,
        watch=lambda req, status, error: seen.append((req.path, status, error)),
    )
    try:
        post(instance, "/api/thing")
    finally:
        shut(instance)
    assert [(path, status) for path, status, _ in seen] == [("/api/thing", 403)]
    assert "read-only" in seen[0][2]


def test_a_watcher_that_throws_does_not_break_the_request(branding):
    def explode(req, status, error):
        raise RuntimeError("no")

    instance = start(branding, watch=explode)
    try:
        status, body = post(instance, "/api/thing")
    finally:
        shut(instance)
    assert status == 200 and json.loads(body)["wrote"] == {}


def test_parse_listen_reads_a_host_a_port_or_both() -> None:
    def p(text: str) -> tuple[str, int]:
        return srv.parse_listen(text, default_host="127.0.0.1", default_port=8088)

    assert p("") == ("127.0.0.1", 8088)  # nothing given: both defaults
    assert p("9000") == ("127.0.0.1", 9000)  # a bare port
    assert p("0.0.0.0") == ("0.0.0.0", 8088)  # a bare host
    assert p("0.0.0.0:9000") == ("0.0.0.0", 9000)  # both
    assert p("[::1]:9000") == ("::1", 9000)  # a bracketed v6 host and port
    assert p("[::1]") == ("::1", 8088)  # a bracketed v6 host alone
    # A trailing colon with no digits is a hostname, not a port.
    assert p("myhost") == ("myhost", 8088)
