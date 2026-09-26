"""Where a program's own URLs come from: the packaging metadata it ships with.

A page that links to the project's website, to the release notes beside its
version number and to the licence it is under needs three URLs.  Written into
the JavaScript they are three string constants per program, repeated in the
README, in the server's start-up banner and in whatever else grows a link --
and every one of them is a rename away from pointing at nothing.

They are already written down once, in ``[project.urls]``:

.. code-block:: toml

    [project.urls]
    Homepage = "https://github.com/pbasista/jkctl"
    Releases = "https://github.com/pbasista/jkctl/releases"
    License = "https://github.com/pbasista/jkctl/blob/main/LICENSE"

which the build back end writes into the installed distribution as
``Project-URL`` headers.  This reads them back.  Labels are matched loosely --
case, spaces and punctuation are ignored -- because the ones PyPI recognises
are spelled a dozen ways ("Bug Tracker", "bug-tracker") and nothing enforces
any of them.

A checkout that was never installed has no metadata at all, and a lookup that
finds nothing returns nothing rather than raising: a missing link is a
wordmark that is not clickable, which is not a reason to refuse to serve the
page.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, metadata

# Everything but letters and digits: "Bug Tracker", "bug-tracker" and
# "bugtracker" are one label.
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def _key(label: str) -> str:
    """Return the label a URL is filed under, ignoring case and punctuation."""
    return _NOT_ALNUM.sub("", label.strip().lower())


def project_links(distribution: str) -> dict[str, str]:
    """Return ``[project.urls]`` for an installed distribution, by loose label.

    ``project_links("jkctl")["homepage"]`` is the ``Homepage`` line of its
    ``pyproject.toml``.  An uninstalled or unknown distribution gives ``{}``.
    """
    try:
        info = metadata(distribution)
    except PackageNotFoundError:
        return {}
    links: dict[str, str] = {}
    for entry in info.get_all("Project-URL") or ():
        label, _, url = str(entry).partition(",")
        url = url.strip()
        if url:
            links.setdefault(_key(label), url)
    return links


__all__ = ["project_links"]
