"""The field table: decoding, checking, printing and the flags it generates.

What is worth testing here is the part that has to be right for all five
audiences at once -- that a value refused on the command line is refused in
the same words through the web, that a field the device did not answer stays
absent rather than becoming a zero, and that a read-only field cannot be
written however it is reached.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import pytest

from devicectl import fields
from devicectl.fields import FieldSpec

AMPS = FieldSpec(
    name="safe_current_a",
    kind=fields.NUMBER,
    address=(0x2068, 0),
    wire="real32",
    label="Safe current",
    json="safeCurrentA",
    flag="--safe-current",
    unit="A",
    minimum=0.0,
    maximum=100.0,
    metavar="AMPS",
    what="the safe current",
    help="what to fall back to",
)

PROTOCOL = FieldSpec(
    name="protocol",
    kind=fields.ENUM,
    address=(0x5217, 0),
    wire="int8",
    label="Meter protocol",
    json="protocol",
    flag="--protocol",
    options={-1: "EMS", 4: "Modbus TCP/IP"},
    what="the meter protocol",
    help="smart meter protocol",
)

SWITCHING = FieldSpec(
    name="phase_switching",
    kind=fields.FLAG,
    address=(0x2185, 0),
    wire="int8",
    label="Phase switching",
    json="phaseSwitching",
    flag="--phase-switching",
    words=("on", "off"),
    help="allow 1-/3-phase switching",
)

ROTATION = FieldSpec(
    name="phase_rotation",
    kind=fields.TEXT,
    address=(0x2069, 0),
    wire="string",
    label="Phase rotation",
    json="phaseRotation",
    flag="--phase-rotation",
    options=("L1", "L1L2L3"),
    what="the phase rotation",
    help="how the phases are wired",
)

ACTUAL = FieldSpec(
    name="heartbeat_actual_s",
    kind=fields.INTEGER,
    address=(0x2086, 0),
    json="heartbeatActualS",
    unit="s",
    access=fields.READ_ONLY,
)

SHARE = FieldSpec(
    name="solar_green_share",
    kind=fields.INTEGER,
    address=(0x3280, 2),
    wire="u16",
    label="  green share",
    json="solarGreenShare",
    flag="--green-share",
    unit="%",
    minimum=0,
    maximum=100,
    metavar="PERCENT",
    what="the green share",
    help="surplus share to charge from",
)

ATTEMPTS = FieldSpec(
    name="tx_attempts",
    kind=fields.INTEGER,
    address=(0x2096, 0),
    wire="u32",
    json="txAttempts",
    flag="--tx-attempts",
    minimum=0,
    what="the message attempts",
    help="transaction message attempts",
)

TABLE = (AMPS, PROTOCOL, SWITCHING, ROTATION, ACTUAL, SHARE, ATTEMPTS)


@dataclass
class State:
    """What a group's reading looks like once the table has decoded it."""

    safe_current_a: float | None = None
    protocol: int | None = None
    phase_switching: bool | None = None
    phase_rotation: str | None = None
    heartbeat_actual_s: int | None = None
    solar_green_share: int | None = None
    tx_attempts: int | None = None


def state(**values: object) -> State:
    """A reading with the named fields set and the rest absent."""
    return State(**values)  # type: ignore[arg-type]


# --- decoding ---------------------------------------------------------------


def test_a_field_the_device_did_not_answer_stays_absent() -> None:
    assert fields.decode(AMPS, None) is None
    assert fields.decode(AMPS, "") is None
    assert fields.decode(ROTATION, "") is None


def test_a_value_that_is_not_the_shape_of_the_field_is_absent_not_zero() -> None:
    # A charger answering "n/a" for a current has declined, not reported 0 A.
    assert fields.decode(AMPS, "n/a") is None
    assert fields.decode(PROTOCOL, "n/a") is None


def test_each_kind_decodes_to_its_own_python_type() -> None:
    assert fields.decode(AMPS, "6") == 6.0
    assert fields.decode(PROTOCOL, 4.0) == 4
    assert fields.decode(SWITCHING, 1) is True
    assert fields.decode(SWITCHING, 0) is False
    assert fields.decode(ROTATION, "L1L2L3") == "L1L2L3"


def test_harvest_decodes_every_addressed_field() -> None:
    answers = {AMPS.address: 6.0, SWITCHING.address: 1}
    got = fields.harvest(TABLE, answers.get)
    assert got["safe_current_a"] == 6.0
    assert got["phase_switching"] is True
    assert got["protocol"] is None


# --- checking ---------------------------------------------------------------


def test_a_number_out_of_range_names_the_range_and_the_unit() -> None:
    with pytest.raises(fields.FieldError) as caught:
        fields.coerce(AMPS, 120)
    assert str(caught.value) == "the safe current must be between 0 and 100 A"


def test_a_percentage_carries_its_sign_without_a_space() -> None:
    with pytest.raises(fields.FieldError) as caught:
        fields.coerce(SHARE, 140)
    assert str(caught.value) == "the green share must be between 0 and 100%"


