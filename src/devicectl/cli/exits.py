"""The exit codes every program here shares.

Zero is success, 130 is the shell's convention for Ctrl-C, and 1 is
everything else -- nothing at that address, no such setting, a value the
device refused.  There is deliberately no code per failure kind; the message
on stderr says which.

A firmware upgrade is the usual exception.  It is the one command likely to
be run unattended across a fleet, where "this image is for another model"
and "it never came back" call for different reactions, so the two weights
that mean the same thing everywhere live here and a program adds whatever
further codes its own upgrade path can end in.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1  # generic failure: nothing found, comms error, invalid input
EXIT_INTERRUPTED = 130  # Ctrl-C, by the usual shell convention

# --- firmware ------------------------------------------------------------------------------

EXIT_INCOMPATIBLE = 2  # the image is not for this device
EXIT_UPDATE_FAILED = 4  # sent, but the install reached no good state

__all__ = [
    "EXIT_ERROR",
    "EXIT_INCOMPATIBLE",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_UPDATE_FAILED",
]
