"""Tests for the frontend linter.

The run against a real UI lives in the program that owns that UI; what is
here is the checker's own behaviour, on trees built for the purpose.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from devicectl.devtools import frontlint


def codes(problems) -> list[str]:
    return [p.code for p in problems]


# --- Style --------------------------------------------------------------------------------


def test_the_classes_a_stylesheet_defines() -> None:
    found = frontlint.css_classes(".pill.on > .dot,\n.card { margin: 0.5em; }\n")
    assert found == {"pill": 1, "on": 1, "dot": 1, "card": 2}


def test_a_rule_nothing_uses_is_dead() -> None:
    styles = {Path("app.css"): ".live { color: red; }\n.gone { color: blue; }\n"}
    users = {Path("app.js"): 'html`<p class="live"></p>`;\n'}
    found = frontlint.check_style_use(
        [Path("app.css")], {**styles, **users}, [Path("app.js")]
    )
    assert [(p.code, p.line) for p in found] == [("C002", 2)]
    assert ".gone is used by nothing" in found[0].message


def test_a_class_only_assembled_at_run_time_counts_as_used() -> None:
    # `` `pill ${state}` `` never writes "warn" into markup, but the name is
    # there in the module, and that is all this check asks for.
    styles = {Path("app.css"): ".warn { color: amber; }\n"}
    users = {Path("app.js"): "const cls = `pill ${bad ? 'warn' : 'ok'}`;\n"}
    assert (
        frontlint.check_style_use(
            [Path("app.css")], {**styles, **users}, [Path("app.js")]
        )
        == []
    )


def test_a_class_nothing_defines_is_a_problem() -> None:
    # The failure this check exists for: a dialog wearing `modal-backdrop`,
    # which no stylesheet ever defined, so it rendered as a plain block.
    styles = {Path("app.css"): ".modal { position: fixed; }\n"}
    users = {
        Path("app.js"): 'html`<div class="modal-backdrop">\n  <div class="modal">`;\n'
    }
    found = frontlint.check_style_defined(
        [Path("app.css")], {**styles, **users}, [Path("app.js")]
    )
    assert [(p.code, p.line) for p in found] == [("C003", 1)]
    assert "modal-backdrop" in found[0].message


def test_a_class_assembled_at_run_time_is_not_held_to_a_stylesheet() -> None:
    # `class=${`pill ${state}`}` is not a literal attribute, so it is not
    # this check's business -- the same generosity C002 shows in reverse.
    styles = {Path("app.css"): ".pill { color: red; }\n"}
    users = {Path("app.js"): "html`<span class=${`pill ${state}`}></span>`;\n"}
    assert (
        frontlint.check_style_defined(
            [Path("app.css")], {**styles, **users}, [Path("app.js")]
        )
        == []
    )


def test_every_class_in_a_literal_attribute_is_checked() -> None:
    styles = {Path("app.css"): ".btn { border: 0; }\n"}
    users = {Path("app.js"): 'html`<button class="btn ghost small">`;\n'}
    found = frontlint.check_style_defined(
        [Path("app.css")], {**styles, **users}, [Path("app.js")]
    )
    assert sorted(p.message.split()[0] for p in found) == [".ghost", ".small"]


def test_a_rule_marked_keep_is_left_alone() -> None:

    styles = {Path("app.css"): ".unseen { color: red; } /* frontlint: keep */\n"}
    assert frontlint.check_style_use([Path("app.css")], styles, []) == []


# --- Wiring -------------------------------------------------------------------------------


def wiring(
    tmp_path: Path, files: dict[str, str], core: dict[str, str] | None = None
) -> list:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    scripts = sorted(tmp_path / name for name in files if name.endswith(".js"))
    shared: Path | None = None
    core_scripts: list[Path] = []
    if core is not None:
        shared = tmp_path / "core"
        (shared / "js").mkdir(parents=True, exist_ok=True)
        for name, text in core.items():
            (shared / name).write_text(text, encoding="utf-8")
        core_scripts = sorted(shared / name for name in core if name.endswith(".js"))
    sources = {
        path: path.read_text(encoding="utf-8") for path in [*scripts, *core_scripts]
    }
    return frontlint.check_wiring(scripts, core_scripts, sources, shared)


def test_an_import_of_a_module_that_is_not_there(tmp_path: Path) -> None:
    found = wiring(tmp_path, {"app.js": "import { fmt } from './missing.js';\n"})
    assert codes(found) == ["J003"]
    assert "no such module: ./missing.js" in found[0].message


def test_an_import_of_a_name_the_module_does_not_export(tmp_path: Path) -> None:
    found = wiring(
        tmp_path,
        {
            "app.js": "import { fmt, pad } from './ui.js';\nfmt(pad(1));\n",
            "ui.js": "export function fmt(v) { return v; }\n",
        },
    )
    assert codes(found) == ["J004"]
    assert "./ui.js does not export pad" in found[0].message


def test_an_export_nobody_imports(tmp_path: Path) -> None:
    found = wiring(
        tmp_path,
        {
            "app.js": "import { fmt } from './ui.js';\nfmt(1);\n",
            "ui.js": "export function fmt(v) { return v; }\nexport function pad(v) { return v; }\n",
        },
    )
    assert [(p.code, p.line) for p in found] == [("J005", 2)]
    assert "pad is exported but never imported" in found[0].message


def test_the_entry_module_answers_to_the_page_not_to_an_import(tmp_path: Path) -> None:
    assert wiring(tmp_path, {"app.js": "export function boot() { return 1; }\n"}) == []


def test_a_star_import_takes_everything(tmp_path: Path) -> None:
    assert (
        wiring(
            tmp_path,
            {
                "app.js": "import * as ui from './ui.js';\nui.fmt(1);\n",
                "ui.js": "export function fmt(v) { return v; }\n",
            },
        )
        == []
    )


# --- The runtime's names -------------------------------------------------------------------


def runtime_names(tmp_path: Path, files: dict[str, str]) -> list:
    """Run check_known_names against a fake tree with a fake vendor."""
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    scripts = sorted(tmp_path / name for name in files if name.endswith(".js"))
    sources = {path: path.read_text(encoding="utf-8") for path in scripts}
    vendor = tmp_path / "vendor.js"
    origins = dict.fromkeys(frontlint.vendor_names(vendor), vendor.name)
    return frontlint.check_known_names(scripts, sources, origins)


def test_a_hook_used_without_its_import(tmp_path: Path) -> None:
    # The properties.js bug: the import line was lost in a refactor and the
    # tab went quietly blank -- the component throws mid-render and the
    # page keeps whatever it showed before.
    found = runtime_names(
        tmp_path,
        {
            "vendor.js": "export{a as h,b as html,c as useState,d as useEffect};\n",
            "page.js": "export const Tab = () => {\n  const [x, setX] = useState(0);\n  return html`<b>${x}</b>`;\n};\n",
        },
    )
    assert codes(found) == ["J006", "J006"]
    assert "useState is used but not imported" in found[0].message
    assert "html is used but not imported" in found[1].message


def test_the_runtime_names_with_their_import_present(tmp_path: Path) -> None:
    found = runtime_names(
        tmp_path,
        {
            "vendor.js": "export{a as h,b as html,c as useState};\n",
            "page.js": (
                "import { html, useState } from './vendor.js';\n"
                "export const Tab = () => html`<b>${useState(0)}</b>`;\n"
            ),
        },
    )
    assert found == []


def test_the_runtime_names_bound_any_other_way(tmp_path: Path) -> None:
    # A local of its own, an aliased import, a star import, a parameter
    # that shadows the name -- all bind it, and none is this check's business.
    found = runtime_names(
        tmp_path,
        {
            "vendor.js": "export{a as html,b as useState,c as render};\n",
            "page.js": (
                "import { useState as useVal } from './vendor.js';\n"
                "import * as preact from './other.js';\n"
                "const render = (v) => v;\n"
                "export const f = ([k, v, html]) => html(v);\n"
                "export const g = () => useVal(0) + preact.html;\n"
            ),
        },
    )
    assert found == []


def test_every_name_of_a_destructured_parameter_is_bound(tmp_path: Path) -> None:
    # The fourth of four: the comma closing one name is the one opening the
    # next, and the pattern used to swallow it.
    found = runtime_names(
        tmp_path,
        {
            "vendor.js": "export{a as html,b as render};\n",
            "page.js": "export const f = ({ a, b, c, render }) => render(a, b, c);\n",
        },
    )
    assert found == []


# --- Components drawn into a template ------------------------------------------------------


def components(tmp_path: Path, files: dict[str, str]) -> list:
    """Run check_components against a fake tree."""
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    scripts = sorted(tmp_path / name for name in files if name.endswith(".js"))
    sources = {path: path.read_text(encoding="utf-8") for path in scripts}
    return frontlint.check_components(scripts, sources)


def test_a_component_drawn_without_its_import(tmp_path: Path) -> None:
    # The alfenctl bug: a card was moved to a shared module and the import
    # line never arrived.  Every other check passed -- the module is valid
    # JavaScript, the class names are all defined, the template parses --
    # and the tab was blank, because the component throws mid-render and
    # the page keeps whatever it last showed.
    found = components(
        tmp_path,
        {
            "page.js": (
                "import { html } from './vendor.js';\n"
                "export const Tab = () => html`<div><${Band} min=${1} /></div>`;\n"
            ),
        },
    )
    assert codes(found) == ["J007"]
    assert "Band is not in scope" in found[0].message
    assert found[0].line == 2


def test_a_component_this_module_has_by_any_route(tmp_path: Path) -> None:
    # Imported, declared here, taken apart from a bag of them, handed in as
    # a parameter: all of them bind it, and none is this check's business.
    found = components(
        tmp_path,
        {
            "page.js": (
                "import { html } from './vendor.js';\n"
                "import { Card } from './ui.js';\n"
                "const { Extra } = parts;\n"
                "function Mine() {}\n"
                "export const Tab = ({ Slot }) => html`\n"
                "  <${Card}><${Mine} /><${Extra} /><${Slot} /><//>\n"
                "`;\n"
            ),
        },
    )
    assert found == []


def test_a_mention_that_is_not_a_use_stays_silent(tmp_path: Path) -> None:
    # A comment, an object key, a word in a template literal: none of them
    # reach the runtime as the vendor's name.
    found = runtime_names(
        tmp_path,
        {
            "vendor.js": "export{a as h,b as html,c as useState};\n",
            "page.js": (
                "import { html } from './vendor.js';\n"
                "// useState would be shorter\n"
                "const key = { html: 1 };\n"
                "export const x = () => html`${(1 / 2).toFixed(1)} h`;\n"
            ),
        },
    )
    assert found == []


def test_a_page_that_asks_for_a_file_that_is_not_there(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="app.css">\n'
        '<script type="module" src="js/app.js"></script>\n'
        '<link rel="icon" href="https://example.invalid/i.png">\n'
        '<body class="a"></body>\n',
        encoding="utf-8",
    )
    (tmp_path / "app.css").write_text(".a { color: red; }\n", encoding="utf-8")
    found = frontlint.check_tree(tmp_path)
    assert [(p.code, p.line) for p in found] == [("H001", 2)]
    assert "no such file: js/app.js" in found[0].message  # the absolute URL is not ours


def test_a_page_that_draws_its_own_tab_icon(tmp_path: Path) -> None:
    """A second drawing of the mark, in the one place nobody will reread it.

    This is how the two pages came to wear one battery in the header and a
    different one in the tab: the tab's was percent-encoded into the page and
    the header's was not, so neither could be changed by changing the other.
    """
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="app.css">\n'
        '<link rel="icon" href="data:image/svg+xml,%3Csvg%3E%3C/svg%3E">\n'
        '<body class="a"></body>\n',
        encoding="utf-8",
    )
    (tmp_path / "app.css").write_text(".a { color: red; }\n", encoding="utf-8")
    found = frontlint.check_tree(tmp_path)
    assert [(p.code, p.line) for p in found] == [("H003", 2)]
    assert "useFavicon" in found[0].message


def test_the_empty_placeholder_icon_is_not_a_drawing(tmp_path: Path) -> None:
    """`data:,` is there so the browser does not go asking for a favicon.ico."""
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="app.css">\n'
        '<link rel="icon" href="data:," />\n'
        '<body class="a"></body>\n',
        encoding="utf-8",
    )
    (tmp_path / "app.css").write_text(".a { color: red; }\n", encoding="utf-8")
    assert frontlint.check_tree(tmp_path) == []


# --- Operation names ----------------------------------------------------------------------

API = """
def read_log(ctx):
    if since:
        return ctx.worker.run(f"Reading the event log since {since}", back)
    return ctx.worker.run("Reading the event log", read)


