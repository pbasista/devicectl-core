"""The request/response primitives the API handlers are written against."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from devicectl.errors import DeviceError
from devicectl.web import http


def request(**kwargs) -> http.Request:
    return http.Request(method=kwargs.pop("method", "POST"), path="/api/x", **kwargs)


def test_an_api_error_carries_the_status_to_answer_with() -> None:
    exc = http.ApiError(404, "no such thing")
    assert (exc.status, exc.message, str(exc)) == (
        404,
        "no such thing",
        "no such thing",
    )


def test_an_api_error_is_a_device_error_so_one_handler_catches_the_lot() -> None:
    assert isinstance(http.ApiError(400, "nope"), DeviceError)


def test_an_empty_body_reads_as_an_empty_document() -> None:
    assert request(body=b"").json() == {}


def test_a_json_object_body_is_parsed() -> None:
    assert request(body=b'{"a": 1}').json() == {"a": 1}


@pytest.mark.parametrize("body", [b"{oops", b"[1, 2]", b'"a string"', b"\xff\xfe"])
def test_a_body_that_is_not_a_json_object_is_a_400(body: bytes) -> None:
    with pytest.raises(http.ApiError) as caught:
        request(body=body).json()
    assert caught.value.status == 400


def test_a_query_parameter_falls_back_to_its_default() -> None:
    req = request(query={"name": "logo.png"})
    assert req.param("name") == "logo.png"
    assert req.param("missing", "fallback") == "fallback"


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " on "])
def test_the_spellings_of_yes(raw: str) -> None:
    assert request(query={"force": raw}).flag("force") is True


@pytest.mark.parametrize("raw", ["", "0", "false", "no", "off", "maybe"])
def test_everything_else_is_no_including_a_bare_flag_with_no_value(raw: str) -> None:
    assert request(query={"force": raw}).flag("force") is False


def test_a_parameter_that_was_not_sent_at_all_takes_the_default() -> None:
    assert request(query={}).flag("force", default=True) is True


def test_ok_renders_json_with_the_status_asked_for() -> None:
    response = http.ok({"a": 1}, status=201)
    assert response.status == 201
    assert json.loads(response.body) == {"a": 1}
    assert response.content_type == "application/json; charset=utf-8"


def test_a_value_no_schema_converted_is_rendered_as_its_own_text() -> None:
    # Better than a 500 in the middle of a page; see the note on `ok`.
    assert json.loads(http.ok({"day": date(2026, 9, 10)}).body) == {"day": "2026-09-10"}


def test_a_query_string_flattens_to_the_last_value_of_each_parameter() -> None:
    assert http.parse_query("a=1&b=&a=2") == {"a": "2", "b": ""}


def test_a_route_says_whether_it_writes_and_whether_it_takes_a_file() -> None:
    plain = http.Route(lambda ctx, req: http.ok({}))
    assert (plain.write, plain.raw_body) == (False, False)
    upload = http.Route(lambda ctx, req: http.ok({}), write=True, raw_body=True)
    assert (upload.write, upload.raw_body) == (True, True)


# --- spooling an upload ---------------------------------------------------------------------


def spooled(**kwargs) -> Path:
    return http.spool(
        request(**kwargs), max_bytes=1024, prefix="devicectl-test-", suffix=".bin"
    )


def test_an_upload_is_written_under_the_name_the_browser_sent() -> None:
    path = spooled(body=b"hello", query={"filename": "settings.toml"})
    try:
        assert path.name == "settings.toml"
        assert path.read_bytes() == b"hello"
    finally:
        http.discard(path)


def test_an_upload_with_no_extension_is_given_the_one_the_caller_named() -> None:
    path = spooled(body=b"x", query={"filename": "image"})
    try:
        assert path.name == "image.bin"
    finally:
        http.discard(path)


def test_a_filename_cannot_climb_out_of_the_directory_it_is_written_into() -> None:
    path = spooled(body=b"x", query={"filename": "../../etc/passwd"})
    try:
        assert path.name == "passwd.bin"
        assert path.parent.name.startswith("devicectl-test-")
    finally:
        http.discard(path)


def test_an_empty_upload_is_refused_rather_than_written() -> None:
    with pytest.raises(http.ApiError) as caught:
        spooled(body=b"")
    assert caught.value.status == 400


def test_an_upload_over_the_limit_is_refused_before_it_is_buffered_to_disk() -> None:
    with pytest.raises(http.ApiError) as caught:
        spooled(body=b"x" * 1025)
    assert caught.value.status == 413


def test_discarding_removes_the_file_and_the_directory_it_was_written_into() -> None:
    path = spooled(body=b"x", query={"filename": "one.bin"})
    directory = path.parent
    http.discard(path)
    assert not directory.exists()


def test_discarding_something_already_gone_is_not_an_error() -> None:
    path = spooled(body=b"x", query={"filename": "one.bin"})
    http.discard(path)
    http.discard(path)


# --- saying what a call was ---------------------------------------------------------------


def test_an_arriving_call_is_named_with_its_query_and_its_body() -> None:
    head, body = http.describe_call(
        http.Request(
            method="POST", path="/api/settings", query={"id": "1"}, body=b'{"a": 2}'
        )
    )
    assert head == "POST /api/settings?id=1"
    assert body == '{\n  "a": 2\n}'


def test_an_answered_call_carries_the_status_and_not_the_body_again() -> None:
    head, body = http.describe_call(
        http.Request(method="POST", path="/api/settings", body=b'{"a": 2}'), 500, "boom"
    )
    assert head == "POST /api/settings  ->  500  boom"
    assert body == ""


def test_a_call_that_worked_says_only_what_it_answered() -> None:
    head, _ = http.describe_call(http.Request(method="GET", path="/api/state"), 200)
    assert head == "GET /api/state  ->  200"


def test_an_upload_is_described_rather_than_printed() -> None:
    _, body = http.describe_call(
        http.Request(method="POST", path="/api/firmware/flash", body=b"\x00\xff" * 40)
    )
    assert body == "(80 bytes, not text)"


def test_a_body_that_is_not_json_is_kept_as_it_was_sent() -> None:
    _, body = http.describe_call(
        http.Request(method="POST", path="/api/x", body=b"not json at all")
    )
    assert body == "not json at all"


def test_a_body_too_long_to_keep_is_cut_and_says_so() -> None:
    raw = ("x" * 4000).encode()
    _, body = http.describe_call(http.Request(method="POST", path="/api/x", body=raw))
    assert len(body) < 1100
    assert body.endswith("... (4000 bytes in all)")
