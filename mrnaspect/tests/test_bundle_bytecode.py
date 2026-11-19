"""Python inside the app is never compiled again, and never written.

Found on the first sealed build, 2026-11-18. A .pyc records the time its source was last
changed; a copy that does not keep file times makes it stale; Python then compiles the
module again and writes the new .pyc. Inside the app that is a write under a signature
that covers every file: after the bundle check had run the bundled Python once, `codesign`
reported "file modified" for .pyc files and refused the bundle.

Two things hold it, and both are held here:

1. `scripts/bundle_bytecode.py` compiles the staged environment as unchecked hash-based
   bytecode (PEP 552), which Python loads without looking at the source's time. Shown on
   a stand-in environment compiled by the interpreter running this test: bytecode is the
   same on every platform, so no Mac is needed.
2. Nothing that runs the bundled Python may write bytecode: the shell starts it with `-B`,
   and so does every check in `check_bundle.sh`.
"""

from __future__ import annotations

import functools
import importlib.util
import os
import py_compile
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "bundle_bytecode.py"
_STAGE = _ROOT / "scripts" / "stage_bundle_env.sh"
_CHECK = _ROOT / "scripts" / "check_bundle.sh"
_LIB_RS = _ROOT / "gui" / "src-tauri" / "src" / "lib.rs"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(), reason="scripts/ is not in this checkout")


@functools.lru_cache(maxsize=1)
def _tool():
    """The script as a module: `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("bundle_bytecode", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _env(parent: Path) -> Path:
    """An environment's Python: a standard library, and one package in site-packages."""
    env = parent / "env"
    lib = env / "lib" / "python3.14"
    (lib / "site-packages" / "pkg").mkdir(parents=True)
    (lib / "os.py").write_text("sep = '/'\n")
    (lib / "json.py").write_text("def dumps(x):\n    return repr(x)\n")
    (lib / "site-packages" / "pkg" / "__init__.py").write_text("VALUE = 1\n")
    (lib / "site-packages" / "pkg" / "core.py").write_text("def f():\n    return 2\n")
    return env


def _pyc(source: Path) -> Path:
    return _tool().cache_of(source)


def _flags(source: Path) -> int:
    return int.from_bytes(_pyc(source).read_bytes()[4:8], "little")


def _times(env: Path) -> dict[Path, int]:
    return {p: p.stat().st_mtime_ns for p in env.rglob("*.pyc")}


# ---------------------------------------------------------------------------
# 1. The script
# ---------------------------------------------------------------------------

def test_every_source_gets_bytecode_that_holds_a_hash_not_a_time(tmp_path: Path) -> None:
    tool = _tool()
    env = _env(tmp_path)
    assert tool.main(["--check", str(env)]) == 1, "nothing is compiled yet"
    assert tool.main([str(env)]) == 0
    for source in tool.sources(env):
        assert tool.state(source) == tool.CURRENT, source
        assert _flags(source) == 0b01, "hash-based, and not compared at import"
    assert tool.main(["--check", str(env)]) == 0