def read_properties(ctx):
    name = f"Reading properties ({category})" if category else "Reading all properties"
    return ctx.worker.run(name, collect)


def read_sessions(ctx):
    return ctx.worker.run("Reading charging sessions", read)
"""
"""A stand-in for web/api.py: the three shapes a name reaches the link in.

Written at the call, grown from an f-string, and chosen into a local before
the call -- the properties read does the last of those.
"""


def lint_against_api(tmp_path: Path, panel: str):
    """Lint a one-module tree whose server half is the fake API above."""
    static = tmp_path / "static"
    (static / "js").mkdir(parents=True)
    (tmp_path / "api.py").write_text(API, encoding="utf-8")
    (static / "js" / "app.js").write_text(panel, encoding="utf-8")
    return [p for p in frontlint.check_tree(static) if p.code == "C004"]


def test_the_names_an_api_publishes_include_the_ones_it_picks_first() -> None:
    assert frontlint.operation_names(API) == {
        "Reading the event log since ",
        "Reading the event log",
        "Reading properties (",
        "Reading all properties",
        "Reading charging sessions",
    }


def test_a_progress_bar_waiting_on_a_name_nobody_publishes(tmp_path: Path) -> None:
    # The transactions bug, exactly: one extra word and the bar never draws.
    found = lint_against_api(
        tmp_path,
        'html`<${Progress} link=${link} what="Reading the charging sessions" />`;\n',
    )
    assert [p.code for p in found] == ["C004"]
    assert "never draws" in found[0].message


def test_a_progress_bar_whose_name_catches_everybody_elses_reads(
    tmp_path: Path,
) -> None:
    # The properties bug: a prefix short enough to match unrelated reads.
    found = lint_against_api(
        tmp_path, 'html`<${Progress} link=${link} what="Reading" />`;\n'
    )
    assert [p.code for p in found] == ["C004"]
    assert "other panels' reads" in found[0].message


def test_one_operation_under_several_names_is_one_operation(tmp_path: Path) -> None:
    # "Reading the event log" and "... since 7d" are one read, so naming the
    # shorter of them is right rather than over-broad.
    assert not lint_against_api(
        tmp_path, 'html`<${Progress} link=${link} what="Reading the event log" />`;\n'
    )


def test_a_panel_may_name_every_operation_it_waits_on(tmp_path: Path) -> None:
    assert not lint_against_api(
        tmp_path,
        "html`<${Progress} link=${link} "
        "what=${['Reading all properties', 'Reading properties']} />`;\n",
    )


# --- What a card claims -------------------------------------------------------------------


def lint_widths(tmp_path: Path, module: str):
    """Lint a one-module tree and return just the card-width reports."""
    (tmp_path / "js").mkdir(parents=True)
    (tmp_path / "js" / "app.js").write_text(module, encoding="utf-8")
    return [p for p in frontlint.check_tree(tmp_path) if p.code == "C005"]


def test_a_short_card_may_not_claim_the_whole_row(tmp_path: Path) -> None:
    found = lint_widths(
        tmp_path,
        "function Firmware() {\n"
        '  return html`<${Card} title="Firmware" width="full">\n'
        "    <div>${releases.map((r) => html`<p>${r.name}</p>`)}</div>\n"
        "  <//>`;\n"
        "}\n",
    )
    assert [p.code for p in found] == ["C005"]
    assert "ends the row" in found[0].message
    assert "table, log or plot" in found[0].message


def test_a_table_earns_the_row(tmp_path: Path) -> None:
    assert not lint_widths(
        tmp_path,
        "function MeterMap() {\n"
        '  return html`<${Card} title="Custom meter map" width="full">\n'
        "    <table><tbody>${rows}</tbody></table>\n"
        "  <//>`;\n"
        "}\n",
    )


def test_a_plot_earns_the_row(tmp_path: Path) -> None:
    # A chart is drawn to the width it is given, and one bar per day over a
    # year of charging wants the row as much as any table does.
    assert not lint_widths(
        tmp_path,
        "function EnergyChart() {\n"
        '  return html`<${Card} title="Energy delivered, by day" width="full">\n'
        '    <svg viewBox="0 0 720 150">${bars}</svg>\n'
        "  <//>`;\n"
        "}\n",
    )


def test_the_table_has_to_be_in_the_card_that_claims_the_row(tmp_path: Path) -> None:
    # Two components in one module: the table belongs to the first, so it
    # does not excuse the second.
    found = lint_widths(
        tmp_path,
        "function MeterTest() {\n"
        "  return html`<${Card}><table></table><//>`;\n"
        "}\n"
        "\n"
        "function WifiScan() {\n"
        '  return html`<${Card} width="full"><p>${ssid}</p><//>`;\n'
        "}\n",
    )
    assert [(p.code, p.line) for p in found] == [("C005", 6)]


def test_a_width_that_follows_the_content_is_not_asked(tmp_path: Path) -> None:
    # `width=${open ? 'full' : undefined}` is the card answering for itself.
    assert not lint_widths(
        tmp_path,
        "function Console() {\n"
        "  return html`<${Card} width=${open ? 'full' : undefined}><p>x</p><//>`;\n"
        "}\n",
    )


# --- The real thing -----------------------------------------------------------------------


# --- the shared static tree, imported through /core/ ----------------------------------------


def test_a_shared_module_is_found_through_the_core_prefix(tmp_path: Path) -> None:
    found = wiring(
        tmp_path,
        {"app.js": "import { Card } from '/core/js/ui.js';\nCard();\n"},
        {"js/ui.js": "export function Card() {}\n"},
    )
    assert found == []


def test_a_name_the_shared_module_does_not_export(tmp_path: Path) -> None:
    found = wiring(
        tmp_path,
        {"app.js": "import { Nope } from '/core/js/ui.js';\nNope();\n"},
        {"js/ui.js": "export function Card() {}\n"},
    )
    assert [p.code for p in found] == ["J004"]


def test_a_shared_module_that_is_not_there(tmp_path: Path) -> None:
    found = wiring(
        tmp_path,
        {"app.js": "import { Card } from '/core/js/gone.js';\nCard();\n"},
        {"js/ui.js": "export function Card() {}\n"},
    )
    assert [p.code for p in found] == ["J003"]


def test_a_core_import_with_no_shared_tree_named(tmp_path: Path) -> None:
    found = wiring(tmp_path, {"app.js": "import { Card } from '/core/js/ui.js';\n"})
    assert [p.code for p in found] == ["J003"]
    assert "shared static tree" in found[0].message


def test_a_shared_export_no_program_here_imports_is_not_dead(tmp_path: Path) -> None:
    """A library exports for two programs; one of them is not the judge."""
    found = wiring(
        tmp_path,
        {"app.js": "import { Card } from '/core/js/ui.js';\nCard();\n"},
        {"js/ui.js": "export function Card() {}\nexport function Badge() {}\n"},
    )
    assert found == []


def test_the_two_stylesheets_are_one_stylesheet(tmp_path: Path) -> None:
    """A program's markup may wear a class the shared sheet defines."""
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text(
        "import { Card } from '/core/js/ui.js';\n"
        'export function App() { return html`<div class="card"></div>`; }\n'
        "Card();\n",
        encoding="utf-8",
    )
    (tmp_path / "app.css").write_text(".own { color: red }\n", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<link href="/core/core.css" /><div class="own"></div>', encoding="utf-8"
    )
    core = tmp_path / "shared"
    (core / "js").mkdir(parents=True)
    (core / "js" / "ui.js").write_text("export function Card() {}\n", encoding="utf-8")
    (core / "core.css").write_text(".card { color: blue }\n", encoding="utf-8")
    assert frontlint.check_tree(tmp_path, core=core) == []


