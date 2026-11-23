"""`scripts/check_bundle.sh` must not turn a wrong path or a failed import into a verdict.

It is the check run on the Mac after a build, and its first run there (2026-10-29) showed
two defects:

1. It never checked that the `.app` exists. Pointed at a path that had lost its
   `bundle/`, it ran every check against nothing and ended "0 passed, 4 failed" -- which
   reads as a broken bundle, not as a typo.
2. Check 5 piped the import test through `tail -3` and then read `$?`, which is tail's.
   A native stack that failed to import was reported as a PASS.

Check 7 (2026-11-13) asks `scripts/strip_bundle_env.py --check` whether the bundle still
carries what staging leaves out: readline (GPL-3.0-only) and pyphen's hyphenation
dictionaries. What that script finds is held
in `test_bundle_leaves_out_gpl.py`; here, only that its verdict becomes the check's.

Check 8 (2026-11-13) asks `scripts/make_notices.py --check` whether the third parties'
notices are in the bundle and whole (`test_third_party_notices.py`), in the same way.

Check 9 (2026-11-18) asks `scripts/bundle_load_paths.py --check` whether a library in the
app is named by a path outside it (`test_bundle_load_paths.py`). Check 11 asks
`scripts/bundle_bytecode.py --check` whether Python would be compiled again, and
rewritten, inside the app (`test_bundle_bytecode.py`). Check 12 (2026-11-19) asks
`scripts/bundle_build_record.py --check` whether the app holds the commit it was built
from, and then Core in the app whether it reports that commit (`test_build_record.py`).
Checks 10 and 13 ask macOS itself: what samtools, minimap2 and the report's native stack
loaded, and whether `codesign` accepts the signature of the whole bundle, asked last so
that it also covers what the checks above did to the app. Away from macOS they say so and
count as neither passed nor failed.

The script is for macOS, but neither defect needs a Mac to show. The `.app` here is a
directory holding a shell stand-in for the interpreter, which answers each of the
script's python calls the way a working, or a broken, bundle would.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_bundle.sh"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(), reason="scripts/ is not in this checkout")

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

#: One branch per python call the script makes, matched on the code it passes.
_STAND_IN = """#!/bin/sh
case "$*" in
    *strip_bundle_env.py*) {left_out} ;;
    *make_notices.py*) {notices} ;;
    *bundle_load_paths.py*--loaded*) {loaded} ;;
    *bundle_load_paths.py*--check*) {load_paths} ;;
    *bundle_bytecode.py*) {bytecode} ;;
    *bundle_build_record.py*) {record} ;;
    *provenance.git_commit*) {said} ;;
    *sys.prefix*) echo '{env}' ;;
    *importlib*) {imports} ;;
    *mrnaspect.__file__*) echo '{env}/lib/python3.14/site-packages/mrnaspect/__init__.py' ;;
    *mrnaspect.reporting.reporter*) exit 0 ;;
    *) echo "unexpected call: $*" >&2; exit 99 ;;
esac
"""
_IMPORTS_OK = "echo '  all native stacks import'"
_IMPORTS_BROKEN = """echo "ModuleNotFoundError: No module named 'weasyprint'" >&2; exit 1"""
_LEFT_OUT_OK = "echo '  ok    readline is not in this environment'"
_LEFT_OUT_BROKEN = """echo "lib/libreadline.8.3.dylib: still here" >&2; exit 1"""
_NOTICES_OK = "echo '  ok    THIRD-PARTY-NOTICES.txt is in the environment'"
_NOTICES_BROKEN = """echo "INCOMPLETE: no licence text was found for mystery 1.0" >&2; exit 1"""
_PATHS_OK = "echo '  ok    607 Mach-O files: every library is named relative'"
_PATHS_BROKEN = ("""echo "lib/libncursesw.6.dylib: re-export /Users/builder/env/lib/"""
                 """libtinfow.6.dylib" >&2; exit 1""")
