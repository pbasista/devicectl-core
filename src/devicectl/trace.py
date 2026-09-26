"""A recording of what actually went over the wire, and the report it makes.

When a device refuses something, the sentence a program can show is the one
its protocol layer raised -- "no/short response", "bad CRC", "modbus
exception 2".  That is enough to know it failed and never enough to know
why: the answer is in the bytes, in what was asked immediately before, and
in the gaps between them, none of which anybody is holding by the time the
message reaches a browser.

So: a ring buffer that can be switched on from the page, a hook the
protocol layer already has for ``--trace`` to feed it, and a plain-text
report to download and send to somebody.  It is off until asked for -- a
few hundred frames a minute of a device that is working is nothing anyone
wants to keep -- and bounded when it is on, because the thing being
debugged is often a program left running overnight.

An entry need not be a frame.  Half of a fault report is what somebody
asked for, so the same recording takes :meth:`Recorder.called` -- the
request a page made and the body it sent -- and prints it between the
frames it caused.

Nothing here knows a protocol.  An entry is a direction, some bytes and a
note, and what those bytes *mean* is a ``decode`` function the program
supplies: :func:`render` calls it per frame and prints whatever it returns
under the hex.  A program with no decoder gets the hex, which is still the
thing that was missing.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

# How many entries a recording keeps before the oldest fall off.  A Modbus
# beat is two frames every three seconds, so this is a few hours of an idle
# page and a good many minutes of somebody actively poking at a device --
# and about four megabytes of report at the worst, which is still a file an
# email will take.
DEFAULT_LIMIT = 20000

# The directions an entry can have.  They are two characters wide on
# purpose: the report puts them in a column.
TX = "TX"
RX = "RX"
NOTE = "--"
UI = "UI"

# How many bytes of a frame go on one line of the report.
HEX_WIDTH = 16


@dataclass(frozen=True)
class Entry:
    """One thing that happened on the link, as the report will print it."""

    at: float
    """When, as a wall clock time -- the report is read against a log."""

    direction: str
    """:data:`TX`, :data:`RX`, :data:`UI` for something the page asked for, or
    :data:`NOTE` for anything else that is not a frame."""

    data: bytes = b""
    """The bytes themselves.  Empty for a note, and for a read that timed out."""

    note: str = ""
    """What the program was doing, in its own words, or what a note says."""

    detail: str = ""
    """Text printed under the entry where the hex would go: a request body, a
    decoded structure, anything that is already words rather than bytes."""


class Recorder:
    """A bounded, thread-safe recording of a link, off until it is started.

    The protocol layer calls :meth:`add` from whichever thread owns the
    link; the page asks for :meth:`state` and :func:`render` from a request
    thread.  Hence the lock, and hence :meth:`add` doing as little as it
    can while holding it.
    """

    def __init__(self, limit: int = DEFAULT_LIMIT) -> None:
        """Make a recorder that keeps at most ``limit`` entries."""
        self.limit = limit
        self._lock = threading.Lock()
        self._entries: deque[Entry] = deque(maxlen=limit)
        self._on = False
        self._started: float | None = None
        self._dropped = 0
        self._automatic = False
        self._stopped: float | None = None

    # --- switching it on and off --------------------------------------------------------

    @property
    def on(self) -> bool:
        """Whether anything handed to :meth:`add` is being kept."""
        return self._on

    def start(self, automatic: bool = False) -> None:
        """Begin recording, from empty.

        From empty rather than from wherever the last one stopped: a
        recording is made to reproduce one thing, and frames from an hour
        ago are noise somebody then has to scroll past.

        ``automatic`` is for a recording nobody asked for by hand -- one a
        failure started (see the page's trace.js).  It is kept so the page
        can say so, and so that turning that behaviour off can stop the
        recording it made without stopping one somebody started themselves.
        """
        with self._lock:
            self._entries.clear()
            self._dropped = 0
            self._on = True
            self._started = time.time()
            self._stopped = None
            self._automatic = automatic
        self.say("recording started" + (" after a failure" if automatic else ""))

    def stop(self) -> None:
        """Stop recording, keeping what was recorded.

        Kept deliberately: the ordinary way this is used is reproduce the
        fault, stop, download.  A stop that emptied the buffer would throw
        away the recording at the moment it became worth having.
        """
        if not self._on:
            return
        self.say("recording stopped")
        with self._lock:
            self._on = False
            self._automatic = False
            self._stopped = time.time()

    def clear(self) -> None:
        """Throw away what was recorded, without changing whether it is on."""
        with self._lock:
            self._entries.clear()
            self._dropped = 0
            self._started = time.time() if self._on else None

    # --- recording ----------------------------------------------------------------------

    def add(
        self, direction: str, data: bytes, note: str = "", detail: str = ""
    ) -> None:
        """Record one frame.  Does nothing at all while the recorder is off."""
        if not self._on:
            return
        with self._lock:
            if len(self._entries) == self.limit:
                self._dropped += 1
            self._entries.append(
                Entry(
                    at=time.time(),
                    direction=direction,
                    data=bytes(data),
                    note=note,
                    detail=detail,
                )
            )

    def say(self, note: str) -> None:
        """Record something that is not a frame: an error, a phase, a choice.

        This is the half a byte log cannot carry.  "no reply" is a silence,
        and a silence looks exactly like the end of the recording unless
        something writes it down.
        """
        self.add(NOTE, b"", note)

    def called(self, note: str, detail: str = "") -> None:
        """Record something somebody asked the program to do.

        A recording of a wire answers what the program said; it never
        answers why it said it.  Half of a fault report is which button was
        pressed and what was in the box beside it -- which, for a program
        driven by a page, is the request the page made and the body it sent.
        Those go in here, between the frames they caused.
        """
        self.add(UI, b"", note, detail=detail)

    def hook(
        self, note: Callable[[], str] | None = None
    ) -> Callable[[str, bytes], None]:
        """Return a ``log(direction, data)`` of the shape protocol layers take.

        ``note`` is asked, per frame, what the program is doing right now --
        which is the one thing the bytes cannot say and the first thing
        anybody reading the report wants.
        """
        return lambda direction, data: self.add(
            direction, data, note() if note is not None else ""
        )

    # --- what the page shows ------------------------------------------------------------

    def entries(self) -> list[Entry]:
        """Everything kept, oldest first."""
        with self._lock:
            return list(self._entries)

    def state(self) -> dict[str, Any]:
        """Describe the recording as the page draws it: on, how much, since when."""
        with self._lock:
            return {
                "on": self._on,
                "automatic": self._on and self._automatic,
                "frames": len(self._entries),
                "dropped": self._dropped,
                "since": self._started,
                # When it stopped, so a stopped recording says how long it
                # ran rather than how long ago it started.
                "until": None if self._on else self._stopped,
                "limit": self.limit,
                "bytes": sum(len(e.data) + len(e.detail) for e in self._entries),
            }


def hexdump(data: bytes, width: int = HEX_WIDTH) -> list[str]:
    """Split ``data`` into lines of space-separated hex, ``width`` bytes each."""
    return [data[at : at + width].hex(" ") for at in range(0, len(data), width)]


def render(
    recorder: Recorder,
    *,
    title: str,
    facts: Sequence[tuple[str, Any]] = (),
    decode: Callable[[str, bytes], str] | None = None,
    preamble: str = "",
) -> str:
    """Render a recording as the plain-text report somebody downloads.

    ``facts`` is what the report has to say for itself before the frames --
    the program's version, the port, the speed, the device -- because a
    trace read a week later by somebody else answers nothing without them.
    ``decode`` turns one frame into a sentence; ``preamble`` is the program's
    own paragraph about how to read what follows.
    """
    entries = recorder.entries()
    state = recorder.state()
    lines = [title, "=" * len(title), ""]
    rows: list[tuple[str, str]] = [(str(k), _fact(v)) for k, v in facts]
    rows.append(("recorded", _window(entries)))
    rows.append(("frames", _count(state)))
    pad = max((len(k) for k, _ in rows), default=0)
    lines += [f"{k.ljust(pad)}   {v}" for k, v in rows]
    if preamble:
        lines += ["", *preamble.strip().splitlines()]
    lines += ["", "-" * 72, ""]
    if not entries:
        lines.append("Nothing was recorded.")
        return "\n".join(lines) + "\n"
    previous: float | None = None
    for entry in entries:
        lines += _frame(entry, previous, decode)
        previous = entry.at
    return "\n".join(lines) + "\n"


def _frame(
    entry: Entry, previous: float | None, decode: Callable[[str, bytes], str] | None
) -> list[str]:
    """One entry, as the two or more lines the report prints it on."""
    gap = "" if previous is None else f"  (+{(entry.at - previous) * 1000:.0f} ms)"
    head = f"{_clock(entry.at)}  {entry.direction}{gap}"
    if entry.note:
        head += f"  {entry.note}"
    lines = [head]
    indent = " " * 14
    lines += [indent + line for line in hexdump(entry.data)]
    lines += [indent + line for line in entry.detail.splitlines()]
    if entry.direction == RX and not entry.data:
        lines.append(indent + "(nothing)")
    said = decode(entry.direction, entry.data) if decode and entry.data else ""
    if said:
        lines.append(indent + said)
    return lines


def _clock(at: float) -> str:
    """Render a wall clock time with milliseconds, the scale a frame lives on."""
    return (
        time.strftime("%H:%M:%S", time.localtime(at)) + f".{int(at * 1000) % 1000:03d}"
    )


def _fact(value: Any) -> str:
    """Render one header value, spelling a missing one out rather than blank."""
    if value is None or value == "":
        return "(not known)"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) or "(none)"
    return str(value)


def _window(entries: Iterable[Entry]) -> str:
    """When the recording starts and stops, and how long it ran."""
    kept = list(entries)
    if not kept:
        return "(nothing)"
    first, last = kept[0].at, kept[-1].at
    day = time.strftime("%Y-%m-%d", time.localtime(first))
    return f"{day} {_clock(first)} .. {_clock(last)}  ({last - first:.1f} s)"


def _count(state: dict[str, Any]) -> str:
    """How many frames were kept, and how many fell off the front."""
    said = f"{state['frames']} kept, {state['bytes']} bytes"
    dropped = int(state["dropped"] or 0)
    if dropped:
        said += f", {dropped} older one(s) dropped (the buffer holds {state['limit']})"
    return said


__all__ = [
    "DEFAULT_LIMIT",
    "HEX_WIDTH",
    "NOTE",
    "RX",
    "TX",
    "UI",
    "Entry",
    "Recorder",
    "hexdump",
    "render",
]
