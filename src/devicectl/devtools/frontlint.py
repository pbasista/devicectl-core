#!/usr/bin/env python3
"""The checks the web UI needs that no off-the-shelf linter does.

Biome (``biome.json``) parses and lints the browser half: syntax, the
recommended rule set, CSS, and the one page.  It is better at all of that
than anything hand-written here could be.  What it does not do -- what no
JavaScript linter does, because it is normally a bundler's job -- is look
across the files:

* **Wiring.**  Every ``import`` has to name a file that exists and a name
  that file exports, and every ``export`` has to be imported by somebody.
  There is no build step here: the modules are served to the browser as
  they are on disk and only meet each other at run time, so a mistyped
  path or export name is a blank page found by reloading, and not before.
* **What a card is allowed to claim.**  A full-width card ends its row, so
  one that spans the grid without a row's worth of content stacks the
  whole tab underneath it.  The stylesheet says which content earns it;
  nothing but a reader was checking.
* **Operation names.**  A progress bar draws only while the operation the
  server published matches the name the panel is waiting for -- one string
  written in ``web/api.py`` and again, in another language, in a
  ``what=`` prop.  Nothing else can see both halves, so a rename turns a
  bar off silently and no test fails.
* **The runtime's names.**  A module that calls ``useState`` or tags a
  template with ``html`` without importing it from the vendored module is
  a component that renders nothing -- the import is a run-time question,
  so no bundler ever gets to answer it.  The names the vendor exports
  are therefore checked like the wiring: used means imported.
* **Components drawn into a template.**  The same fault, one level up and
  invisible to the check above, which reads the code with its templates
  blanked out: ``html`<${Band} ...>``` with no ``Band`` in scope throws
  while the card is being built and takes the tab with it, leaving the
  page showing whatever was on it before.  Every other check passes --
  the file is valid JavaScript, the template parses, the class names all
  exist -- so this one asks the one question left: is it in scope.

That is the whole of it, and it is meant to stay that way: anything a
general JavaScript or CSS linter can check belongs in ``biome.json``, not
here.  Run it over a program's own UI::

    python -m devicectl.devtools.frontlint src/<program>/web/static

or let that program's ``pytest`` do it; either way a problem is one line of
``path:line: CODE message``.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# The module every other one is reachable from; its exports answer to the
# page, not to an import.
ENTRY_MODULE = "app.js"

# Files under here are somebody else's and are checked by nobody.
VENDOR = "vendor"

# The URL prefix the server reserves for the shared frontend, and which a
# program's own modules import the shared ones by.  It is an absolute URL
# rather than a relative path because the two trees are two directories on
# two sides of a package boundary and only meet at the server: `../` out of
# a program's static root would name whatever happens to sit beside it.
CORE_PREFIX = "/core/"

# Written on a rule whose class is only ever assembled at run time -- a log
# level, a job state -- which this checker has no way of seeing.
KEEP = "frontlint: keep"

IMPORT_NAMED = re.compile(r"import\s*\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]")
IMPORT_STAR = re.compile(r"import\s*\*\s*as\s*(\w+)\s*from\s*['\"]([^'\"]+)['\"]")
IMPORT_ANY = re.compile(r"^\s*import\b[^;]*from\s*['\"]([^'\"]+)['\"]", re.M)
IMPORT_NAMES = re.compile(r"import\s*\{([^}]*)\}", re.S)
EXPORTED = re.compile(
    r"^export\s+(?:async\s+)?(?:function|const|let|var|class)\s+(\w+)", re.M
)
HTML_ASSET = re.compile(r"(?:src|href)=\"([^\"]+)\"")

# A tab icon written into the page itself.  See :func:`_check_page_icons`.
HTML_ICON = re.compile(r"<link\b[^>]*\brel=\"icon\"[^>]*>", re.I | re.S)


# What a module can take from the vendored preact-htm bundle.  A name used
# without its import is a ReferenceError that kills the component mid-render,
# so the page keeps whatever it last showed -- found, if at all, by hand.

VENDOR_EXPORT = re.compile(r"export\{([^}]*)\}")
VENDOR_NAME = re.compile(r"([a-zA-Z_$][\w$]*)\s+as\s+([a-zA-Z_$][\w$]*)")
# The shapes that mean "this identifier really is the runtime's": a hook or
# factory call, a tagged template, a component base class.  A bare mention
# (a comment, an object key) is not.  The scan runs over the code with its
# string and template interiors blanked out, so an ``h`` at the end of a
# literal and an ``html`` in a comment are never in these shapes at all.
RUNTIME_USE = {
    "call": re.compile(r"(?<![\w$.])(\w+)\s*\("),
    "tag": re.compile(r"(?<![\w$.])(\w+)\s*`"),
    "extends": re.compile(r"\bextends\s+(\w+)"),
}

# A component interpolated into an `html` template: `<${Card}` and its
# closing `<//>`.  This is the one way a module reaches another module's
# code that the scan above cannot see, because it happens *inside* a
# template literal and that scan blanks template interiors out.  A name
# written into a `class=` or a piece of prose is not this shape.
COMPONENT_USE = re.compile(r"<\$\{\s*([A-Za-z_$][\w$]*)\s*\}")


class _Scan:
    """A JavaScript module being read for the code in it.

    What is wanted is the names a module reaches for at runtime, with
    nothing that is merely written about them: no comments, no strings, and
    none of the prose inside a template.  Length and newlines are preserved,
    so an offset into the result is an offset into the original and line
    numbers survive.

    This was two regular expressions -- one for comments, one for a string
    in any of its three quotes -- applied one after the other, and it could
    not be.  A template literal holds code inside ``${...}``, that code
    holds templates of its own, and an expression that matches from a
    backtick to the next backtick cannot tell a nested template's opening
    quote from its own closing one.  So the file was read as an alternating
    band of "string" and "code" that had little to do with where the strings
    were: the pairing came out right or wrong by the parity of how many
    backticks happened to be above it, and prose that landed in a "code"
    band was read as source.  A page was reported for using a name that
    appears only in a sentence about it, and moving an unrelated tag five
    hundred lines earlier is what made it appear.

    The comments went the same way: ``<//>`` is htm's closing tag, on a
    hundred lines of both pages, and blanking it as the start of a comment
    took the rest of the line with it -- including the backtick that closed
    the template it was in.

    So this walks the text, which is the only thing that can answer the
    question.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.out = list(text)
        self.size = len(text)

    def blanked(self) -> str:
        """Read the whole module and return it with its prose taken out."""
        self.code(0, nested=False)
        return "".join(self.out)

    def blank(self, start: int, stop: int) -> None:
        """Replace a span with spaces, leaving its newlines where they are."""
        for index in range(start, stop):
            if self.out[index] != "\n":
                self.out[index] = " "

    def comment(self, at: int) -> int:
        """Read a // or /* comment; return where it ends."""
        if self.text[at : at + 2] == "//":
            stop = self.text.find("\n", at)
            stop = self.size if stop < 0 else stop
        else:
            stop = self.text.find("*/", at + 2)
            stop = self.size if stop < 0 else stop + 2
        self.blank(at, stop)
        return stop

    def quoted(self, at: int) -> int:
        """Read a ' or " string; return where it ends.

        An opening quote with no closing one before the end of the line is
        an apostrophe in prose -- "the pack's contactors" -- which is only
        reached at all inside a ``${...}``.  It is left where it is, rather
        than swallowing the rest of the file.
        """
        quote = self.text[at]
        index = at + 1
        while index < self.size and self.text[index] not in (quote, "\n"):
            index += 2 if self.text[index] == "\\" else 1
        if index >= self.size or self.text[index] != quote:
            return at + 1
        self.blank(at + 1, index)
        return index + 1

    def template(self, at: int) -> int:
        """Read a `template`, recursing into every ${...} it holds."""
        index = at + 1
        while index < self.size:
            here = self.text[index]
            if here == "\\":
                self.blank(index, min(index + 2, self.size))
                index += 2
            elif here == "`":
                return index + 1
            elif self.text[index : index + 2] == "${":
                index = self.code(index + 2, nested=True)
            else:
                self.blank(index, index + 1)
                index += 1
        return index

    def code(self, at: int, nested: bool) -> int:
        """Read ordinary code; when nested, stop after its closing brace."""
        index, depth = at, 0
        while index < self.size:
            here = self.text[index]
            if self.text[index : index + 2] in ("//", "/*"):
                index = self.comment(index)
            elif here in "\"'":
                index = self.quoted(index)
            elif here == "`":
                index = self.template(index)
            else:
                if here == "{":
                    depth += 1
                elif here == "}":
                    if nested and depth == 0:
                        return index + 1
                    depth -= 1
                index += 1
        return index