_LOADED_OK = "echo '  ok    all from the app or the system'"
_LOADED_BROKEN = """echo "samtools loaded /Users/builder/env/lib/libtinfow.6.dylib" >&2; exit 1"""
_BYTECODE_OK = "echo '  ok    3335 sources have bytecode that does not depend on file times'"
_BYTECODE_BROKEN = ("""echo "lib/python3.14/os.py: compiled against the file's time" >&2; """
                    """exit 1""")
_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_RECORD_OK = (f"echo '  ok    built from {_COMMIT} (working tree clean), "
              f"staged 2026-11-19T00:00:00+00:00'")
_RECORD_MISSING = ("""echo "no _build.json in the app's mrnaspect package: staging from a """
                   """git checkout always writes one" >&2; exit 1""")
_RECORD_NO_COMMIT = ("echo '  note  no build record: built from a source that is not a "
                     "git checkout; the app'\\''s results name no commit'")
_SAID_OK = f"echo {_COMMIT}"
_SAID_NOTHING = "echo ''"


def _app(parent: Path, imports: str, left_out: str = _LEFT_OUT_OK,
         notices: str = _NOTICES_OK, load_paths: str = _PATHS_OK,
         loaded: str = _LOADED_OK, bytecode: str = _BYTECODE_OK,
         record: str = _RECORD_OK, said: str = _SAID_OK,
         sealed: bool = True) -> Path:
    """An .app whose env has `bin/python3 -> python3.14`, as the staged env does."""
    app = parent / "mRNAspect.app"
    env = app / "Contents" / "Resources" / "env"
    (env / "bin").mkdir(parents=True)
    python = env / "bin" / "python3.14"
    python.write_text(_STAND_IN.format(env=env, imports=imports, left_out=left_out,
                                       notices=notices, load_paths=load_paths,
                                       loaded=loaded, bytecode=bytecode,
                                       record=record, said=said))
    python.chmod(0o755)
    (env / "bin" / "python3").symlink_to("python3.14")
    if sealed:
        (app / "Contents" / "_CodeSignature").mkdir()
    return app


def _environ(tmp_path: Path, system: str, codesign: int = 0) -> dict[str, str]:
    """The script's environment, on a host that answers `uname -s` with `system`.

    Checks 10 and 13 are for macOS, and this suite runs on Linux and on a Mac. So the
    host is a stand-in too: `uname` says which system it is and `codesign` exits as told,
    which holds the result to the same count wherever the suite runs.
    """
    tools = tmp_path / "host-tools"
    tools.mkdir(exist_ok=True)
    for name, body in (("uname", f"echo {system}"),
                       ("codesign", f"echo 'codesign: {codesign}' >&2; exit {codesign}")):
        (tools / name).write_text(f"#!/bin/sh\n{body}\n")
        (tools / name).chmod(0o755)
    # macOS always sets TMPDIR, and the script reads it under `set -u`.
    return {"PATH": f"{tools}{os.pathsep}{os.environ['PATH']}", "TMPDIR": str(tmp_path)}


def _run(app: Path, tmp_path: Path, system: str = "Linux",
         codesign: int = 0) -> tuple[int, str, str]:
    proc = subprocess.run(["bash", str(_SCRIPT), str(app)], capture_output=True,
                          text=True, env=_environ(tmp_path, system, codesign), timeout=60)
    return proc.returncode, _ANSI.sub("", proc.stdout), proc.stderr


def test_a_working_bundle_passes_every_check(tmp_path: Path) -> None:
    """The control: without it, the failure below could be the stand-in's."""
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK), tmp_path)
    assert "12 passed, 0 failed" in out, out + err
    assert code == 0
    assert out.count("NOTE  not macOS") == 2, "checks 10 and 13 say they were not asked"


def test_on_macos_the_app_is_also_run_and_its_seal_verified(tmp_path: Path) -> None:
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK), tmp_path, system="Darwin")
    assert "PASS  nothing was loaded from outside the app" in out, out + err
    assert "PASS  the bundle's signature covers what is in it" in out, out
    assert "14 passed, 0 failed" in out, out
    assert code == 0


