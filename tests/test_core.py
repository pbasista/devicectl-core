"""The small shared pieces: errors, the report shape, progress, commands."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from devicectl import doctor, progress
from devicectl.cli.command import Command, Need
from devicectl.cli.target import first_set
from devicectl.errors import DeviceError
from devicectl.report import SILENT, Reporter, Wait
from devicectl.web.http import ApiError

# --- errors ---------------------------------------------------------------------------------


def test_a_program_derives_its_own_root_and_the_shared_code_still_catches_it() -> None:
    class AlfenError(DeviceError):
        pass

    class LicenseError(AlfenError, ValueError):
        pass

    with pytest.raises(DeviceError):
        raise LicenseError("that key is for another station")
    assert isinstance(LicenseError(""), ValueError)


# --- the reporter ---------------------------------------------------------------------------


def test_the_silent_reporter_accepts_everything_and_says_nothing() -> None:
    wait = Wait(
        elapsed_s=1.0,
        deadline_s=60.0,
        typical_s=20.0,
        label="rebooting",
        poll=1,
        next_poll_in_s=2.0,
    )
    SILENT.step("one")
    SILENT.detail("two")
    SILENT.warn("three")
    SILENT.sending(1, 2, 0.5)
    SILENT.waiting(wait)
    SILENT.polled(wait)


def test_a_reporter_need_only_override_what_it_can_show() -> None:
    said: list[str] = []

    class Warnings(Reporter):
        def warn(self, message: str) -> None:
            said.append(message)

    reporter: Reporter = Warnings()
    reporter.step("ignored")
    reporter.warn("could not log in")
    assert said == ["could not log in"]


# --- progress -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0s"), (42, "42s"), (59.9, "59s"), (60, "1m00s"), (185, "3m05s")],
)
def test_a_duration_reads_compactly(seconds: float, text: str) -> None:
    assert progress.fmt_duration(seconds) == text


@pytest.mark.parametrize("fraction", [-1.0, 0.0, 0.5, 1.0, 2.0])
def test_a_bar_is_always_its_full_width(fraction: float) -> None:
    assert len(progress.bar(fraction)) == progress.PROGRESS_BAR_WIDTH


def test_a_bar_is_clamped_at_both_ends() -> None:
    assert progress.bar(-1.0) == "-" * progress.PROGRESS_BAR_WIDTH
    assert progress.bar(2.0) == "#" * progress.PROGRESS_BAR_WIDTH
    assert progress.bar(0.5).count("#") == progress.PROGRESS_BAR_WIDTH // 2


def test_nothing_is_drawn_when_stderr_is_not_a_terminal(capsys) -> None:
    progress.write_live("half way")
    progress.end_live()
    assert capsys.readouterr().err == ""


# --- the command table ----------------------------------------------------------------------


def handler(link, args: argparse.Namespace) -> int:
    return 0


def test_a_command_needs_the_device_ready_unless_it_says_otherwise() -> None:
    assert Command(handler).need(None) is Need.READY


def test_one_action_of_a_command_may_need_less_than_the_command() -> None:
    # `props list` reads the catalog off disk; `props set` has to be logged in.
    command = Command(handler, per_action={"list": Need.NOTHING})
    assert command.need("list") is Need.NOTHING
    assert command.need("set") is Need.READY
    assert command.need(None) is Need.READY


def test_a_command_that_needs_nothing_still_answers_for_its_actions() -> None:
    assert Command(handler, needs=Need.NOTHING).need("anything") is Need.NOTHING


def test_a_command_addresses_one_device_unless_it_says_otherwise() -> None:
    assert Command(handler).fans_out_for(None) is False
    assert Command(handler, fans_out=True).fans_out_for(None) is True


def test_only_the_reading_actions_of_a_command_fan_out() -> None:
    # `current show` may be asked of four chargers; `current set` may not.
    command = Command(handler, fans_out=("show",))
    assert command.fans_out_for("show") is True
    assert command.fans_out_for("set") is False
    assert command.fans_out_for(None) is False


# --- precedence -----------------------------------------------------------------------------


def test_the_first_value_actually_given_wins() -> None:
    assert first_set(None, "config", "default", default="factory") == "config"


def test_a_value_that_was_given_falsely_is_still_a_value() -> None:
    # 0 and "" are answers; only None means "nobody said".
    assert first_set(None, 0, 9, default=9) == 0
    assert first_set(None, "", default="x") == ""


def test_with_nothing_given_the_default_stands() -> None:
    assert first_set(None, None, default=7) == 7


# --- the report shape -----------------------------------------------------------------------


def report(*findings: doctor.Finding) -> doctor.Report:
    return doctor.Report(findings=list(findings))


def finding(severity: str, area: str = "z") -> doctor.Finding:
    return doctor.Finding(severity, area, f"{area} is {severity}")


def test_a_clean_report_has_no_worst_and_is_ok() -> None:
    clean = doctor.Report()
    assert clean.worst is None
    assert clean.ok is True


def test_the_worst_weight_present_is_what_a_report_is_judged_by() -> None:
    assert report(finding(doctor.NOTE), finding(doctor.ERROR)).worst == doctor.ERROR
    assert report(finding(doctor.NOTE), finding(doctor.WARNING)).worst == doctor.WARNING


def test_a_report_with_warnings_but_no_errors_is_still_ok() -> None:
    assert report(finding(doctor.WARNING)).ok is True
    assert report(finding(doctor.ERROR)).ok is False


def test_findings_read_worst_first_then_by_area() -> None:
    ordered = report(
        finding(doctor.NOTE, "clock"),
        finding(doctor.ERROR, "sockets"),
        finding(doctor.WARNING, "network"),
        finding(doctor.ERROR, "identity"),
    ).sorted()
    assert [(f.severity, f.area) for f in ordered] == [
        (doctor.ERROR, "identity"),
        (doctor.ERROR, "sockets"),
        (doctor.WARNING, "network"),
        (doctor.NOTE, "clock"),
    ]


def test_a_weight_nobody_recognises_sorts_last_rather_than_raising() -> None:
    odd = report(finding("catastrophe"), finding(doctor.NOTE, "a"))
    assert [f.severity for f in odd.sorted()] == [doctor.NOTE, "catastrophe"]
    assert odd.worst == doctor.NOTE


def test_what_could_not_be_read_is_not_a_finding() -> None:
    # "we could not look" is a different sentence from "healthy", and a
    # report that blurred the two would be worth less than one that admits it.
    partial = doctor.Report(unavailable=["network: the device did not answer"])
    assert partial.findings == []
    assert partial.ok is True
    assert partial.worst is None


def test_a_program_may_carry_its_own_readings_on_the_report() -> None:
    from dataclasses import dataclass, field

    @dataclass
    class Detailed(doctor.Report):
        readings: dict = field(default_factory=dict)

    detailed = Detailed(findings=[finding(doctor.ERROR)], readings={"soc": 41})
    assert detailed.ok is False
    assert detailed.readings == {"soc": 41}


def test_a_finding_serialises_to_the_four_keys_the_card_reads() -> None:
    # The shared HealthCard reads exactly these; both programs serialise
    # through this one function so the component meets one shape.
    with_fix = doctor.finding_json(
        doctor.Finding(doctor.ERROR, "clock", "42 s behind", "run: sync clock")
    )
    assert with_fix == {
        "severity": "error",
        "area": "clock",
        "detail": "42 s behind",
        "fix": "run: sync clock",
    }
    # A finding with nothing to do about it carries an explicit null, not a
    # missing key -- the card tests for the field.
    assert doctor.finding_json(doctor.Finding(doctor.NOTE, "a", "b"))["fix"] is None


def test_an_api_error_is_reportable_as_one_line_like_any_other() -> None:
    assert str(ApiError(400, "expected a JSON object")) == "expected a JSON object"


# --- paths ----------------------------------------------------------------------------------


def test_config_dir_follows_the_platform_convention(monkeypatch) -> None:
    from devicectl import paths

    # XDG wins wherever it is set, whatever the platform.
    monkeypatch.setenv("XDG_CONFIG_HOME", "/x/cfg")
    assert paths.config_dir("widget") == Path("/x/cfg/widget")

    # Off Windows with no XDG, it is ~/.config, which is also macOS's home
    # for a command-line tool.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    assert paths.config_dir("widget") == Path.home() / ".config" / "widget"

    # On Windows with no XDG, %APPDATA%.
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", "/Users/x/AppData/Roaming")
    assert paths.config_dir("widget") == Path("/Users/x/AppData/Roaming/widget")
