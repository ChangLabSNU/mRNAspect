"""Core's deny list, enforced instead of observed.

Core — the layer the GUI ships — must
not contain research-data paths, CLI-argument parsing, or environment assumptions, and
must never import the Snakemake/training tooling. Core has held to that, but nothing
checked it, and "currently clean" is a fact about today rather than a property of the
package. An import lint was the intended enforcement and it did not exist.

Two things this is careful about, both of which would make it useless:

* It reads STRING CONSTANTS via the AST, not raw text. Several wrapper scripts describe
  the lab paths they used to hard-code, in comments, as the reason the setting exists
  (`run_align.sh:40`, `start_server.sh:42`). A grep-based lint would fail on the
  explanation of the fix.
* Every module under `mrnaspect/` must be classified. A lint with a hand-written list of
  files silently stops covering the package the moment someone adds one, so an
  unclassified module is itself a failure.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from .conftest import LAB_PRIVATE

PKG = Path(__file__).resolve().parent.parent

#: Core: the layer the product ships and the GUI consumes.
CORE_DIRS = ("analysis", "reporting", "wrappers", "runtime")
CORE_FILES = ("snapshot_keys.py", "provenance.py", "__init__.py")
#: Core parts of realtime/: the watcher belongs to the pipeline module.
CORE_REALTIME = ("watcher.py", "sealing.py", "backpressure.py", "manifest.py",
                 "__init__.py")

#: NOT Core: drivers and consumers. Each is allowed to parse arguments and know about
#: its environment, because that is what a driver is for.
NON_CORE = {
    "main.py",                  # the offline driver's CLI
    "realtime/main.py",         # RealtimePipeline is Core; its CLI half is not
    "realtime/mimic.py",        # Tier B replay helper
}

#: Lab and user roots that must never be baked into a shipped string. The lab's own
#: mounts are named in an untracked file (conftest LAB_PRIVATE), not here.
_LAB_ROOTS = [r.strip("/") for r in LAB_PRIVATE.get("roots", ())]
_ROOT_NAMES = "|".join(map(re.escape, [*_LAB_ROOTS, "Users", "home"]))
LAB_PATH = re.compile(rf"^/({_ROOT_NAMES})/")
#: The same without ``home``, for shell text, where ``/home/`` is not a string of its own.
_IN_SHELL = re.compile(rf"[\"' =]/({'|'.join(map(re.escape, [*_LAB_ROOTS, 'Users']))})/")
#: Research tooling Core must not reach for, in either direction.
FORBIDDEN_IMPORTS = {"snakemake", "argparse", "pandas", "sklearn", "torch", "bonito"}


def _classify(path: Path) -> str:
    rel = path.relative_to(PKG).as_posix()
    if rel.startswith("tests/"):
        return "tests"
    if rel in NON_CORE:
        return "non-core"
    parts = rel.split("/")
    if parts[0] in CORE_DIRS:
        return "core"
    if parts[0] == "realtime" and parts[-1] in CORE_REALTIME:
        return "core"
    if rel in CORE_FILES:
        return "core"
    return "unclassified"


def _py_files() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def _core_py() -> list[Path]:
    return [p for p in _py_files() if _classify(p) == "core"]


def test_every_module_is_classified() -> None:
    """A new module must be placed deliberately, not default into being unchecked."""
    stray = [p.relative_to(PKG).as_posix() for p in _py_files()
             if _classify(p) == "unclassified"]
    assert not stray, (
        "unclassified module(s): " + ", ".join(stray) +
        " — add to CORE_DIRS/CORE_FILES/CORE_REALTIME if the GUI ships it, or to "
        "NON_CORE if it is a driver")


def test_core_is_not_empty() -> None:
    """Guards the guard: a classifier that matched nothing would pass everything."""
    assert len(_core_py()) >= 15, f"only {len(_core_py())} Core modules found"


@pytest.mark.parametrize("path", _core_py(), ids=lambda p: p.name)
def test_core_imports_nothing_from_the_research_layer(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    bad = sorted(found & FORBIDDEN_IMPORTS)
    assert not bad, (
        f"{path.relative_to(PKG)} imports {bad} — Core ships inside the GUI and must "
        f"not carry CLI parsing or the research/training stack")


@pytest.mark.parametrize("path", _core_py(), ids=lambda p: p.name)
def test_core_bakes_in_no_lab_path(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = sorted({
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and LAB_PATH.match(node.value)})
    assert not bad, (
        f"{path.relative_to(PKG)} contains lab path(s) {bad} — a path that exists only "
        f"on one machine must be a setting, not a default")


def test_core_shell_wrappers_bake_in_no_lab_path() -> None:
    """The wrappers are Core too, and they are where the lab paths actually used to be.

    Comments are stripped rather than searched: several of these scripts explain, in a
    comment, which absolute path they no longer hard-code.
    """
    offenders: list[str] = []
    for sh in sorted((PKG / "wrappers").glob("*.sh")):
        for i, line in enumerate(sh.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if LAB_PATH.search(code) or _IN_SHELL.search(code):
                offenders.append(f"{sh.name}:{i}: {code.strip()}")
    assert not offenders, "lab path(s) in a shipped wrapper:\n" + "\n".join(offenders)
