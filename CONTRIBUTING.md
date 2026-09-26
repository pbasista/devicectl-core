# Contributing

## What belongs here

Something belongs in `devicectl-core` when **two** programs already need it
and neither's version is the right one to keep. One program's speculative
need is not enough: an abstraction with a single consumer is a guess, and
this package exists because a pair of guesses drifted apart in two repos
rather than being reconciled in one.

Three rules follow from that:

* **No runtime dependencies.** Everything here is standard library. A
  transport belongs in the program that speaks the protocol.
* **Nothing here knows what a device is.** No retry policy, no register
  catalog, no connection. The seam is always a callable or a dataclass the
  program fills in.
* **Where the two programs disagree, reconcile before extracting.** Picking
  one side and leaving the other to adapt is how the divergence started.

## Conventions

Both consumer programs share these, and code moved from either keeps them.

* Docstrings on everything, enforced (`D`, pep257). They say *why*: what a
  reader could work out from the code does not need repeating, and what
  they could not -- a protocol quirk, a decision that looks arbitrary --
  does.
* `PLR2004` is on, so a number with meaning gets a name.
* Line length 88, `E501` not enforced on prose. The formatter never breaks
  a string, and some of them have to stay verbatim.
* Tests are named as sentences about behaviour, not after the function
  under test.

## Checks

```bash
uv sync --group browser
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
```

The `browser` group carries the V8 that `devtools/htmcheck.py` parses
templates in. Without it those tests skip themselves, saying so -- which
is fine locally and is why CI installs it.

## Releasing

The version lives in `src/devicectl/__init__.py` and nowhere else.
`git tag v0.1.0 && git push --tags` runs the checks, builds, and publishes
to PyPI; the tag has to match.
