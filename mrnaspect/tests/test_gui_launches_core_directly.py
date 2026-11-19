"""The GUI must launch Core without a package manager, and must name real modules.

Two different failures, neither of which any compiler on any host will catch for us.

**The product one.** Until 2026-10-12 the GUI ran Core as `pixi run … mrnaspect`, so the
shipped app required pixi on the end user's machine — on every machine this product is
meant to reach. The packaging spike had already established that Core needs no pixi at
runtime (`<env>/bin/python -m mrnaspect.main` is enough), but nothing stopped the dependency
coming back.

**The unverifiable one.** `cargo` does not exist on the Linux host this suite runs on
(no webkit2gtk, no root — see the header of `gui/src-tauri/src/lib.rs`), so the Rust
layer is typechecked only on the developer's Mac. But a compiler would not catch this
one anyway: the module the GUI runs is a STRING LITERAL. `-m mrnaspect.realtime.mian` builds
perfectly and fails at run time, on the operator's machine, mid-run. So the modules are
checked here, where they are checkable.

What this is careful about, because it is what makes such a lint useless:

* It asserts on what the code DOES — the argument of `Command::new` — not on whether the
  word "pixi" appears. `resolve_python` explains the removal in its doc comment (and
  until 2026-11-14 read `<core_dir>/.pixi/envs/default/bin/python` as a PATH); a
  grep-based lint would fail on the fix and on its own rationale. `test_core_layer_lint`
  records the same trap.
* It scans every source file under `gui/`, not the one file the bug was found in.
  `test_wrapper_portability` was scoped to `wrappers/` and so missed a `date +%s%N` added
  one directory over — the same defect shape, a guard scoped to a location rather than to
  the class of file it applies to.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GUI = REPO / "gui"

#: Spawning any of these means the end user must install a development tool.
PACKAGE_MANAGERS = {"pixi", "conda", "mamba", "micromamba", "poetry", "uv", "pipenv"}

SOURCE_SUFFIXES = {".rs", ".ts", ".tsx", ".js", ".mjs", ".html"}
SKIP_DIRS = {"node_modules", "target", "dist", "gen"}


def _sources() -> list[Path]:
    out = []
    for p in sorted(GUI.rglob("*")):
        if not p.is_file() or p.suffix not in SOURCE_SUFFIXES:
            continue
        if SKIP_DIRS.intersection(p.relative_to(GUI).parts):
            continue
        out.append(p)
    return out


def test_the_gui_tree_has_source_to_scan():
    """A lint that silently matches nothing passes forever.

    The sharding script shipped with `case $f in $PATTERN)`, which bash expands at parse
    time: it matched no files while reporting "0 port-using files" as success. A guard
    must refuse to be vacuous.
    """
    files = _sources()
    assert files, f"no GUI sources found under {GUI}"
    assert any(p.suffix == ".rs" for p in files), "no Rust source found; did the tree move?"


@pytest.mark.parametrize("path", _sources(), ids=lambda p: str(p.relative_to(GUI)))
def test_the_gui_spawns_no_package_manager(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    # The argument of a process spawn, not the word anywhere in the file.
    spawned = set(re.findall(r'Command::new\(\s*"([^"]+)"', text))
    spawned |= set(re.findall(r'\bspawn\(\s*"([^"]+)"', text))
    offending = {s for s in spawned if Path(s).name in PACKAGE_MANAGERS}
    assert not offending, (
        f"{path.relative_to(REPO)} spawns {sorted(offending)}. The shipped app must run "
        f"Core through an interpreter path (see resolve_python), not through a package "
        f"manager the end user would have to install."
    )


def test_every_module_the_gui_runs_is_importable():
    """`-m <module>` is a string the Rust compiler never validates."""
    lib = GUI / "src-tauri" / "src" / "lib.rs"
    text = lib.read_text(encoding="utf-8")
    # `"-m", "mrnaspect.main"` — allow a line break between the flag and its value.
    modules = re.findall(r'"-m"\s*,\s*"([A-Za-z_][\w.]*)"', text)
    assert modules, f"no `-m <module>` found in {lib.relative_to(REPO)}"
    # Both drivers, or the realtime path is launching the offline one.
    assert set(modules) == {"mrnaspect.main", "mrnaspect.realtime.main"}, (
        f"expected both drivers, found {sorted(set(modules))}")
    for mod in modules:
        assert importlib.util.find_spec(mod) is not None, (
            f"{lib.relative_to(REPO)} runs `python -m {mod}`, which does not import")
