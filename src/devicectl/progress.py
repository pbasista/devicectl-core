"""Terminal progress rendering for whatever takes long enough to need it.

Plain stderr ``#``/``-`` bars carrying a percentage, elapsed time and an
estimate of what is left.  The live-updating single line is used only when
stderr is a TTY; piped, or in ``--debug``, the callers log discrete lines
instead.  No progress-bar dependency -- a few dozen lines do the job.
"""

from __future__ import annotations

import sys

# Width, in characters, of the textual progress bar.
PROGRESS_BAR_WIDTH = 24
# Don't redraw more often than this.  It smooths the burst at the start of an
# upload, where the first chunks only fill a buffer, and it keeps a stream of
# small blocks from redrawing faster than a terminal can usefully show.
PROGRESS_MIN_INTERVAL_S = 0.1
# Granularity of a countdown while waiting for a device to come back.
PROGRESS_TICK_S = 1.0
# With no live bar to draw, log a progress line each time this fraction of the
# body has gone by (0.1 -> every ~10%).
PROGRESS_DEBUG_STEP = 0.1

BYTES_PER_KB = 1000
BYTES_PER_MB = 1_000_000
SECONDS_PER_MINUTE = 60
# Here because the two programs had each worked out for themselves, in four
# modules apiece, how long an hour and a day are.
SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE
SECONDS_PER_DAY = 24 * SECONDS_PER_HOUR


def fmt_duration(seconds: float) -> str:
    """Format a duration compactly, e.g. ``42s`` or ``3m05s``."""
    total = int(seconds)
    if total < SECONDS_PER_MINUTE:
        return f"{total}s"
    return f"{total // SECONDS_PER_MINUTE}m{total % SECONDS_PER_MINUTE:02d}s"


def bar(fraction: float) -> str:
    """Return a fixed-width ``#``/``-`` bar for ``fraction`` clamped to [0, 1]."""
    fraction = max(0.0, min(1.0, fraction))
    filled = int(fraction * PROGRESS_BAR_WIDTH)
    return "#" * filled + "-" * (PROGRESS_BAR_WIDTH - filled)


def write_live(text: str) -> None:
    """Overwrite the current stderr line with ``text`` (only when stderr is a TTY)."""
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[K" + text)  # \r + clear-to-end-of-line
        sys.stderr.flush()


def end_live() -> None:
    """End a live progress line with a newline (only when stderr is a TTY)."""
    if sys.stderr.isatty():
        sys.stderr.write("\n")
        sys.stderr.flush()


__all__ = [
    "BYTES_PER_KB",
    "BYTES_PER_MB",
    "PROGRESS_BAR_WIDTH",
    "PROGRESS_DEBUG_STEP",
    "PROGRESS_MIN_INTERVAL_S",
    "PROGRESS_TICK_S",
    "SECONDS_PER_DAY",
    "SECONDS_PER_HOUR",
    "SECONDS_PER_MINUTE",
    "bar",
    "end_live",
    "fmt_duration",
    "write_live",
]
