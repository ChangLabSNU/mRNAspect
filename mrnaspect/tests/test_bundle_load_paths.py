"""No library in the app may be named by a path on the computer that built it.

Found on 2026-11-18, on the built app. conda writes the folder an environment is installed
in into some of its files, and on osx-arm64 ncurses' `libncursesw.6.dylib` re-exports
`<that folder>/lib/libtinfow.6.dylib` by its full path. `samtools` loads that library. The
app carries a COPY of the environment, so its samtools asked for a file in the development
environment: there on the build machine, where every check was made and passed, and on no
other Mac, where macOS would refuse to start samtools and every run would fail at
alignment.

`scripts/bundle_load_paths.py` reads the load commands of every Mach-O file, rewrites such
a reference to the copy inside the environment (macOS's `install_name_tool`, then
`codesign`), and can ask macOS what a run really loaded. Held here:

1. the reader, on Mach-O files written by this test (no Mac is needed to read one);
2. what counts as leaving the app, and what a reference is rewritten to;
3. that rewriting is verified from the files afterwards, not from the plan;
4. the reading of what macOS's dyld prints, on the lines the build machine printed;
5. the two shell scripts call it, and `tauri.conf.json` asks for a sealed bundle.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "bundle_load_paths.py"
_STAGE = _ROOT / "scripts" / "stage_bundle_env.sh"
_CHECK = _ROOT / "scripts" / "check_bundle.sh"
_CONF = _ROOT / "gui" / "src-tauri" / "tauri.conf.json"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(), reason="scripts/ is not in this checkout")

#: Where a build machine installed the environment the app's copy was made from.
_BUILT_IN = "/Users/builder/work/.pixi/envs/default"

_KIND = {"load": 0x0C, "weak": 0x80000018, "re-export": 0x8000001F}
_ARM64 = 0x0100000C


@functools.lru_cache(maxsize=1)
def _tool():
    """The script as a module: `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("bundle_load_paths", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses looks its module up by name
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Mach-O files, written here
# ---------------------------------------------------------------------------

def _padded(text: str) -> bytes:
    raw = text.encode() + b"\0"
    return raw + b"\0" * (-len(raw) % 8)


def _macho(loads=(), rpaths=(), cputype: int = _ARM64) -> bytes:
    """A 64-bit Mach-O holding only the load commands this is about."""
    commands = []
    for kind, name in loads:
        text = _padded(name)
        # cmd, cmdsize, offset of the name, timestamp, current and compatibility version
        commands.append(struct.pack("<IIIIII", _KIND[kind], 24 + len(text), 24, 2,
                                    0x10000, 0x10000) + text)
    for path in rpaths:
        text = _padded(path)
        commands.append(struct.pack("<III", 0x8000001C, 12 + len(text), 12) + text)
    body = b"".join(commands)
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, cputype, 0, 6, len(commands),
                         len(body), 0, 0)
    return header + body + b"\0" * 32


def _fat(*slices: bytes) -> bytes:
    """Several architectures in one file, as a universal binary holds them."""
    at = 8 + 20 * len(slices)
    table, blob = b"", b""
    for one in slices:
        table += struct.pack(">IIIII", _ARM64, 0, at + len(blob), len(one), 0)
        blob += one
    return struct.pack(">II", 0xCAFEBABE, len(slices)) + table + blob


def _env(parent: Path) -> Path:
    """An environment with the defect: ncurses names libtinfo in the build folder."""
    env = parent / "env"
    for folder in ("conda-meta", "lib", "bin"):
        (env / folder).mkdir(parents=True)
    (env / "lib" / "libtinfow.6.dylib").write_bytes(_macho())
    (env / "lib" / "libncursesw.6.dylib").write_bytes(_macho(
        loads=[("re-export", f"{_BUILT_IN}/lib/libtinfow.6.dylib"),
               ("load", "/usr/lib/libSystem.B.dylib")],
        rpaths=["@loader_path/"]))
    (env / "bin" / "samtools").write_bytes(_macho(
        loads=[("load", "@rpath/libncursesw.6.dylib"),
               ("load", "@rpath/libtinfow.6.dylib"),
               ("load", "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")],
        rpaths=["@loader_path/../lib/"]))
    (env / "share").mkdir()
    (env / "share" / "notes.txt").write_text("not a Mach-O file\n")
    return env


def _leaving(tool, root: Path):
    files, unread = tool.machos(root)
    assert not unread, unread
    return tool.outside(files)


# ---------------------------------------------------------------------------
# 1. The reader
# ---------------------------------------------------------------------------

def test_the_reader_reads_what_a_file_names(tmp_path: Path) -> None:
    tool = _tool()
    path = tmp_path / "lib.dylib"
    path.write_bytes(_macho(loads=[("load", "@rpath/libz.1.dylib"),
                                   ("re-export", "/somewhere/libx.dylib")],
                            rpaths=["@loader_path/"]))
    (one,) = tool.read_macho(path)
    assert one.arch == "arm64"
    assert one.loads == (("load", "@rpath/libz.1.dylib"),
                         ("re-export", "/somewhere/libx.dylib"))
    assert one.rpaths == ("@loader_path/",)


def test_every_architecture_of_a_universal_file_is_read(tmp_path: Path) -> None:
    """A reference in the second slice is as real as one in the first."""
    path = tmp_path / "universal.dylib"
    path.write_bytes(_fat(_macho(loads=[("load", "@rpath/a.dylib")]),
                          _macho(loads=[("load", "/somewhere/b.dylib")])))
    slices = _tool().read_macho(path)
    assert [s.loads for s in slices] == [(("load", "@rpath/a.dylib"),),
                                         (("load", "/somewhere/b.dylib"),)]


@pytest.mark.parametrize("content", [
    b"#!/bin/sh\necho not a Mach-O\n",
    b"\x7fELF" + b"\0" * 60,
    b"",
    # A Java class file begins with the universal-binary magic, then its version.
    b"\xca\xfe\xba\xbe\x00\x00\x00\x41" + b"\0" * 40,
])
def test_a_file_that_is_not_mach_o_has_nothing_to_read(tmp_path: Path,
                                                       content: bytes) -> None:
    path = tmp_path / "other"
    path.write_bytes(content)
    assert _tool().read_macho(path) == []


def test_a_mach_o_that_cannot_be_read_is_reported_not_passed_over(tmp_path: Path) -> None:
    """A file that was not read is a file nothing is known about."""
    tool = _tool()
    env = _env(tmp_path)
    whole = _macho(loads=[("load", "@rpath/libz.1.dylib")])
    (env / "lib" / "cut_short.dylib").write_bytes(whole[:40])
    files, unread = tool.machos(env)
    assert [path.name for path, _why in unread] == ["cut_short.dylib"]
    assert "cut_short.dylib" not in [path.name for path, _slices in files]
    assert tool.main(["--check", str(env)]) == 1


# ---------------------------------------------------------------------------
# 2. What leaves the app, and what it is rewritten to
# ---------------------------------------------------------------------------

def test_only_the_reference_into_the_build_folder_leaves(tmp_path: Path) -> None:
    """Relative names and the system's libraries are what every Mac can resolve."""
    tool = _tool()
    env = _env(tmp_path)
    assert _leaving(tool, env) == [tool.Outside(
        env / "lib" / "libncursesw.6.dylib", "re-export",
        f"{_BUILT_IN}/lib/libtinfow.6.dylib")]


def test_a_full_run_path_leaves_and_a_relative_one_does_not(tmp_path: Path) -> None:
    tool = _tool()
    (tmp_path / "a.dylib").write_bytes(_macho(
        loads=[("load", "@rpath/b.dylib")],
        rpaths=[f"{_BUILT_IN}/lib", "lib", "@loader_path/", "/usr/lib/swift"]))
    assert _leaving(tool, tmp_path) == [
        tool.Outside(tmp_path / "a.dylib", "run path", f"{_BUILT_IN}/lib")]


def test_the_plan_points_at_the_copy_in_the_same_environment(tmp_path: Path) -> None:
    """`@loader_path/` is resolved from the file itself, so it needs no run path."""
    tool = _tool()
    env = _env(tmp_path)
    deep = env / "lib" / "python3.14" / "site-packages" / "pkg"
    deep.mkdir(parents=True)
    (env / "lib" / "libz.1.dylib").write_bytes(_macho())
    (deep / "_ext.so").write_bytes(_macho(loads=[("load", f"{_BUILT_IN}/lib/libz.1.dylib")]))
    changes, orphans = tool.plan(env, _leaving(tool, env))
    assert orphans == []
    assert changes == [
        tool.Change(env / "lib" / "libncursesw.6.dylib",
                    f"{_BUILT_IN}/lib/libtinfow.6.dylib",
                    "@loader_path/libtinfow.6.dylib"),
        tool.Change(deep / "_ext.so", f"{_BUILT_IN}/lib/libz.1.dylib",
                    "@loader_path/../../../libz.1.dylib"),
    ]


def test_inside_an_app_the_environment_is_found_from_the_file(tmp_path: Path) -> None:
    """`--dry-run` on a built .app plans the same rewrite as on the staged folder."""
    tool = _tool()
    resources = tmp_path / "mRNAspect.app" / "Contents" / "Resources"
    resources.mkdir(parents=True)
    env = _env(resources)
    changes, orphans = tool.plan(tmp_path / "mRNAspect.app",
                                 _leaving(tool, tmp_path / "mRNAspect.app"))
    assert orphans == []
    assert [(c.file, c.new) for c in changes] == [
        (env / "lib" / "libncursesw.6.dylib", "@loader_path/libtinfow.6.dylib")]


def test_a_library_the_environment_does_not_hold_is_not_guessed(tmp_path: Path) -> None:
    """A file of the same NAME elsewhere in the tree is not the library that was meant."""
    tool = _tool()
    env = _env(tmp_path)
    (env / "libmissing.dylib").write_bytes(_macho())
    (env / "lib" / "needs.dylib").write_bytes(
        _macho(loads=[("load", "/opt/elsewhere/lib/libmissing.dylib")]))
    changes, orphans = tool.plan(env, _leaving(tool, env))
    assert [o.path for o in orphans] == ["/opt/elsewhere/lib/libmissing.dylib"]
    assert [c.file.name for c in changes] == ["libncursesw.6.dylib"]
    assert tool.main(["--dry-run", str(env)]) == 1


# ---------------------------------------------------------------------------
# 3. Rewriting, with stand-ins for Apple's two tools
# ---------------------------------------------------------------------------

class _Tools:
    """Stands in for `subprocess.run`: records each command; `install_name_tool` rewrites
    the file as the real one would, unless told to leave it."""

    def __init__(self, rewrite: bool = True, codesign: int = 0) -> None:
        self.rewrite, self.codesign, self.calls = rewrite, codesign, []

    def __call__(self, command, **_kwargs):
        self.calls.append([Path(command[0]).name, *command[1:]])
        name = Path(command[0]).name
        if name == "install_name_tool" and self.rewrite:
            path = Path(command[-1])
            pairs = dict(zip(command[2:-1:3], command[3:-1:3]))
            (one,) = _tool().read_macho(path)
            path.write_bytes(_macho(
                loads=[(kind, pairs.get(lib, lib)) for kind, lib in one.loads],
                rpaths=one.rpaths))
        code = self.codesign if name == "codesign" else 0
        return subprocess.CompletedProcess(command, code, "", "refused" if code else "")


def _as_macos(monkeypatch: pytest.MonkeyPatch, tools: _Tools) -> None:
    tool = _tool()
    monkeypatch.setattr(tool, "_on_macos", lambda: True)
    monkeypatch.setattr(tool.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(tool.subprocess, "run", tools)


def test_a_rewrite_changes_the_reference_and_signs_the_file_again(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A changed Mach-O no longer matches its signature, and Apple Silicon will not run
    it: the signing is part of the rewrite, not a step after it."""
    tool = _tool()
    env = _env(tmp_path)
    target = str(env / "lib" / "libncursesw.6.dylib")
    tools = _Tools()
    _as_macos(monkeypatch, tools)
    assert tool.main([str(env)]) == 0
    assert tools.calls == [
        ["install_name_tool", "-change", f"{_BUILT_IN}/lib/libtinfow.6.dylib",
         "@loader_path/libtinfow.6.dylib", target],
        ["codesign", "--force", "--sign", "-", target],
    ]
    assert _leaving(tool, env) == []
    # And again, as on a staging that reuses the tree: nothing is left to rewrite.
    tools.calls.clear()
    assert tool.main([str(env)]) == 0
    assert tools.calls == []


def test_a_rewrite_that_changed_nothing_is_a_failure(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """What is verified is the files as they now are, not that the tools were called."""
    env = _env(tmp_path)
    _as_macos(monkeypatch, _Tools(rewrite=False))
    assert _tool().main([str(env)]) == 1
    assert f"{_BUILT_IN}/lib/libtinfow.6.dylib" in capsys.readouterr().err


def test_a_tool_that_refuses_stops_the_staging(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    env = _env(tmp_path)
    _as_macos(monkeypatch, _Tools(codesign=1))
    assert _tool().main([str(env)]) == 1
    err = capsys.readouterr().err
    assert "codesign failed on" in err and "libncursesw.6.dylib" in err


def test_away_from_macos_a_rewrite_is_refused_and_a_check_still_reads(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading needs no Mac, which is how a built .app is examined on Linux."""
    tool = _tool()
    env = _env(tmp_path)
    monkeypatch.setattr(tool, "_on_macos", lambda: False)
    assert tool.main([str(env)]) == 2
    assert tool.main(["--check", str(env)]) == 1
    assert tool.main(["--dry-run", str(env)]) == 0
    assert _leaving(tool, env) != [], "a dry run must not change a file"


def test_a_folder_without_mach_o_is_nothing_to_do_on_linux_and_an_error_on_macos(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Staging runs this on Linux too, where the environment holds ELF files. On macOS an
    environment without a single Mach-O file is not an environment."""
    tool = _tool()
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "libz.so.1").write_bytes(b"\x7fELF" + b"\0" * 60)
    monkeypatch.setattr(tool, "_on_macos", lambda: False)
    assert tool.main([str(tmp_path)]) == 0
    assert tool.main(["--check", str(tmp_path)]) == 0
    monkeypatch.setattr(tool, "_on_macos", lambda: True)
    assert tool.main(["--check", str(tmp_path)]) == 1


# ---------------------------------------------------------------------------
# 4. What macOS says it loaded
# ---------------------------------------------------------------------------

#: The build machine's own answer for the app's samtools (2026-11-18), with the folders
#: renamed: the third line is the library loaded from the development environment.
_DYLD = """\
dyld[37453]: <8477C47A-AC05-3CD5-8515-46C631F8CE90> {app}/Contents/Resources/env/lib/libtinfow.6.dylib
dyld[37453]: <62D19FBA-FCE5-37FE-B9F1-72CF8F434251> {app}/Contents/Resources/env/lib/libncursesw.6.dylib
dyld[37453]: <8477C47A-AC05-3CD5-8515-46C631F8CE90> {outside}
dyld[37453]: <ACEF34FB-D4A9-33A2-8552-049483DD1137> /usr/lib/libncurses.5.4.dylib
dyld[37453]: move loaded to delayed: libncurses.5.4.dylib
"""
_STRAY = f"{_BUILT_IN}/lib/libtinfow.6.dylib"


def test_a_library_loaded_from_the_build_folder_is_named(tmp_path: Path) -> None:
    app = tmp_path / "mRNAspect.app"
    log = _DYLD.format(app=app, outside=_STRAY)
    assert _tool().loaded_outside(log, app) == (4, [_STRAY])


def test_a_run_that_loads_only_the_app_and_the_system_is_clean(tmp_path: Path) -> None:
    app = tmp_path / "mRNAspect.app"
    log = _DYLD.format(app=app, outside="/System/Library/Frameworks/X.framework/X")
    assert _tool().loaded_outside(log, app) == (4, [])


class _Run:
    """Stands in for the command `--loaded` starts."""

    def __init__(self, stderr: str, code: int = 0) -> None:
        self.stderr, self.code, self.command, self.env = stderr, code, None, None

    def __call__(self, command, **kwargs):
        self.command, self.env = command, kwargs["env"]
        return subprocess.CompletedProcess(command, self.code, "", self.stderr)


@pytest.mark.parametrize("outside, code, expected", [
    (_STRAY, 0, 1),                                        # loaded from the build folder
    ("/usr/lib/libz.1.dylib", 0, 0),                       # the system's
    ("/usr/lib/libz.1.dylib", 1, 1),                       # the command itself failed
])
def test_loaded_runs_the_command_and_judges_what_dyld_printed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outside: str, code: int,
        expected: int) -> None:
    tool = _tool()
    app = tmp_path / "mRNAspect.app"
    app.mkdir()
    run = _Run(_DYLD.format(app=app, outside=outside), code)
    monkeypatch.setattr(tool, "_on_macos", lambda: True)
    monkeypatch.setattr(tool.subprocess, "run", run)
    command = ["/app/env/bin/python3", "-I", "-c", "import numpy"]
    assert tool.main(["--loaded", str(app), "--", *command]) == expected
    assert run.command == command, "the command after -- is run as it was given"
    assert run.env["DYLD_PRINT_LIBRARIES"] == "1"


def test_a_run_dyld_said_nothing_about_shows_nothing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """macOS drops DYLD_* variables for protected binaries. A silent run must not pass
    as a clean one."""
    tool = _tool()
    monkeypatch.setattr(tool, "_on_macos", lambda: True)
    monkeypatch.setattr(tool.subprocess, "run", _Run("samtools 1.24\n"))
    assert tool.main(["--loaded", str(tmp_path), "--", "samtools", "--version"]) == 1
    assert "shows nothing" in capsys.readouterr().err


def test_loaded_is_refused_away_from_macos(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool()
    monkeypatch.setattr(tool, "_on_macos", lambda: False)
    assert tool.main(["--loaded", str(tmp_path), "--", "samtools", "--version"]) == 2


# ---------------------------------------------------------------------------
# 5. The scripts call it, and the bundle is sealed
# ---------------------------------------------------------------------------

def test_the_script_runs_as_a_program(tmp_path: Path) -> None:
    """As staging and `check_bundle.sh` start it: `python -I <script>`."""
    env = _env(tmp_path)
    proc = subprocess.run([sys.executable, "-I", str(_SCRIPT), "--check", str(env)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"lib/libncursesw.6.dylib: re-export {_BUILT_IN}/lib/libtinfow.6.dylib" \
        in proc.stderr
    proc = subprocess.run([sys.executable, "-I", str(_SCRIPT), "--dry-run", str(env)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "would rewrite  lib/libncursesw.6.dylib" in proc.stdout
    assert "@loader_path/libtinfow.6.dylib" in proc.stdout


def test_staging_rewrites_before_core_is_installed_and_verified() -> None:
    """So the checks staging makes afterwards run on what ships."""
    text = _STAGE.read_text()
    call = text.index('scripts/bundle_load_paths.py" "$DEST"')
    assert call < text.index('step "Installing Core for real"')
    assert call > text.index("strip_bundle_env.py"), (
        "what is left out goes first: a file that is removed needs no rewrite")
    line = text[call:text.index("\n\n", call)]
    assert "|| fail" in line, "a reference that is left must stop the staging"


def test_the_built_app_is_asked_three_ways() -> None:
    text = _CHECK.read_text()
    assert 'bundle_load_paths.py" --check "$APP"' in text
    assert '--loaded "$APP" -- "$@"' in text
    assert 'loaded "$RES/bin/minimap2" --version' in text
    assert 'codesign --verify --deep --strict --verbose=2 "$APP"' in text
    assert '"$APP/Contents/_CodeSignature"' in text


def test_samtools_is_run_the_ways_core_runs_it() -> None:
    """Not `samtools --version`. That lists htslib's plugins for remote files, which
    htslib looks for in a folder compiled into the library, so on the build machine it
    loaded seventeen libraries from the development environment (2026-11-18) while no
    command Core runs opens one. The check has to ask about what the app does, and to
    follow when Core runs samtools another way.
    """
    align = (_ROOT / "mrnaspect" / "wrappers" / "run_align.sh").read_text()
    core = set(re.findall(r"\$SAMTOOLS ([a-z]+)", align))
    checked = re.findall(r'loaded "\$RES/bin/samtools" (\S+)', _CHECK.read_text())
    assert core, "run_align.sh no longer names samtools as $SAMTOOLS"
    assert set(checked) == core, f"Core runs {sorted(core)}; the check runs {sorted(checked)}"


def test_a_run_is_named_with_its_subcommand(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """Six samtools lines in the Mac's output have to say which is which."""
    tool = _tool()
    app = tmp_path / "mRNAspect.app"
    app.mkdir()
    monkeypatch.setattr(tool, "_on_macos", lambda: True)
    monkeypatch.setattr(tool.subprocess, "run",
                        _Run(_DYLD.format(app=app, outside="/usr/lib/libz.1.dylib")))
    assert tool.main(["--loaded", str(app), "--", "/app/bin/samtools", "sort", "-o",
                      "out.bam", "in.sam"]) == 0
    assert "ok    samtools sort: 4 libraries" in capsys.readouterr().out


def test_the_bundler_is_asked_to_seal_the_app() -> None:
    """Every file of the environment is signed ad hoc on its own, but macOS judges a
    downloaded app by the signature of the whole bundle. `signingIdentity: "-"` makes the
    bundler write one (`codesign --force -s -`), still without an Apple identity.

    `hardenedRuntime` is off because the bundler otherwise adds it to that signature, and
    it buys nothing without notarization: the window's process stays as it was.
    `minimumSystemVersion` says what the binaries need; the bundler's default, 10.13,
    is a system no arm64 binary runs on.
    """
    mac = json.loads(_CONF.read_text())["bundle"]["macOS"]
    assert mac["signingIdentity"] == "-"
    assert mac["hardenedRuntime"] is False
    assert mac["minimumSystemVersion"] == "11.0"
