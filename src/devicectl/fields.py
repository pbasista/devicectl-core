"""One description per setting, for every place a setting has to be described.

A configuration group -- a charger's load balancing, a battery's protection
thresholds -- is a list of fields, and a program built the obvious way ends up
writing that list five times: once to decode what the device answered, once to
print it, once to serialise it for a browser, once as ``add_argument`` calls,
and once inside the function that writes it back.  The five copies drift, and
they drift quietly: a field gains a bound in the validator that the slider in
the browser does not know about, or a JSON key is renamed and the terminal
keeps the old label.

A :class:`FieldSpec` is that description written once.  It carries what the
field *is* (a number, a flag, one of an enumeration), where it lives on the
device, what it may be set to, and what each of the three audiences calls it:
a label for a terminal, a key for a document, a flag for a command line.  The
functions below then do each of the five jobs by walking the same tuple.

The device-facing halves -- :attr:`FieldSpec.address` and
:attr:`FieldSpec.wire` -- are deliberately untyped.  This module never
dereferences either: it hands them back to the caller, who knows whether an
address is an object-dictionary ``(index, sub)`` pair or a Modbus register,
and whether a wire type is an EDS code or a struct format.  What is shared is
the bookkeeping around them, not the protocol.

Nothing here is required: a group with an irregularity -- a bit field written
as two booleans, a value that is really two registers, a row that names its
neighbour -- keeps that part by hand and describes the rest.  A table that has
to grow a flag for every exception is not paying for itself.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from devicectl.errors import DeviceError

# What a field's value *is*, which decides how it is decoded, checked and
# shown.  A device's own type codes are finer than this on purpose: whether a
# count arrives as a byte or a word changes how it is encoded and nothing
# else, and the encoding is the caller's business.
NUMBER = "number"  # a real quantity: amps, volts, degrees
INTEGER = "integer"  # a whole number: seconds, a percentage, a count
TEXT = "text"  # a string
FLAG = "flag"  # on or off
ENUM = "enum"  # one of a named set of codes

READ_ONLY = "r"
READ_WRITE = "rw"

ON_OFF = ("on", "off")

# For a field that is an enumeration on the wire and a switch to a person.
ON_OFF_CODES = {"on": 1, "off": 0}


class FieldError(DeviceError, ValueError):
    """A value a field could not sensibly be given.

    A :class:`~devicectl.errors.DeviceError`, so the command line prints it as
    one line and the web server answers 400, with nothing to add at either end;
    and a :class:`ValueError`, because that is what it is.  A program whose
    group already has an error class of its own derives it from both -- ``class
    LoadBalancingError(AlfenError, FieldError)`` -- so that code catching
    either keeps catching it.
    """


@dataclass(frozen=True)
class FieldSpec:
    """One setting, described once for all five of its audiences.

    Only :attr:`name` is required.  A field with no :attr:`label` is not
    printed, one with no :attr:`json` is not serialised, one with no
    :attr:`flag` is not on the command line, and one whose :attr:`access` is
    ``r`` is never written -- which is how a table holds a group's read-only
    neighbours (a negotiated heartbeat, an address the charger was handed)
    without pretending they can be set.
    """

    name: str
    """The attribute on the state object, and the keyword :func:`values` uses."""

    kind: str = TEXT
    """One of :data:`NUMBER`, :data:`INTEGER`, :data:`TEXT`, :data:`FLAG`, :data:`ENUM`."""

    address: Any = None
    """Where the field lives on the device.  Opaque here; the caller reads it."""

    wire: Any = None
    """How a write is encoded.  Opaque here too."""

    label: str | None = None
    """What a terminal calls it, or None to leave it out of the rows."""

    json: str | None = None
    """What a document calls it, or None to leave it out."""

    flag: str | None = None
    """Its command-line flag (``--safe-current``), or None for none."""

    unit: str = ""
    """The unit a printed value carries: ``A``, ``s``, ``%``, ``W``."""

    minimum: float | None = None
    maximum: float | None = None
    """The range a value has to be inside.  For :data:`TEXT`, the length."""

    decimals: int | None = None
    """How many decimal places a reading is shown to, or None for as many as it has.

    A device that measures to the millivolt and one that measures to the volt
    both answer with a number; only the field knows which of them this is, and
    a row that prints 3.2999999 has lost that.
    """

    default: Any = None
    """What the device leaves the factory with, when the field says so.

    Shown beside the value rather than written: a settings table that can say
    "you have 2.8 V here, the default is 3.0 V" answers the question somebody
    is actually asking before they change anything.
    """

    options: Mapping[Any, str] | Sequence[Any] | None = None
    """The values allowed: a code-to-label mapping, or a plain sequence.

    A code is whatever the device's own description calls it -- an integer in
    a catalog written by hand, a decimal string in one read out of a vendor's
    datasource -- so the key type is the caller's, like :attr:`address`.
    """

    words: tuple[str, str] = ("enabled", "disabled")
    """What a :data:`FLAG` reads as when it is on and when it is off."""

    help: str | None = None
    """The command-line help, without the enumeration: that is added."""

    metavar: str | None = None
    """The command line's placeholder (``AMPS``, ``S``, ``URL``)."""

    what: str | None = None
    """How a complaint names the field: "the safe current" rather than "Safe current"."""

    access: str = READ_WRITE

    aliases: Mapping[str, Any] | None = None
    """Words the command line takes instead of raw values, and what each means.

    For a setting whose codes nobody should have to remember -- ``--mode rfid``
    rather than ``--mode 2`` -- and for one that is a code on the wire but a
    switch to a person, which is :data:`ON_OFF_CODES`.  A :data:`FLAG` gets
    ``on``/``off`` without asking.
    """

    render: Callable[[Any, Any], str | None] | None = None
    """Print this one by hand, given ``(value, state)``.  For composite rows."""

    @property
    def writable(self) -> bool:
        """Whether this field may be written at all."""
        return self.access == READ_WRITE

    @property
    def subject(self) -> str:
        """How a complaint should name this field."""
        return self.what or self.label or self.name

    @property
    def labels(self) -> Mapping[Any, str] | None:
        """The code-to-label table, when the options carry one."""
        return self.options if isinstance(self.options, Mapping) else None

    @property
    def domain(self) -> tuple[Any, ...] | None:
        """Every value this field allows, or None when it is unrestricted."""
        return None if self.options is None else tuple(self.options)


