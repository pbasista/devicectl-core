"""The broadcaster: sticky replay, bounded queues, and never blocking a publisher."""

from __future__ import annotations

from devicectl.web.events import MAX_QUEUED_EVENTS, Broadcaster


def drain(subscription) -> list[tuple[str, dict]]:
    got = []
    while True:
        event = subscription.get(timeout=0)
        if event is None:
            return got
        got.append((event.name, event.data))


def test_every_subscriber_gets_a_copy() -> None:
    bus = Broadcaster()
    one, two = bus.subscribe(), bus.subscribe()
    bus.publish("link", {"state": "idle"})
    assert drain(one) == [("link", {"state": "idle"})] == drain(two)


def test_a_subscriber_that_arrives_late_misses_what_it_missed() -> None:
    bus = Broadcaster()
    bus.publish("job", {"id": "1"})
    assert drain(bus.subscribe()) == []


def test_state_published_as_sticky_is_replayed_to_whoever_connects_next() -> None:
    # The point of sticky: a browser that reloads sees the current world at
    # once instead of waiting for something to change.
    bus = Broadcaster()
    bus.publish("link", {"state": "idle"}, sticky=True)
    bus.publish("link", {"state": "ready"}, sticky=True)
    assert drain(bus.subscribe()) == [("link", {"state": "ready"})]


def test_the_backlog_is_ordered_by_name_so_it_is_the_same_every_time() -> None:
    bus = Broadcaster()
    bus.publish("status", {}, sticky=True)
    bus.publish("link", {}, sticky=True)
    assert [name for name, _ in drain(bus.subscribe())] == ["link", "status"]


def test_a_subscriber_that_stops_reading_loses_its_oldest_not_the_newest() -> None:
    bus = Broadcaster()
    subscription = bus.subscribe()
    for n in range(MAX_QUEUED_EVENTS + 10):
        bus.publish("tick", {"n": n})
    got = drain(subscription)
    assert len(got) == MAX_QUEUED_EVENTS
    assert got[-1] == ("tick", {"n": MAX_QUEUED_EVENTS + 9})


def test_publishing_to_a_full_subscriber_does_not_block_the_others() -> None:
    bus = Broadcaster()
    stalled = bus.subscribe()
    for n in range(MAX_QUEUED_EVENTS + 1):
        bus.publish("tick", {"n": n})
    reader = bus.subscribe()
    bus.publish("tick", {"n": "last"})
    assert drain(reader) == [("tick", {"n": "last"})]
    assert stalled.get(timeout=0) is not None


def test_closing_wakes_a_reader_and_unsubscribes() -> None:
    bus = Broadcaster()
    subscription = bus.subscribe()
    subscription.close()
    assert subscription.closed
    assert subscription.get(timeout=0) is None  # the None that ends a stream
    assert bus.subscriber_count == 0


def test_a_subscription_used_as_a_context_manager_always_unsubscribes() -> None:
    bus = Broadcaster()
    with bus.subscribe():
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0


def test_shutdown_ends_every_open_stream() -> None:
    bus = Broadcaster()
    one, two = bus.subscribe(), bus.subscribe()
    bus.shutdown()
    assert one.closed and two.closed
    assert bus.subscriber_count == 0


def test_the_latest_sticky_event_can_be_read_without_subscribing() -> None:
    bus = Broadcaster()
    assert bus.sticky("link") is None
    bus.publish("link", {"state": "ready"}, sticky=True)
    assert bus.sticky("link") == {"state": "ready"}
    bus.sticky("link")["state"] = "tampered"  # a copy, not the event's own dict
    assert bus.sticky("link") == {"state": "ready"}


def test_clients_are_described_oldest_connection_first() -> None:
    bus = Broadcaster()
    bus.subscribe({"since": 2, "agent": "second"})
    bus.subscribe({"since": 1, "agent": "first"})
    assert [c["agent"] for c in bus.clients()] == ["first", "second"]


def test_every_client_is_given_an_id_of_its_own() -> None:
    bus = Broadcaster()
    ids = [bus.subscribe({}).client["id"] for _ in range(3)]
    assert ids == ["1", "2", "3"]