def test_a_relative_root_still_matches_imports_to_modules(
    tmp_path: Path, monkeypatch
) -> None:
    """A root given relative to the cwd, which is how the command line gives it.

    An import resolves to an absolute path, so a tree scanned relative to
    somewhere keyed every module twice -- and then nothing imported
    anything, and every export in the program looked dead.
    """
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text(
        "import { Card } from './ui.js';\nCard();\n", encoding="utf-8"
    )
    (tmp_path / "js" / "ui.js").write_text(
        "export function Card() {}\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path.parent)
    assert frontlint.check_tree(Path(tmp_path.name)) == []


def test_the_shared_frontend_passes_its_own_linter() -> None:
    """The library, held to the checks that are answerable without a program.

    The other two -- an export nothing here imports, a rule no markup here
    wears -- are a program's to answer, and every program that adopts this
    package answers them by linting with ``core=`` pointing at this tree.
    """
    static = Path(frontlint.__file__).resolve().parents[1] / "web" / "static"
    problems = frontlint.check_tree(static, library=True)
    assert not problems, "\n".join(p.render(static.parent) for p in problems)


def test_the_shared_frontend_passes_biome() -> None:
    """Biome does the parsing and the rules; this is the same run CI makes.

    It is a single native binary and there is no Node here, so it is not a
    project dependency and may simply be absent: `biome.json` says what it
    checks, and the CI job installs it and runs it in its own right.
    """
    biome = shutil.which("biome")
    if biome is None:
        pytest.skip("biome is not installed (see biome.json and the CI workflow)")
    root = Path(frontlint.__file__).resolve().parents[3]
    if not (root / "biome.json").is_file():
        pytest.skip("not an editable checkout, so there is no biome.json to run")
    done = subprocess.run([biome, "ci"], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_shared_helper_used_without_its_import(tmp_path: Path) -> None:
    """The same check, for a name another module on the page exports.

    Biome cannot answer this: without a bundler it sees one file at a
    time, so a call to a helper nothing imported is, to it, a free
    variable that must be coming from somewhere.
    """
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "panels.js").write_text(
        "export function panelWait() {}\n", encoding="utf-8"
    )
    (tmp_path / "js" / "app.js").write_text(
        "import { panelWait } from './panels.js';\npanelWait();\n", encoding="utf-8"
    )
    (tmp_path / "js" / "access.js").write_text(
        "export function Access() {\n  return panelWait();\n}\n", encoding="utf-8"
    )
    found = [p for p in frontlint.check_tree(tmp_path) if p.code == "J006"]
    assert [(p.path.name, p.line) for p in found] == [("access.js", 2)]
    assert "panelWait is used but not imported from panels.js" in found[0].message


def test_a_shared_rule_no_one_program_wears_is_not_dead(tmp_path: Path) -> None:
    """C002 asks about a program's own stylesheet, never the shared one.

    One program not wearing `.toolbar` says nothing about the other, and a
    single run can only see one of them.
    """
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text(
        "import { Card } from '/core/js/ui.js';\n"
        'export function App() { return html`<div class="own"></div>`; }\n'
        "Card();\n",
        encoding="utf-8",
    )
    (tmp_path / "app.css").write_text(".own { color: red }\n", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<link href="/core/core.css" />', encoding="utf-8"
    )
    core = tmp_path / "shared"
    (core / "js").mkdir(parents=True)
    (core / "js" / "ui.js").write_text("export function Card() {}\n", encoding="utf-8")
    (core / "core.css").write_text(".toolbar { display: flex }\n", encoding="utf-8")
    assert frontlint.check_tree(tmp_path, core=core) == []