def test_a_floor_with_no_ceiling_reads_as_it_would_be_said() -> None:
    with pytest.raises(fields.FieldError) as caught:
        fields.coerce(ATTEMPTS, -1)
    assert str(caught.value) == "the message attempts cannot be negative"


def test_a_code_outside_the_enumeration_lists_the_ones_that_are_in_it() -> None:
    with pytest.raises(fields.FieldError) as caught:
        fields.coerce(PROTOCOL, 9)
    assert str(caught.value) == "the meter protocol is -1 (EMS), 4 (Modbus TCP/IP)"


def test_a_string_outside_its_options_lists_them() -> None:
    with pytest.raises(fields.FieldError) as caught:
        fields.coerce(ROTATION, "L4")
    assert str(caught.value) == "the phase rotation is one of L1, L1L2L3"


def test_something_that_is_not_a_number_is_refused_as_one() -> None:
    with pytest.raises(fields.FieldError, match="must be a number"):
        fields.coerce(AMPS, "later")
    with pytest.raises(fields.FieldError, match="must be a number"):
        fields.coerce(AMPS, float("nan"))


def test_a_flag_takes_anything_and_writes_a_bit() -> None:
    assert fields.coerce(SWITCHING, True) == 1
    assert fields.coerce(SWITCHING, 0) == 0


def test_values_drops_what_was_not_named() -> None:
    got = fields.values(TABLE, {"safe_current_a": 8, "protocol": None})
    assert got == {"safe_current_a": 8.0}


def test_a_read_only_field_is_refused_however_it_is_reached() -> None:
    with pytest.raises(fields.FieldError, match="read-only"):
        fields.values(TABLE, {"heartbeat_actual_s": 300})


def test_a_setting_the_group_does_not_have_is_refused_rather_than_ignored() -> None:
    with pytest.raises(fields.FieldError, match="no 'safe_current' setting"):
        fields.values(TABLE, {"safe_current": 8})
    with pytest.raises(fields.FieldError, match="no 'safeCurrent' setting"):
        fields.from_document(TABLE, {"safeCurrent": 8})


def test_a_document_names_its_fields_the_way_the_browser_does() -> None:
    assert fields.from_document(TABLE, {"safeCurrentA": 8, "protocol": 4}) == {
        "safe_current_a": 8.0,
        "protocol": 4,
    }


def test_writes_pair_each_value_with_its_address_and_encoding() -> None:
    checked = fields.values(TABLE, {"safe_current_a": 8, "phase_switching": True})
    assert fields.writes(TABLE, checked) == {
        (0x2068, 0): (8.0, "real32"),
        (0x2185, 0): (1, "int8"),
    }


def test_a_device_that_reports_its_own_types_overrides_the_table() -> None:
    checked = fields.values(TABLE, {"safe_current_a": 8})
    got = fields.writes(TABLE, checked, wire=lambda spec: f"live:{spec.name}")
    assert got == {(0x2068, 0): (8.0, "live:safe_current_a")}


# --- showing ----------------------------------------------------------------


def test_rows_skip_what_the_device_did_not_answer() -> None:
    assert fields.rows(TABLE, state(safe_current_a=6.0)) == [("Safe current", "6 A")]


def test_rows_come_out_in_the_table_s_own_order() -> None:
    reading = state(
        safe_current_a=6.0,
        protocol=4,
        phase_switching=False,
        phase_rotation="L1L2L3",
        solar_green_share=70,
    )
    assert fields.rows(TABLE, reading) == [
        ("Safe current", "6 A"),
        ("Meter protocol", "Modbus TCP/IP"),
        ("Phase switching", "off"),
        ("Phase rotation", "L1L2L3"),
        ("  green share", "70%"),
    ]


def test_a_code_with_no_label_is_shown_rather_than_hidden() -> None:
    assert fields.display(PROTOCOL, 9) == "unknown (9)"


def test_a_field_may_print_itself_when_its_row_names_a_neighbour() -> None:
    spec = FieldSpec(
        name="p1_interface",
        kind=fields.ENUM,
        label="P1 interface",
        options={1: "telnet"},
        render=lambda value, reading: (
            None if value is None else f"{spec.labels[value]} ({reading.p1_address})"
        ),
    )

    @dataclass
    class Reading:
        """A neighbour the row wants to name."""

        p1_interface: int | None = None
        p1_address: str | None = None

    assert fields.rows((spec,), Reading(1, "192.168.1.9")) == [
        ("P1 interface", "telnet (192.168.1.9)")
    ]
    assert fields.rows((spec,), Reading()) == []


def test_a_document_keeps_an_absent_field_as_null() -> None:
    doc = fields.document(TABLE, state(protocol=4))
    assert doc["protocol"] == 4
    assert doc["safeCurrentA"] is None
    assert set(doc) == {
        "safeCurrentA",
        "protocol",
        "phaseSwitching",
        "phaseRotation",
        "heartbeatActualS",
        "solarGreenShare",
        "txAttempts",
    }


