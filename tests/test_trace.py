"""The link recorder: what it keeps, what it refuses to keep, and the report."""

from __future__ import annotations

import threading

from devicectl.trace import NOTE, RX, TX, UI, Recorder, hexdump, render


def test_a_recorder_that_was_never_started_keeps_nothing() -> None:
    rec = Recorder()
    rec.add(TX, b"\x01\x03")
    assert rec.entries() == []
    assert rec.state()["on"] is False


def test_starting_empties_whatever_the_last_recording_left() -> None:
    rec = Recorder()
    rec.start()
    rec.add(TX, b"\x01")
    rec.stop()
    rec.start()
    # Only the "recording started" note the start itself writes.
    assert [e.direction for e in rec.entries()] == [NOTE]


def test_stopping_keeps_what_was_recorded() -> None:
    rec = Recorder()
    rec.start()
    rec.add(TX, b"\x01\x03\x14\x00")
    rec.stop()
    kept = [e for e in rec.entries() if e.direction == TX]
    assert len(kept) == 1
    assert kept[0].data == b"\x01\x03\x14\x00"
    assert rec.state()["on"] is False


def test_the_oldest_frames_fall_off_and_are_counted() -> None:
    rec = Recorder(limit=4)
    rec.start()  # one note
    for n in range(6):
        rec.add(TX, bytes([n]))
    assert len(rec.entries()) == 4
    assert rec.state()["dropped"] == 3
    assert [e.data for e in rec.entries()] == [b"\x02", b"\x03", b"\x04", b"\x05"]


def test_a_silence_is_recorded_because_nothing_else_would_show_it() -> None:
    rec = Recorder()
    rec.start()
    rec.add(TX, b"\x01\x03")
    rec.say("no/short response")
    assert [e.note for e in rec.entries()][-1] == "no/short response"


def test_the_hook_asks_what_the_program_is_doing_per_frame() -> None:
    rec = Recorder()
    rec.start()
    doing = ["reading the dashboard"]
    log = rec.hook(lambda: doing[0])
    log(TX, b"\x01")
    doing[0] = "writing the temperatures"
    log(RX, b"\x02")
    assert [e.note for e in rec.entries() if e.direction != NOTE] == [
        "reading the dashboard",
        "writing the temperatures",
    ]


def test_recording_from_several_threads_loses_nothing() -> None:
    rec = Recorder(limit=1000)
    rec.start()

    def busy() -> None:
        for n in range(100):
            rec.add(TX, bytes([n % 256]))

    threads = [threading.Thread(target=busy) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len([e for e in rec.entries() if e.direction == TX]) == 400


def test_hexdump_wraps_at_the_report_width() -> None:
    assert hexdump(bytes(range(3))) == ["00 01 02"]
    assert len(hexdump(bytes(40), width=16)) == 3


def test_the_report_carries_the_facts_the_bytes_cannot() -> None:
    rec = Recorder()
    rec.start()
    rec.add(TX, b"\x01\x03\x14\x00\x00\x20", "reading the dashboard")
    rec.say("no/short response")
    text = render(
        rec,
        title="jkctl serial trace",
        facts=[("port", "/dev/ttyUSB0"), ("units", []), ("label", None)],
        decode=lambda direction, data: f"{len(data)} bytes going {direction}",
        preamble="Newest last.",
    )
    assert text.startswith("jkctl serial trace\n==================\n")
    assert "port       /dev/ttyUSB0" in text
    assert "units      (none)" in text
    assert "label      (not known)" in text
    assert "Newest last." in text
    assert "01 03 14 00 00 20" in text
    assert "6 bytes going TX" in text
    assert "reading the dashboard" in text
    assert "no/short response" in text


def test_an_empty_report_says_so_rather_than_ending_after_its_header() -> None:
    assert "Nothing was recorded." in render(Recorder(), title="trace")


def test_a_read_that_answered_nothing_is_drawn_as_nothing() -> None:
    rec = Recorder()
    rec.start()
    rec.add(RX, b"")
    assert "(nothing)" in render(rec, title="trace")


# --- what somebody asked for --------------------------------------------------------------


def test_what_the_page_asked_for_is_recorded_between_the_frames_it_caused() -> None:
    rec = Recorder()
    rec.start()
    rec.called("POST /api/settings", '{"changes": {"tmpMosOT": "80"}}')
    rec.add(TX, b"\x01\x10")
    rec.called("POST /api/settings  ->  500  no/short response")
    kinds = [(e.direction, e.note) for e in rec.entries()]
    assert kinds[1:] == [
        (UI, "POST /api/settings"),
        (TX, ""),
        (UI, "POST /api/settings  ->  500  no/short response"),
    ]
    assert rec.entries()[1].detail == '{"changes": {"tmpMosOT": "80"}}'


def test_a_call_prints_its_body_where_the_hex_would_go() -> None:
    rec = Recorder()
    rec.start()
    rec.called("POST /api/settings", '{\n  "id": 1\n}')
    text = render(rec, title="t")
    assert "              {" in text
    assert '                "id": 1' in text
    assert "(nothing)" not in text


def test_a_read_that_timed_out_still_says_nothing_came_back() -> None:
    rec = Recorder()
    rec.start()
    rec.add(RX, b"")
    assert "(nothing)" in render(rec, title="t")


def test_what_a_call_carries_counts_towards_the_size_of_the_recording() -> None:
    rec = Recorder()
    rec.start()
    rec.called("POST /api/x", "12345")
    assert rec.state()["bytes"] == 5


def test_a_recording_a_failure_started_says_so_until_it_stops():
    rec = Recorder()
    rec.start(automatic=True)
    assert rec.state()["automatic"]
    assert rec.entries()[0].note == "recording started after a failure"
    rec.stop()
    assert not rec.state()["automatic"]
    assert rec.state()["until"] >= rec.state()["since"]
    rec.start()
    assert not rec.state()["automatic"] and rec.state()["until"] is None
