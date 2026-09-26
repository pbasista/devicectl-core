"""Turning an expected failure into one line on stderr and an exit code.

Every module raises its own subclass of
:class:`~devicectl.errors.DeviceError`, and none of them needs a handler of
its own: they all end the same way.  So a program's ``main`` is its dispatch
wrapped in :func:`run`, which is the only place a failure is printed.

A program that has a transport of its own -- HTTP, a serial port -- passes a
``translate`` to say what its exceptions mean, because "cannot reach the
charger: [Errno 111]" is a better line than the ``OSError`` underneath it.
"""

from __future__ import annotations

import sys
from typing import Callable

from devicectl.cli.exits import EXIT_ERROR, EXIT_INTERRUPTED
from devicectl.cli.output import error
from devicectl.errors import DeviceError

Translator = Callable[[BaseException], str | None]


def run(
    dispatch: Callable[[], int],
    *,
    translate: Translator | None = None,
    interrupted: str = "Interrupted.",
) -> int:
    """Run ``dispatch``, reporting an expected failure rather than raising it.

    ``translate`` is asked first and returns the line to print, or ``None``
    to let the shared handling decide.  Anything neither recognises is left
    to propagate: an unexpected exception is a bug, and a bug deserves its
    traceback.
    """
    try:
        return dispatch()
    except KeyboardInterrupt:
        # A newline first: Ctrl+C echoes at the cursor, wherever a progress
        # line had left it.
        print(f"\n{interrupted}", file=sys.stderr)
        return EXIT_INTERRUPTED
    except BaseException as exc:
        message = translate(exc) if translate is not None else None
        if message is None:
            message = _message_for(exc)
        if message is None:
            raise
        error(message)
        return EXIT_ERROR


def _message_for(exc: BaseException) -> str | None:
    """Return the line for a failure any program of this kind can have."""
    if isinstance(exc, DeviceError):
        return str(exc)
    if isinstance(exc, KeyError):
        # Something named on the command line that does not exist.  A
        # KeyError's str() is the repr of its key, which reads as a quoted
        # word rather than as a sentence, so unwrap it.
        return str(exc.args[0]) if exc.args else str(exc)
    if isinstance(exc, OSError):
        return str(exc)
    return None


__all__ = ["Translator", "run"]
