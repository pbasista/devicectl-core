#!/usr/bin/env python3
"""The checks that need the page actually drawn.

``pytest`` proves the server sends the right JSON, Biome parses the browser
half, :mod:`~devicectl.devtools.frontlint` resolves it across files and
:mod:`~devicectl.devtools.htmcheck` proves every template parses.  All four
passed while a page was showing a drop-down with no usable entries, a switch
word rendered as the number 16, a unit sitting on its own line under its
input, a value column wide enough to push four columns off-screen, and a
model number split across two lines.  None of those is a crash, so nothing
without a layout engine could see them.

This drives a real browser over a program's own UI and fails on the ones
that can be stated exactly:

* **A page error**, or an error on the console, on any tab.
* **A value broken mid-token.**  A model number or a serial is one token and
  splitting it makes it unreadable and unsearchable.
* **A table wider than the box it scrolls in**, which is a column pushed
  off-screen rather than a table that scrolls.
* **A tab that rendered nothing**, which is how a wiring mistake looks from
  outside.
* **A page that scrolls sideways**, which is a column, an input or a table
  that has run off the side rather than wrapped or scrolled in its own box.
* **A setpoint that will not answer the arrow keys**, which is a control
  that looks live, drags, draws correctly -- and cannot be set to the value
  somebody actually wants, because a pixel of track is worth more than a
  step.
* **A drag that does not hand the setpoint the keyboard**, which is the
  same fault one step earlier: the arrow keys work, and nothing the pointer
  does ever points them at anything.

Every tab, at four widths, because a layout is only correct at the width it
was checked at -- and the narrowest is a phone, where a two-column row has
to become one column or the value's editor ends up off the screen.

A program supplies two things and gets the rest: a context manager that
serves its UI on a given port, and the map of tab name to hash route.  See
:func:`main`.
"""

from __future__ import annotations

import argparse
import socket
import sys
from typing import Any, Callable, ContextManager, Mapping, Sequence

# The widths a layout has to survive: a desktop, a small laptop, a tablet in
# landscape, and a phone -- which is what is actually in your pocket when the
# alarm goes off.
WIDTHS = (1440, 1280, 1024, 420)

# Below this, a table is meant to be scrolled and a hyphenated label is meant
# to break: the two checks that ask whether something fits are asking a
# question the phone width has already answered no to, on purpose.  What
# still holds there is that the page itself must not run off the side.
FITS_ABOVE = 700

# How long a tab gets to finish its own reads before it is judged.
SETTLE_MS = 2200

# Below this many characters a tab has not drawn anything: the chrome around
# it -- the header, the tab strip, the link pill -- is already more than this.
DREW_NOTHING = 100

# How tall the window is.  Only the width is varied; the height changes
# nothing these checks ask about.
VIEWPORT_HEIGHT = 1000

# One client rect per line box is the only reliable line count: a table cell's
# height includes its padding, so height over line-height calls every cell a
# wrap.  Elements holding several words are skipped -- prose is meant to wrap.
FIND_WRAPS = """() => {
  const bad = [];
  for (const el of document.querySelectorAll('.row > .v, .row > .k, td, th, .badge, h2, .dim')) {
    const text = el.textContent.trim();
    if (!text || /\\s/.test(text)) continue;
    if (el.children.length || el.querySelector('input,select,button')) continue;
    const range = document.createRange();
    range.selectNodeContents(el);
    if (range.getClientRects().length > 1) bad.push(text);
  }
  return bad;
}"""

# A page that scrolls sideways.  Not a box that scrolls -- a table in its own
# scroller is fine and deliberate -- but the document itself, which is
# something that could not fit and was not told what to do about it.  The
# element is named as well, or the answer is "somewhere on this page".
FIND_SIDEWAYS = """() => {
  const room = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth <= room + 1) return [];
  const bad = [];
  for (const el of document.querySelectorAll('body *')) {
    const box = el.getBoundingClientRect();
    if (box.width === 0 || (box.right <= room + 1 && box.left >= -1)) continue;
    let p = el.parentElement, held = false;
    while (p) {
      const flow = getComputedStyle(p).overflowX;
      if (flow === 'auto' || flow === 'scroll' || flow === 'hidden') { held = true; break; }
      p = p.parentElement;
    }
    if (!held) {
      const name = el.className ? `.${String(el.className).split(' ')[0]}` : '';
      bad.push(`${el.tagName.toLowerCase()}${name} reaches ${Math.round(box.right)}px`);
    }
  }
  return [...new Set(bad)].slice(0, 3);
}"""