def _blank_strings_and_comments(text: str) -> str:
    """Return the module with every string, template and comment blanked."""
    return _Scan(text).blanked()


@dataclass(frozen=True)
class Problem:
    """One thing wrong, at one place, in the words a reader needs."""

    path: Path
    line: int
    code: str
    message: str

    def render(self, root: Path) -> str:
        """Format as an editor-clickable line."""
        try:
            where = self.path.relative_to(root)
        except ValueError:  # pragma: no cover - only if given an outside path
            where = self.path
        return f"{where}:{self.line}: {self.code} {self.message}"


def _line_of(text: str, index: int) -> int:
    """Return the 1-based line number of a character offset."""
    return text.count("\n", 0, index) + 1


def _blank_css(text: str) -> str:
    """Return the stylesheet with its comments replaced by spaces."""
    out = list(text)
    for match in re.finditer(r"/\*.*?\*/", text, re.S):
        for index in range(*match.span()):
            if out[index] != "\n":
                out[index] = " "
    return "".join(out)


def css_classes(text: str) -> dict[str, int]:
    """Return every class the stylesheet defines, with the line it is on."""
    blanked = _blank_css(text)
    classes: dict[str, int] = {}
    for match in re.finditer(r"\.(-?[_a-zA-Z][\w-]*)", blanked):
        # A class in a selector, not `0.5` or a file extension in a url().
        classes.setdefault(match.group(1), _line_of(text, match.start()))
    return classes


