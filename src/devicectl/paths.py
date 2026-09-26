"""Where a program keeps its per-user files, chosen the same way everywhere.

A device tool has a configuration file, and often a cached token or a small
state file beside it.  Where those live is not the program's own decision to
make twice: it is the platform's convention, and the two programs here had
each written the same resolution out under their own directory name -- close
enough to drift (one docstring named macOS, the other did not) and identical
enough to share.

The directory name is the one thing that is the program's: :func:`config_dir`
takes it and applies the convention.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def config_dir(name: str) -> Path:
    """Return the directory a program named ``name`` keeps its config in.

    ``XDG_CONFIG_HOME`` wins wherever it is set, so a dotfiles setup that
    exports it keeps working.  Otherwise Windows uses ``%APPDATA%``, where
    Windows programs keep per-user settings; everywhere else uses
    ``~/.config``, which is both the XDG default and where command-line
    tools put themselves on macOS.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / name
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / name
    return Path.home() / ".config" / name


__all__ = ["config_dir"]
