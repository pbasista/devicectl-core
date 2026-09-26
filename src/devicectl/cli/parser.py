"""Filling in what the command line left out.

Two small rewrites of ``argv`` before argparse ever sees it.  Both exist for
the same reason: the options a command needs live on its *action* parsers,
so a word left out is not merely a missing word -- it takes the options with
it, and argparse's complaint names the wrong thing.

Both leave a leading word alone, so a mistyped command or action still gets
argparse's "invalid choice" rather than being quietly handed somewhere else,
and both leave ``-h`` alone, because it has to reach the parser that lists
the choices.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# Options that must reach the root parser rather than a subcommand.
ROOT_OPTIONS = ("-h", "--help", "--version")

HELP_OPTIONS = ("-h", "--help")


def insert_default_action(
    argv: Sequence[str], defaults: Mapping[str, str]
) -> list[str]:
    """Return ``argv`` with a command's default ACTION filled in, if missing.

    ``<prog> tags`` becomes ``<prog> tags list``, and options meant for that
    default action are handed through to it (``<prog> scn --peers`` becomes
    ``<prog> scn status --peers``).
    """
    argv = list(argv)
    if not argv or argv[0] not in defaults:
        return argv
    rest = argv[1:]
    if rest and (not rest[0].startswith("-") or rest[0] in HELP_OPTIONS):
        return argv
    return [argv[0], defaults[argv[0]], *rest]


def insert_default_command(argv: Sequence[str], command: str) -> list[str]:
    """Return ``argv`` with ``command`` filled in when no command was typed.

    For a program with an obvious thing to do when run bare -- serving its
    web interface, usually -- because a page of usage text is not what
    somebody who typed the bare name came for.  Options meant for it are
    handed through, so ``<prog> --port 8080`` reaches that command rather
    than being told ``--port`` belongs to a subcommand.
    """
    argv = list(argv)
    if not argv:
        return [command]
    first = argv[0]
    if first in ROOT_OPTIONS or not first.startswith("-"):
        return argv
    return [command, *argv]


def default_actions(commands: Mapping[str, Any]) -> dict[str, str]:
    """Read every command's default ACTION out of the command table.

    The alternative is a second table listing them, which has to be kept in
    step with the first by hand and silently does nothing when it is not --
    a default named for a command that no longer exists never fires, and a
    command that grew actions never gets one.
    """
    return {
        name: command.default_action
        for name, command in commands.items()
        if getattr(command, "default_action", "")
    }


__all__ = [
    "default_actions",
    "insert_default_action",
    "insert_default_command",
]
