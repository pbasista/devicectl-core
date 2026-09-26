"""Checks on the browser half that no off-the-shelf linter performs.

Each is a module with a ``main`` and runs as ``python -m
devicectl.devtools.<name> <static-root>``, so a program's test suite is a
few lines pointing it at its own ``web/static``.
"""
