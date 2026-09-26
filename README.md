# devicectl-core

Shared building blocks for programs that drive one device at a time: a
subcommand table, a progress protocol, a serialising worker that owns the
one connection a device allows, and a build-free browser UI -- its HTTP
server, its event stream and the design system it is drawn with.

It exists because two such programs -- [`alfenctl`][alfenctl] for an Alfen
wallbox on the network and [`jkctl`][jkctl] for a JK BMS on an RS485 bus --
turned out to be the same program with different protocols underneath, and
the second one was produced by copying the first. Line for line, after
normalising the two names, their frontend linters agreed on 528 lines out
of 529 and their event broadcasters on 161 out of 167. The drift had begun
anyway: fixes were being made on one side only. This package is the part
that was the same, with one name each.

## What is here

| Module | What it holds |
|---|---|
| `devicectl.errors` | `DeviceError`, the root of every expected failure |
| `devicectl.report` | `Reporter`, `Wait`, `SILENT` |
| `devicectl.progress` | the stderr progress bar and the live line |
| `devicectl.doctor` | `Finding`, `Report`, `finding_json`, and the three weights |
| `devicectl.fields` | `FieldSpec`, one description of a setting for all its audiences |
| `devicectl.paths` | `config_dir`, the per-user config directory by platform |
| `devicectl.cli.command` | `Command`, `Need`, `Handler` |
| `devicectl.cli.exits` | the exit codes that mean the same thing everywhere |
| `devicectl.cli.target` | `first_set`, the one-line precedence rule |
| `devicectl.web.worker` | `Worker`, the one thread that owns the one connection |
| `devicectl.web.events` | `Broadcaster`, `Subscription`, `Event` |
| `devicectl.web.http` | `Request`, `Response`, `ApiError`, `Route`, the upload spool |
| `devicectl.web.server` | `serve`, `Branding`, `parse_listen`, and the four guards |
| `devicectl.devtools` | `frontlint`, `htmcheck` and `rendercheck`, run as `python -m` |
| `web/static/` | the design system (`core.css`) and the shared widget library, over the vendored Preact + htm |

## What is deliberately not here

**Any runtime dependency.** Everything is standard library, so a program
that depends on this pays for its own protocol layer and nothing else --
and the package stays installable on a Raspberry Pi, where a dependency
with no armv7 wheel is a compiler run rather than a download.

**Anything that knows what a device is.** No transport, no retry policy, no
register catalog. Retry in particular does not generalise: one program
retries at the authentication layer (a 401 means log in again), the other
at the protocol layer (a framing fault is not a Modbus exception), and a
shared abstraction over the two would describe neither.

## Using it

```python
from devicectl.cli.command import Command, Need
from devicectl.errors import DeviceError
from devicectl.web import http


class WidgetError(DeviceError):
    """Anything this program refuses to do, in one line."""


def get_state(ctx: "Context", req: http.Request) -> http.Response:
    return http.ok({"ready": ctx.worker.ready})


ROUTES: dict[tuple[str, str], http.Route["Context"]] = {
    ("GET", "/api/state"): http.Route(get_state),
}
```

The frontend checks take the static root to check:

```bash
python -m devicectl.devtools.frontlint src/widgetctl/web/static
python -m devicectl.devtools.htmcheck  src/widgetctl/web/static
```

`htmcheck` runs each `html` template through the real vendored parser
inside V8, so it needs an engine. That engine is ~80 MB and publishes no
armv7 wheel, so it lives in a dependency group of its own and the check
skips itself, saying so, without it:

```bash
uv sync --group browser
```

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
```

## Licence

EUPL-1.2, except the vendored Preact + htm bundle, which is MIT -- see
`NOTICE`.

[alfenctl]: https://github.com/pbasista/alfenctl
[jkctl]: https://github.com/pbasista/jkctl