def resolve_import(path: Path, spec: str, core: Path | None) -> Path | None:
    """Return the file an import specifier names, or None if it cannot.

    Two kinds of specifier reach a browser here.  A relative one is a path
    from the importing module, as everywhere else.  One beginning
    :data:`CORE_PREFIX` is a URL the server answers from the shared
    package's own static tree, which lives in another distribution
    entirely -- so it can only be resolved by someone who was told where
    that tree is, and the answer is None when nobody was.
    """
    if spec.startswith(CORE_PREFIX):
        if core is None:
            return None
        return (core / spec[len(CORE_PREFIX) :]).resolve()
    return (path.parent / spec).resolve()


def _check_page_assets(
    pages: list[Path], sources: dict[Path, str], core: Path | None
) -> list[Problem]:
    """Check that every asset a page links to is a file that is there."""
    found: list[Problem] = []
    for path in pages:
        for match in HTML_ASSET.finditer(sources[path]):
            target = match.group(1)
            if target.startswith(("http:", "https:", "data:", "#", "//")):
                continue
            named = resolve_import(path, target, core)
            if named is None:
                named = path.parent / target.lstrip("/")
            if not named.is_file():
                line = _line_of(sources[path], match.start())
                found.append(Problem(path, line, "H001", f"no such file: {target}"))
    return found


def _check_page_icons(pages: list[Path], sources: dict[Path, str]) -> list[Problem]:
    """Report a tab icon drawn in the page rather than taken from the mark.

    A program has one mark and wears it in two places: in front of its
    wordmark, and in the browser's tab.  Both come from one list of shapes,
    rendered by ``Glyph`` and serialised by ``useFavicon`` -- see js/shell.js.

    This is the rule that was broken.  Both programs had a second drawing
    percent-encoded into a ``data:`` URI here: unreadable, unreachable from
    anything, and never going to be edited when the header's mark changed.
    One of them ended up a battery of different proportions charged to a
    different level, the other a bolt of a different shape, and nothing could
    say so except opening the two side by side.  An icon with a drawing in it
    is the only shape that failure can take, so it is the thing to refuse.
    """
    found: list[Problem] = []
    for path in pages:
        for match in HTML_ICON.finditer(sources[path]):
            target = HTML_ASSET.search(match.group(0))
            href = target.group(1) if target else ""
            # `data:,` is the empty placeholder a page keeps so the browser
            # does not go asking for a /favicon.ico while the module loads.
            if not href.startswith("data:") or href == "data:,":
                continue
            found.append(
                Problem(
                    path,
                    _line_of(sources[path], match.start()),
                    "H003",
                    "a tab icon drawn here rather than from the program's mark; "
                    "declare the mark once and stamp it with useFavicon",
                )
            )
    return found


