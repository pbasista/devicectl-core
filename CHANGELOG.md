# Changelog

Notable changes, newest first. The version is set in
`src/devicectl/__init__.py`.

## 0.1.0 — 2026-09-26

The first release. `alfenctl` and `jkctl` were one program written twice;
this is the half they share, extracted into one package with **no required
runtime dependencies** -- everything here is standard library.

What that half turned out to be:

- **Errors and reports.** `DeviceError`, the root of every expected failure;
  `Reporter`, `Wait` and `SILENT`, the protocol that drives a terminal and a
  browser from one long operation; and `Finding`, `Report` and `finding_json`,
  the shape of a health check.
- **A CLI framework.** The command table (`Command`, `Need`, `Handler`), the
  `argv` rewrites that fill in a default command or action, the fan-out that
  runs one read across several devices, the shared exit codes, and the funnel
  every expected failure lands in.
- **The web transport.** `serve()` and its four guards (Host, token, the UI
  header, `--read-only`), the SSE event stream, the `Worker` that owns the one
  connection a device allows, the upload spool, and `parse_listen`.
- **A build-free browser UI.** The design system (`core.css`) and the shared
  widget library -- header, device tiles, tables, charts, draggable setpoint
  bands, the health card and the page-wide draft store -- drawn over a vendored
  Preact + htm, with no build step.
- **Dev-time checks.** `frontlint`, `htmcheck` and `rendercheck`, the
  cross-file and in-browser checks that keep a build-free frontend honest.
- **Small shared helpers.** `FieldSpec`, one description of a setting for all
  its audiences; `config_dir`, the per-user config directory by platform; the
  duration and HTTP-status constants; and the clock-drift phrasing.

See [README.md](README.md) for the module-by-module map.
