"""A result made by the desktop app names the commit the app was built from.

Found 2026-11-19. Inside the app Core is a copy in `site-packages`, in no repository, so
its results recorded `git_commit: null` and a version. Four builds made on one day were all
"0.1.0", and when a tester's result came back, nothing in it said which build had run.

Three things hold it:

1. `scripts/bundle_build_record.py` writes what git says, at staging, into the installed
   package, replaces an older record, and removes one when there is no commit to give.
2. `mrnaspect.provenance` believes that record before git: a staged environment and a
   built app lie INSIDE the checkout that built them, where git would answer for the
   checkout as it is now.
3. The source tree never holds a record, or every result of a checkout would name it.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mrnaspect import provenance

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "bundle_build_record.py"
_STAGE = _ROOT / "scripts" / "stage_bundle_env.sh"

_needs_script = pytest.mark.skipif(
    not _SCRIPT.exists(), reason="scripts/ is not in this checkout")
_needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not here")

_COMMIT = "0123456789abcdef0123456789abcdef01234567"


@functools.lru_cache(maxsize=1)
def _tool():
    """The script as a module: `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("bundle_build_record", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _env(parent: Path) -> Path:
    """An environment with Core installed in it, as staging leaves it."""
    package = parent / "env" / "lib" / "python3.14" / "site-packages" / "mrnaspect"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__ = '0.1.0'\n")
    return parent / "env"


def _record_file(env: Path) -> Path:
    return env / "lib" / "python3.14" / "site-packages" / "mrnaspect" / "_build.json"


def _git(folder: Path, *args: str) -> str:
    done = subprocess.run(
        # An identity for the commits, in words: no address belongs in this tree.
        ["git", "-C", str(folder), "-c", "user.name=test", "-c", "user.email=nobody",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True, timeout=60)
    return done.stdout.strip()


def _checkout(parent: Path) -> Path:
    """A repository with one commit and one tracked file."""
    folder = parent / "checkout"
    folder.mkdir()
    _git(folder, "init", "--quiet")
    (folder / "tracked.txt").write_text("one\n")
    _git(folder, "add", "tracked.txt")
    _git(folder, "commit", "--quiet", "-m", "one")
    return folder


# ---------------------------------------------------------------------------
# 1. The script
# ---------------------------------------------------------------------------

@_needs_script
@_needs_git
def test_staging_writes_the_commit_git_reports(tmp_path: Path,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    tool = _tool()
    env, source = _env(tmp_path), _checkout(tmp_path)
    head = _git(source, "rev-parse", "HEAD")
    assert tool.main([str(env), "--source", str(source)]) == 0
    record = json.loads(_record_file(env).read_text())
    assert record["commit"] == head and record["dirty"] is False
    assert datetime.fromisoformat(record["staged_at"]).tzinfo is not None
    assert f"built from {head} (working tree clean)" in capsys.readouterr().out
    assert tool.main(["--check", str(env), "--source", str(source)]) == 0
    assert list(_record_file(env).parent.glob("*.tmp")) == []


@_needs_script
@_needs_git
def test_a_modified_tracked_file_is_recorded_as_dirty_and_an_untracked_one_is_not(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`dirty` is what tells a reader that the commit does not describe the code."""
    tool = _tool()
    env, source = _env(tmp_path), _checkout(tmp_path)
    (source / "results.txt").write_text("not tracked\n")
    assert tool.main([str(env), "--source", str(source)]) == 0
    assert json.loads(_record_file(env).read_text())["dirty"] is False
    (source / "tracked.txt").write_text("two\n")
    assert tool.main([str(env), "--source", str(source)]) == 0
    assert json.loads(_record_file(env).read_text())["dirty"] is True
    assert "MODIFIED" in capsys.readouterr().out


@_needs_script
@_needs_git
def test_a_second_staging_replaces_the_record(tmp_path: Path) -> None:
    """The staged environment is reused and pip does not own this file: without a
    rewrite, the app would name the commit of an earlier build."""
    tool = _tool()
    env, source = _env(tmp_path), _checkout(tmp_path)
    assert tool.main([str(env), "--source", str(source)]) == 0
    first = json.loads(_record_file(env).read_text())["commit"]
    (source / "tracked.txt").write_text("two\n")
    _git(source, "commit", "--quiet", "-am", "two")
    assert tool.main([str(env), "--source", str(source)]) == 0
    second = json.loads(_record_file(env).read_text())["commit"]
    assert second == _git(source, "rev-parse", "HEAD") != first


@_needs_script
def test_a_source_with_no_commit_removes_an_older_record(
        tmp_path: Path, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A downloaded archive. git is not started for it: on a Mac without the developer
    tools, starting `/usr/bin/git` opens a system dialog."""
    tool = _tool()
    env = _env(tmp_path)
    _record_file(env).write_text(json.dumps(
        {"commit": _COMMIT, "dirty": False, "staged_at": "2026-11-18T00:00:00+00:00"}))
    archive = tmp_path / "archive"
    archive.mkdir()

    def refuse(*_args, **_kwargs):
        raise AssertionError("git was started")

    monkeypatch.setattr(tool.subprocess, "run", refuse)
    assert tool.main([str(env), "--source", str(archive)]) == 0
    assert not _record_file(env).exists()
    assert "is not a git checkout" in capsys.readouterr().out
    assert tool.main(["--check", str(env), "--source", str(archive)]) == 0
    assert tool.main(["--check", str(env)]) == 0


@_needs_script
@_needs_git
def test_a_missing_record_fails_the_check_where_one_is_expected(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tool = _tool()
    env, source = _env(tmp_path), _checkout(tmp_path)
    assert tool.main(["--check", str(env), "--source", str(source)]) == 1
    assert "staging from a git checkout always writes one" in capsys.readouterr().err
    assert not _record_file(env).exists(), "a check writes nothing"


@_needs_script
@pytest.mark.parametrize("text, why", [
    ("not json", "is not JSON"),
    ("[]", "not a JSON object"),
    (json.dumps({"commit": "5d24c01", "dirty": False,
                 "staged_at": "2026-11-19T00:00:00+00:00"}), "commit is not 40"),
    (json.dumps({"commit": _COMMIT, "dirty": "no",
                 "staged_at": "2026-11-19T00:00:00+00:00"}), "dirty is not true or false"),
    (json.dumps({"commit": _COMMIT, "dirty": False, "staged_at": "yesterday"}),
     "staged_at is not a time"),
])
def test_a_file_that_is_not_a_record_fails_the_check(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, why: str) -> None:
    tool = _tool()
    env = _env(tmp_path)
    _record_file(env).write_text(text)
    assert tool.main(["--check", str(env)]) == 1
    assert why in capsys.readouterr().err


@_needs_script
@_needs_git
def test_the_check_notes_a_checkout_that_has_moved_on(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Not a failure: an app built yesterday is still that app."""
    tool = _tool()
    env, source = _env(tmp_path), _checkout(tmp_path)
    assert tool.main([str(env), "--source", str(source)]) == 0
    (source / "tracked.txt").write_text("two\n")
    _git(source, "commit", "--quiet", "-am", "two")
    capsys.readouterr()
    assert tool.main(["--check", str(env), "--source", str(source)]) == 0
    assert "the app was built from another commit" in capsys.readouterr().out


@_needs_script
def test_a_folder_without_core_is_refused(tmp_path: Path) -> None:
    tool = _tool()
    assert tool.main([str(tmp_path), "--source", str(tmp_path)]) == 2
    (tmp_path / "lib").mkdir()
    assert tool.main([str(tmp_path), "--source", str(tmp_path)]) == 2
    assert tool.main(["--check", str(tmp_path)]) == 2
    env = _env(tmp_path)
    assert tool.main([str(env)]) == 2, "writing needs to be told the source"


@_needs_script
@_needs_git
def test_the_script_runs_as_a_program(tmp_path: Path) -> None:
    """As staging and `check_bundle.sh` start it: `python -I -B <script>`."""
    env, source = _env(tmp_path), _checkout(tmp_path)
    done = subprocess.run([sys.executable, "-I", "-B", str(_SCRIPT), str(env),
                           "--source", str(source)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok    built from " in done.stdout
    done = subprocess.run([sys.executable, "-I", "-B", str(_SCRIPT), "--check", str(env)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


# ---------------------------------------------------------------------------
# 2. Core reads it, and believes it before git
# ---------------------------------------------------------------------------

def _installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
               record: object | None, inside_a_repository: bool = False) -> Path:
    """Core's own folder, moved for the test to an installed copy of it."""
    root = tmp_path / "checkout" if inside_a_repository else tmp_path
    package = root / "dist" / "env" / "lib" / "site-packages" / "mrnaspect"
    package.mkdir(parents=True)
    if inside_a_repository:
        (root / ".git").mkdir()
    if record is not None:
        (package / "_build.json").write_text(
            record if isinstance(record, str) else json.dumps(record))
    provenance._root_for.cache_clear()
    provenance._read_build_record.cache_clear()
    monkeypatch.setattr(provenance, "_REPO_ANCHOR", package)
    return package


def _refuse(*_args, **_kwargs):
    raise AssertionError("git was started")


_RECORD = {"commit": _COMMIT, "dirty": False, "staged_at": "2026-11-19T00:00:00+00:00"}


def test_an_installed_core_reports_the_recorded_commit_without_starting_git(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(tmp_path, monkeypatch, _RECORD)
    monkeypatch.setattr(provenance.subprocess, "run", _refuse)
    assert provenance.git_commit() == _COMMIT
    assert provenance.git_commit(short=True) == _COMMIT[:7]
    assert provenance.git_is_dirty() is False
    assert provenance.build_record() == _RECORD


def test_the_record_is_believed_before_the_repository_around_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`dist/bundle-env` and the built `.app` are inside the checkout. git there answers
    for the checkout as it is NOW; after a pull that is not the code that was copied."""
    _installed(tmp_path, monkeypatch, {**_RECORD, "dirty": True}, inside_a_repository=True)
    monkeypatch.setattr(provenance.subprocess, "run", _refuse)
    assert provenance.git_commit() == _COMMIT
    assert provenance.git_is_dirty() is True


def test_without_a_record_nothing_changes(tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside a repository: no commit, and git is not started (the developer-tools
    dialog). Inside one: git is asked, as before."""
    _installed(tmp_path / "a", monkeypatch, None)
    monkeypatch.setattr(provenance.subprocess, "run", _refuse)
    assert provenance.build_record() is None
    assert provenance.git_commit() is None and provenance.git_is_dirty() is None

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "abc1234\n", "")

    _installed(tmp_path / "b", monkeypatch, None, inside_a_repository=True)
    monkeypatch.setattr(provenance.subprocess, "run", run)
    assert provenance.git_commit(short=True) == "abc1234"
    assert calls == [["git", "rev-parse", "--short", "HEAD"]]
    provenance._root_for.cache_clear()


@pytest.mark.parametrize("record", [
    "not json", "[]", {"commit": "5d24c01", "dirty": False},
    {"commit": _COMMIT, "dirty": "no"}, {"commit": _COMMIT},
])
def test_a_file_that_is_not_a_record_gives_no_commit_and_says_so(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture, record: object) -> None:
    """A result is still written, with no commit, rather than lost at its last step; the
    bundle check is where such a file fails."""
    package = _installed(tmp_path, monkeypatch, record)
    monkeypatch.setattr(provenance.subprocess, "run", _refuse)
    with caplog.at_level("WARNING", logger="mrnaspect.provenance"):
        assert provenance.build_record() is None
        assert provenance.git_commit() is None
    assert str(package / "_build.json") in caplog.text


def test_the_stamp_carries_the_build(tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(tmp_path, monkeypatch, _RECORD)
    monkeypatch.setattr(provenance.subprocess, "run", _refuse)
    stamp = provenance.capture_provenance(
        code={}, now=datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert stamp["git_commit"] == _COMMIT and stamp["git_dirty"] is False
    assert stamp["build"] == _RECORD
    json.dumps(stamp)


def test_in_a_checkout_the_stamp_has_no_build() -> None:
    provenance._read_build_record.cache_clear()
    stamp = provenance.capture_provenance(code={})
    assert "build" in stamp and stamp["build"] is None


@_needs_script
@_needs_git
def test_core_accepts_what_the_script_writes(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Two readers of one file, in two places: held to each other on a real record."""
    tool = _tool()
    env, source = _env(tmp_path), _checkout(tmp_path)
    assert tool.main([str(env), "--source", str(source)]) == 0
    written = json.loads(_record_file(env).read_text())
    assert tool.problem_with(written) is None
    provenance._root_for.cache_clear()
    provenance._read_build_record.cache_clear()
    monkeypatch.setattr(provenance, "_REPO_ANCHOR", _record_file(env).parent)
    assert provenance._BUILD_RECORD_NAME == tool.NAME
    assert provenance.build_record() == written
    provenance._root_for.cache_clear()


# ---------------------------------------------------------------------------
# 3. Where the record may and may not be
# ---------------------------------------------------------------------------

def test_the_source_tree_holds_no_record() -> None:
    """With one beside the package, every result of this checkout would name its commit
    instead of asking git."""
    assert not (Path(provenance.__file__).resolve().parent / "_build.json").exists()


@pytest.mark.skipif(not (_ROOT / ".gitignore").exists(),
                    reason=".gitignore is not in this checkout")
def test_a_record_in_the_source_tree_could_not_be_committed() -> None:
    assert "/mrnaspect/_build.json" in (_ROOT / ".gitignore").read_text().splitlines()


@pytest.mark.skipif(not _STAGE.exists(), reason="scripts/ is not in this checkout")
def test_staging_records_after_the_install_and_asks_core_what_it_reports() -> None:
    """After `pip install`, which may replace the package folder; before the bytecode
    step, which must be the last to run the staged interpreter."""
    text = _STAGE.read_text()
    install = text.index('step "Installing Core for real"')
    record = text.index('scripts/bundle_build_record.py" "$DEST" --source "$ROOT"')
    asked = text.index("print(provenance.git_commit() or")
    compiled = text.index('scripts/bundle_bytecode.py" "$DEST"')
    assert install < record < asked < compiled
    assert "|| fail" in text[record:text.index("\n\n", record)]