def _name_origins(
    vendor_module: Path | None, modules: list[Path], sources: dict[Path, str]
) -> dict[str, str]:
    """Map every name a page can use to the module that binds it."""
    origins: dict[str, str] = {}
    if vendor_module is not None:
        origins.update(dict.fromkeys(vendor_names(vendor_module), vendor_module.name))
    for module in modules:
        for name in EXPORTED.findall(sources[module]):
            origins.setdefault(name, module.name)
    return origins


def check_tree(
    root: Path, *, core: Path | None = None, library: bool = False
) -> list[Problem]:
    """Check the whole static tree: the wiring, the style, the page.

    ``core`` names the shared package's static tree, whose modules this one
    imports through :data:`CORE_PREFIX`.  Both trees are read: the shared
    modules' exports are what a program's imports are checked against, and
    the two stylesheets are one stylesheet as far as the browser is
    concerned, so a class either of them defines answers for markup in
    either of them.

    ``library`` says the tree being checked *is* the shared one, with no
    program around it.  Two checks are then not answerable and are left
    out: an export the shared package's own modules never import is the
    normal case for a library, and so is a rule in ``core.css`` that only a
    program's markup wears.  Both are checked when a program is linted with
    ``core=`` pointing here.
    """
    # Absolute from here down: an import resolves to an absolute path, and
    # the two are keys into the same dictionaries.  A relative root -- which
    # is what a command line hands over -- would key every module twice and
    # quietly answer "nobody imports this" about all of them.
    root = root.resolve()
    # In library mode the tree being checked *is* the shared one, so it is
    # its own `/core/`: the modules in it reach each other by the prefix a
    # browser will serve them under, not by a relative path that only works
    # before the package is installed.
    core = core.resolve() if core is not None else (root if library else None)
    # In library mode the two are one tree, and reading it twice would
    # report everything in it twice.
    shared = core if core is not None and core != root else None

    def own(pattern: str) -> list[Path]:
        """Find the program's own files of one kind.

        Not the vendored runtime's, and not the shared tree's if that
        happens to sit inside this one.
        """
        return sorted(
            p
            for p in root.rglob(pattern)
            if VENDOR not in p.parts and not (shared and shared in p.parents)
        )

    scripts = own("*.js")
    styles = own("*.css")
    pages = own("*.html")
    core_scripts = (
        sorted(p for p in shared.rglob("*.js") if VENDOR not in p.parts)
        if shared is not None
        else []
    )
    core_styles = (
        sorted(p for p in shared.rglob("*.css") if VENDOR not in p.parts)
        if shared is not None
        else []
    )
    every = [*scripts, *styles, *pages, *core_scripts, *core_styles]
    sources = {path: path.read_text(encoding="utf-8") for path in every}

    found = _check_page_assets(pages, sources, core)
    found += _check_page_icons(pages, sources)

    # The one vendored module the UI's own code imports by name.  There is
    # exactly one on a page -- a second copy of Preact is a second set of
    # hooks, and a component from one of them rendered inside the other
    # throws on its first `useState` -- so a program that imports the
    # shared frontend imports the shared runtime with it, and its own tree
    # has no `vendor/` at all.  Whichever tree carries it answers for both.
    vendor_module = next(root.glob(f"{VENDOR}/*.js"), None)
    if vendor_module is None and shared is not None:
        vendor_module = next(shared.glob(f"{VENDOR}/*.js"), None)

    # Where every name on this page comes from: the runtime's, and every
    # module's own exports.  A name used without the import that binds it
    # is a ReferenceError the moment that line runs, and nothing else here
    # -- not Biome, which cannot see across files without a bundler --
    # answers for it.
    origins = _name_origins(vendor_module, [*scripts, *core_scripts], sources)
    found += check_known_names([*scripts, *core_scripts], sources, origins)
    found += check_components([*scripts, *core_scripts], sources)

    found += check_wiring(scripts, core_scripts, sources, core, library=library)
    users = [*scripts, *core_scripts, *pages]
    # A rule in the shared stylesheet is answerable only by every program
    # that uses it at once, which no single run can see: one of them not
    # wearing `.toolbar` says nothing about the other.  So C002 asks about a
    # program's own stylesheet only -- and about nothing at all in library
    # mode, where there is no program.  C003 is the other way round: the two
    # sheets are one sheet as far as the browser is concerned, so a class
    # either of them defines answers for markup in either of them.
    if not library:
        found += check_style_use(styles, sources, users)
    found += check_style_defined([*styles, *core_styles], sources, users)
    found += check_card_widths([*scripts, *core_scripts], sources)
    # The server half of the page, one directory up from its static files.
    api = root.parent / "api.py"
    if api.is_file():
        found += check_operation_names(scripts, sources, api)

    return sorted(found, key=lambda p: (str(p.path), p.line, p.code))


