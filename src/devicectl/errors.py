"""The base class every error a program raises on purpose derives from.

A command fails for one of two reasons.  Either the device, the link or the
input was not what it needed -- a setting that does not exist, a firmware
image for another model, something that will not answer -- which is ordinary
and gets one clear line on stderr.  Or the program has a bug, which deserves
a traceback.

:class:`DeviceError` is the first kind.  Each program derives one class of
its own from it (``AlfenError``, ``JkError``) and every module raises a
subclass of *that*, so a caller who cares can still tell a refused register
apart from a bad firmware file.  What the shared code needs is only the
root: :func:`devicectl.cli.main` catches it, prints the message and exits,
and the web server turns it into a 400 -- neither has to know the list.

Errors that are specifically about a *value* also derive from
:class:`ValueError`, because that is what they are and callers already catch
them that way.
"""

from __future__ import annotations

import traceback

# How much of a long message is worth showing in a UI ticker or a log line.
MESSAGE_LIMIT = 400

# Exception classes whose name adds nothing to their message: nobody needs
# to be told that "no such property" was a ValueError.
PLAIN = ("RuntimeError", "ValueError")


class DeviceError(Exception):
    """An expected failure, reportable to the user as a single line."""

    traceable: bool = False
    """Whether the device, or the link to it, is what failed.

    A subclass for "the device did not answer" or "the device refused" sets
    this, and the web layer then says so in the error reply -- which is what
    lets the page start recording the wire on the one kind of failure a
    recording of the wire can explain.  A value out of range, a port nobody
    chose, a busy worker: those leave it False.
    """


def describe(exc: BaseException) -> str:
    """Return one readable line for an exception.

    For an activity ticker, a job's ``error`` field, or anywhere else a
    failure has to fit on a line beside the thing that failed.
    """
    text = str(exc).strip()
    if not text:
        text = exc.__class__.__name__
    elif exc.__class__.__name__ not in PLAIN:
        text = f"{exc.__class__.__name__}: {text}"
    return text.splitlines()[0][:MESSAGE_LIMIT]


def format_traceback(exc: BaseException) -> str:
    """Return the full traceback text, for ``--debug`` logging of a failure."""
    return "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    ).strip()


__all__ = ["DeviceError", "describe", "format_traceback"]