def test_the_option_tables_ride_along_keyed_as_json_allows() -> None:
    assert fields.option_tables(TABLE) == {
        "protocol": {"-1": "EMS", "4": "Modbus TCP/IP"},
        "phaseRotation": ["L1", "L1L2L3"],
    }


def test_the_bounds_ride_along_too() -> None:
    assert fields.bounds(TABLE) == {
        "safeCurrentA": {"min": 0.0, "max": 100.0},
        "solarGreenShare": {"min": 0, "max": 100},
        "txAttempts": {"min": 0},
    }


# --- the command line -------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    """A parser carrying the table's flags and nothing else."""
    p = argparse.ArgumentParser(prog="test", exit_on_error=False)
    fields.add_arguments(TABLE, p)
    return p


def test_a_read_only_field_gets_no_flag() -> None:
    flags = {flag for action in parser()._actions for flag in action.option_strings}
    assert "--heartbeat-actual" not in flags
    assert {"--safe-current", "--protocol", "--phase-switching"} <= flags


def test_each_kind_asks_argparse_for_what_it_needs() -> None:
    kinds = {
        action.dest: action for action in parser()._actions if action.option_strings
    }
    assert kinds["safe_current_a"].type is float
    assert kinds["safe_current_a"].metavar == "AMPS"
    assert kinds["protocol"].type is int
    assert kinds["protocol"].metavar == "N"
    assert kinds["phase_switching"].choices == ("on", "off")
    assert kinds["phase_rotation"].choices == ("L1", "L1L2L3")


def test_a_numeric_enum_lists_its_codes_in_the_help() -> None:
    assert fields.help_text(PROTOCOL) == (
        "smart meter protocol: -1 (EMS), 4 (Modbus TCP/IP)"
    )
    assert fields.help_text(AMPS) == "what to fall back to"


def test_a_parsed_command_line_becomes_checked_settings() -> None:
    args = parser().parse_args(["--safe-current", "8", "--phase-switching", "on"])
    assert fields.from_namespace(TABLE, args) == {
        "safe_current_a": 8.0,
        "phase_switching": 1,
    }


def test_a_command_line_value_out_of_range_is_refused_in_the_field_s_words() -> None:
    args = parser().parse_args(["--safe-current", "500"])
    with pytest.raises(fields.FieldError, match="between 0 and 100 A"):
        fields.from_namespace(TABLE, args)


def test_a_code_may_be_spelled_as_a_word_on_the_command_line() -> None:
    spec = FieldSpec(
        name="mode",
        kind=fields.ENUM,
        flag="--mode",
        options={0: "plug and charge", 2: "RFID reader"},
        aliases={"plug-and-charge": 0, "rfid": 2},
        help="how a driver identifies themselves",
    )
    p = argparse.ArgumentParser(prog="test")
    fields.add_arguments((spec,), p)
    action = next(a for a in p._actions if a.option_strings == ["--mode"])
    assert action.choices == ("plug-and-charge", "rfid")
    args = p.parse_args(["--mode", "rfid"])
    assert fields.from_namespace((spec,), args) == {"mode": 2}


def test_a_code_may_be_spelled_on_or_off_on_the_command_line() -> None:
    spec = FieldSpec(
        name="measurement_includes_ev",
        kind=fields.ENUM,
        flag="--includes-ev",
        options={0: "excluding", 1: "including"},
        aliases=fields.ON_OFF_CODES,
        help="whether the meter already counts the car",
    )
    p = argparse.ArgumentParser(prog="test")
    fields.add_arguments((spec,), p)
    action = next(a for a in p._actions if a.option_strings == ["--includes-ev"])
    assert action.choices == ("on", "off")
    # The enumeration is the row's business, not the flag's, when it is a switch.
    assert fields.help_text(spec) == "whether the meter already counts the car"
    args = p.parse_args(["--includes-ev", "on"])
    assert fields.from_namespace((spec,), args) == {"measurement_includes_ev": 1}


def test_a_field_may_carry_how_it_is_shown_and_what_it_left_the_factory_as() -> None:
    # Neither is read here: a program prints its own rows, and the point of
    # carrying them is that its terminal and its browser print the same ones.
    spec = FieldSpec(
        name="volCellUV",
        kind=fields.NUMBER,
        label="Cell UVP",
        unit="V",
        minimum=1.2,
        maximum=4.4,
        decimals=3,
        default=2.5,
    )
    assert (spec.decimals, spec.default) == (3, 2.5)
    assert FieldSpec(name="bare").decimals is None
    assert FieldSpec(name="bare").default is None


def test_a_code_may_be_whatever_the_device_s_own_description_calls_it() -> None:
    # A catalog written by hand keys its options by integer; one read out of a
    # vendor datasource keys them by decimal string.  Both are codes.
    spec = FieldSpec(name="dry1Trigger", options={"0": "Off", "1": "On"})
    assert spec.labels == {"0": "Off", "1": "On"}
    assert spec.domain == ("0", "1")