# Every setpoint on every band, asked whether the keyboard moves it.
#
# A grip is a `role="slider"`, and the one thing every slider anywhere has
# to do is answer the arrow keys.  They are also the only way to set the
# last decimal of one: a band three hundred pixels wide spans a volt, so a
# pixel is two millivolts and the pointer cannot ask for 3.451 at all.  A
# grip that does not answer them is a control that is half dead in the one
# direction nothing else checks -- it draws, it fits, it drags -- and that
# is exactly how it got shipped: a band drawn to one register's decimals,
# holding another register whose value was written back rounded, so the
# keypress moved the setpoint and the round put it straight back.
#
# Both directions are tried before anything is reported, because a setpoint
# sitting less than a step from the bound its neighbour imposes really
# cannot move that way, and that is not a fault.
#
# This leaves the page holding an unsent draft, which is why it is asked
# last.  Nothing is written: the card's own Apply is what writes.
NUDGE_GRIPS = """async () => {
  const bad = [];
  const settle = () => new Promise((done) => setTimeout(done, 25));
  for (const grip of document.querySelectorAll('.band button.grip')) {
    // A tab that keeps another tab's cards in the document has its bands
    // in there too, and nothing hidden can be focused or dragged.
    if (grip.checkVisibility && !grip.checkVisibility()) continue;
    const name = grip.getAttribute('aria-label') || '(unnamed)';
    const low = Number(grip.getAttribute('aria-valuemin'));
    const high = Number(grip.getAttribute('aria-valuemax'));
    const at = () => Number(grip.getAttribute('aria-valuenow'));
    const before = at();
    grip.focus();
    if (document.activeElement !== grip) {
      bad.push(`${name} will not take the keyboard`);
      continue;
    }
    const ways = [];
    if (before < high) ways.push('ArrowRight');
    if (before > low) ways.push('ArrowLeft');
    let moved = ways.length === 0;
    for (const key of ways) {
      grip.dispatchEvent(
        new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
      );
      await settle();
      if (at() !== before) { moved = true; break; }
    }
    if (!moved) {
      bad.push(`${name} did not move for an arrow key (${before}, between ${low} and ${high})`);
    }
  }
  return bad;
}"""

# Whether a setpoint is the thing the next keypress would go to.
HAS_THE_KEYBOARD = """() => {
  const on = document.activeElement;
  return Boolean(on && on.classList && on.classList.contains('grip'));
}"""

# Where on the screen to drag, for the first band on this tab that has
# something to drag.  Asked in the page rather than through the engine's
# own element handling, which waits for a hidden element to become visible
# and a tab with a band behind a collapsed card has one that never will.
# `scrollIntoView` first, because a drag is in viewport coordinates and half
# a page is below the fold.
FIND_A_BAND = """(least) => {
  for (const plot of document.querySelectorAll('.band .plot.settable')) {
    if (!plot.querySelector('button.grip')) continue;
    if (plot.checkVisibility && !plot.checkVisibility()) continue;
    if (plot.getBoundingClientRect().width < least) continue;
    plot.scrollIntoView({ block: 'center' });
    const box = plot.getBoundingClientRect();
    if (box.width < least || box.height <= 0) continue;
    return { x: box.x, y: box.y, width: box.width, height: box.height };
  }
  return null;
}"""

# Narrower than this a band is not a control anybody drags, and a drag
# across it says nothing.  It is also what a band collapsed to nothing by a
# layout fault measures, which is a question the other checks ask.
DRAGGABLE_PX = 60

FIND_WIDE_TABLES = """() => {
  const bad = [];
  for (const table of document.querySelectorAll('table')) {
    const box = table.closest('.scroll') || table.parentElement;
    if (!box) continue;
    const over = table.getBoundingClientRect().width - box.clientWidth;
    if (over > 1) {
      const head = [...table.querySelectorAll('th')].map((th) => th.textContent.trim());
      bad.push(`${Math.round(over)}px over in [${head.join(', ')}]`);
    }
  }
  return bad;
}"""


def free_port() -> int:
    """Ask the kernel for a port nothing else is on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def require_playwright(how: str) -> Any:
    """Return ``sync_playwright``, or say how to get it and stop.

    ``how`` is the command that was being run, so the message ends with the
    line the reader was about to retype.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "playwright is not installed; it lives in the browser group:\n"
            "  uv sync --group browser\n"
            "  uv run python -m playwright install chromium   # once\n"
            f"  {how}"
        ) from None
    return sync_playwright


