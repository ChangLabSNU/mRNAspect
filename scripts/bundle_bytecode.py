#!/usr/bin/env python3
"""Compile the environment's Python once, in a form that a copy cannot make stale.

WHY THIS EXISTS. Python keeps each module's compiled form in `__pycache__/*.pyc`, and by
default a .pyc records the TIME its source was last changed. When the times differ Python
compiles the module again and writes a new .pyc. Copying an environment does not always
keep file times (a bundler, a zip, a download), so inside the app every such .pyc can
become stale at once. Two things follow, and both were seen on the first sealed build
(2026-11-18):

* Python rewrote .pyc files INSIDE the app the first time it ran, and the app's signature
  covers every file in it: `codesign` then reported "file modified" and no longer accepted
  the bundle.
* where the app cannot be written to, the same modules are compiled again at every start.

A module with no .pyc at all does the same: its .pyc is ADDED to the sealed app.

So the environment that ships is compiled here, whole, as "unchecked hash-based" bytecode
(PEP 552): the .pyc holds a hash of its source instead of a time, and Python loads it
without looking at the source file's time. Nothing is recompiled after a copy and nothing
is written. The app's shell also starts Python with `-B` (never write bytecode), so a
module this could not compile still leaves the app as it was.

    python3 scripts/bundle_bytecode.py <env>           compile what is not current, verify
    python3 scripts/bundle_bytecode.py --check <env>   verify only; writes nothing

"Current" means: a .pyc for this interpreter, unchecked hash-based, whose hash IS the hash
of the source beside it. So a second run compiles only what changed (Core, which is
installed again at every staging), and a .pyc left over from an older source is caught.

`stage_bundle_env.sh` runs the first as its last step; `check_bundle.sh` runs the second
on the built .app. Exit 0: every source that can be compiled has current bytecode. Exit 1:
one does not, and it is named. Exit 2: not given an environment.

A source that does not compile (a few libraries ship files that are data for their own
tests, or templates) cannot be imported either; it is counted and named, not hidden.

Standard library only: it runs on the interpreter of the environment it compiles.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import py_compile
import sys
from pathlib import Path

#: Bits of a .pyc header's flags (PEP 552): the header holds a hash of the source rather
#: than its time; and, if so, whether Python compares it with the source at import.
_HASH_BASED, _CHECK_SOURCE = 0b01, 0b10
CURRENT = "current"


def library_folders(env: Path) -> list[Path]:
    """The folders Python imports from: `lib/python3.x`, with `site-packages` inside."""
    return sorted(p for p in (env / "lib").glob("python3*") if (p / "os.py").is_file())


def sources(env: Path) -> list[Path]:
    """Every Python source the environment's interpreter could import."""
    found: list[Path] = []
    for folder in library_folders(env):
        for where, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            found.extend(Path(where) / name for name in names if name.endswith(".py"))
    return sorted(found)


def cache_of(source: Path) -> Path:
    """Where this interpreter keeps the compiled form of `source`."""
    return Path(importlib.util.cache_from_source(str(source), optimization=""))


def state(source: Path) -> str:
    """`CURRENT`, or what is wrong with the .pyc of `source`, in words."""
    try:
        with open(cache_of(source), "rb") as fh:
            head = fh.read(16)
    except FileNotFoundError:
        return "no compiled file"
    if len(head) < 16 or head[:4] != importlib.util.MAGIC_NUMBER:
        return "compiled by another Python"
    flags = int.from_bytes(head[4:8], "little")
    if not flags & _HASH_BASED:
        return "compiled against the file's time"
    if flags & _CHECK_SOURCE:
        return "compared with its source at every import"
    if head[8:16] != importlib.util.source_hash(source.read_bytes()):
        return "compiled from another version of the source"
    return CURRENT


def cannot_compile(source: Path) -> str | None:
    """Why `source` is not Python this interpreter can compile, or `None` if it is.

    Nothing is written. Such a file cannot be imported, so it needs no bytecode.
    """
    try:
        compile(source.read_bytes(), str(source), "exec", dont_inherit=True)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def compile_one(source: Path) -> str | None:
    """Write the unchecked hash-based .pyc of `source`.

    Returns:
        `None` when written, or why the source does not compile.

    Raises:
        OSError: When the file cannot be written. That is a failure of the staging, never
            a source to pass over.
    """
    try:
        py_compile.compile(
            str(source), cfile=str(cache_of(source)), doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    except py_compile.PyCompileError as exc:
        return exc.msg.strip().splitlines()[-1]
    except (ValueError, RecursionError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _relative(path: Path, env: Path) -> str:
    try:
        return str(path.relative_to(env))
    except ValueError:
        return str(path)


def run(env: Path, write: bool) -> int:
    """Compile (when `write`) and verify. Returns the exit code."""
    files = sources(env)
    if not files:
        print(f"no Python source under {env}/lib/python3*: this is not an environment",
              file=sys.stderr)
        return 2
    written = 0
    not_python: list[tuple[Path, str]] = []
    wrong: list[tuple[Path, str]] = []
    for source in files:
        found = state(source)
        if found == CURRENT:
            continue
        if write:
            why = compile_one(source)
            if why is None:
                written += 1
                found = state(source)
                if found != CURRENT:    # read back: what is verified is the file
                    wrong.append((source, found))
            else:
                not_python.append((source, why))
        else:
            why = cannot_compile(source)
            if why is None:
                wrong.append((source, found))
            else:
                not_python.append((source, why))
    for source, why in not_python[:5]:
        print(f"  note  not compiled, {_relative(source, env)}: {why}"[:200])
    if len(not_python) > 5:
        print(f"  note  and {len(not_python) - 5} more that do not compile")
    if wrong:
        for source, found in wrong[:20]:
            print(f"  {_relative(source, env)}: {found}", file=sys.stderr)
        if len(wrong) > 20:
            print(f"  and {len(wrong) - 20} more", file=sys.stderr)
        print(f"{len(wrong)} of {len(files)} sources would be compiled again when the "
              f"app runs", file=sys.stderr)
        return 1
    done = len(files) - len(not_python)
    print(f"  ok    {done} sources have bytecode that does not depend on file times"
          + (f" ({written} compiled now)" if write else "")
          + (f"; {len(not_python)} do not compile and cannot be imported"
             if not_python else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile an environment's Python so that a copy needs no recompiling.")
    parser.add_argument("--check", action="store_true", help="verify only; write nothing")
    parser.add_argument("env", type=Path, help="a staged environment, or the one in an app")
    args = parser.parse_args(argv)
    if not (args.env / "lib").is_dir():
        print(f"not an environment (no lib/): {args.env}", file=sys.stderr)
        return 2
    try:
        return run(args.env.resolve(), write=not args.check)
    except OSError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