def test_a_relative_path_to_the_app_is_the_same_app(tmp_path: Path) -> None:
    """2026-11-14, the Mac: run from gui/ with `src-tauri/target/.../mRNAspect.app`, checks
    5 and 6 failed on a whole bundle. They compare where Python says it is (always a full
    path) with the path as it was typed."""
    app = _app(tmp_path / "gui" / "target", _IMPORTS_OK)
    proc = subprocess.run(["bash", str(_SCRIPT), "target/mRNAspect.app"], cwd=app.parents[1],
                          capture_output=True, text=True,
                          env=_environ(tmp_path, "Linux"), timeout=60)
    out = _ANSI.sub("", proc.stdout)
    assert "12 passed, 0 failed" in out, out + proc.stderr
    assert proc.returncode == 0


def test_a_stack_that_fails_to_import_fails_check_5(tmp_path: Path) -> None:
    code, out, err = _run(_app(tmp_path, _IMPORTS_BROKEN), tmp_path)
    assert "FAIL  a native stack failed to import" in out, out + err
    assert "native stacks import from the bundle" not in out, out
    assert "11 passed, 1 failed" in out, out
    assert code == 1


def test_a_bundle_that_still_carries_readline_fails_check_7(tmp_path: Path) -> None:
    """And what the script found is shown, so the Mac's output says which file."""
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK, _LEFT_OUT_BROKEN), tmp_path)
    assert "FAIL  something staging leaves out is still in the bundle" in out, out + err
    assert "lib/libreadline.8.3.dylib: still here" in out, out
    assert "11 passed, 1 failed" in out, out
    assert code == 1


@pytest.mark.parametrize("where", [
    "src-tauri/target/release/macos/mRNAspect.app",  # 2026-10-29: bundle/ left out
    "src-tauri/target/release/bundle/macos",         # the folder, not the .app in it
])
def test_a_path_that_is_not_an_app_stops_before_any_check(
        tmp_path: Path, where: str) -> None:
    _app(tmp_path / "src-tauri" / "target" / "release" / "bundle" / "macos",
         _IMPORTS_OK)
    code, out, err = _run(tmp_path / where, tmp_path)
    assert code == 2, out + err
    assert "passed," not in out, "checks ran against a path that is not an app"
    assert f"not an .app bundle: {tmp_path / where}" in err
    assert "bundle/macos/" in err


def test_notices_that_are_missing_or_incomplete_fail_check_8(tmp_path: Path) -> None:
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK, notices=_NOTICES_BROKEN), tmp_path)
    assert "FAIL  the third parties' notices are missing or incomplete" in out, out + err
    assert "no licence text was found for mystery 1.0" in out, out
    assert "11 passed, 1 failed" in out, out
    assert code == 1


def test_a_library_named_outside_the_app_fails_check_9(tmp_path: Path) -> None:
    """And the file and the path are shown: that line is what says what to fix."""
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK, load_paths=_PATHS_BROKEN), tmp_path)
    assert "FAIL  a library is named by a path outside the app" in out, out + err
    assert "lib/libncursesw.6.dylib: re-export /Users/builder/env/lib/libtinfow.6.dylib" \
        in out, out
    assert "11 passed, 1 failed" in out, out
    assert code == 1


def test_a_run_that_loads_from_outside_fails_check_10(tmp_path: Path) -> None:
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK, loaded=_LOADED_BROKEN), tmp_path,
                          system="Darwin")
    assert "FAIL  a run loaded a library from outside the app, or did not run" in out, \
        out + err
    assert "samtools loaded /Users/builder/env/lib/libtinfow.6.dylib" in out, out
    assert "13 passed, 1 failed" in out, out
    assert code == 1


def test_an_app_that_is_not_sealed_fails_check_13(tmp_path: Path) -> None:
    """Asked before `codesign` is: a bundle with no signature of its own has nothing for
    it to verify, and what it says of one differs between versions of macOS."""
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK, sealed=False), tmp_path,
                          system="Darwin")
    assert "FAIL  no Contents/_CodeSignature: the bundle is not signed as a whole" in out, \
        out + err
    assert "13 passed, 1 failed" in out, out
    assert code == 1


