"""A Tauri plugin is wired in four places, and missing one of them is silent.

`@tauri-apps/plugin-dialog` needs: the npm dependency, the cargo dependency, the builder
registration, and the capability. Three of those fail loudly when absent — npm cannot
resolve the import, cargo cannot find the crate, the builder will not compile. **The
capability does not.** Without `dialog:default` the button compiles, the app runs, the
click fires, and nothing opens. That is the one this file exists for.

No compiler runs on the host that writes this code — there is no cargo, node or tsc here
— so the four points are checked against each other statically, the way
`test_gui_core_contract.py` checks the camelCase/snake_case boundary a compiler would
also not have caught.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_GUI = Path(__file__).resolve().parents[2] / "gui"
_PKG = _GUI / "package.json"
_CARGO = _GUI / "src-tauri" / "Cargo.toml"
_LIB = _GUI / "src-tauri" / "src" / "lib.rs"
_CAPS = _GUI / "src-tauri" / "capabilities" / "default.json"
_HTML = _GUI / "index.html"
_MAIN = _GUI / "src" / "main.ts"

pytestmark = pytest.mark.skipif(
    not _HTML.exists(), reason="GUI sources are not in this checkout")

#: npm package -> (cargo crate, builder symbol, capability prefix). Every Tauri plugin
#: the shell uses belongs here; the test then holds all four ends together for each.
PLUGINS = {
    "@tauri-apps/plugin-dialog": ("tauri-plugin-dialog", "tauri_plugin_dialog", "dialog"),
}


def _permissions() -> list[str]:
    return json.loads(_CAPS.read_text())["permissions"]


@pytest.mark.parametrize("npm", sorted(PLUGINS))
def test_every_declared_plugin_is_wired_at_all_four_points(npm) -> None:
    crate, symbol, prefix = PLUGINS[npm]
    deps = json.loads(_PKG.read_text()).get("dependencies", {})
    missing = []
    if npm not in deps:
        missing.append(f"package.json dependency {npm!r}")
    if crate not in _CARGO.read_text():
        missing.append(f"Cargo.toml dependency {crate!r}")
    if symbol not in _LIB.read_text():
        missing.append(f"{symbol} in lib.rs")
    if not any(p.startswith(prefix + ":") for p in _permissions()):
        missing.append(f"a {prefix!r} permission in capabilities/default.json")
    assert not missing, (
        f"{npm} is half-wired; missing:\n  " + "\n  ".join(missing) + "\n"
        "The capability is the dangerous one: without it the call compiles, runs, and "
        "silently does nothing.")


def test_the_capability_is_not_quietly_dropped() -> None:
    """Named separately because it is the only failure a build would not catch."""
    assert "dialog:default" in _permissions(), (
        "dialog:default is gone from capabilities/default.json. Every Browse button "
        "will compile and do nothing.")


# --- the buttons and what they open -----------------------------------------

#: Field id -> the picker it must open. A directory for the places runs read and write and
#: for This Mac's models folder, a file for the reference and for the server BINARY (not
#: its bundle folder). `model` is not here: since I6 it is a list of the models in that
#: folder, chosen by name, so it has no picker of its own.
EXPECTED_PICKERS = {
    "input": "dir", "output": "dir", "models": "dir",
    "ref": "fasta", "server": "file", "config": "yaml",
    # A model folder for one run; it becomes an option of the Model list
    # (test_gui_model_choice.py), because a <select> has no path to write into.
    "model": "model",
}


def _buttons() -> dict[str, str]:
    html = _HTML.read_text()
    return {m.group(2): m.group(1) for m in
            re.finditer(r'data-pick="([a-z]+)"\s+data-for="([a-z]+)"', html)}


def test_every_path_field_has_a_browse_button() -> None:
    assert _buttons().keys() == EXPECTED_PICKERS.keys()


def test_each_field_opens_the_right_kind_of_picker() -> None:
    """A directory picker on the reference would make it unselectable."""
    assert _buttons() == EXPECTED_PICKERS


def test_every_kind_used_in_the_html_is_implemented_in_the_typescript() -> None:
    """A typo in data-pick would otherwise look up undefined and open a default dialog."""
    ts = _MAIN.read_text()
    table = ts[ts.index("const PICKERS"):ts.index("for (const button")]
    for kind in set(_buttons().values()):
        assert re.search(rf"\b{kind}:\s*\{{", table), (
            f"data-pick={kind!r} is used in index.html but not defined in PICKERS")


def test_a_cancelled_dialog_leaves_the_field_alone() -> None:
    """`open` returns null on cancel. Writing it would turn "never mind" into "", and
    for Reference there is no default to fall back to."""
    ts = _MAIN.read_text()
    body = ts[ts.index("button.addEventListener"):]
    assert 'typeof chosen === "string"' in body, (
        "the chosen path must be type-checked before it is written to the field; "
        "assigning a cancelled dialog's null would clear the field")


def test_the_picker_does_not_reintroduce_a_prefilled_path() -> None:
    """The buttons must not have smuggled a `value=` back into a path field."""
    html = _HTML.read_text()
    for field in EXPECTED_PICKERS:
        if f'<select id="{field}"' in html:
            continue      # a list, not a path field; test_gui_model_choice.py holds it
        block = re.search(rf'<input id="{field}"[^>]*>', html, re.S)
        assert block, f"no input found for {field}"
        assert "value=" not in block.group(0), (
            f"{field} opens pre-filled again; placeholder, never value (A6)")
