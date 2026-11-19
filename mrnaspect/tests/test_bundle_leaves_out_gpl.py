"""The app ships no GPL-only library: readline is left out of the staged environment.

Decided on 2026-11-13. conda-forge's Python depends on
GNU readline (GPL-3.0-only), so every solve brings it, and `stage_bundle_env.sh` copied it
into the environment the .app carries. Only Python's interactive prompt uses it and the
app never opens one, so `scripts/strip_bundle_env.py` removes the package, its record and
the one extension module bound to it, and says so if anything still links the library.

The same script leaves out pyphen's hyphenation dictionaries (2026-11-13; L6).
pyphen itself is tri-licensed and stays, because WeasyPrint imports it. Its dictionaries
are "distributed under GPL, LGPL and/or MPL" each (pyphen's own LICENSE), and the report
never hyphenates, so none is ever opened.

Three things are held here:

1. the script, on a directory shaped like the environment (no Mac is needed to show it);
2. the lock: a re-solve that brings another GPL-only package, or a second package bound
   to readline, fails here and names it;
3. the two shell scripts call it: staging before Core is installed and verified, and
   `check_bundle.sh` on the built .app.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "strip_bundle_env.py"
_STAGE = _ROOT / "scripts" / "stage_bundle_env.sh"
_CHECK = _ROOT / "scripts" / "check_bundle.sh"
_LOCK = _ROOT / "pixi.lock"

pytestmark = pytest.mark.skipif(
    not _STAGE.exists(), reason="scripts/ is not in this checkout")

#: What a 64-bit Mach-O file and an ELF file begin with: the scan reads only such files.
_MACHO = b"\xcf\xfa\xed\xfe"
_ELF = b"\x7fELF"


@functools.lru_cache(maxsize=1)
def _strip():
    """The script as a module: `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("strip_bundle_env", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses looks its module up by name
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. The script, on a stand-in environment
# ---------------------------------------------------------------------------

#: readline's own files as its conda record lists them on osx-arm64 (three of the names
#: are links to the versioned library, as in the real package).
_OWN = [
    "lib/libreadline.8.3.dylib", "lib/libreadline.8.dylib", "lib/libreadline.dylib",
    "lib/libhistory.8.3.dylib", "lib/libhistory.8.dylib", "lib/libhistory.dylib",
    "include/readline/readline.h", "include/readline/history.h",
    "share/doc/readline/README", "lib/pkgconfig/readline.pc",
]
_LINKS = {"lib/libreadline.8.dylib": "libreadline.8.3.dylib",
          "lib/libreadline.dylib": "libreadline.8.3.dylib",
          "lib/libhistory.8.dylib": "libhistory.8.3.dylib",
          "lib/libhistory.dylib": "libhistory.8.3.dylib"}
_RECORD = "conda-meta/readline-8.3-h8b90a29_1.json"
#: Python's extension module: not in readline's record, and the only thing that loads it.
_BOUND = "lib/python3.14/lib-dynload/readline.cpython-314-darwin.so"
#: pyphen's dictionaries and the notes that describe them: a part of a package that stays.
_PYPHEN = "lib/python3.14/site-packages/pyphen"
_DICTS = [f"{_PYPHEN}/dictionaries/hyph_en_US.dic", f"{_PYPHEN}/dictionaries/hyph_ro_RO.dic",
          f"{_PYPHEN}/dictionaries/README_hyph_ro_RO.txt"]
#: Neighbours that must stay: readline's own dependency, a BSD line editor, the module
#: that completes names at a prompt (pure Python), and a text file that names the library.
_KEPT = {
    "lib/libncurses.6.dylib": _MACHO + b"\0ncurses",
    "lib/libedit.0.dylib": _MACHO + b"\0@rpath/libncurses.6.dylib\0",
    "lib/python3.14/lib-dynload/_curses.cpython-314-darwin.so":
        _MACHO + b"\0@rpath/libncurses.6.dylib\0",
    "lib/python3.14/rlcompleter.py": b"import readline\n",
    "lib/python3.14/config-3.14-darwin/Makefile":
        b"READLINE_LIBS= -lreadline\n# libreadline.8.dylib\n",
    "include/ncurses.h": b"/* ncurses */\n",
    "conda-meta/ncurses-6.6-h1b0a1f3_0.json":
        json.dumps({"name": "ncurses", "version": "6.6", "license": "X11 AND BSD-3-Clause",
                    "files": ["lib/libncurses.6.dylib", "include/ncurses.h"]}).encode(),
    "bin/python3.14": _MACHO + b"\0@rpath/libpython3.14.dylib\0",
    # pyphen itself, and the licence the notices will quote for it.
    f"{_PYPHEN}/__init__.py": b"LANGUAGES = {}\n",
    "lib/python3.14/site-packages/pyphen-0.18.1.dist-info/licenses/COPYING.MPL": b"MPL\n",
}


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _env(parent: Path, *, files: list[str] | None = None) -> Path:
    """An environment holding readline as conda installs it, and its neighbours."""
    env = parent / "env"
    for rel in _OWN:
        if rel in _LINKS:
            continue
        data = _MACHO + b"\0@rpath/libncurses.6.dylib\0" if rel.endswith(".dylib") else b"x\n"
        _write(env / rel, data)
    for rel, target in _LINKS.items():
        (env / rel).symlink_to(target)
    _write(env / _RECORD, json.dumps({
        "name": "readline", "version": "8.3", "license": "GPL-3.0-only",
        "files": _OWN if files is None else files}).encode())
    _write(env / _BOUND, _MACHO + b"\0@rpath/libreadline.8.dylib\0")
    for rel in _DICTS:
        _write(env / rel, b"UTF-8\n")
    for rel, data in _KEPT.items():
        _write(env / rel, data)
    return env


def _run(*args: object) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(_SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=120)


def _present(env: Path, rels) -> list[str]:
    """Those of ``rels`` still there. `lexists`, so a link left dangling counts."""
    return [r for r in rels if os.path.lexists(env / r)]


def test_the_package_its_record_and_pythons_module_are_removed(tmp_path: Path) -> None:
    env = _env(tmp_path)
    proc = _run(env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _present(env, [*_OWN, _RECORD, _BOUND]) == []
    # Its own folders go with their last file; a folder it shared stays.
    assert not (env / "include" / "readline").exists()
    assert not (env / "share" / "doc" / "readline").exists()
    assert (env / "include" / "ncurses.h").exists()
    assert "readline 8.3" in proc.stdout and "GPL-3.0-only" in proc.stdout


def test_the_dictionaries_go_and_pyphen_stays(tmp_path: Path) -> None:
    env = _env(tmp_path)
    proc = _run(env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _present(env, _DICTS) == []
    assert "dictionaries" in proc.stdout
    # pyphen lists this folder when it is imported, so the folder itself must stay even
    # when nothing is left in it.
    assert (env / _PYPHEN / "dictionaries").is_dir()
    assert (env / _PYPHEN / "__init__.py").exists()


def test_a_dictionary_still_there_fails_the_check_and_is_named(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert _run(env).returncode == 0
    _write(env / _DICTS[1], b"UTF-8\n")
    proc = _run("--check", env)
    assert proc.returncode == 1
    assert "hyph_ro_RO.dic" in proc.stderr


def test_nothing_else_is_touched(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert _run(env).returncode == 0
    for rel, data in _KEPT.items():
        assert (env / rel).read_bytes() == data, rel


def test_a_second_run_finds_nothing_to_do_and_passes(tmp_path: Path) -> None:
    """Staging runs it on a reused tree every time."""
    env = _env(tmp_path)
    assert _run(env).returncode == 0
    again = _run(env)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "not in this environment" in again.stdout


@pytest.mark.parametrize("entry", ["../victim", "lib/../../victim", "/etc/hostname"])
def test_a_record_that_points_outside_the_environment_removes_nothing(
        tmp_path: Path, entry: str) -> None:
    """The script deletes what a JSON file lists, so the list is read before any of it."""
    victim = tmp_path / "victim"
    victim.write_text("not the environment's\n")
    env = _env(tmp_path, files=[*_OWN, entry])
    proc = _run(env)
    assert proc.returncode != 0
    assert "outside" in proc.stderr
    assert victim.exists()
    assert _present(env, [*_OWN, _RECORD, _BOUND]) == [*_OWN, _RECORD, _BOUND]


def test_a_folder_that_is_not_an_environment_is_refused(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    proc = _run(tmp_path)
    assert proc.returncode == 2
    assert "conda-meta" in proc.stderr


def test_another_package_whose_name_begins_the_same_is_not_taken(tmp_path: Path) -> None:
    env = _env(tmp_path)
    other = "conda-meta/readline-sync-1.0-0.json"
    _write(env / other, json.dumps({"name": "readline-sync", "version": "1.0",
                                    "license": "MIT", "files": ["lib/sync.txt"]}).encode())
    _write(env / "lib/sync.txt", b"x\n")
    assert _run(env).returncode == 0
    assert _present(env, [other, "lib/sync.txt"]) == [other, "lib/sync.txt"]


def test_check_mode_removes_nothing_and_names_what_it_found(tmp_path: Path) -> None:
    env = _env(tmp_path)
    proc = _run("--check", env)
    assert proc.returncode == 1
    assert _present(env, [*_OWN, _RECORD, _BOUND]) == [*_OWN, _RECORD, _BOUND]
    for named in (_RECORD, "lib/libreadline.8.3.dylib", _BOUND):
        assert named in proc.stderr, proc.stderr


def test_check_mode_passes_once_it_is_left_out(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert _run(env).returncode == 0
    proc = _run("--check", env)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("magic, needed", [
    (_MACHO, b"@rpath/libreadline.8.dylib"),
    (_ELF, b"libreadline.so.8"),
    (_MACHO, b"@rpath/libhistory.8.dylib"),
])
def test_a_binary_that_still_links_the_library_fails_the_check(
        tmp_path: Path, magic: bytes, needed: bytes) -> None:
    """Removing a library something loads would break that thing on the user's Mac."""
    env = _env(tmp_path)
    assert _run(env).returncode == 0
    _write(env / "bin/sqlite3", magic + b"\0" * 8 + needed + b"\0")
    proc = _run("--check", env)
    assert proc.returncode == 1
    assert "bin/sqlite3" in proc.stderr


def test_removal_itself_fails_when_something_else_links_the_library(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _write(env / "bin/sqlite3", _MACHO + b"\0@rpath/libreadline.8.dylib\0")
    proc = _run(env)
    assert proc.returncode == 1
    assert "bin/sqlite3" in proc.stderr


def test_a_text_file_that_names_the_library_is_not_a_binary_that_links_it(
        tmp_path: Path) -> None:
    """Python's own Makefile says `-lreadline`; that is a record of its build."""
    env = _env(tmp_path)
    assert _run(env).returncode == 0
    assert "config-3.14-darwin/Makefile" in "".join(_KEPT)
    assert _run("--check", env).returncode == 0


# ---------------------------------------------------------------------------
# 2. The lock
# ---------------------------------------------------------------------------

def _name(entry: dict) -> str:
    if entry.get("name"):
        return entry["name"]
    filename = (entry.get("conda") or entry.get("pypi") or "").rsplit("/", 1)[-1]
    return re.sub(r"-[^-]+-[^-]+\.(conda|tar\.bz2)$", "", filename)


@functools.lru_cache(maxsize=1)
def _lock() -> dict:
    yaml = pytest.importorskip("yaml")
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    return yaml.load(_LOCK.read_text(), Loader=loader)


def _packages(platform: str) -> list[dict]:
    """The default environment's packages on one platform, as the lock records them."""
    lock = _lock()
    wanted = {e.get("conda") or e.get("pypi")
              for e in lock["environments"]["default"]["packages"][platform]}
    return [p for p in lock["packages"] if (p.get("conda") or p.get("pypi")) in wanted]


def _platforms() -> list[str]:
    return sorted(_lock()["environments"]["default"]["packages"])


@pytest.mark.parametrize("licence, only", [
    ("GPL-3.0-only", True),
    ("GPL-2.0-or-later", True),
    ("AGPL-3.0-only", True),
    # pyphen's tag: read as written, every part binds, so it must be looked at by hand.
    ("GPL-2.0-or-later AND LGPL-2.1-or-later AND MPL-1.1", True),
    ("GPL-2.0-only OR FTL", False),
    ("BSD-2-Clause OR GPL-2.0-or-later", False),
    ("LGPL-2.1-only or MPL-1.1", False),
    ("GPL-3.0-only WITH GCC-exception-3.1", False),
    ("LGPL-2.1-or-later", False),
    ("MIT", False),
    (None, False),
])
def test_gpl_only_reads_the_expressions_the_lock_uses(licence, only: bool) -> None:
    assert _strip().gpl_only(licence) is only


def test_every_gpl_only_package_the_app_would_ship_is_left_out_or_was_read(
        ) -> None:
    """A re-solve that brings a new one must be decided, not shipped."""
    strip = _strip()
    for platform in strip.SHIPS_ON:
        tagged = {_name(p): p.get("license") for p in _packages(platform)
                  if strip.gpl_only(p.get("license"))}
        undecided = set(tagged) - set(strip.LEFT_OUT) - set(strip.CHOSEN)
        assert not undecided, (
            f"{platform}: GPL-only in pixi.lock and neither left out of the bundle nor "
            f"read at its source: { {n: tagged[n] for n in sorted(undecided)} }")


def test_what_the_script_names_is_what_the_lock_holds() -> None:
    """Neither table may outlive its reason: a name gone from the lock, or a licence that
    changed, means the entry has to be read again."""
    strip = _strip()
    for platform in strip.SHIPS_ON:
        licences = {_name(p): p.get("license") for p in _packages(platform)}
        for name, policy in strip.LEFT_OUT.items():
            assert licences.get(name) == policy.licence, (platform, name, licences.get(name))
        for name in strip.CHOSEN:
            assert name in licences, f"{name} left the lock; drop it from CHOSEN"
            assert strip.gpl_only(licences[name]), (
                f"{name} is tagged {licences[name]!r} now; CHOSEN no longer needs it")


def test_only_python_depends_on_what_is_left_out() -> None:
    """On every platform of the lock. A second package built against readline would be
    broken by the removal, and the lock is where that shows first."""
    strip = _strip()
    for platform in _platforms():
        for package in _packages(platform):
            needs = {re.split(r"[ <>=!~]", d, maxsplit=1)[0]
                     for d in package.get("depends") or []}
            hit = needs & set(strip.LEFT_OUT)
            if hit:
                assert _name(package) == "python", (
                    f"{platform}: {_name(package)} depends on {sorted(hit)}")


def test_in_the_environment_here_only_pythons_module_links_the_library() -> None:
    """The same question asked of real files (linux-64 on this host; the Mac's answer is
    `check_bundle.sh`'s). Skipped where there is no environment."""
    env = _ROOT / ".pixi" / "envs" / "default"
    strip = _strip()
    records = [r for r in env.glob("conda-meta/readline-*.json")
               if json.loads(r.read_text()).get("name") == "readline"]
    if not records:
        pytest.skip("no pixi environment with readline here")
    record = json.loads(records[0].read_text())
    policy = strip.LEFT_OUT["readline"]

    libraries = {Path(f).name.split(".")[0] for f in record["files"]
                 if f.startswith("lib/") and (".so" in f or ".dylib" in f)}
    assert libraries == set(policy.libraries)

    own = set(record["files"])
    bound = {str(p.relative_to(env)) for pattern in policy.bound for p in env.glob(pattern)}
    assert bound, "Python's readline module is not where the script looks for it"
    others = set(strip.linkers(env)) - own - bound
    assert not others, f"these link readline and would break without it: {sorted(others)}"


def test_in_the_environment_here_every_dictionary_is_one_the_script_takes() -> None:
    """The globs name pyphen's layout; a release that moves the files must fail here."""
    env = _ROOT / ".pixi" / "envs" / "default"
    every = set(env.glob("lib/python*/site-packages/pyphen/**/*.dic"))
    if not every:
        pytest.skip("no pixi environment with pyphen here")
    taken = {p for part in _strip().PARTS_LEFT_OUT.values()
             for pattern in part.files for p in env.glob(pattern)}
    assert every <= taken, sorted(str(p) for p in every - taken)[:3]


def test_the_report_never_asks_for_hyphenation() -> None:
    """What makes the dictionaries dead weight: WeasyPrint opens one only for
    `hyphens: auto`. A template that starts to hyphenate needs them back."""
    package = _ROOT / "mrnaspect"
    asking = [str(p.relative_to(_ROOT)) for p in package.rglob("*")
              if p.is_file() and p.suffix in {".html", ".css", ".py", ".j2"}
              and "tests" not in p.parts and "hyphens" in p.read_text(errors="replace")]
    assert not asking, asking


# ---------------------------------------------------------------------------
# 3. The shell scripts call it
# ---------------------------------------------------------------------------

def test_staging_leaves_it_out_before_core_is_installed_and_verified() -> None:
    """Every check staging makes then runs on the environment as it ships."""
    text = _STAGE.read_text()
    call = text.index("strip_bundle_env.py")
    assert text.index('cp -a "$SRC" "$DEST"') < call < text.index("Installing Core for real")
    line = text[text.rindex("\n", 0, call):text.index("\n", call)]
    assert '"$DEST/bin/python" -I' in line, line
    assert "|| fail" in text[call:text.index("Installing Core for real")]


def test_staging_asserts_that_readline_no_longer_imports() -> None:
    text = _STAGE.read_text()
    verifying = text[text.index('step "Verifying"'):]
    assert "import readline" in verifying
    # And Core is run after that, which the script already did: the interpreter starts
    # and Core's entry point answers without the module.
    assert "-m mrnaspect.main --help" in verifying


def test_staging_renders_a_pdf_without_the_dictionaries() -> None:
    """The one thing the dictionaries could break is the PDF, so staging writes one, with
    hyphenation asked for, on the environment that ships."""
    text = _STAGE.read_text()
    verifying = text[text.index('step "Verifying"'):]
    assert "weasyprint" in verifying and "write_pdf" in verifying
    assert "hyphens" in verifying


def test_check_bundle_asks_the_built_app_the_same_question() -> None:
    text = _CHECK.read_text()
    call = text.index("strip_bundle_env.py")
    assert "--check" in text[call:text.index("\n", call)]
    assert '"$RES"' in text[call:text.index("\n", call)]
