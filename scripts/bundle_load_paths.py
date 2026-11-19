#!/usr/bin/env python3
"""Make every library the app loads come from inside the app, and show that it does.

WHY THIS EXISTS. conda writes the folder an environment is installed in into some of its
files. Most of that is harmless (a pkg-config file, a compiled-in default the app never
reads), but a few libraries name ANOTHER LIBRARY that way: on osx-arm64, ncurses'
`libncursesw.6.dylib` re-exports `<install folder>/lib/libtinfow.6.dylib` by its full
path. `samtools` loads that library, so a copy of the environment keeps asking for a file
in the folder it was copied FROM.

On the computer that built the app that folder still exists, so everything runs, and
every check made there passes (2026-11-18: the built app's samtools loaded
`libtinfow.6.dylib` from the development environment, beside the copy in the app). On any
other Mac the folder is not there and macOS refuses to start samtools at all.

So this script reads the load commands of every Mach-O file under a folder and treats a
library named by a path that is neither relative to the file (`@rpath/`, `@loader_path/`,
`@executable_path/`) nor the system's (`/usr/lib/`, `/System/`) as a defect.

    python3 scripts/bundle_load_paths.py <env>                 rewrite, re-sign, verify
    python3 scripts/bundle_load_paths.py --dry-run <env>       say what would be rewritten
    python3 scripts/bundle_load_paths.py --check <env or .app> verify only
    python3 scripts/bundle_load_paths.py --loaded <.app> -- <command ...>
                                                               run it; list what macOS
                                                               loaded from outside

`stage_bundle_env.sh` runs the first on every staging; `check_bundle.sh` runs `--check`
and `--loaded` on the built .app. Exit 0: nothing is loaded from outside. Exit 1:
something is, and it is named. Exit 2: wrong usage, or a mode this platform cannot run.

Rewriting uses Apple's own tools (`install_name_tool`, then `codesign`: a changed file's
signature no longer matches, and Apple Silicon will not run it until it is signed again),
so it runs on macOS only. Reading does not: `--check` works on any platform, which is how
a built .app can be examined on Linux.

Standard library only: it runs on the interpreter of the environment it is examining.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: A path that begins with one of these is resolved from where the loading file is.
ANCHORED = ("@rpath/", "@loader_path/", "@executable_path/")
#: Libraries every Mac has. dyld serves them from its shared cache.
SYSTEM = ("/usr/lib/", "/System/")

_MH_MAGIC_64 = 0xFEEDFACF            # little-endian 64-bit: every arm64 and x86_64 file
_FAT_MAGIC = b"\xca\xfe\xba\xbe"     # several architectures in one file (also: Java)
_UNREAD = (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",   # 32-bit, either byte order
           b"\xfe\xed\xfa\xcf",                        # 64-bit big-endian
           b"\xca\xfe\xba\xbf")                        # fat with 64-bit offsets
_LC_REQ_DYLD = 0x80000000
#: The load commands that name another library, by what dyld does with it.
_DYLIB_COMMANDS = {
    0x0C: "load",
    0x18 | _LC_REQ_DYLD: "weak",
    0x1F | _LC_REQ_DYLD: "re-export",
    0x20: "lazy",
    0x23 | _LC_REQ_DYLD: "upward",
}
_LC_RPATH = 0x1C | _LC_REQ_DYLD
_ARCH = {0x0100000C: "arm64", 0x01000007: "x86_64"}


@dataclass(frozen=True)
class Slice:
    """One architecture of a Mach-O file.

    Attributes:
        arch: `arm64`, `x86_64`, or the cpu type in hex.
        loads: (kind, path) of every library it names, in file order.
        rpaths: The run-path list `@rpath/` is searched in.
    """

    arch: str
    loads: tuple[tuple[str, str], ...]
    rpaths: tuple[str, ...]


@dataclass(frozen=True)
class Outside:
    """A reference that leaves the folder. `kind` is a dylib kind or `run path`."""

    file: Path
    kind: str
    path: str


@dataclass(frozen=True)
class Change:
    """One reference to rewrite: in `file`, the library named `old` becomes `new`."""

    file: Path
    old: str
    new: str


def _on_macos() -> bool:
    """Whether this is macOS: the only place the files can be rewritten or run."""
    return sys.platform == "darwin"


def _c_string(buf: bytes, start: int) -> str:
    end = buf.find(b"\0", start)
    return buf[start:len(buf) if end == -1 else end].decode("utf-8", "replace")


def _read_slice(fh, base: int) -> Slice:
    fh.seek(base)
    header = fh.read(32)
    if len(header) < 32 or struct.unpack("<I", header[:4])[0] != _MH_MAGIC_64:
        raise ValueError("not a 64-bit little-endian Mach-O where one was announced")
    cputype, _sub, _filetype, ncmds, sizeofcmds = struct.unpack("<IIIII", header[4:24])
    commands = fh.read(sizeofcmds)
    if len(commands) < sizeofcmds:
        raise ValueError("the file ends inside its load commands")
    loads: list[tuple[str, str]] = []
    rpaths: list[str] = []
    at = 0
    for _ in range(ncmds):
        if at + 8 > len(commands):
            raise ValueError("a load command starts past the end of the commands")
        cmd, cmdsize = struct.unpack("<II", commands[at:at + 8])
        if cmdsize < 8 or at + cmdsize > len(commands):
            raise ValueError(f"a load command of impossible size ({cmdsize})")
        body = commands[at:at + cmdsize]
        if cmd in _DYLIB_COMMANDS or cmd == _LC_RPATH:
            # Both begin, after cmd and cmdsize, with the offset of a C string.
            text = _c_string(body, struct.unpack("<I", body[8:12])[0])
            if cmd == _LC_RPATH:
                rpaths.append(text)
            else:
                loads.append((_DYLIB_COMMANDS[cmd], text))
        at += cmdsize
    return Slice(_ARCH.get(cputype, hex(cputype)), tuple(loads), tuple(rpaths))


def read_macho(path: Path) -> list[Slice]:
    """Every architecture slice of a Mach-O file; `[]` for any other kind of file.

    Raises:
        ValueError: For a Mach-O this reader cannot read (32-bit, big-endian, or cut
            short). Such a file is reported, never passed over: a file that was not read
            is a file nothing is known about.
    """
    with open(path, "rb") as fh:
        magic = fh.read(4)
        if magic == struct.pack("<I", _MH_MAGIC_64):
            return [_read_slice(fh, 0)]
        if magic in _UNREAD:
            raise ValueError("a Mach-O of a kind this reader does not read")
        if magic != _FAT_MAGIC:
            return []
        count = struct.unpack(">I", fh.read(4).ljust(4, b"\0"))[0]
        if count == 0 or count > 16:     # a Java class file: same magic, then a version
            return []
        table = fh.read(20 * count)
        if len(table) < 20 * count:
            raise ValueError("the file ends inside its architecture table")
        offsets = [struct.unpack(">I", table[20 * i + 8:20 * i + 12])[0]
                   for i in range(count)]
        return [_read_slice(fh, offset) for offset in offsets]


def machos(root: Path) -> tuple[list[tuple[Path, list[Slice]]], list[tuple[Path, str]]]:
    """Every Mach-O file under `root` with its slices, and the files that could not be read.

    Symbolic links are not followed: a link's target is either in the tree, and read
    there, or outside it, and then it is not this tree's file.
    """
    found: list[tuple[Path, list[Slice]]] = []
    unread: list[tuple[Path, str]] = []
    for folder, _dirs, names in os.walk(root):
        for name in sorted(names):
            path = Path(folder) / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                slices = read_macho(path)
            except (OSError, ValueError, struct.error) as exc:
                unread.append((path, f"{type(exc).__name__}: {exc}"))
                continue
            if slices:
                found.append((path, slices))
    found.sort()
    return found, unread


def leaves(path: str) -> bool:
    """Whether a library named by `path` is looked for outside the app and the system."""
    return not path.startswith(ANCHORED) and not path.startswith(SYSTEM)


def outside(files: list[tuple[Path, list[Slice]]]) -> list[Outside]:
    """Every reference, in the files given, that leaves the folder.

    A run path counts when it is a full path outside the system: `@rpath/x` would then be
    searched for in a folder of the build machine. A relative one (`lib`) is left alone;
    it is resolved from the working directory and names no machine.
    """
    out: list[Outside] = []
    for path, slices in files:
        seen: set[tuple[str, str]] = set()
        for one in slices:
            for kind, name in one.loads:
                if leaves(name) and (kind, name) not in seen:
                    seen.add((kind, name))
                    out.append(Outside(path, kind, name))
            for rpath in one.rpaths:
                key = ("run path", rpath)
                if rpath.startswith("/") and not rpath.startswith(SYSTEM) \
                        and key not in seen:
                    seen.add(key)
                    out.append(Outside(path, "run path", rpath))
    return out


def _environment_of(path: Path, root: Path) -> Path:
    """The conda environment `path` is in (the folder holding `conda-meta`), else `root`."""
    for parent in path.parents:
        if (parent / "conda-meta").is_dir():
            return parent
        if parent == root:
            break
    return root


def _inside(old: str, environment: Path) -> Path | None:
    """The file in `environment` that the full path `old` names in the folder it was
    installed in: the longest tail of `old` that exists here. `<anywhere>/lib/libz.dylib`
    is `<environment>/lib/libz.dylib`. A bare file name is never enough.
    """
    parts = PurePosixPath(old).parts[1:]
    for start in range(len(parts) - 1):
        candidate = environment.joinpath(*parts[start:])
        if candidate.is_file():
            return candidate
    return None


def plan(root: Path, found: list[Outside]) -> tuple[list[Change], list[Outside]]:
    """Which references can be rewritten, and which cannot.

    A library reference is rewritten to `@loader_path/<the way from the file to the copy
    of that library in its own environment>`, which needs no run path to be right. One
    whose library is not in the environment, and every run path, is returned as it is:
    nothing here can say what it should become.
    """
    changes: list[Change] = []
    orphans: list[Outside] = []
    for one in found:
        target = None if one.kind == "run path" else \
            _inside(one.path, _environment_of(one.file, root))
        if target is None:
            orphans.append(one)
            continue
        way = os.path.relpath(target, one.file.parent)
        changes.append(Change(one.file, one.path, f"@loader_path/{way}"))
    return changes, orphans


def apply(changes: list[Change]) -> None:
    """Rewrite the references and sign each changed file again (macOS).

    Raises:
        RuntimeError: When a tool is missing or refuses; the message names the file.
    """
    tools = {name: shutil.which(name) for name in ("install_name_tool", "codesign")}
    missing = [name for name, where in tools.items() if where is None]
    if missing:
        raise RuntimeError(
            f"{' and '.join(missing)} not found. They come with macOS and the Xcode "
            f"command line tools (xcode-select --install).")
    by_file: dict[Path, list[Change]] = {}
    for change in changes:
        by_file.setdefault(change.file, []).append(change)
    for path, mine in sorted(by_file.items()):
        mode = path.stat().st_mode
        if not mode & stat.S_IWUSR:
            path.chmod(mode | stat.S_IWUSR)
        rewrite = [tools["install_name_tool"]]
        for change in mine:
            rewrite += ["-change", change.old, change.new]
        # --force replaces the signature the rewrite has just made stale; `-` is the
        # ad-hoc identity, which is what every file of the environment carries already.
        for command in (rewrite + [str(path)],
                        [tools["codesign"], "--force", "--sign", "-", str(path)]):
            done = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if done.returncode != 0:
                raise RuntimeError(
                    f"{Path(command[0]).name} failed on {path}: "
                    f"{(done.stderr or done.stdout).strip()}")
        if not mode & stat.S_IWUSR:
            path.chmod(mode)


_DYLD_LINE = re.compile(r"^dyld\[\d+\]: <[0-9A-Fa-f-]+> (/.+)$")


def loaded_outside(log: str, root: Path) -> tuple[int, list[str]]:
    """Read what `DYLD_PRINT_LIBRARIES=1` printed.

    Returns:
        How many libraries dyld reported, and the ones that are neither under `root` nor
        the system's, each once, in the order they were loaded.
    """
    home = os.path.realpath(root) + os.sep
    count = 0
    out: list[str] = []
    for line in log.splitlines():
        match = _DYLD_LINE.match(line.strip())
        if match is None:
            continue
        count += 1
        named = match.group(1)
        if named.startswith(SYSTEM) or os.path.realpath(named).startswith(home):
            continue
        if named not in out:
            out.append(named)
    return count, out


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _report(found: list[Outside], unread: list[tuple[Path, str]], root: Path) -> None:
    for path, why in unread:
        print(f"  could not read {_relative(path, root)}: {why}", file=sys.stderr)
    for one in found:
        print(f"  {_relative(one.file, root)}: {one.kind} {one.path}", file=sys.stderr)


def _check(root: Path) -> int:
    files, unread = machos(root)
    found = outside(files)
    if not files and not unread:
        if _on_macos():
            print(f"no Mach-O file under {root}: this is not an environment or an app",
                  file=sys.stderr)
            return 1
        print(f"  ok    no Mach-O file under {root} (not a macOS environment): "
              f"nothing to check")
        return 0
    if found or unread:
        _report(found, unread, root)
        print(f"{len(found)} reference(s) leave {root}"
              + (f"; {len(unread)} file(s) could not be read" if unread else ""),
              file=sys.stderr)
        return 1
    print(f"  ok    {len(files)} Mach-O files: every library is named relative to the "
          f"file that loads it, or is the system's")
    return 0


def _fix(root: Path, dry_run: bool) -> int:
    files, unread = machos(root)
    if unread:
        _report([], unread, root)
        return 1
    if not files:
        return _check(root)
    changes, orphans = plan(root, outside(files))
    for change in changes:
        print(f"  {'would rewrite' if dry_run else 'rewrite'}  "
              f"{_relative(change.file, root)}: {change.old} -> {change.new}")
    if orphans:
        _report(orphans, [], root)
        print(f"{len(orphans)} reference(s) leave {root} and nothing in it can take "
              f"their place", file=sys.stderr)
        return 1
    if dry_run:
        print(f"  {len(changes)} reference(s) in "
              f"{len({change.file for change in changes})} file(s) would be rewritten")
        return 0
    if changes:
        if not _on_macos():
            print("rewriting needs install_name_tool and codesign: run this on macOS",
                  file=sys.stderr)
            return 2
        try:
            apply(changes)
        except RuntimeError as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1
    # Read again from disk: what is verified is the files, not the plan.
    return _check(root)


def _loaded(root: Path, command: list[str]) -> int:
    if not _on_macos():
        print("--loaded reads what macOS's dyld prints: run this on macOS",
              file=sys.stderr)
        return 2
    if not command:
        print("--loaded needs a command after --", file=sys.stderr)
        return 2
    # Set here, on the command itself. Through /usr/bin/env or a shell it would be lost:
    # macOS removes DYLD_* variables when a protected system binary starts.
    done = subprocess.run(command, capture_output=True, text=True, timeout=600,
                          env={**os.environ, "DYLD_PRINT_LIBRARIES": "1"})
    count, out = loaded_outside(done.stderr, root)
    name = Path(command[0]).name
    if len(command) > 1 and command[1].isalpha():       # `samtools sort`, not `python3 -I`
        name = f"{name} {command[1]}"
    if done.returncode != 0:
        rest = [line for line in done.stderr.splitlines()
                if not _DYLD_LINE.match(line.strip())]
        print(f"{name} exited {done.returncode}:", file=sys.stderr)
        print("\n".join(f"  {line}" for line in rest[-15:]), file=sys.stderr)
        return 1
    if count == 0:
        print(f"{name}: dyld reported no library, so this run shows nothing",
              file=sys.stderr)
        return 1
    if out:
        for path in out:
            print(f"  {name} loaded {path}", file=sys.stderr)
        print(f"{name}: {len(out)} of {count} libraries came from outside {root}",
              file=sys.stderr)
        return 1
    print(f"  ok    {name}: {count} libraries, all from the app or the system")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Make every library the app loads come from inside the app.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify only")
    mode.add_argument("--dry-run", action="store_true", help="say what would be rewritten")
    mode.add_argument("--loaded", action="store_true",
                      help="run the command after -- and list what dyld loaded from outside")
    parser.add_argument("root", type=Path, help="a staged environment or a built .app")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="with --loaded: -- <command> [arguments]")
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print(f"not a folder: {args.root}", file=sys.stderr)
        return 2
    root = args.root.resolve()
    if args.loaded:
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        return _loaded(root, command)
    if args.command:
        print(f"unexpected arguments: {' '.join(args.command)}", file=sys.stderr)
        return 2
    if args.check:
        return _check(root)
    return _fix(root, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