def vendor_names(vendor_module: Path) -> set[str]:
    """Return the names the vendored module exports, from its bundle."""
    names: set[str] = set()
    for exported in VENDOR_EXPORT.findall(vendor_module.read_text(encoding="utf-8")):
        names.update(name for _, name in VENDOR_NAME.findall(exported))
    return names


def check_known_names(
    scripts: list[Path], sources: dict[Path, str], origins: dict[str, str]
) -> list[Problem]:
    """Report a name used without the import that binds it.

    A module that calls ``useState``, tags with ``html`` or calls
    ``panelWait`` relies on another module at run time.  The import that
    binds them is ordinary JavaScript, so a bundler would catch its
    absence; there is no bundler here, and the failure mode is the worst
    kind of quiet -- the component throws mid-render and the page keeps
    the tab that was open before it, so the tree looks fine and one card
    in it is simply not there.

    ``origins`` maps every name anything on this page exports -- the
    vendored runtime's, and every module's own -- to where it comes from.
    Only names on that list are answerable: a free variable this checker
    has never heard of is somebody else's business.

    Generous by design on both sides.  A name counts as bound if it
    arrives by any route -- a named import from anywhere, a star import,
    or a local ``const``/``function`` of its own -- and as used only in
    the shapes that really reach the runtime: a call, a tagged template,
    or a base class after ``extends``.  A name in a comment or an object
    key is neither, and stays silent.  One report per name, at its first
    use.
    """
    found: list[Problem] = []
    for path in scripts:
        text = sources[path]
        bound = _bound_names(text)
        # The code with its strings, templates and comments blanked: a
        # mention in prose or inside a literal never reaches the runtime.
        code = _blank_strings_and_comments(text)
        reported: set[str] = set()
        for pattern in RUNTIME_USE.values():
            for match in pattern.finditer(code):
                name = match.group(1)
                if name not in origins or name in bound or name in reported:
                    continue
                reported.add(name)
                found.append(
                    Problem(
                        path,
                        _line_of(text, match.start()),
                        "J006",
                        f"{name} is used but not imported from {origins[name]}",
                    )
                )
    return found


def _bound_names(text: str) -> set[str]:
    """Every name this module has, by any route.

    Generous by design: a named import from anywhere, a star import, a
    local declaration, a destructured binding, a parameter.  Over-counting
    here only means a check stays quiet; under-counting would mean it
    accused a module of not having what it plainly has.
    """
    bound: set[str] = set()
    for names in IMPORT_NAMES.findall(text):
        bound.update(
            name.strip().split(" as ")[0] for name in names.split(",") if name.strip()
        )
    # Destructured bindings and parameter names: `[a, b]`, `{k: v}`,
    # `(a, b) =>`.  A parameter shadows an imported name harmlessly.
    #
    # Never after a `$`, which is the one shape that looks exactly like a
    # destructured binding and is not one: `<${Card}` -- a brace, a word, a
    # brace -- is a component being *used*, and counting it as a binding
    # would have every template introduce its own components.
    # The closing mark is looked at, not taken: taken, the comma after one
    # name was the comma the next one needed in front of it, and every
    # second name in `{ a, b, c, d }` went uncounted.
    bound.update(re.findall(r"(?<!\$)[([{,]\s*(\w+)\s*(?=[,)\]}=:])", text))
    bound.update(re.findall(r"\b(?:const|let|var|function|class)\s+(\w+)", text))
    bound.update(re.findall(r"\b(\w+)\s+as\s+\w+", text))
    return bound