Specs = Sequence[FieldSpec]


def by_name(specs: Specs) -> dict[str, FieldSpec]:
    """Index a table by field name."""
    return {spec.name: spec for spec in specs}


def by_flag(specs: Specs) -> dict[str, FieldSpec]:
    """Index the settable fields by the command-line flag that names them."""
    return {spec.flag: spec for spec in specs if spec.flag and spec.writable}


# --- reading ----------------------------------------------------------------


def decode(spec: FieldSpec, raw: Any) -> Any:
    """Turn what the device answered into the value the state object holds.

    None for anything the device did not say, or said in a shape the field
    cannot be: a station that answers an empty string for a current has not
    reported 0 A, it has declined to answer, and the difference is the whole
    point of the em dash a dashboard draws.
    """
    if raw is None or raw == "":
        return None
    if spec.kind == TEXT:
        return str(raw)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if spec.kind == NUMBER:
        return number
    if spec.kind == FLAG:
        return bool(int(number))
    return int(number)


def harvest(specs: Specs, read: Callable[[Any], Any]) -> dict[str, Any]:
    """Decode every addressed field, given a way to look one up by address."""
    return {
        spec.name: decode(spec, read(spec.address))
        for spec in specs
        if spec.address is not None
    }


# --- checking ---------------------------------------------------------------


def _enumerated(spec: FieldSpec) -> str:
    """Name every value a field allows, the way a complaint should list them."""
    labels = spec.labels
    if labels is None:
        return "one of " + ", ".join(str(value) for value in spec.domain or ())
    return ", ".join(f"{value} ({label})" for value, label in sorted(labels.items()))


def _ranged(spec: FieldSpec, value: float) -> None:
    """Raise unless ``value`` is inside the field's range."""
    low, high = spec.minimum, spec.maximum
    if low is not None and high is None and value < low:
        if low == 0:
            raise FieldError(f"{spec.subject} cannot be negative")
        raise FieldError(f"{spec.subject} cannot be below {low:g}{_suffix(spec)}")
    if high is not None and low is None and value > high:
        raise FieldError(f"{spec.subject} cannot be above {high:g}{_suffix(spec)}")
    if low is not None and high is not None and not low <= value <= high:
        raise FieldError(
            f"{spec.subject} must be between {low:g} and {high:g}{_suffix(spec)}"
        )


def _suffix(spec: FieldSpec) -> str:
    """Return the unit as it trails a number in a sentence."""
    if not spec.unit:
        return ""
    return spec.unit if spec.unit == "%" else f" {spec.unit}"


