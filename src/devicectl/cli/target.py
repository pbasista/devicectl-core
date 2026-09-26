"""Choosing between sources of the same setting.

Which device a command talks to can be answered by the command line, by a
named table in the config file, by that file's own defaults, or by whatever
the program can discover for itself.  Each program resolves that its own way,
but they all resolve it by asking the same question of one value after
another, and this is that question.
"""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def first_set(*values: T | None, default: T) -> T:
    """Return the first value that was actually given, else ``default``.

    Sources are passed most specific first, so a precedence rule reads as one
    line instead of a stack of conditionals.
    """
    return next((value for value in values if value is not None), default)


__all__ = ["first_set"]