def check_components(scripts: list[Path], sources: dict[Path, str]) -> list[Problem]:
    """Report a component drawn into a template that the module has not got.

    ``html`<${Band} .../>``` with no ``Band`` in scope is a ReferenceError
    thrown while the card is being built, which takes the whole tab with
    it: the page shows whatever was on screen before, nothing says why,
    and every other check on this page passes.  :func:`check_known_names`
    cannot see it -- it reads the code with template interiors blanked,
    which is where every one of these lives.

    Unlike that check, this one does not need to know where a name should
    have come from.  A component is either in scope or it is not, and
    anything in this shape that is not in scope is wrong however it was
    meant to be bound.

    """
    found: list[Problem] = []
    for path in scripts:
        text = sources[path]
        bound = _bound_names(text)
        reported: set[str] = set()
        for match in COMPONENT_USE.finditer(text):
            name = match.group(1)
            if name in bound or name in reported:
                continue
            reported.add(name)
            found.append(
                Problem(
                    path,
                    _line_of(text, match.start()),
                    "J007",
                    f"<${{{name}}} is drawn here and {name} is not in scope",
                )
            )
    return found


def check_wiring(
    scripts: list[Path],
    core_scripts: list[Path],
    sources: dict[Path, str],
    core: Path | None,
    *,
    library: bool = False,
) -> list[Problem]:
    """Check that the modules can actually find each other in a browser.

    The shared package's modules are read alongside the program's own, so
    an import through :data:`CORE_PREFIX` is held to the same two
    questions as any other: the file has to be there, and it has to export
    the name.  Only the program's own exports are held to being imported --
    a shared module exports for two programs and is complete when neither
    of them is looking.
    """
    found: list[Problem] = []
    every = [*scripts, *core_scripts]
    exported = {path: set(EXPORTED.findall(sources[path])) for path in every}
    imported: dict[Path, set[str]] = {path: set() for path in every}

    for path in every:
        found.extend(_check_imports(path, sources[path], exported, imported, core))
    if not library:
        found.extend(_check_unused_exports(scripts, sources, exported, imported))
    return found


def _check_imports(
    path: Path,
    text: str,
    exported: dict[Path, set[str]],
    imported: dict[Path, set[str]],
    core: Path | None,
) -> list[Problem]:
    """Check one module's imports, and note what it took from each target."""
    found: list[Problem] = []
    for match in IMPORT_ANY.finditer(text):
        spec = match.group(1)
        target = resolve_import(path, spec, core)
        if target is not None and target.is_file():
            continue
        said = (
            f"no such module: {spec}"
            if target is not None
            else f"{spec} needs the shared static tree, and none was named"
        )
        found.append(Problem(path, _line_of(text, match.start()), "J003", said))
    for match in IMPORT_NAMED.finditer(text):
        target = resolve_import(path, match.group(2), core)
        if target not in exported:
            continue  # vendored or missing; reported above if missing
        for raw in match.group(1).split(","):
            name = raw.split(" as ")[0].strip()
            if not name:
                continue
            imported[target].add(name)
            if name not in exported[target]:
                found.append(
                    Problem(
                        path,
                        _line_of(text, match.start()),
                        "J004",
                        f"{match.group(2)} does not export {name}",
                    )
                )
    for match in IMPORT_STAR.finditer(text):
        target = resolve_import(path, match.group(2), core)
        if target in exported:
            imported[target].update(exported[target])
    return found


def _check_unused_exports(
    scripts: list[Path],
    sources: dict[Path, str],
    exported: dict[Path, set[str]],
    imported: dict[Path, set[str]],
) -> list[Problem]:
    """Report a name a program's own module exports that nothing imports."""
    found: list[Problem] = []
    for path in scripts:
        if path.name == ENTRY_MODULE:
            continue
        for name in sorted(exported[path] - imported[path]):
            match = re.search(
                rf"^export\s+\S+\s+{re.escape(name)}\b", sources[path], re.M
            )
            line = _line_of(sources[path], match.start()) if match else 1
            found.append(
                Problem(path, line, "J005", f"{name} is exported but never imported")
            )
    return found


def check_style_use(
    styles: list[Path], sources: dict[Path, str], users: list[Path]
) -> list[Problem]:
    """Report CSS classes that the JavaScript and the page never mention.

    Deliberately generous: a class counts as used if its name appears
    anywhere in a module or the page, because half of them are assembled at
    run time (``` `pill ${state}` ```) and a checker that guessed at those
    would cry wolf.  What is left over is a rule for an element that is gone.
    """
    text = "\n".join(sources[path] for path in users)
    found: list[Problem] = []
    for path in styles:
        lines = sources[path].splitlines()
        for name, line in sorted(css_classes(sources[path]).items()):
            if KEEP in lines[line - 1]:
                continue
            if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text):
                found.append(Problem(path, line, "C002", f".{name} is used by nothing"))
    return found


