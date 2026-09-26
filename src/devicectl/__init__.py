"""Shared building blocks for single-device control programs.

Two programs -- one for a wallbox on the network, one for a battery
management system on a serial bus -- turned out to be the same program with
different protocols underneath: a subcommand table over argparse, a
serialising worker that owns the one connection, an event stream to a
build-free browser UI, and a progress protocol that lets one code path drive
both a terminal and that UI.  Everything here is the part that was the same.

Nothing in this package knows what a device is.  It has no required runtime
dependencies and imports nothing outside the standard library, so the
protocol layer -- ``httpx``, ``pyserial``, whatever the next one needs --
stays in the program that speaks it.
"""

from __future__ import annotations

__version__ = "0.1.0"
