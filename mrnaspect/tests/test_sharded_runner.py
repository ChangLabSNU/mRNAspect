"""The sharded test runner reports success only when every shard it started reported.

``scripts/run_tests_sharded.sh`` ran its workers in the background, waited with a bare
``wait`` and read whichever ``shard_<n>.rc`` files it found. A worker that died before
writing its file was not counted: with worker 0 gone and three others passing one test
each, it printed ``3 passed ... across 4 shards`` and exited 0.

The real script is run here on a small stand-in repository, with a stand-in ``pixi`` on
PATH in place of pytest, so the bookkeeping is what is tested.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_tests_sharded.sh"

#: Stands in for `pixi run -e default python -m pytest <files> ...`. FAKE_DIE names a test
#: file whose worker is killed before it can write its result; FAKE_FAIL one that fails;
#: FAKE_SLOW one that takes two seconds. FAKE_SPOIL names a test file whose shard's result
#: file (FAKE_SPOIL_RC) is overwritten with something that is no exit code, once written.
_FAKE_PIXI = """#!/usr/bin/env bash
for a in "$@"; do
  if [ -n "${FAKE_DIE:-}" ] && [[ "$a" == *"$FAKE_DIE" ]]; then kill -9 "$PPID"; sleep 5; fi
  if [ -n "${FAKE_SLOW:-}" ] && [[ "$a" == *"$FAKE_SLOW" ]]; then sleep 2; fi
  if [ -n "${FAKE_SPOIL:-}" ] && [[ "$a" == *"$FAKE_SPOIL" ]]; then
    ( for _ in $(seq 100); do [ -s "$FAKE_SPOIL_RC" ] && break; sleep 0.05; done
      echo oops > "$FAKE_SPOIL_RC" ) >/dev/null 2>&1 &
  fi
  if [ -n "${FAKE_FAIL:-}" ] && [[ "$a" == *"$FAKE_FAIL" ]]; then
    echo "1 failed in 0.01s"; exit 1
  fi
done
echo "1 passed in 0.01s"
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Four test files: one pinned to shard 0 by its name, three spread over shards 1-3."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(_SCRIPT, root / "scripts" / _SCRIPT.name)
    tests = root / "mrnaspect" / "tests"
    tests.mkdir(parents=True)
    for name in ("test_realtime_x.py", "test_a.py", "test_b.py", "test_c.py"):
        (tests / name).write_text("")
    fake = root / "bin" / "pixi"
    fake.parent.mkdir()
    fake.write_text(_FAKE_PIXI)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return root


def _run(repo: Path, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(repo / "scripts" / _SCRIPT.name), "shards", "4"], cwd=repo,
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PATH": f"{repo / 'bin'}{os.pathsep}{os.environ['PATH']}", **env})


def test_all_shards_passing_exits_zero(repo: Path) -> None:
    done = _run(repo)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "4 passed in" in done.stdout and "across 4 shards" in done.stdout
    assert sorted(p.name for p in (repo / "shards").glob("*.rc")) == [
        f"shard_{k}.rc" for k in range(4)]


def test_a_shard_that_dies_before_reporting_fails_the_run(repo: Path) -> None:
    done = _run(repo, FAKE_DIE="test_realtime_x.py")
    assert not (repo / "shards" / "shard_0.rc").exists(), "the stand-in did not kill worker 0"
    assert done.returncode != 0, done.stdout
    assert "passed in" not in done.stdout.split("----")[-1], "no success line for a partial run"
    line = next(l for l in done.stdout.splitlines() if l.startswith("shard_0"))
    assert "NO RESULT" in line and "shards/shard_0.log" in line


def test_a_failing_shard_fails_the_run_and_names_its_log(repo: Path) -> None:
    done = _run(repo, FAKE_FAIL="test_b.py")
    assert done.returncode != 0
    assert "FAILURES" in done.stdout
    assert "failed: see shards/shard_2.log" in done.stdout


def test_a_result_that_is_not_an_exit_code_fails_the_run(repo: Path) -> None:
    """A result file is there and does not hold a number (a full disk, a torn write).

    The first shard is slow, so the runner is still waiting for it while the last one's
    file is spoiled: when its turn comes the file is read as it then is.
    """
    done = _run(repo, FAKE_SLOW="test_realtime_x.py", FAKE_SPOIL="test_c.py",
                FAKE_SPOIL_RC="shards/shard_3.rc")
    assert (repo / "shards" / "shard_3.rc").read_text().strip() == "oops"
    assert done.returncode != 0, done.stdout
    line = next(l for l in done.stdout.splitlines() if l.startswith("shard_3"))
    assert "INVALID RESULT 'oops'" in line and "shards/shard_3.log" in line
    assert "passed in" not in done.stdout.split("----")[-1], "no success line for a partial run"
