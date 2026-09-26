"""What one subcommand is: a function to run, and what it needs open first.

The alternative -- a chain of ``if args.command == ...`` -- hid three
different things in the same shape: which function runs, whether the link to
the device is needed at all, and whether it has to be ready to use.  Two
commands in thirty need less than the rest, and in a chain that is a special
case buried at the top; here it is a field.

The web API's ``ROUTES`` table is the same idea for the same reason.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# Every handler takes the same two arguments whether it wants them or not, so
# the table can stay one shape.  What arrives first depends on the Need the
# command declares -- None, or whatever the program opened -- and each handler
# annotates the one it actually takes.  No single signature describes all
# three, so this one leaves the first parameter open rather than pretending
# otherwise.
Handler = Callable[[Any, argparse.Namespace], int]


class Need(Enum):
    """What has to be open before a command's handler runs.

    Three levels, because every program here has found it needs exactly
    three.  What they *mean* is the program's own: for a charger the link is
    a connection and ready is a logged-in session; for a serial bus the link
    is the open port and ready is one addressed unit on it.
    """

    NOTHING = "nothing"
    """No device at all: browsing for one, or starting the web server."""

    LINK = "link"
    """The link is open, but nothing has been selected or authenticated on it."""

    READY = "ready"
    """Open, and usable: logged in, or addressed.  Almost everything."""


@dataclass(frozen=True)
class Command:
    """One subcommand."""

    run: Handler
    needs: Need = Need.READY
    per_action: Mapping[str, Need] = field(default_factory=dict)
    """Actions of this command that need less than the command itself."""

    default_action: str = ""
    """What this command does when its ACTION is left out.

    Declared here rather than in a table beside the parser, because a
    separate table has to be kept in step with this one by hand and fails
    silently when it is not: a default named for a command that no longer
    exists never fires, and a command that grew actions never gets one.

    Only an action that needs no further input and changes nothing belongs
    here.  A command whose every action writes -- setting a password, say --
    deliberately has none, so typing the bare command is a usage error
    rather than a surprise.
    """

    fans_out: bool | Sequence[str] = False
    """Whether this command may be run over several devices at once.

    ``True`` for a command that only reads, a list of action names for one
    where only some of its actions do (``fans_out=("show",)``), and the
    default for everything else.  Only reads fan out: "do this to every
    device I own" is not a thing to type by accident, and each of them wants
    its own diff and its own confirmation anyway.

    A program whose devices are addressed one at a time reads this before it
    opens anything; :mod:`devicectl.cli.fanout` is what it does afterwards.
    """

    def need(self, action: str | None) -> Need:
        """Return what this command needs open, given the ACTION it was called with."""
        return self.per_action.get(action or "", self.needs)

    def fans_out_for(self, action: str | None) -> bool:
        """Whether this command fans out, given the ACTION it was called with."""
        if isinstance(self.fans_out, bool):
            return self.fans_out
        return (action or "") in self.fans_out


__all__ = ["Command", "Handler", "Need"]