def _coerce_text(spec: FieldSpec, value: Any) -> str:
    """Check a string against the field's enumeration and its two lengths."""
    text = str(value)
    if spec.domain is not None and text not in spec.domain:
        raise FieldError(f"{spec.subject} is {_enumerated(spec)}")
    if spec.maximum is not None and len(text) > int(spec.maximum):
        raise FieldError(f"{spec.subject} is at most {int(spec.maximum)} characters")
    if spec.minimum is not None and len(text) < int(spec.minimum):
        raise FieldError(f"{spec.subject} is at least {int(spec.minimum)} characters")
    return text


def _coerce_number(spec: FieldSpec, value: Any) -> float:
    """Read a number, refusing the two ways one can fail to be one."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FieldError(f"{spec.subject} must be a number") from None
    if math.isnan(number):
        raise FieldError(f"{spec.subject} must be a number")
    return number


def _in_domain(spec: FieldSpec, code: int) -> int:
    """Check a whole number against the field's enumeration."""
    if spec.domain is not None and code not in spec.domain:
        raise FieldError(f"{spec.subject} is {_enumerated(spec)}")
    return code


def coerce(spec: FieldSpec, value: Any) -> Any:
    """Return ``value`` as the device should be given it, or raise.

    Every refusal a device would make silently is made here instead, in the
    field's own words: out of range, outside the enumeration, too long.
    """
    if spec.kind == FLAG:
        return int(bool(value))
    if spec.kind == TEXT:
        return _coerce_text(spec, value)
    number = _coerce_number(spec, value)
    if spec.kind == ENUM:
        return _in_domain(spec, int(number))
    if spec.kind == INTEGER:
        whole = _in_domain(spec, int(number))
        _ranged(spec, whole)
        return whole
    _ranged(spec, number)
    return number


def values(specs: Specs, given: Mapping[str, Any]) -> dict[str, Any]:
    """Check every named setting, dropping the ones that were left out.

    ``given`` is whatever the caller was handed -- a namespace turned into a
    dict, a JSON body already mapped onto field names -- with None meaning
    "not named", which is how one flag out of twenty gets set without the
    other nineteen being rewritten to what they already were.
    """
    table = by_name(specs)
    out: dict[str, Any] = {}
    for name, value in given.items():
        if value is None:
            continue
        spec = table.get(name)
        if spec is None:
            raise FieldError(f"there is no {name!r} setting here")
        if not spec.writable:
            raise FieldError(f"{spec.subject} is read-only")
        out[name] = coerce(spec, value)
    return out


def writes(
    specs: Specs,
    checked: Mapping[str, Any],
    *,
    wire: Callable[[FieldSpec], Any] | None = None,
) -> dict[Any, tuple[Any, Any]]:
    """Turn checked values into ``address -> (value, wire type)`` pairs.

    ``wire`` overrides where the encoding comes from, for a device that
    reports each field's type itself rather than declaring it in a catalog.
    """
    table = by_name(specs)
    out: dict[Any, tuple[Any, Any]] = {}
    for name, value in checked.items():
        spec = table[name]
        if spec.address is None:
            raise FieldError(f"{spec.subject} has no address to write to")
        out[spec.address] = (value, spec.wire if wire is None else wire(spec))
    return out


# --- showing ----------------------------------------------------------------


def amount(spec: FieldSpec, value: float) -> str:
    """Render a number with its unit, the way a row carries it."""
    text = f"{value:g}" if isinstance(value, float) else f"{value}"
    return f"{text}{_suffix(spec)}"


def display(spec: FieldSpec, value: Any, state: Any = None) -> str | None:
    """Render one value for a terminal, or None when there is nothing to show."""
    if spec.render is not None:
        return spec.render(value, state)
    if value is None or value == "":
        return None
    if spec.kind == FLAG:
        return spec.words[0] if value else spec.words[1]
    if spec.kind == TEXT:
        return str(value)
    labels = spec.labels
    if labels is not None:
        code = int(value)
        return labels.get(code, f"unknown ({code})")
    return amount(spec, value)


def rows(specs: Specs, state: Any) -> list[tuple[str, str]]:
    """Render the labelled fields as label/value pairs, skipping what is absent."""
    out: list[tuple[str, str]] = []
    for spec in specs:
        if spec.label is None:
            continue
        shown = display(spec, getattr(state, spec.name, None), state)
        if shown is not None:
            out.append((spec.label, shown))
    return out


def document(specs: Specs, state: Any) -> dict[str, Any]:
    """Render the serialised fields as a document, absences included.

    A field the device did not answer stays in as ``null`` rather than
    dropping out, so a page can draw a steady row of fields with an em dash
    where the answer is missing instead of changing shape between polls.
    """
    return {
        spec.json: getattr(state, spec.name, None)
        for spec in specs
        if spec.json is not None
    }


