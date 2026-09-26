"""Running one read over every device the command line named.

One device is the ordinary case and stays exactly as it was: no heading, no
wrapper, the same bytes on standard output.  Several is the interesting one.
A handler prints its own output, which is right for one and wrong for four:
``--json`` over four devices should answer with one document keyed by
device, not with four documents concatenated into something no parser will
take.  So the printing is diverted for the duration, and the handler never
learns that it fanned out.

Only reads fan out.  A command that reconfigures or restarts a device keeps
its single target, because "do this to every one I own" is not a thing to
type by accident, and each of them wants its own diff and its own
confirmation anyway.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Callable, TypeVar

from devicectl.cli.exits import EXIT_ERROR, EXIT_OK
from devicectl.cli.main import Translator
from devicectl.cli.output import collect_json, error, render_json, stop_collecting
from devicectl.errors import DeviceError

DeviceT = TypeVar("DeviceT")

# How wide the heading rule is drawn over a section.
HEADING_WIDTH = 56


def parse_range(text: str | int, limit: range, *, what: str = "id") -> list[int] | None:
    """Parse one address, a comma list, a range, or ``all``.

    ``None`` means "every address there is", which usually cannot be
    resolved until something is open, so it is left for the caller to sweep
    for.  Order is kept and repeats are dropped, so ``2,1,2`` reads two
    devices in the order asked for.
    """
    if isinstance(text, int):  # a caller that already had a number
        text = str(text)
    text = text.strip().lower()
    if text in ("all", "*"):
        return None
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part[1:]:  # a range, e.g. 1-4; a leading - is not one
            low, _, high = part.partition("-")
            out.extend(range(int(low), int(high) + 1))
        else:
            out.append(int(part))
    if not out:
        raise ValueError(f"cannot read --{what} {text!r}")
    for value in out:
        if value not in limit:
            raise ValueError(
                f"--{what} {value} is outside {limit.start}..{limit.stop - 1}"
            )
    return list(dict.fromkeys(out))


def fan_out(
    devices: Sequence[DeviceT],
    one: Callable[[DeviceT], int],
    *,
    key: Callable[[DeviceT], str],
    heading: Callable[[DeviceT], str],
    as_json: bool = False,
    translate: Translator | None = None,
) -> int:
    """Run ``one(device)`` over each device and combine the results.

    With one device this is exactly what the handler did before.  With
    several, each section gets a heading, and ``--json`` comes back as one
    object keyed by ``key`` rather than as several documents in a row.

    The exit code is the first failure, so a bank with one silent device
    says so, and the others are still read.  ``translate`` says what a
    program's own transport failures mean, exactly as it does for
    :func:`devicectl.cli.main.run`: over several devices they have to be
    caught here instead, or the first charger that does not answer would
    take the other three with it.
    """
    if not devices:
        return EXIT_OK
    if len(devices) == 1:
        return one(devices[0])
    combined: dict[str, Any] = {}
    worst = EXIT_OK
    for index, device in enumerate(devices):
        if as_json:
            collected = collect_json()
            try:
                code = _guarded(one, device, heading(device), translate)
            finally:
                stop_collecting()
            combined[key(device)] = (
                collected[0] if len(collected) == 1 else list(collected)
            )
        else:
            if index:
                print()
            title = heading(device)
            print(f"=== {title} " + "=" * max(0, HEADING_WIDTH - len(title)))
            print()
            code = _guarded(one, device, title, translate)
        worst = worst or code
    if as_json:
        render_json(combined)
    return worst


def _guarded(
    one: Callable[[Any], int],
    device: Any,
    title: str,
    translate: Translator | None = None,
) -> int:
    """Run one device's read, turning a silent device into a message and a code.

    Anything neither the shared error nor the program's ``translate``
    recognises is left to propagate: a bug is a bug on four devices too, and
    it deserves its traceback rather than a heading and a 1.
    """
    try:
        return one(device)
    except DeviceError as exc:
        error(f"{title}: {exc}")
        return EXIT_ERROR
    except Exception as exc:
        message = translate(exc) if translate is not None else None
        if message is None:
            raise
        error(f"{title}: {message}")
        return EXIT_ERROR


__all__ = ["fan_out", "parse_range"]
