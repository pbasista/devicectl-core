"""The pieces of HTTP the API handlers actually touch.

The server itself is :mod:`http.server` and the handlers are plain functions,
so what sits between them is small: a parsed request, a reply ready to write,
a routing entry that says whether an endpoint changes anything, one
exception carrying the status to answer with, and a way to say what a
request was for a recording to keep.

None of it is a framework.  It exists so that a handler is a function of
``(context, request) -> response`` and can be called directly from a test
without a socket.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar
from urllib.parse import parse_qs

from devicectl.errors import DeviceError

# The context a handler is given is the program's own -- what it holds is
# entirely up to the program -- so the routing table is generic over it and
# the shared server never looks inside.
CtxT = TypeVar("CtxT")

# Every status either half of the transport answers with, in one place: the
# server module had grown its own copy of three of these, and a program that
# wants to say 404 should not have to know which of the two to ask.
HTTP_OK = 200
HTTP_SEE_OTHER = 303
HTTP_BAD_REQUEST = 400
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_METHOD_NOT_ALLOWED = 405
HTTP_CONFLICT = 409
HTTP_PAYLOAD_TOO_LARGE = 413
HTTP_SERVER_ERROR = 500
HTTP_UNAVAILABLE = 503

# Query-string spellings of "yes".  Anything else, including an empty value,
# is false: `?force` alone is not an assertion that force is wanted.
TRUTHY = ("1", "true", "yes", "on")


class ApiError(DeviceError):
    """A request that cannot be served, with the status to answer."""

    def __init__(self, status: int, message: str) -> None:
        """Record the HTTP status alongside the message."""
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Request:
    """One parsed HTTP request."""

    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def json(self) -> dict[str, Any]:
        """Parse the body as a JSON object."""
        if not self.body:
            return {}
        try:
            doc = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(HTTP_BAD_REQUEST, f"malformed JSON body: {exc}") from None
        if not isinstance(doc, dict):
            raise ApiError(HTTP_BAD_REQUEST, "expected a JSON object")
        return doc

    def param(self, name: str, default: str = "") -> str:
        """One query-string parameter."""
        return self.query.get(name, default)

    def flag(self, name: str, default: bool = False) -> bool:
        """One query-string parameter read as a boolean."""
        raw = self.query.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in TRUTHY


@dataclass
class Response:
    """One reply, ready to write."""

    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def ok(payload: Any, status: int = 200) -> Response:
    """Render a JSON reply.

    ``default=str`` is the last resort for a value no schema function
    converted -- a ``datetime`` or an ``Enum`` that reached here intact.
    Rendering it as its own text is a worse answer than the right type but a
    much better one than a 500 in the middle of a page.
    """
    return Response(
        status=status, body=json.dumps(payload, default=str).encode("utf-8")
    )


@dataclass(frozen=True)
class Route(Generic[CtxT]):
    """One endpoint: what runs it, and whether it changes anything.

    Generic over the context so a program's routing table is checked against
    the context its own handlers take, while the server that dispatches it
    never needs to know what that is.
    """

    handler: Callable[[CtxT, Request], Response]
    write: bool = False
    """Refused outright when the server was started ``--read-only``."""

    raw_body: bool = False
    """Takes an uploaded file rather than JSON."""


# How much of a request body a recording keeps.  Long enough for every
# settings change a page sends in one go, short enough that a mistyped
# upload does not become the report.
MAX_RECORDED_BODY = 1000


def describe_call(
    request: Request, status: int | None = None, error: str = ""
) -> tuple[str, str]:
    """Say what a request was, and how it went, for a recording to keep.

    Two calls per request rather than one: the arriving request, with its
    body, goes in before the work starts, and the outcome goes in after it,
    so a recording reads in the order things happened -- what was asked,
    what that put on the wire, and what came back to the page.  ``status``
    of ``None`` is the first of those.

    Returns the one-line headline and the body beneath it, which is empty
    for the outcome: repeating the payload under the answer would double
    every request in the report.
    """
    where = request.path
    if request.query:
        where += "?" + "&".join(f"{k}={v}" for k, v in request.query.items())
    head = f"{request.method} {where}"
    if status is None:
        return head, _body(request.body)
    return f"{head}  ->  {status}" + (f"  {error}" if error else ""), ""


def _body(raw: bytes) -> str:
    """Render a request body as something a person can read, or describe it.

    An uploaded firmware image is a megabyte of nothing anybody can read and
    is said to be that; JSON is re-rendered rather than passed through, so a
    page that sends one long line is not one long line here.
    """
    if not raw:
        return ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"({len(raw)} bytes, not text)"
    try:
        text = json.dumps(json.loads(text), indent=2)
    except json.JSONDecodeError:
        pass
    if len(text) > MAX_RECORDED_BODY:
        return text[:MAX_RECORDED_BODY] + f"\n... ({len(raw)} bytes in all)"
    return text


def parse_query(raw: str) -> dict[str, str]:
    """Flatten a query string to the last value of each parameter."""
    return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}


def spool(
    req: Request,
    *,
    max_bytes: int,
    prefix: str,
    suffix: str = "",
    too_large: str = "that file is larger than anything this takes",
) -> Path:
    """Write an uploaded body to a temporary file, for code that wants a path.

    The uploaded name is kept where the browser sent one, because a firmware
    image or a settings export is often identified by it; ``suffix`` names
    the extension to give a file that arrived without one.  The file goes in
    a directory of its own so :func:`discard` can remove both without
    guessing what else is in there.
    """
    if not req.body:
        raise ApiError(HTTP_BAD_REQUEST, "no file content was uploaded")
    if len(req.body) > max_bytes:
        raise ApiError(HTTP_PAYLOAD_TOO_LARGE, too_large)
    name = Path(req.param("filename", "upload")).name or "upload"
    directory = Path(tempfile.mkdtemp(prefix=prefix))
    path = directory / (name if Path(name).suffix else name + suffix)
    path.write_bytes(req.body)
    return path


def discard(path: Path) -> None:
    """Remove a spooled upload and the directory it was written into."""
    shutil.rmtree(path.parent, ignore_errors=True)


__all__ = [
    "HTTP_BAD_REQUEST",
    "HTTP_CONFLICT",
    "HTTP_FORBIDDEN",
    "HTTP_METHOD_NOT_ALLOWED",
    "HTTP_NOT_FOUND",
    "HTTP_OK",
    "HTTP_PAYLOAD_TOO_LARGE",
    "HTTP_SEE_OTHER",
    "HTTP_SERVER_ERROR",
    "HTTP_UNAVAILABLE",
    "MAX_RECORDED_BODY",
    "ApiError",
    "Request",
    "Response",
    "Route",
    "describe_call",
    "discard",
    "ok",
    "parse_query",
    "spool",
]
