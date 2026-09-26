"""Drawing a slow operation's progress on a terminal.

The one live stderr line, redrawn as things move and ended with a newline
when the phase is over.  Two things turn it off: a pipe, where nothing would
ever erase the escape codes, and a debug or trace mode, whose log lines
would shred it.  Both fall back to plain lines, which is also what a log
file wants.

What a transfer *counts* is the program's own -- bytes for one, 128-byte
blocks for another -- so :meth:`Reporter.sending` is left to the subclass.
Everything around it, including closing off a half-drawn line when a failure
lands on top of it, is here.
"""

from __future__ import annotations

import sys
from types import TracebackType

from devicectl.progress import end_live, write_live
from devicectl.report import Reporter


class TerminalReporter(Reporter):
    """Report a slow operation's progress to the terminal.

    Use it as a context manager: leaving the block closes off a live line
    that a failure would otherwise have left half-drawn, with the error
    message landing on top of it.
    """

    def __init__(self, *, quiet: bool = False) -> None:
        """Report to stderr, drawing a live line unless ``quiet`` or a pipe.

        ``quiet`` is what a program's ``--debug`` or ``--trace`` passes: its
        own logging is going to the same stream, and the two cannot share a
        line that is redrawn in place.
        """
        self.quiet = quiet
        self.live = not quiet and sys.stderr.isatty()
        self._drawn = False  # a live line is on screen, awaiting its newline
        self._last_draw = 0.0

    def __enter__(self) -> "TerminalReporter":
        """Return the reporter; nothing is drawn until something happens."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """End any live line, so the next thing printed starts on its own."""
        self._close()

    def _close(self) -> None:
        """Finish the live line, if one is on screen."""
        if self._drawn:
            end_live()
            self._drawn = False

    def _draw(self, text: str) -> None:
        """Put ``text`` on the live line (no-op when there is no live line)."""
        if not self.live:
            return
        write_live(text)
        self._drawn = True

    def step(self, message: str) -> None:
        """Print the new phase on a line of its own."""
        self._close()
        print(f"{message}...")

    def detail(self, message: str) -> None:
        """Print a detail, indented under the phase it belongs to."""
        self._close()
        print(f"  {message}")

    def warn(self, message: str) -> None:
        """Print a warning to stderr."""
        self._close()
        print(f"warning: {message}", file=sys.stderr)


__all__ = ["TerminalReporter"]