# A `class="a b c"` written straight into a template, with nothing
# interpolated into it.  Those are the ones a stylesheet can be held to:
# `class=${`pill ${state}`}` is assembled at run time and is not this
# check's business.
STATIC_CLASS = re.compile(r'class="([^"$<>{}]*)"')


def check_style_defined(
    styles: list[Path], sources: dict[Path, str], users: list[Path]
) -> list[Problem]:
    """Report classes written into markup that no stylesheet defines.

    The mirror of :func:`check_style_use`, and the half that was missing:
    a rule nothing uses is dead weight, but an element wearing a class
    nothing defines is a *bug*, and an invisible one -- the browser applies
    no styling and reports nothing, so the element simply renders as a
    plain block wherever it happens to sit.  That is what
    ``class="modal-backdrop"`` did to the whitelist's "add a tag" dialog:
    a modal that was not a modal, on a page where every other one was.

    Only literal, fully-written class attributes are checked, for the same
    reason the other direction is generous: a name assembled at run time is
    not there to be read.
    """
    defined: set[str] = set()
    for path in styles:
        defined |= set(css_classes(sources[path]))
    found: list[Problem] = []
    for path in users:
        text = sources[path]
        for match in STATIC_CLASS.finditer(text):
            for name in match.group(1).split():
                if name in defined:
                    continue
                found.append(
                    Problem(
                        path,
                        _line_of(text, match.start()),
                        "C003",
                        f".{name} is worn by an element but no stylesheet defines it",
                    )
                )
    return found


# `what="Reading the event log"` or `what=${['A', 'B']}` on a Progress.
# Only the literal forms: a name assembled at run time is not there to be
# read, and is not this check's business.
PROGRESS_WHAT = re.compile(
    r"\$\{Progress\}[^>]*?\bwhat=(\$\{\[[^\]]*\]\}|\"[^\"]*\")", re.S
)
# `ctx.worker.run("Reading the whitelist", ...)` and its f-string cousins.
# An f-string's literal head is what a prefix match can be held to.
WORKER_RUN = re.compile(r"worker\.run\(\s*f?\"([^\"{]*)")
# The other shape: a handler that picks its wording first and passes the
# variable -- `name = "Reading all properties"`, `label = f"Writing {x}"`.
WORKER_RUN_VAR = re.compile(r"worker\.run\(\s*([a-z_][a-z_0-9]*)\s*[,)]")
ASSIGNED = r"^\s*{name}\s*=[^\n]*"
# A whole double-quoted literal, f-string or not, scanned left to right so
# that what sits *between* two of them is never mistaken for a third.
PY_STRING = re.compile(r"f?\"((?:[^\"\\\n]|\\.)*)\"")


def operation_names(api_source: str) -> set[str]:
    """Return the operation names ``web/api.py`` publishes to the link.

    Most are written at the call.  The handlers that choose their wording
    first -- one name for a category and another for the whole charger --
    pass a local instead, so the literals assigned to any local that
    reaches ``worker.run`` count too.
    """
    names = {name for name in WORKER_RUN.findall(api_source) if name}
    for local in set(WORKER_RUN_VAR.findall(api_source)):
        for line in re.findall(
            ASSIGNED.format(name=re.escape(local)), api_source, re.M
        ):
            names.update(
                head
                for literal in PY_STRING.findall(line)
                if (head := literal.split("{")[0])
            )
    return names


