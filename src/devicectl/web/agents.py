"""Reading a browser's user agent into something a person can act on.

The link pill counts the browsers on the event stream; this is what tells
"my other tab" from "someone else on the network holding the device".  Two
tabs of one browser share an address and a user agent, so what can be said
is coarse -- which is fine, because the question being answered is only
whether the other watcher is me.

None of this is a security control.  A user agent is a stranger's header,
so it is read for a label and never trusted: :func:`name_watchers` drops
anything unprintable, because those lines are printed to a terminal.
"""

from __future__ import annotations

from typing import Any

# Enough of a user-agent reading to tell two browsers on the same machine
# apart, in the order that matters: Edge and Opera both claim Chrome, Chrome
# claims Safari, and every Android is also a Linux.
_BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
    ("curl/", "curl"),
)
_SYSTEMS = (
    ("Android", "Android"),
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Windows", "Windows"),
    ("Macintosh", "macOS"),
    ("CrOS", "ChromeOS"),
    ("X11", "Linux"),
    ("Linux", "Linux"),
)

# How much of an unrecognised user agent is worth showing.
AGENT_SNIPPET = 40

# Addresses not worth naming: a tab on this machine is the ordinary case.
LOOPBACK = ("127.0.0.1", "::1", "localhost")


def describe_agent(agent: str) -> str:
    """Return a short name for a browser ("Firefox on Linux")."""
    if not agent.strip():
        return "unknown client"
    browser = next((name for token, name in _BROWSERS if token in agent), "")
    system = next((name for token, name in _SYSTEMS if token in agent), "")
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or agent.split()[0][:AGENT_SNIPPET]


def client_json(client: dict[str, Any]) -> dict[str, Any]:
    """One watching browser, with its user agent read into something short."""
    return {
        "id": client.get("id"),
        "address": client.get("address") or "",
        "port": client.get("port"),
        "agent": client.get("agent") or "",
        "label": describe_agent(str(client.get("agent") or "")),
        "since": client.get("since"),
    }


def name_watchers(clients: list[dict[str, Any]]) -> list[str]:
    """Name the browsers on the stream: one line each, oldest first.

    Two tabs of one browser share a user agent and an address, so they are
    counted together rather than listed twice.  A watcher somewhere else on
    the network is named with the address it came from, which is the part
    that tells it from a tab of my own.  The user agent is a stranger's
    header and these lines are printed to a terminal, so anything
    unprintable in it is dropped.
    """
    counted: dict[str, int] = {}
    for client in clients:
        label = describe_agent(str(client.get("agent") or ""))
        address = str(client.get("address") or "")
        if address and address not in LOOPBACK:
            label = f"{label} at {address}"
        label = "".join(ch for ch in label if ch.isprintable())
        counted[label] = counted.get(label, 0) + 1
    return [
        f"{label} ({count} tabs)" if count > 1 else label
        for label, count in counted.items()
    ]


__all__ = ["LOOPBACK", "client_json", "describe_agent", "name_watchers"]
