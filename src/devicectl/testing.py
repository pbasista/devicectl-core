"""Check that a fake can be handed to code that expects the real thing.

Every one of these programs tests its commands against a fake device, and
nothing has ever held the fake to the shape of the thing it stands in for.
A fake drifts silently: the client grows a parameter, the fake does not,
and the tests go on passing against a device that cannot exist.  Worse,
they pass against a *call* that cannot be made -- the fake answers
``login()`` while every real caller says ``login(timeout=...)``.

:func:`stands_in_for` compares the two by signature, and says what would
break.  It is not a type checker: it looks only at how each method may be
called, which is the part a fake gets wrong.

``real`` may be a class or a :class:`~typing.Protocol`, and the difference
is how strict the answer is.  A class is a menu -- a fake implements the
handful of methods its tests reach, and only those are compared.  A
protocol is a contract -- every member of it must be there.
"""

from __future__ import annotations

import inspect
from collections.abc import Container, Iterator
from typing import Any

__all__ = ["assert_stands_in_for", "stands_in_for"]

_POSITIONAL = (
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
)


def _members(cls: type, ignore: Container[str]) -> Iterator[tuple[str, Any]]:
    """Walk the public callables ``cls`` itself defines, in definition order."""
    for name, value in vars(cls).items():
        if name.startswith("_") or name in ignore:
            continue
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        if callable(value):
            yield name, value


def _is_protocol(cls: type) -> bool:
    """Whether ``cls`` is a Protocol rather than an ordinary class."""
    return bool(getattr(cls, "_is_protocol", False))


def _protocol_names(cls: type, ignore: Container[str]) -> list[str]:
    """List every member a protocol asks an implementation for."""
    names = set(getattr(cls, "__protocol_attrs__", ()))
    if not names:  # before 3.12 the set is not kept on the class
        names = {n for n in vars(cls) if not n.startswith("_")}
        names |= {
            n for n in getattr(cls, "__annotations__", {}) if not n.startswith("_")
        }
    return sorted(n for n in names if n not in ignore)


def _params(fn: Any) -> list[inspect.Parameter]:
    """``fn``'s parameters, without the one the instance fills in."""
    try:
        found = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):  # a C function with no signature to read
        return []
    return found[1:] if found and found[0].name in ("self", "cls") else found


def _absorbs(params: list[inspect.Parameter]) -> tuple[bool, bool]:
    """Whether these parameters end in ``*args``, and whether in ``**kwargs``."""
    kinds = {p.kind for p in params}
    return (
        inspect.Parameter.VAR_POSITIONAL in kinds,
        inspect.Parameter.VAR_KEYWORD in kinds,
    )


_EMPTY = inspect.Parameter.empty


def _wrong_position(
    want: inspect.Parameter,
    here: inspect.Parameter | None,
    at: int,
    absorbs: tuple[bool, bool],
) -> str | None:
    """Say why ``want`` could not be passed by position, or nothing."""
    if here is None or here.kind not in _POSITIONAL:
        return None if all(absorbs) else f"takes no {want.name} in position {at}"
    # A positional-only parameter is passed by position alone, so the name
    # the stand-in gives it is its own business.
    if want.kind is not inspect.Parameter.POSITIONAL_ONLY and here.name != want.name:
        return f"calls {want.name} '{here.name}'"
    return None


def _wrong_keyword(
    want: inspect.Parameter,
    here: inspect.Parameter | None,
    star_kwargs: bool,
) -> str | None:
    """Say why ``want`` could not be passed by name, or nothing."""
    if here is not None and here.kind is not inspect.Parameter.POSITIONAL_ONLY:
        return None
    return None if star_kwargs else f"does not take {want.name}="


def _missing_star(want: inspect.Parameter, absorbs: tuple[bool, bool]) -> str | None:
    """Say why a ``*args`` or ``**kwargs`` of ``want`` has nowhere to go."""
    star_args, star_kwargs = absorbs
    if want.kind is inspect.Parameter.VAR_POSITIONAL and not star_args:
        return f"does not take *{want.name}"
    if want.kind is inspect.Parameter.VAR_KEYWORD and not star_kwargs:
        return f"does not take **{want.name}"
    return None


def _call_differences(real: Any, fake: Any) -> list[str]:
    """List every way ``real`` may be called that ``fake`` would not survive."""
    wanted = _params(real)
    given = _params(fake)
    absorbs = _absorbs(given)
    by_name = {p.name: p for p in given}
    positional = [p for p in given if p.kind in _POSITIONAL]
    problems: list[str] = []

    at = 0
    for want in wanted:
        if want.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            problems += filter(None, [_missing_star(want, absorbs)])
            continue
        if want.kind in _POSITIONAL:
            here = positional[at] if at < len(positional) else None
            at += 1
            wrong = _wrong_position(want, here, at, absorbs)
        else:
            here = by_name.get(want.name)
            wrong = _wrong_keyword(want, here, absorbs[1])
        if wrong:
            problems.append(wrong)
        elif want.default is not _EMPTY and here is not None and here.default is _EMPTY:
            problems.append(f"insists on {want.name}, which is optional")

    asked = {p.name for p in wanted}
    spares = positional[at:] + [
        p for p in given if p.kind is inspect.Parameter.KEYWORD_ONLY
    ]
    for spare in spares:
        if spare.default is _EMPTY and spare.name not in asked:
            problems.append(f"insists on {spare.name}, which no caller passes")
    return problems


def stands_in_for(real: type, fake: type, *, ignore: Container[str] = ()) -> list[str]:
    """List what stops ``fake`` being handed to code that expects ``real``.

    Returns a sentence per problem, and an empty list when there is none.
    Names in ``ignore`` are skipped on both sides, which is how a fake keeps
    a helper of its own that the real thing has no reason to grow.
    """
    complaints = []
    # A protocol is a lower bound, so a stand-in for one may do more than it
    # asks; a class is the whole menu, so anything extra is a call nothing
    # makes.  `serial.Serial` is the case that proves it: it satisfies every
    # serial protocol worth writing and has thirty methods besides.
    contract = _protocol_names(real, ignore) if _is_protocol(real) else None
    for name, method in _members(fake, ignore):
        if contract is not None and name not in contract:
            continue
        against = getattr(real, name, None)
        if against is None:
            complaints.append(
                f"{fake.__name__}.{name} answers a call {real.__name__} does not take"
            )
            continue
        for problem in _call_differences(against, method):
            complaints.append(f"{fake.__name__}.{name} {problem}")

    if contract is not None:
        for name in contract:
            if not hasattr(fake, name):
                complaints.append(f"{fake.__name__} has no {name}")
    return complaints


def assert_stands_in_for(
    real: type, fake: type, *, ignore: Container[str] = ()
) -> None:
    """Fail the test, readably, if ``fake`` could not stand in for ``real``."""
    problems = stands_in_for(real, fake, ignore=ignore)
    if problems:
        raise AssertionError(
            f"{fake.__name__} cannot stand in for {real.__name__}:\n  "
            + "\n  ".join(problems)
        )
