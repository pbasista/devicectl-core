"""Tests for the fake-against-the-real-thing check.

The check is itself the thing that keeps other suites honest, so what it
must get right is the two mistakes: calling a difference a problem when the
call still works, and calling a problem a difference when it does not.
"""

from __future__ import annotations

from typing import Protocol

import pytest

from devicectl.testing import assert_stands_in_for, stands_in_for


class Real:
    """The thing being stood in for."""

    def login(self, timeout: float | None = None) -> None:
        """Take an optional timeout."""

    def write(self, data: bytes) -> None:
        """Take one required argument."""

    def send(self, text: str, *, urgent: bool = False) -> None:
        """Take a keyword-only flag."""

    def read(self, n: int = 1, /) -> bytes:
        """Take one positional-only argument."""
        return b""


def test_the_same_shape_is_no_difference():
    """A fake that repeats the signatures has nothing to answer for."""

    class Fake:
        def login(self, timeout: float | None = None) -> None: ...
        def write(self, data: bytes) -> None: ...
        def send(self, text: str, *, urgent: bool = False) -> None: ...
        def read(self, n: int = 1, /) -> bytes: ...

    assert stands_in_for(Real, Fake) == []


def test_a_fake_need_not_answer_every_call():
    """A class is a menu: a fake implements the part its tests reach."""

    class Fake:
        def login(self, timeout: float | None = None) -> None: ...

    assert stands_in_for(Real, Fake) == []


def test_a_dropped_optional_argument_is_a_difference():
    """`login()` passes the fake's tests while `login(timeout=5)` breaks."""

    class Fake:
        def login(self) -> None: ...

    assert stands_in_for(Real, Fake) == ["Fake.login takes no timeout in position 1"]


def test_a_renamed_argument_is_a_difference():
    """It is passed by name often enough that the name is part of the call."""

    class Fake:
        def write(self, payload: bytes) -> None: ...

    assert stands_in_for(Real, Fake) == ["Fake.write calls data 'payload'"]


def test_a_positional_only_argument_may_be_called_anything():
    """Nobody passes it by name, so nobody can be broken by the name."""

    class Fake:
        def read(self, size: int = 1) -> bytes: ...

    assert stands_in_for(Real, Fake) == []


def test_an_argument_made_mandatory_is_a_difference():
    """The real thing has a default; a caller that leans on it breaks."""

    class Fake:
        def login(self, timeout: float) -> None: ...

    assert stands_in_for(Real, Fake) == [
        "Fake.login insists on timeout, which is optional"
    ]


def test_an_argument_of_the_fakes_own_is_a_difference():
    """Nothing passes it, so every real call to the fake fails."""

    class Fake:
        def write(self, data: bytes, twice: bool) -> None: ...

    assert stands_in_for(Real, Fake) == [
        "Fake.write insists on twice, which no caller passes"
    ]


def test_an_extra_optional_argument_is_fine():
    """A fake may offer its tests a knob nothing else turns."""

    class Fake:
        def write(self, data: bytes, twice: bool = False) -> None: ...

    assert stands_in_for(Real, Fake) == []


def test_a_missing_keyword_only_argument_is_a_difference():
    """There is no position to pass it in, so it has to be there by name."""

    class Fake:
        def send(self, text: str) -> None: ...

    assert stands_in_for(Real, Fake) == ["Fake.send does not take urgent="]


def test_a_keyword_only_argument_may_be_taken_positionally_too():
    """A wider parameter still answers every call the narrow one does."""

    class Fake:
        def send(self, text: str, urgent: bool = False) -> None: ...

    assert stands_in_for(Real, Fake) == []


def test_star_args_answer_anything():
    """A fake that forwards everything cannot be called wrongly."""

    class Fake:
        def login(self, *args, **kwargs) -> None: ...
        def send(self, *args, **kwargs) -> None: ...

    assert stands_in_for(Real, Fake) == []


def test_a_method_the_real_thing_lacks_is_a_difference():
    """This is the fake testing a state that cannot occur."""

    class Fake:
        def reboot_twice(self) -> None: ...

    assert stands_in_for(Real, Fake) == [
        "Fake.reboot_twice answers a call Real does not take"
    ]


def test_a_name_may_be_declared_the_fakes_own_business():
    """`ignore` is how a fake keeps a helper without being told off."""

    class Fake:
        def peek(self) -> None: ...

    assert stands_in_for(Real, Fake, ignore={"peek"}) == []


class Port(Protocol):
    """A contract rather than a menu."""

    in_waiting: int

    def write(self, data: bytes, /) -> None:
        """Put bytes on the wire."""

    def close(self) -> None:
        """Let the port go."""


def test_a_protocol_wants_all_of_itself():
    """Half a contract is not a contract."""

    class Half:
        in_waiting = 0

        def write(self, data: bytes) -> None: ...

    assert stands_in_for(Port, Half) == ["Half has no close"]


def test_a_protocol_is_satisfied_by_the_whole_of_it():
    """And the names are still its own business where it is positional."""

    class Whole:
        in_waiting = 0

        def write(self, buf: bytes) -> None: ...
        def close(self) -> None: ...

    assert stands_in_for(Port, Whole) == []


def test_a_protocol_wants_its_plain_attributes_too():
    """A member that is read rather than called still has to be there."""

    class NoCount:
        def write(self, buf: bytes) -> None: ...
        def close(self) -> None: ...

    assert stands_in_for(Port, NoCount) == ["NoCount has no in_waiting"]


def test_a_property_answers_for_a_plain_attribute():
    """How the stand-in computes it is its own business."""

    class Counts:
        def write(self, buf: bytes) -> None: ...
        def close(self) -> None: ...

        @property
        def in_waiting(self) -> int:
            return 0

    assert stands_in_for(Port, Counts) == []


def test_a_protocol_does_not_mind_what_else_is_there():
    """It is a lower bound: `serial.Serial` has thirty methods besides."""

    class More:
        in_waiting = 0

        def write(self, buf: bytes) -> None: ...
        def close(self) -> None: ...
        def send_break(self, duration: float = 0.25) -> None: ...

    assert stands_in_for(Port, More) == []


def test_an_inherited_method_counts_as_having_it():
    """A fake that subclasses its way to the contract has still met it."""

    class Base:
        in_waiting = 0

        def close(self) -> None: ...

    class Sub(Base):
        def write(self, buf: bytes) -> None: ...

    assert stands_in_for(Port, Sub) == []


def test_the_assertion_names_the_pair_and_every_problem():
    """A failure should be readable without opening this module."""

    class Fake:
        def login(self) -> None: ...
        def write(self, payload: bytes) -> None: ...

    with pytest.raises(AssertionError) as caught:
        assert_stands_in_for(Real, Fake)
    said = str(caught.value)
    assert "Fake cannot stand in for Real" in said
    assert "takes no timeout in position 1" in said
    assert "calls data 'payload'" in said


def test_nothing_to_say_raises_nothing():
    """The happy path is silent."""

    class Fake:
        def login(self, timeout: float | None = None) -> None: ...

    assert_stands_in_for(Real, Fake)
