"""Printing: tables, label/value blocks, the one timestamp format, the one prompt.

Every command that shows something goes through here, so column widths, JSON
shape and clock formatting stay the same across dozens of commands without
each one deciding for itself.  :func:`confirm` is here for the same reason:
the commands that can wreck something should all ask about it the same way.

Messages on stderr open with ``error:`` when the command failed, ``warning:``
when it carried on regardless, and ``note:`` for an advisory -- so a script
reading stderr can tell the three apart without parsing prose.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable

from devicectl.cli.exits import EXIT_ERROR, EXIT_OK

# How clock readings are printed: seconds matter, sub-seconds do not.
CLOCK_FORMAT = "%Y-%m-%d %H:%M:%S"

# Longest a value may be before the table view abbreviates it.
TABLE_VALUE_MAX = 40

# The answers that mean yes.  Anything else, including silence, is no.
YES = ("y", "yes")

# The character widths `unicodedata` calls double.
WIDE = ("W", "F")


def confirm(question: str) -> bool:
    """Ask a yes/no question; anything but an explicit yes is a no.

    End-of-file counts as a no, so a command left to run unattended stops
    rather than raising at the prompt.  Every command that asks also takes
    ``-y`` to skip the question; declining exits non-zero, because nothing
    that was asked for was done.
    """
    try:
        return input(f"{question} [y/N] ").strip().lower() in YES
    except EOFError:
        return False


def display_width(text: str) -> int:
    """Return how many terminal columns ``text`` occupies.

    A CJK character takes two columns while ``len`` counts it as one, so a
    table padded by ``len`` comes out visibly ragged wherever one appears --
    and a recovered protocol table is exactly where one appears.
    """
    return sum(2 if unicodedata.east_asian_width(c) in WIDE else 1 for c in text)


def _pad(text: str, width: int) -> str:
    """Left-align ``text`` in ``width`` terminal columns."""
    return text + " " * max(0, width - display_width(text))


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Print a left-aligned table with columns sized to their widest cell."""
    if not rows:
        return
    widths = [display_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], display_width(cell))
    print("  ".join(_pad(h, widths[i]) for i, h in enumerate(headers)).rstrip())
    for row in rows:
        print("  ".join(_pad(c, widths[i]) for i, c in enumerate(row)).rstrip())