def _dragged_keeps_the_keyboard(page: Any, where: str, problems: list[str]) -> None:
    """Drag a setpoint and check the keyboard followed it.

    The arrow keys moving a focused grip is worth nothing if no ordinary
    use of the page ever focuses one.  Somebody who has just dragged a
    setpoint into place with the pointer is exactly the person who wants
    the last three decimals, and they will reach for the keyboard where
    their hand already is -- so the press has to aim it.

    Which grip it lands on is not asked: a press on the track takes hold of
    the nearest setpoint, and which one is nearest to the middle of a band
    is a fact about that band rather than about this.
    """
    box = page.evaluate(FIND_A_BAND, DRAGGABLE_PX)
    if box is None:
        return
    middle = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + box["width"] * 0.5, middle)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.55, middle, steps=5)
    page.mouse.up()
    if not page.evaluate(HAS_THE_KEYBOARD):
        problems.append(f"{where}: dragging a setpoint did not give it the keyboard")


def _judge(
    page: Any, where: str, width: int, problems: list[str], *, keys: bool = False
) -> None:
    """Ask one drawn tab every question that can be asked of it.

    ``keys`` also works the setpoints, which is asked at one width only:
    whether a slider answers the keyboard is not a question about how wide
    the window is, and asking it four times over costs four times as long
    and says the same thing.
    """
    if len(page.inner_text("body")) < DREW_NOTHING:
        problems.append(f"{where}: rendered almost nothing")
        return
    if width >= FITS_ABOVE:
        for text in page.evaluate(FIND_WRAPS):
            problems.append(f"{where}: {text!r} is broken across lines")
        for detail in page.evaluate(FIND_WIDE_TABLES):
            problems.append(f"{where}: a table overflows -- {detail}")
    for detail in page.evaluate(FIND_SIDEWAYS):
        problems.append(f"{where}: the page scrolls sideways -- {detail}")
    if keys:
        _dragged_keeps_the_keyboard(page, where, problems)
        for detail in page.evaluate(NUDGE_GRIPS):
            problems.append(f"{where}: {detail}")


def check(
    url: str,
    tabs: Mapping[str, str],
    wanted: Sequence[str],
    widths: Sequence[int],
    *,
    how: str = "rendercheck",
    settle_ms: int = SETTLE_MS,
) -> list[str]:
    """Drive an already-serving page and return everything wrong with it."""
    sync_playwright = require_playwright(how)
    problems: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width in widths:
            page = browser.new_page(
                viewport={"width": width, "height": VIEWPORT_HEIGHT}
            )
            page.on(
                "pageerror",
                lambda exc, w=width: problems.append(f"{w}px: page error: {exc}"),
            )
            page.on(
                "console",
                lambda msg, w=width: (
                    problems.append(f"{w}px: console: {msg.text}")
                    if msg.type == "error"
                    else None
                ),
            )
            for name in wanted:
                page.goto(url.rstrip("/") + "/#" + tabs[name])
                page.wait_for_timeout(settle_ms)
                _judge(
                    page,
                    f"{width}px #{name}",
                    width,
                    problems,
                    keys=width == widths[0],
                )
            page.close()
        browser.close()
    return list(dict.fromkeys(problems))


def main(
    serve: Callable[[int], ContextManager[Any]],
    tabs: Mapping[str, str],
    *,
    program: str,
    argv: Sequence[str] | None = None,
) -> int:
    """Parse the arguments, serve, run the checks, report.

    ``serve`` is handed a port and must yield once the UI answers on it.
    """
    how = f"uv run tools/rendercheck.py   # in {program}"
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "tabs", nargs="*", choices=[*tabs, []], help="which tabs (default: all)"
    )
    ap.add_argument(
        "--width",
        type=int,
        action="append",
        metavar="PX",
        help=f"a viewport width to check (repeatable; default {', '.join(map(str, WIDTHS))})",
    )
    args = ap.parse_args(argv)
    widths = tuple(args.width or WIDTHS)
    wanted = list(args.tabs) or list(tabs)
    # Fail on a missing engine before starting a server for it.
    require_playwright(how)
    port = free_port()
    with serve(port):
        problems = check(f"http://127.0.0.1:{port}/", tabs, wanted, widths, how=how)
    if not problems:
        print(f"rendercheck: clean ({len(wanted)} tabs at {len(widths)} widths)")
        return 0
    for line in problems:
        print(f"  {line}", file=sys.stderr)
    print(f"rendercheck: {len(problems)} problem(s)", file=sys.stderr)
    return 1


__all__ = [
    "DRAGGABLE_PX",
    "DREW_NOTHING",
    "FIND_A_BAND",
    "FITS_ABOVE",
    "HAS_THE_KEYBOARD",
    "NUDGE_GRIPS",
    "SETTLE_MS",
    "WIDTHS",
    "check",
    "free_port",
    "main",
    "require_playwright",
]