def test_a_seal_codesign_does_not_accept_fails_check_13(tmp_path: Path) -> None:
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK), tmp_path, system="Darwin",
                          codesign=1)
    assert "FAIL  codesign does not accept the bundle's signature" in out, out + err
    assert "codesign: 1" in out, "what codesign said is shown"
    assert "13 passed, 1 failed" in out, out
    assert code == 1


def test_python_that_would_be_compiled_again_fails_check_11(tmp_path: Path) -> None:
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK, bytecode=_BYTECODE_BROKEN), tmp_path)
    assert "FAIL  Python would be compiled again, and rewritten, inside the app" in out, \
        out + err
    assert "lib/python3.14/os.py: compiled against the file's time" in out, out
    assert "11 passed, 1 failed" in out, out
    assert code == 1


def test_an_app_without_its_build_record_fails_check_12(tmp_path: Path) -> None:
    """Staging from a checkout always writes it; without it, a result names no build."""
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK, record=_RECORD_MISSING), tmp_path)
    assert ("FAIL  the app has no usable record of the commit it was built from, or it is "
            "of another commit") in out, out + err
    assert "no _build.json in the app's mrnaspect package" in out, out
    assert "11 passed, 1 failed" in out, out
    assert code == 1


def test_a_release_s_commit_is_handed_to_the_record_check(tmp_path: Path) -> None:
    """The second argument is the commit a release is of; check 12 asks for exactly it."""
    seen = tmp_path / "record-args.txt"
    record = f"echo \"$*\" > '{seen}'; {_RECORD_OK}"
    app = _app(tmp_path, _IMPORTS_OK, record=record)
    proc = subprocess.run(["bash", str(_SCRIPT), str(app), _COMMIT], capture_output=True,
                          text=True, env=_environ(tmp_path, "Linux"), timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"--expect {_COMMIT}" in seen.read_text()
    # Without it the app is held to the checkout it is checked from.
    subprocess.run(["bash", str(_SCRIPT), str(app)], capture_output=True, text=True,
                   env=_environ(tmp_path, "Linux"), timeout=60)
    assert "--expect" not in seen.read_text() and "--source" in seen.read_text()


def test_an_app_of_another_commit_fails_check_12(tmp_path: Path) -> None:
    other = ("echo 'this checkout is at ffff, and the app was built from another commit, "
             f"{_COMMIT}.' >&2; exit 1")
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK, record=other), tmp_path)
    assert "built from another commit" in out, out + err
    assert "11 passed, 1 failed" in out and code == 1


def test_core_that_does_not_report_the_recorded_commit_fails_check_12(
        tmp_path: Path) -> None:
    """The file is one half. What a result carries is what Core says, run from the app."""
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK, said=_SAID_NOTHING), tmp_path)
    assert f"FAIL  Core in the app reports '', not the recorded {_COMMIT}" in out, out + err
    assert "11 passed, 1 failed" in out, out
    assert code == 1


def test_an_app_built_from_a_source_with_no_commit_is_said_not_failed(
        tmp_path: Path) -> None:
    """A downloaded archive has no commit to give. Counted as neither passed nor failed,
    and Core is not asked for a commit that cannot be there."""
    code, out, err = _run(_app(tmp_path, _IMPORTS_OK, record=_RECORD_NO_COMMIT,
                               said="echo 'asked' >&2; exit 99"), tmp_path)
    assert "NOTE  no commit was there to record" in out, out + err
    assert "11 passed, 0 failed" in out, out
    assert code == 0


def test_the_seal_is_asked_after_every_other_check(tmp_path: Path) -> None:
    """So that a check which changed a file of the app is caught by the last one."""
    _code, out, _err = _run(_app(tmp_path, _IMPORTS_OK), tmp_path, system="Darwin")
    headings = re.findall(r"^== (\d+)\. (.*)$", out, re.M)
    assert [int(n) for n, _title in headings] == list(range(1, 14)), headings
    assert headings[-1][1].startswith("the app is sealed"), headings[-1]
