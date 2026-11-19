#!/usr/bin/env python3
"""Leave out of a staged environment what the app must not carry.

WHY THIS EXISTS. conda-forge's Python depends on GNU readline, and readline is
GPL-3.0-only: shipping it would ask the app to carry the GPL's text and offer the
library's source with every copy. Only Python's interactive prompt uses it, and the app
never opens one (`lib.rs` runs `python3 -m mrnaspect.main`). So it is left out
(2026-11-13): the package's own files, its conda record, and the one
extension module of Python's that loads it.

It cannot be left out of the SOLVE, because the dependency is Python's own. It is removed
from the copy that ships, `dist/bundle-env`, and the development environment keeps it.

pyphen's hyphenation dictionaries go the same way (L6). pyphen itself is tri-licensed
and stays, because WeasyPrint imports it; the dictionaries are "distributed under GPL,
LGPL and/or MPL" each, and the report never hyphenates, so none is ever opened.

    python3 scripts/strip_bundle_env.py <env>           remove, then verify
    python3 scripts/strip_bundle_env.py --check <env>   verify only (a built .app's env)

`stage_bundle_env.sh` runs the first on every staging; `check_bundle.sh` runs the second
on the built .app. Exit 0: nothing left. Exit 1: something is, and it is named. Exit 2:
not given an environment.

Standard library only: it runs on the interpreter of the environment it is cleaning.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import json
import mmap
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: The platforms an app is built for. The licence test reads `pixi.lock` for these.
#: linux-64 is a development platform: its solve also holds binutils' linker
#: (`ld_impl_linux-64`, GPL-3.0-only), to be decided if a Linux bundle is ever made.
SHIPS_ON = ("osx-arm64",)


@dataclass(frozen=True)
class LeftOut:
    """One package the shipped environment does not carry.

    Attributes:
        licence: Its licence as `pixi.lock` tags it; a test holds the two together.
        why: Why nothing the app does needs it.
        libraries: Its shared libraries, each by the name before the first dot.
        bound: Globs, relative to the environment, of files OUTSIDE its own record that
            exist only to load it.
        modules: Python modules that must no longer import afterwards.
    """

    licence: str
    why: str
    libraries: tuple[str, ...]
    bound: tuple[str, ...]
    modules: tuple[str, ...]


LEFT_OUT: dict[str, LeftOut] = {
    "readline": LeftOut(
        licence="GPL-3.0-only",
        why="only Python's interactive prompt uses it, and the app never opens one",
        libraries=("libreadline", "libhistory"),
        bound=("lib/python*/lib-dynload/readline.*",),
        modules=("readline",),
    ),
}

@dataclass(frozen=True)
class PartLeftOut:
    """Files of a package that STAYS, which the app never opens.

    Attributes:
        licence: What the files are under, as their package states it.
        why: Why nothing the app does opens them.
        files: Globs, relative to the environment. Their folders are kept.
        still_imports: Python modules that must import afterwards, as before.
    """

    licence: str
    why: str
    files: tuple[str, ...]
    still_imports: tuple[str, ...]


PARTS_LEFT_OUT: dict[str, PartLeftOut] = {
    "pyphen's hyphenation dictionaries": PartLeftOut(
        licence="GPL, LGPL and/or MPL, each its own",
        why="the report sets no `hyphens`, so WeasyPrint never opens one",
        # The notes beside them describe the dictionaries and go with them. The folder
        # stays: pyphen lists it when it is imported.
        files=("lib/python*/site-packages/pyphen/dictionaries/hyph_*.dic",
               "lib/python*/site-packages/pyphen/dictionaries/README_*.txt"),
        still_imports=("pyphen",),
    ),
}

#: Packages the lock tags so that every part reads as GPL, where the source offers a
#: choice. Each was read at its source; the notices carry the licence taken.
CHOSEN: dict[str, str] = {
    "pyphen": (
        "its own LICENSE: 'released under the GPL 2.0+/LGPL 2.1+/MPL 1.1 tri-license', "
        "any one of the three, and conda-forge writes that with AND. MPL-1.1 is taken. "
        "Its hyphenation dictionaries, each under its own licence, are left out "
        "(PARTS_LEFT_OUT)."
    ),
}

#: What a Mach-O (thin, either width, or fat) and an ELF file begin with.
_BINARY_MAGIC = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe",
                 b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf", b"\x7fELF")


class OutsideEnvironment(ValueError):
    """A record lists a path that does not stay inside the environment."""


def gpl_only(licence: str | None) -> bool:
    """True when every way of taking ``licence`` is the GPL itself.

    Read as the lock writes it: ``OR`` offers a choice, ``AND`` binds every part, and
    ``WITH`` names an exception that lifts the GPL's reach (the GCC runtime's). The LGPL
    is not the GPL here; the AGPL is.
    """
    if not licence:
        return False

    def binds(choice: str) -> bool:
        return any(re.search(r"(?<!L)GPL", part) and not re.search(r"\bwith\b", part, re.I)
                   for part in re.split(r"\s+and\s+", choice, flags=re.I))

    return all(binds(choice) for choice in re.split(r"\s+or\s+", licence, flags=re.I))


def _records(env: Path, name: str) -> list[tuple[Path, dict]]:
    """The conda records in ``env`` of the package named exactly ``name``.

    The file name alone does not say: `readline-sync-1.0-0.json` begins the same way.
    """
    found = []
    for path in sorted((env / "conda-meta").glob(f"{name}-*.json")):
        data = json.loads(path.read_text())
        if data.get("name") == name:
            found.append((path, data))
    return found


def _inside(env: Path, rel: str) -> Path:
    """``env / rel``, refused unless it stays inside ``env``.

    This script deletes what a JSON file lists, so each entry is held to the environment
    first: no absolute path, no ``..`` that climbs out, no linked folder leading away.
    """
    root = env.resolve()
    target = env / rel
    parent = target.parent.resolve()
    if os.path.isabs(rel) or target.name in ("", ".", "..") \
            or (parent != root and root not in parent.parents):
        raise OutsideEnvironment(rel)
    return target


def _prune(env: Path, folder: Path) -> None:
    """Remove ``folder`` and its parents while they are empty, stopping at ``env``."""
    root = env.resolve()
    folder = folder.resolve()
    while folder != root and root in folder.parents and folder.is_dir() \
            and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def strip(env: Path) -> list[tuple[str, str, list[str]]]:
    """Remove every left-out package, and every left-out part, from ``env``.

    Everything is read and checked before anything is deleted, and a package's record
    goes last, so a run that stops halfway is finished by the next one.

    Args:
        env: The staged environment (`dist/bundle-env`).

    Returns:
        ``(what, its licence, [relative paths removed])`` for each thing found there.

    Raises:
        OutsideEnvironment: A record lists a path outside ``env``; nothing was removed.
    """
    plans = []
    for name, policy in LEFT_OUT.items():
        for record, data in _records(env, name):
            targets = [_inside(env, rel) for rel in data.get("files", [])]
            for pattern in policy.bound:
                targets += sorted(env.glob(pattern))
            plans.append((f"{name} {data.get('version', '?')}", policy.licence,
                          record, targets))

    removed = []
    for label, licence, record, targets in plans:
        gone = []
        for target in targets:
            if os.path.lexists(target):  # lexists: a link whose target just went counts
                target.unlink()
                gone.append(str(target.relative_to(env)))
        for folder in sorted({t.parent for t in targets}, key=lambda p: -len(p.parts)):
            _prune(env, folder)
        record.unlink()
        removed.append((label, licence, gone))

    for label, part in PARTS_LEFT_OUT.items():
        gone = []
        for pattern in part.files:
            for target in sorted(env.glob(pattern)):
                target.unlink()
                gone.append(str(target.relative_to(env)))
        if gone:
            removed.append((label, part.licence, gone))
    return removed


def linkers(env: Path) -> list[str]:
    """Binaries under ``env`` that name a left-out library to load.

    A Mach-O load command and an ELF NEEDED entry both hold the library's file name as
    plain text, so each binary is searched for it. Only binaries are opened in full: a
    Makefile that says ``-lreadline`` records a build, it loads nothing.

    Returns:
        Relative paths, sorted. A left-out library that is still present names itself.
    """
    names = [re.escape(lib.encode()) for policy in LEFT_OUT.values()
             for lib in policy.libraries]
    needle = re.compile(b"(?:" + b"|".join(names) + rb")\.(?:\d+\.)*(?:dylib|so)")
    found = []
    for folder, _, files in os.walk(env):
        for name in files:
            path = os.path.join(folder, name)
            if os.path.islink(path) or os.path.getsize(path) < 4:
                continue
            with open(path, "rb") as handle:
                if handle.read(4) not in _BINARY_MAGIC:
                    continue
                with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
                    if needle.search(data):
                        found.append(os.path.relpath(path, env))
    return sorted(found)


def problems(env: Path, *, scan: bool) -> list[str]:
    """What of the left-out packages is still in ``env``, one line each.

    Args:
        env: The environment to look at.
        scan: Also search every binary for a link to a left-out library (seconds on a
            full environment, so staging asks only after it removed something).
    """
    found = []
    seen = set()

    def report(path: Path | str, what: str) -> None:
        rel = str(Path(path).relative_to(env)) if isinstance(path, Path) else path
        if rel not in seen:
            seen.add(rel)
            found.append(f"{rel}: {what}")

    ours = Path(sys.prefix).resolve() == env.resolve()
    for name, policy in LEFT_OUT.items():
        for record, _ in _records(env, name):
            report(record, f"{name} ({policy.licence}) is still recorded as installed")
        for library in policy.libraries:
            for path in sorted((env / "lib").glob(f"{library}.*")):
                report(path, f"{name}'s library is still here")
        for pattern in policy.bound:
            for path in sorted(env.glob(pattern)):
                report(path, f"loads {name}, and is still here")
        if ours:
            importlib.invalidate_caches()
            for module in policy.modules:
                # PathFinder, not find_spec: a module already imported would answer for
                # itself from sys.modules.
                if importlib.machinery.PathFinder.find_spec(module) is not None:
                    found.append(f"`import {module}` still resolves in this interpreter")
    for label, part in PARTS_LEFT_OUT.items():
        left = [p for pattern in part.files for p in sorted(env.glob(pattern))]
        for path in left[:3]:
            report(path, f"one of {label}, still here")
        if len(left) > 3:
            found.append(f"... and {len(left) - 3} more of {label}")
        if ours:
            for module in part.still_imports:
                try:
                    importlib.import_module(module)
                except Exception as exc:  # whatever it raises is the finding
                    found.append(f"`import {module}` fails without {label}: {exc!r}")
    if scan:
        for rel in linkers(env):
            report(rel, "links a library that is left out")
    return found


def main(argv: list[str]) -> int:
    """Run from the command line; see the module docstring."""
    args = [a for a in argv if a != "--check"]
    check = len(args) != len(argv)
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: strip_bundle_env.py [--check] <env>", file=sys.stderr)
        return 2
    env = Path(args[0])
    if not (env / "conda-meta").is_dir():
        print(f"not a conda environment (no conda-meta/ in it): {env}", file=sys.stderr)
        return 2

    removed: list[tuple[str, str, list[str]]] = []
    if not check:
        try:
            removed = strip(env)
        except OutsideEnvironment as exc:
            print(f"  FAIL  a conda record lists a path outside the environment: {exc}; "
                  "nothing was removed", file=sys.stderr)
            return 1

    found = problems(env, scan=check or bool(removed))
    for line in found:
        print(f"  FAIL  {line}", file=sys.stderr)
    if found:
        return 1

    for label, licence, gone in removed:
        print(f"  ok    left out {label} ({licence}): {len(gone)} files")
    taken = " ".join(label for label, _, _ in removed)
    for name, policy in LEFT_OUT.items():
        if name not in taken.split():
            print(f"  ok    {name} ({policy.licence}) is not in this environment")
    for label in PARTS_LEFT_OUT:
        if label not in taken:
            print(f"  ok    {label} are not in this environment")
    if check or removed:
        print("  ok    no binary links a library that is left out")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