def check_operation_names(
    scripts: list[Path], sources: dict[Path, str], api: Path
) -> list[Problem]:
    """Report progress bars waiting on an operation nobody publishes.

    ``Progress`` matches the running operation by prefix, so a panel's
    ``what`` is half of a pair whose other half is a string literal in
    ``web/api.py``.  Neither language's tooling can see the pair: Biome
    does not read Python, pytest does not read the templates, and the
    failure is a bar that never draws -- which is exactly how the
    transactions tab shipped with "Reading *the* charging sessions"
    against a worker saying "Reading charging sessions".

    Both directions of the match are wrong in their own way and both are
    reported.  A ``what`` no operation starts with waits forever; a
    ``what`` shorter than the operation it means matches every other
    read that shares its opening words, which is how the properties
    panel came to draw a bar for the event log.

    One operation may publish under several names -- an f-string's head
    and the fuller wording it grows into, "Reading the event log" and
    "Reading the event log since 7d" -- so what is counted is families,
    not literals: names that all begin with the shortest of them are one
    read under different words, and naming that read is correct.
    """
    published = operation_names(api.read_text(encoding="utf-8"))
    found: list[Problem] = []
    for path in scripts:
        text = sources[path]
        for match in PROGRESS_WHAT.finditer(text):
            line = _line_of(text, match.start())
            for name in re.findall(r"['\"]([^'\"]*)['\"]", match.group(1)):
                matched = sorted(op for op in published if op.startswith(name))
                if not matched:
                    found.append(
                        Problem(
                            path,
                            line,
                            "C004",
                            f"no operation in web/api.py begins {name!r}, "
                            "so this progress bar never draws",
                        )
                    )
                elif not all(op.startswith(matched[0]) for op in matched):
                    found.append(
                        Problem(
                            path,
                            line,
                            "C004",
                            f"{name!r} is a prefix of several unrelated operations "
                            f"({', '.join(matched[:3])}...), so this bar draws for "
                            "other panels' reads",
                        )
                    )
    return found


# `width="full"` on a Card, and the three things that earn it: a table, the
# log, and a plot -- a chart is drawn to the width it is given, and one bar
# per day over a year of charging is a row's worth of content by any reading
# of the word.
CARD_FULL = re.compile(r"\bwidth=\"full\"")
ROW_WIDE = re.compile(r"<table\b|class=\"logs\b|<svg\b")
# Components are top-level functions here, so the one a match sits in runs
# from the `function` line above it to the next one at column zero.
TOP_LEVEL_FUNCTION = re.compile(r"^(?:export\s+)?function\s+\w+", re.M)


def enclosing_function(text: str, index: int) -> str:
    """Return the top-level function body an offset falls inside."""
    starts = [m.start() for m in TOP_LEVEL_FUNCTION.finditer(text)]
    before = [start for start in starts if start <= index]
    if not before:
        return text
    after = [start for start in starts if start > index]
    return text[before[-1] : after[0] if after else len(text)]


def check_card_widths(scripts: list[Path], sources: dict[Path, str]) -> list[Problem]:
    """Report cards claiming a whole row without a row's worth of content.

    ``.card.full`` spans the grid from edge to edge, which also *ends* the
    row -- every card after it starts a new one.  The stylesheet has said
    since the grid was built that this is for content genuinely a row wide
    -- a table, the log, a plot -- and nine cards had it anyway: short
    panels spanning 1600px with the rest of the tab stacked underneath
    them.

    A comment cannot be held to, so this is the same sentence as a check.
    Only the literal ``width="full"`` is read: a card whose width follows
    what it is holding writes ``width=${open ? 'full' : undefined}``, and
    that one is answering the question already.
    """
    found: list[Problem] = []
    for path in scripts:
        text = sources[path]
        for match in CARD_FULL.finditer(text):
            if ROW_WIDE.search(enclosing_function(text, match.start())):
                continue
            found.append(
                Problem(
                    path,
                    _line_of(text, match.start()),
                    "C005",
                    'width="full" ends the row for every card after it, and '
                    'this one holds no table, log or plot -- use "wide" or '
                    "leave it a column",
                )
            )
    return found


USAGE = (
    "usage: python -m devicectl.devtools.frontlint <static-root> "
    "[--core <shared-static-root>] [--library]"
)


def main(argv: list[str] | None = None) -> int:
    """Check the static tree named on the command line."""
    args = list(sys.argv[1:] if argv is None else argv)
    library = "--library" in args
    args = [arg for arg in args if arg != "--library"]
    core: Path | None = None
    if "--core" in args:
        at = args.index("--core")
        if at + 1 >= len(args):
            print(USAGE)
            return 2
        core = Path(args[at + 1])
        del args[at : at + 2]
    if not args:
        print(USAGE)
        return 2
    root = Path(args[0])
    problems = check_tree(root, core=core, library=library)
    for problem in problems:
        print(problem.render(root.resolve().parent))
    print(
        f"frontlint: {len(problems)} problem(s) in {root}"
        if problems
        else f"frontlint: clean ({root})"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
