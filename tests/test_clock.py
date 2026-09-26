"""The one phrase both programs use for a clock that is out."""

from __future__ import annotations

import pytest

from devicectl.clock import format_drift


@pytest.mark.parametrize(
    ("seconds", "said"),
    [
        (None, None),
        (0.4, "in sync"),
        (-1.9, "in sync"),
        (12, "12 seconds ahead"),
        (-12, "12 seconds behind"),
        (60, "1 minute ahead"),
        (-150, "2 minutes behind"),
        (3 * 3600 + 1800, "3.5 hours ahead"),
        (-86400 * 2.5, "2.5 days behind"),
        (-86400 * 365 * 2.5, "2.5 years behind"),
    ],
)
def test_the_drift_is_said_with_its_direction(seconds, said):
    assert format_drift(seconds) == said


def test_it_never_names_the_computer():
    # The page is read on other machines than the one the program runs on.
    assert "computer" not in format_drift(-1e9)
