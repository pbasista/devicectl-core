"""How far a device's clock is from this computer's, in words.

Both programs show it, on the same card of the same dashboard, and each had
its own sentence for it: one said "3.5 hours ahead of this computer", the
other printed a bare "03h 30m" beside a label and left the direction out
altogether.  One phrase now, with the direction in it and the computer left
out of it -- the page is often read on a different machine from the one the
program runs on, and "this computer" is then the wrong one.
"""

from __future__ import annotations

from devicectl.progress import SECONDS_PER_DAY, SECONDS_PER_HOUR, SECONDS_PER_MINUTE

# Closer than this and the two clocks are the same clock.  A reading takes a
# round trip, and a device that answers in a second and a half is not a
# device a second and a half out.
DRIFT_NOISE_S = 2.0

SECONDS_PER_YEAR = 365 * SECONDS_PER_DAY

IN_SYNC = "in sync"

# The scale matters: a device that has never been set is not seconds out but
# years, since it boots with its clock at whatever its firmware was built on.
_UNITS = (
    (SECONDS_PER_MINUTE, 1, "second", 0),
    (SECONDS_PER_HOUR, SECONDS_PER_MINUTE, "minute", 0),
    (SECONDS_PER_DAY, SECONDS_PER_HOUR, "hour", 1),
    (SECONDS_PER_YEAR, SECONDS_PER_DAY, "day", 1),
    (None, SECONDS_PER_YEAR, "year", 1),
)


def format_drift(seconds: float | None) -> str | None:
    """Say ``12 seconds ahead``, ``2.5 years behind`` or ``in sync``.

    Positive is a device clock ahead of this computer's.  None -- a device
    that reports no time -- is handed back as None, because what to say
    instead is the caller's: a charger and a battery do not fail to have a
    clock for the same reason.
    """
    if seconds is None:
        return None
    size = abs(seconds)
    if size < DRIFT_NOISE_S:
        return IN_SYNC
    for below, per, word, digits in _UNITS:
        if below is None or size < below:
            amount = round(size / per, digits)
            said = f"{amount:.{digits}f}"
            plural = "" if amount == 1 else "s"
            return f"{said} {word}{plural} {'ahead' if seconds > 0 else 'behind'}"
    raise AssertionError("unreachable: the last unit has no upper bound")


__all__ = ["DRIFT_NOISE_S", "IN_SYNC", "format_drift"]