def print_rows(title: str, rows: list[tuple[str, str]]) -> None:
    """Print a titled block of aligned label/value lines."""
    if not rows:
        return
    if title:
        print(f"{title}:\n")
    width = max(display_width(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {_pad(label, width)}  {value}")


def print_sections(
    title: str, sections: list[tuple[str, list[tuple[str, str]]]]
) -> None:
    """Print a titled block of label/value lines under headings.

    For a reading a web page splits into cards: the headings are the cards'
    titles, so a row can carry the card's short name for it ("Average" under
    *Cells*) and still say what it is.  The values line up across sections,
    so the block reads as one list.  An empty section is left out.
    """
    sections = [(heading, rows) for heading, rows in sections if rows]
    if not sections:
        return
    if title:
        print(f"{title}:")
    width = max(display_width(label) for _, rows in sections for label, _ in rows)
    for heading, rows in sections:
        print(f"\n  {heading}")
        for label, value in rows:
            print(f"    {_pad(label, width)}  {value}")


# Where a --json document goes while a command is running over several
# devices.
#
# A handler prints its own JSON, which is right for one device and wrong for
# four: a fan-out should answer with one document keyed by device, not with
# four documents concatenated into something no parser will take.
# :mod:`devicectl.cli.fanout` redirects the printing here for the duration,
# so every --json command fans out without each of them learning how.
_collector: list[Any] | None = None


def collect_json() -> list[Any]:
    """Divert :func:`print_json` into a list until :func:`stop_collecting`."""
    global _collector
    _collector = []
    return _collector


def stop_collecting() -> None:
    """Send :func:`print_json` back to standard output."""
    global _collector
    _collector = None


def print_json(doc: Any) -> None:
    """Print a document as the one JSON shape every command emits."""
    if _collector is not None:
        _collector.append(doc)
        return
    render_json(doc)


def render_json(doc: Any) -> None:
    """Print a document, whatever the collector is doing.  For fanout's own use."""
    print(json.dumps(doc, indent=2, default=_jsonable))


def shorten(value: Any, limit: int = TABLE_VALUE_MAX) -> str:
    """Return a short one-line rendering of a value, for tables and diffs."""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def note(message: str) -> None:
    """Print an advisory to stderr, so a script's own output is undisturbed."""
    print(f"note: {message}", file=sys.stderr)


def warn(message: str) -> None:
    """Print a warning to stderr: the command carried on regardless."""
    print(f"warning: {message}", file=sys.stderr)


def error(message: str) -> None:
    """Print a failure to stderr, with the prefix argparse itself uses."""
    print(f"error: {message}", file=sys.stderr)


def aborted() -> None:
    """Say that a confirmation was declined.  Not a malfunction."""
    print("Aborted.", file=sys.stderr)


# --- the two ends of a file argument --------------------------------------------------------
#
# Two rules, and neither of them is ours: they are what a person who has used
# a Unix command line before already expects.
#
# A file argument of ``-`` is the standard stream -- output where a file
# would be written, input where one would be read.  It is obeyed wherever it
# is typed, terminal or not: somebody who types it means it, and an explicit
# argument that is quietly ignored is the kind of surprise that makes a
# program feel untrustworthy on the day it is met.
#
# Nothing a person did not ask for is overwritten.  Every path that would
# land on an existing file goes through :func:`may_overwrite`, which asks;
# ``-y``/``--yes`` is how a script says yes in advance.


# What a file argument spells when it means the standard stream.
STDIO = "-"


def is_stdio(file: str | Path | None) -> bool:
    """Whether a file argument names the standard stream rather than a file."""
    return file is not None and str(file) == STDIO


def may_overwrite(path: Path, *, yes: bool = False) -> bool:
    """Whether ``path`` may be written, asking first if something is there.

    Says "Aborted." itself on a no, because every caller said exactly that.
    """
    if not path.exists() or yes:
        return True
    if confirm(f"'{path}' already exists. Overwrite?"):
        return True
    aborted()
    return False


def read_in(file: str | Path | None) -> str:
    """Return the text of ``file``, or of standard input when it is ``-``.

    The reading half of :func:`write_out`, and the reason every command that
    takes a file takes a pipe as well: ``curl ... | alfenctl import -``.  A
    missing file raises :class:`OSError`, which callers with something to add
    ("cannot read the tag list: ...") catch, and the rest let the error
    funnel print.

    Binary inputs keep their own path: a firmware package is identified partly
    by its filename, and a stream has none.
    """
    if file is None or is_stdio(file):
        return sys.stdin.read()
    return Path(file).read_text(encoding="utf-8")


def write_out(
    payload: str | bytes,
    file: str | Path | None,
    *,
    default_name: str | Callable[[], str],
    yes: bool = False,
    summary: str = "",
) -> int:
    """Write ``payload`` where the caller asked, and return an exit code.

    ``-`` is standard output, on a terminal as much as through a pipe.  With
    no file named at all there is nothing to obey, so the two differ: a pipe
    gets the bytes, and a terminal gets ``default_name`` in the working
    directory, because a dump nobody redirected would only scroll past.

    ``default_name`` may be a callable, for the callers that have to ask the
    device its name to build one: a piped export then never asks.

    ``summary`` says what was written ("412 log lines"), for the callers that
    would rather count than just name the path.
    """
    target: str | Path | None = file
    if target is None and sys.stdout.isatty():
        target = default_name if isinstance(default_name, str) else default_name()
    if target is None or is_stdio(target):
        if isinstance(payload, str):
            sys.stdout.write(payload)
        else:
            sys.stdout.buffer.write(payload)
        return EXIT_OK
    path = Path(str(target))
    if not may_overwrite(path, yes=yes):
        return EXIT_ERROR
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_bytes(payload)
    print(f"Wrote {summary} to {path}" if summary else f"Wrote {path}", file=sys.stderr)
    return EXIT_OK


def _jsonable(value: Any) -> Any:
    """Render the few non-JSON types a decoded field can hold."""
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


__all__ = [
    "CLOCK_FORMAT",
    "STDIO",
    "TABLE_VALUE_MAX",
    "aborted",
    "collect_json",
    "confirm",
    "display_width",
    "error",
    "is_stdio",
    "may_overwrite",
    "note",
    "print_json",
    "print_rows",
    "print_sections",
    "print_table",
    "read_in",
    "render_json",
    "shorten",
    "stop_collecting",
    "warn",
    "write_out",
]
