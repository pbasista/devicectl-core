"""The shared command-line layer: printing, argv rewriting, and the funnel."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pytest

from devicectl.cli import fanout, output, parser
from devicectl.cli.command import Command, Need
from devicectl.cli.exits import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_OK
from devicectl.cli.main import run
from devicectl.errors import DeviceError

# --- printing --------------------------------------------------------------------------------


def test_a_table_sizes_each_column_to_its_widest_cell(capsys):
    output.print_table(["ID", "NAME"], [["1", "short"], ["22", "much longer"]])
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["ID  NAME", "1   short", "22  much longer"]


def test_a_table_with_no_rows_prints_nothing(capsys):
    output.print_table(["ID", "NAME"], [])
    assert capsys.readouterr().out == ""


def test_a_wide_character_is_two_columns(capsys):
    # A recovered protocol table is where this bites: len() counts a Chinese
    # character as one and the terminal draws it as two, so a table padded
    # by len() comes out visibly ragged.
    assert output.display_width("通信") == 4
    assert output.display_width("ab") == 2
    output.print_table(["A", "B"], [["通信", "x"], ["abcd", "y"]])
    lines = capsys.readouterr().out.splitlines()
    # Measured in columns, not code points: 通信 is two of the first and four
    # of the second, which is the whole point.
    assert output.display_width(lines[1].split("x")[0]) == output.display_width(
        lines[2].split("y")[0]
    )


def test_a_row_block_aligns_its_labels(capsys):
    output.print_rows("Status", [("Model", "NG910"), ("Firmware version", "6.6.2")])
    out = capsys.readouterr().out
    assert "Status:\n" in out
    assert "  Model             NG910" in out
    assert "  Firmware version  6.6.2" in out


def test_a_row_block_with_nothing_to_say_says_nothing(capsys):
    output.print_rows("Status", [])
    assert capsys.readouterr().out == ""


def test_sections_share_one_column_and_drop_empty_ones(capsys):
    output.print_sections(
        "BMS 1",
        [
            ("Pack", [("Voltage", "52.80 V")]),
            ("Empty", []),
            ("Cells", [("Balance current", "0 A")]),
        ],
    )
    assert capsys.readouterr().out == (
        "BMS 1:\n"
        "\n  Pack\n    Voltage          52.80 V\n"
        "\n  Cells\n    Balance current  0 A\n"
    )


def test_the_three_stderr_prefixes(capsys):
    output.note("a")
    output.warn("b")
    output.error("c")
    output.aborted()
    assert capsys.readouterr().err.splitlines() == [
        "note: a",
        "warning: b",
        "error: c",
        "Aborted.",
    ]


def test_a_long_value_is_shortened_with_an_ellipsis():
    assert output.shorten("x" * 10, limit=5) == "xxxx…"
    assert output.shorten("short", limit=40) == "short"
    assert output.shorten(42) == "42"


def test_bytes_are_rendered_as_hex_rather_than_refused(capsys):
    output.print_json({"blob": b"\x01\xff"})
    assert json.loads(capsys.readouterr().out) == {"blob": "01ff"}


def test_anything_else_json_cannot_take_becomes_its_own_text(capsys):
    output.print_json({"when": argparse.Namespace(a=1)})
    assert "Namespace" in capsys.readouterr().out


def test_confirm_takes_only_an_explicit_yes(monkeypatch):
    for answer, expected in [("y", True), ("YES", True), ("", False), ("n", False)]:
        monkeypatch.setattr("builtins.input", lambda _p, a=answer: a)
        assert output.confirm("Really?") is expected


def test_an_unattended_command_declines_rather_than_raising(monkeypatch):
    def eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert output.confirm("Really?") is False


# --- filling in what was left out --------------------------------------------------------------

DEFAULTS = {"tags": "list", "scn": "status"}


def test_a_command_with_a_default_action_gets_it():
    assert parser.insert_default_action(["tags"], DEFAULTS) == ["tags", "list"]


def test_options_meant_for_the_default_action_are_handed_through():
    # The connection options live on the action parsers, so leaving the
    # action out takes them with it.
    assert parser.insert_default_action(["scn", "--peers"], DEFAULTS) == [
        "scn",
        "status",
        "--peers",
    ]


def test_an_action_that_was_typed_is_left_alone():
    assert parser.insert_default_action(["tags", "add"], DEFAULTS) == ["tags", "add"]


def test_a_mistyped_action_still_reaches_argparse():
    # ...so it gets "invalid choice" rather than being quietly rewritten.
    assert parser.insert_default_action(["tags", "adz"], DEFAULTS) == ["tags", "adz"]


def test_help_reaches_the_parser_that_lists_the_actions():
    assert parser.insert_default_action(["tags", "-h"], DEFAULTS) == ["tags", "-h"]


def test_a_command_with_no_default_is_untouched():
    assert parser.insert_default_action(["password"], DEFAULTS) == ["password"]
    assert parser.insert_default_action([], DEFAULTS) == []


def test_the_bare_program_gets_its_default_command():
    assert parser.insert_default_command([], "ui") == ["ui"]


def test_options_typed_bare_go_to_the_default_command():
    assert parser.insert_default_command(["--port", "8080"], "ui") == [
        "ui",
        "--port",
        "8080",
    ]


@pytest.mark.parametrize("flag", ["-h", "--help", "--version"])
def test_the_root_options_still_reach_the_root_parser(flag):
    assert parser.insert_default_command([flag], "ui") == [flag]


def test_a_named_command_is_not_handed_to_the_default_one():
    assert parser.insert_default_command(["status"], "ui") == ["status"]


def test_the_default_actions_come_from_the_command_table():
    # A second table listing them has to be kept in step by hand, and fails
    # silently when it is not.
    table = {
        "tags": Command(lambda link, args: 0, default_action="list"),
        "password": Command(lambda link, args: 0),
        "ui": Command(lambda link, args: 0, needs=Need.NOTHING),
    }
    assert parser.default_actions(table) == {"tags": "list"}


# --- the funnel ------------------------------------------------------------------------------


class WidgetError(DeviceError):
    pass


def test_a_command_that_worked_returns_its_own_code():
    assert run(lambda: EXIT_OK) == EXIT_OK
    assert run(lambda: 3) == 3


def test_an_expected_failure_is_one_line_on_stderr(capsys):
    def boom():
        raise WidgetError("that value is out of range")

    assert run(boom) == EXIT_ERROR
    assert capsys.readouterr().err == "error: that value is out of range\n"


def test_a_missing_name_reads_as_a_sentence_not_a_quoted_word(capsys):
    def boom():
        raise KeyError("no such register: volCellUV")

    assert run(boom) == EXIT_ERROR
    assert capsys.readouterr().err == "error: no such register: volCellUV\n"


def test_an_os_error_is_reported_rather_than_raised(capsys):
    def boom():
        raise FileNotFoundError(2, "No such file or directory")

    assert run(boom) == EXIT_ERROR
    assert "No such file" in capsys.readouterr().err


def test_ctrl_c_is_the_shells_own_code(capsys):
    def boom():
        raise KeyboardInterrupt

    assert run(boom) == EXIT_INTERRUPTED
    assert capsys.readouterr().err == "\nInterrupted.\n"


def test_a_program_translates_its_own_transport_errors(capsys):
    class Refused(Exception):
        pass

    def translate(exc):
        return "cannot reach the charger" if isinstance(exc, Refused) else None

    def boom():
        raise Refused

    assert run(boom, translate=translate) == EXIT_ERROR
    assert capsys.readouterr().err == "error: cannot reach the charger\n"


def test_a_bug_keeps_its_traceback():
    # An unexpected exception is a bug, and a bug deserves its traceback
    # rather than one tidy line that hides where it came from.
    def boom():
        raise ZeroDivisionError("division by zero")

    with pytest.raises(ZeroDivisionError):
        run(boom)


# --- fanning out -----------------------------------------------------------------------------


def test_one_address_a_list_a_range_and_all():
    limit = range(16)
    assert fanout.parse_range("3", limit) == [3]
    assert fanout.parse_range(3, limit) == [3]
    assert fanout.parse_range("1,2,3", limit) == [1, 2, 3]
    assert fanout.parse_range("1-4", limit) == [1, 2, 3, 4]
    assert fanout.parse_range("all", limit) is None
    assert fanout.parse_range("*", limit) is None


def test_the_order_asked_for_is_kept_and_repeats_dropped():
    assert fanout.parse_range("2,1,2", range(16)) == [2, 1]


def test_an_address_off_the_end_is_refused():
    with pytest.raises(ValueError, match="outside 0..15"):
        fanout.parse_range("99", range(16))


def test_an_unreadable_range_says_so():
    with pytest.raises(ValueError, match="cannot read"):
        fanout.parse_range(",,", range(16))


def counted(seen: list[str]):
    def one(device: str) -> int:
        seen.append(device)
        output.print_json({"device": device})
        return EXIT_OK

    return one


def test_one_device_is_exactly_what_the_handler_did_before(capsys):
    seen: list[str] = []
    code = fanout.fan_out(
        ["a"], counted(seen), key=str, heading=lambda d: f"BMS {d}", as_json=True
    )
    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {"device": "a"}


def test_several_devices_come_back_as_one_document_keyed_by_device(capsys):
    seen: list[str] = []
    fanout.fan_out(
        ["a", "b"], counted(seen), key=str, heading=lambda d: f"BMS {d}", as_json=True
    )
    assert json.loads(capsys.readouterr().out) == {
        "a": {"device": "a"},
        "b": {"device": "b"},
    }
    assert seen == ["a", "b"]


def test_without_json_each_section_gets_a_heading(capsys):
    fanout.fan_out(
        ["a", "b"], counted([]), key=str, heading=lambda d: f"BMS {d}", as_json=False
    )
    out = capsys.readouterr().out
    assert "=== BMS a ===" in out
    assert "=== BMS b ===" in out


def test_a_silent_device_is_reported_and_the_others_still_read(capsys):
    def one(device: str) -> int:
        if device == "b":
            raise WidgetError("nothing answered")
        output.print_json({"device": device})
        return EXIT_OK

    code = fanout.fan_out(
        ["a", "b", "c"], one, key=str, heading=lambda d: f"BMS {d}", as_json=True
    )
    captured = capsys.readouterr()
    assert code == EXIT_ERROR
    assert "BMS b: nothing answered" in captured.err
    doc = json.loads(captured.out)
    assert doc["a"] == {"device": "a"}
    assert doc["c"] == {"device": "c"}


def test_a_program_s_own_transport_failure_stops_one_device_not_four(capsys):
    class Refused(Exception):
        pass

    def one(device: str) -> int:
        if device == "b":
            raise Refused
        return EXIT_OK

    def translate(exc):
        return "cannot reach the charger" if isinstance(exc, Refused) else None

    code = fanout.fan_out(
        ["a", "b", "c"],
        one,
        key=str,
        heading=lambda d: f"station {d}",
        translate=translate,
    )
    assert code == EXIT_ERROR
    assert "station b: cannot reach the charger" in capsys.readouterr().err


def test_a_bug_on_one_device_is_still_a_bug(capsys):
    def one(device: str) -> int:
        raise ZeroDivisionError("a bug is a bug on four devices too")

    with pytest.raises(ZeroDivisionError):
        fanout.fan_out(["a", "b"], one, key=str, heading=str, translate=lambda _e: None)


def test_the_collector_is_put_back_even_when_a_device_raises(capsys):
    def one(device: str) -> int:
        raise WidgetError("no")

    fanout.fan_out(["a", "b"], one, key=str, heading=str, as_json=True)
    capsys.readouterr()
    output.print_json({"back": "to stdout"})
    assert json.loads(capsys.readouterr().out) == {"back": "to stdout"}


# --- writing a file --------------------------------------------------------------------------


def test_a_pipe_gets_the_bytes(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert output.write_out("body", None, default_name="fallback.txt") == EXIT_OK
    assert capsys.readouterr().out == "body"
    assert not (tmp_path / "fallback.txt").exists()


def test_a_terminal_gets_a_file_instead_of_a_scrolling_dump(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert output.write_out("body", None, default_name="fallback.txt") == EXIT_OK
    assert (tmp_path / "fallback.txt").read_text() == "body"


def test_a_named_file_is_written_whatever_stdout_is(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    target = tmp_path / "named.txt"
    assert output.write_out("body", target, default_name="other.txt") == EXIT_OK
    assert target.read_text() == "body"
    assert not (tmp_path / "other.txt").exists()


def test_a_dash_is_standard_output_even_on_a_terminal(tmp_path, monkeypatch, capsys):
    # What `-` means everywhere else, so it means it here: somebody who typed
    # it meant it, and a default filename would be ignoring what they said.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert output.write_out("body", "-", default_name="fallback.txt") == EXIT_OK
    assert capsys.readouterr().out == "body"
    assert not (tmp_path / "fallback.txt").exists()


def test_a_dash_is_standard_output_as_a_path_too(tmp_path, monkeypatch, capsys):
    # A caller whose file argument is a Path means the same thing by it.
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert output.write_out("body", Path("-"), default_name="fallback.txt") == EXIT_OK
    assert capsys.readouterr().out == "body"


def test_a_dash_costs_no_round_trip_for_a_name_it_will_not_use(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    output.write_out("body", "-", default_name=_refuse_to_be_called)
    assert capsys.readouterr().out == "body"


def test_a_dash_reads_standard_input(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("piped"))
    assert output.read_in("-") == "piped"


def test_no_file_at_all_reads_standard_input_too(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("piped"))
    assert output.read_in(None) == "piped"


def test_a_named_file_is_read_from_the_disk(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", _RefusesToBeRead())
    source = tmp_path / "in.txt"
    source.write_text("from the file")
    assert output.read_in(source) == "from the file"
    assert output.read_in(str(source)) == "from the file"


class _RefusesToBeRead:
    """A stdin that fails the test if a named file was read from it instead."""

    def read(self, *_args):
        raise AssertionError("should not have read standard input")


def test_bytes_go_out_as_bytes(tmp_path):
    target = tmp_path / "image.bin"
    assert output.write_out(b"\x00\xff", target, default_name="x.bin") == EXIT_OK
    assert target.read_bytes() == b"\x00\xff"


def test_an_existing_file_is_asked_about(tmp_path, monkeypatch, capsys):
    target = tmp_path / "there.txt"
    target.write_text("old")
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert output.write_out("new", target, default_name="x.txt") == EXIT_ERROR
    assert target.read_text() == "old"
    assert "Aborted." in capsys.readouterr().err


def test_yes_does_not_ask(tmp_path, monkeypatch):
    target = tmp_path / "there.txt"
    target.write_text("old")
    monkeypatch.setattr("builtins.input", _refuse_to_be_called)
    assert output.write_out("new", target, default_name="x.txt", yes=True) == EXIT_OK
    assert target.read_text() == "new"


def test_the_written_line_counts_when_it_is_given_something_to_count(tmp_path, capsys):
    target = tmp_path / "out.txt"
    output.write_out("body", target, default_name="x.txt", summary="412 log lines")
    assert capsys.readouterr().err == f"Wrote 412 log lines to {target}\n"


def test_the_written_line_names_the_path_when_it_is_not(tmp_path, capsys):
    target = tmp_path / "out.txt"
    output.write_out("body", target, default_name="x.txt")
    assert capsys.readouterr().err == f"Wrote {target}\n"


def test_a_path_with_nothing_at_it_is_not_asked_about(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", _refuse_to_be_called)
    assert output.may_overwrite(tmp_path / "free.txt") is True


def _refuse_to_be_called(*_args):
    raise AssertionError("should not have asked")


def test_a_default_name_that_costs_something_is_not_asked_for_when_piping(
    capsys, monkeypatch
):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    output.write_out("body", None, default_name=_refuse_to_be_called)
    assert capsys.readouterr().out == "body"


def test_a_default_name_may_be_worked_out_only_when_it_is_needed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    output.write_out("body", None, default_name=lambda: "asked.txt")
    assert (tmp_path / "asked.txt").read_text() == "body"