def test_a_copy_that_loses_file_times_changes_nothing(tmp_path: Path) -> None:
    """The point of it. With a .pyc that records a time, this is where every module
    becomes stale; here Python has nothing to compare."""
    tool = _tool()
    env = _env(tmp_path)
    assert tool.main([str(env)]) == 0
    for source in tool.sources(env):
        os.utime(source, (1_000_000_000, 1_000_000_000))
    assert tool.main(["--check", str(env)]) == 0
    before = _times(env)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import pkg.core; "
            "assert pkg.core.f() == 2")
    site = env / "lib" / "python3.14" / "site-packages"
    # No -B: if Python judged a .pyc stale it would rewrite it, and this would show.
    done = subprocess.run([sys.executable, "-I", "-c", code, str(site)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert _times(env) == before, "importing rewrote or added a .pyc"


def test_bytecode_that_records_a_time_is_replaced(tmp_path: Path) -> None:
    """What pip leaves when Core is installed, at every staging."""
    tool = _tool()
    env = _env(tmp_path)
    source = env / "lib" / "python3.14" / "site-packages" / "pkg" / "core.py"
    py_compile.compile(str(source), cfile=str(_pyc(source)), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    assert tool.state(source) == "compiled against the file's time"
    assert tool.main([str(env)]) == 0
    assert tool.state(source) == tool.CURRENT


def test_bytecode_of_an_older_source_is_caught(tmp_path: Path,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    """Python would load it without a question: nothing else compares it with the source.
    So the hash in each .pyc is compared here, at staging and on the built app."""
    tool = _tool()
    env = _env(tmp_path)
    assert tool.main([str(env)]) == 0
    source = env / "lib" / "python3.14" / "site-packages" / "pkg" / "core.py"
    source.write_text("def f():\n    return 3\n")
    assert tool.state(source) == "compiled from another version of the source"
    assert tool.main(["--check", str(env)]) == 1
    assert "pkg/core.py: compiled from another version of the source" \
        in capsys.readouterr().err
    assert tool.main([str(env)]) == 0, "a second staging compiles what changed"
    assert tool.state(source) == tool.CURRENT


def test_a_second_run_compiles_nothing(tmp_path: Path,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    tool = _tool()
    env = _env(tmp_path)
    assert tool.main([str(env)]) == 0
    assert "(4 compiled now)" in capsys.readouterr().out
    before = _times(env)
    assert tool.main([str(env)]) == 0
    assert "(0 compiled now)" in capsys.readouterr().out
    assert _times(env) == before


def test_a_check_writes_nothing(tmp_path: Path) -> None:
    """It runs on the built, sealed app."""
    tool = _tool()
    env = _env(tmp_path)
    assert tool.main(["--check", str(env)]) == 1
    assert list(env.rglob("*.pyc")) == []
    assert list(env.rglob("__pycache__")) == []


def test_a_file_that_is_not_python_is_named_and_does_not_fail(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Some libraries ship files ending in .py that are templates or data for their own
    tests. One that does not compile cannot be imported, so it needs no bytecode."""
    tool = _tool()
    env = _env(tmp_path)
    (env / "lib" / "python3.14" / "site-packages" / "pkg" / "template.py").write_text(
        "def {{ name }}(:\n")
    assert tool.main([str(env)]) == 0
    out = capsys.readouterr().out
    assert "not compiled, lib/python3.14/site-packages/pkg/template.py" in out
    assert "1 do not compile" in out
    assert tool.main(["--check", str(env)]) == 0


def test_bytecode_compiled_by_another_python_is_not_current(tmp_path: Path) -> None:
    tool = _tool()
    env = _env(tmp_path)
    assert tool.main([str(env)]) == 0
    source = env / "lib" / "python3.14" / "os.py"
    pyc = _pyc(source)
    pyc.write_bytes(b"\x00\x00\r\n" + pyc.read_bytes()[4:])
    assert tool.state(source) == "compiled by another Python"
    assert tool.main(["--check", str(env)]) == 1


def test_a_folder_that_is_not_an_environment_is_refused(tmp_path: Path) -> None:
    tool = _tool()
    assert tool.main([str(tmp_path)]) == 2
    (tmp_path / "lib").mkdir()
    assert tool.main([str(tmp_path)]) == 2


def test_the_script_runs_as_a_program(tmp_path: Path) -> None:
    """As staging and `check_bundle.sh` start it: `python -I -B <script>`. `-B` stops
    Python writing bytecode on import; it does not stop this script writing it."""
    env = _env(tmp_path)
    done = subprocess.run([sys.executable, "-I", "-B", str(_SCRIPT), str(env)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "4 sources have bytecode that does not depend on file times" in done.stdout
    done = subprocess.run([sys.executable, "-I", "-B", str(_SCRIPT), "--check", str(env)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


# ---------------------------------------------------------------------------
# 2. Nothing that runs the bundled Python writes bytecode
# ---------------------------------------------------------------------------

def test_staging_compiles_last() -> None:
    """After Core is installed and after every step that runs the staged interpreter:
    each of them can leave a .pyc of the other kind."""
    text = _STAGE.read_text()
    call = text.index('scripts/bundle_bytecode.py" "$DEST"')
    for earlier in ('step "Installing Core for real"', 'step "Verifying"',
                    "python3 -m mrnaspect.main does not run"):
        assert text.index(earlier) < call, earlier
    assert "|| fail" in text[call:text.index("\n\n", call)]
    assert text.count('"$DEST/bin/python', call + 1) == 0, (
        "something runs the staged interpreter after the bytecode step")


@pytest.mark.skipif(not _LIB_RS.exists(), reason="GUI sources are not in this checkout")
def test_the_shell_never_lets_python_write_bytecode() -> None:
    """Every place `lib.rs` starts Core passes `-B` before `-m`. A .pyc written into
    Contents/Resources is a change to a signed bundle."""
    text = re.sub(r"//[^\n]*", "", _LIB_RS.read_text())
    modules = re.findall(r'"-m"\s*,\s*"mrnaspect[\w.]*"', text)
    guarded = re.findall(r'"-B"\s*,\s*"-m"\s*,\s*"mrnaspect[\w.]*"', text)
    assert len(modules) >= 4, "the places this test knows of have moved"
    assert len(guarded) == len(modules), (
        f"{len(modules) - len(guarded)} of {len(modules)} starts of Core lack -B")


def test_every_check_runs_the_bundled_python_with_b() -> None:
    """The first sealed build was checked without it: the check rewrote .pyc files in the
    app and broke the seal it then tested. A check leaves the app as it found it."""
    text = _CHECK.read_text()
    runs = re.findall(r'"\$PY" (-[^\n]*)', text)
    assert len(runs) >= 8, runs
    for options in runs:
        assert re.match(r"(-I )?-B\b", options), f'"$PY" {options}'
    assert 'bundle_bytecode.py" --check "$RES"' in text