def option_tables(specs: Specs) -> dict[str, Any]:
    """Render every field's allowed values, keyed by its JSON name.

    A mapping becomes an object keyed by the code as a string, because JSON
    has no integer keys; a plain sequence stays a list.
    """
    out: dict[str, Any] = {}
    for spec in specs:
        if spec.json is None or spec.options is None:
            continue
        labels = spec.labels
        out[spec.json] = (
            {str(code): label for code, label in labels.items()}
            if labels is not None
            else list(spec.options)  # type: ignore[arg-type]
        )
    return out


def bounds(specs: Specs) -> dict[str, dict[str, float]]:
    """Render every field's range, keyed by its JSON name."""
    out: dict[str, dict[str, float]] = {}
    for spec in specs:
        if spec.json is None or spec.kind not in (NUMBER, INTEGER):
            continue
        limits = {
            side: value
            for side, value in (("min", spec.minimum), ("max", spec.maximum))
            if value is not None
        }
        if limits:
            out[spec.json] = limits
    return out


# --- the command line -------------------------------------------------------


def help_text(spec: FieldSpec) -> str | None:
    """Return a flag's help, with the enumeration appended when it takes a code."""
    said = spec.help
    if spec.labels is None or spec.aliases is not None:
        return said
    listed = _enumerated(spec)
    return f"{said}: {listed}" if said else listed


def argument(spec: FieldSpec) -> dict[str, Any]:
    """Return the ``add_argument`` keywords one field asks for."""
    kwargs: dict[str, Any] = {"dest": spec.name, "help": help_text(spec)}
    if spec.aliases is not None:
        kwargs["choices"] = tuple(spec.aliases)
        return kwargs
    if spec.kind == FLAG:
        kwargs["choices"] = ON_OFF
        return kwargs
    if spec.kind == TEXT:
        if spec.domain is not None:
            kwargs["choices"] = tuple(spec.domain)
        else:
            kwargs["metavar"] = spec.metavar
        return kwargs
    kwargs["type"] = float if spec.kind == NUMBER else int
    if spec.kind != ENUM and spec.domain is not None:
        kwargs["choices"] = tuple(spec.domain)
    else:
        kwargs["metavar"] = spec.metavar or "N"
    return kwargs


def add_arguments(specs: Specs, parser: Any) -> None:
    """Add a flag for every settable field that has one."""
    for spec in specs:
        if spec.flag is None or not spec.writable:
            continue
        parser.add_argument(spec.flag, **argument(spec))


def from_namespace(specs: Specs, args: Any) -> dict[str, Any]:
    """Collect the settings a parsed command line named, and check them.

    A word the flag takes comes back as the value it stands for, so a field
    that is a code on the wire and a switch -- or a name -- on the command line
    is spelled once.
    """
    given: dict[str, Any] = {}
    for spec in specs:
        if spec.flag is None or not spec.writable:
            continue
        value = getattr(args, spec.name, None)
        if value is None:
            continue
        if spec.aliases is not None:
            given[spec.name] = spec.aliases[value]
        elif spec.kind == FLAG:
            given[spec.name] = value == ON_OFF[0]
        else:
            given[spec.name] = value
    return values(specs, given)


def from_document(specs: Specs, doc: Mapping[str, Any]) -> dict[str, Any]:
    """Collect the settings a JSON body named, by their JSON keys, and check them.

    A key the group does not define is refused rather than ignored: a page
    sending ``safeCurrent`` where the field is ``safeCurrentA`` should be told
    so, not left wondering why nothing changed.
    """
    known = {spec.json: spec for spec in specs if spec.json is not None}
    given: dict[str, Any] = {}
    for key, value in doc.items():
        spec = known.get(key)
        if spec is None:
            raise FieldError(f"there is no {key!r} setting here")
        if value is not None:
            given[spec.name] = value
    return values(specs, given)


def settable(specs: Specs) -> Iterable[FieldSpec]:
    """Every field a caller may write."""
    return (spec for spec in specs if spec.writable)


__all__ = [
    "ENUM",
    "FLAG",
    "INTEGER",
    "NUMBER",
    "ON_OFF",
    "ON_OFF_CODES",
    "READ_ONLY",
    "READ_WRITE",
    "TEXT",
    "FieldError",
    "FieldSpec",
    "add_arguments",
    "amount",
    "argument",
    "bounds",
    "by_flag",
    "by_name",
    "coerce",
    "decode",
    "display",
    "document",
    "from_document",
    "from_namespace",
    "harvest",
    "help_text",
    "option_tables",
    "rows",
    "settable",
    "values",
    "writes",
]
