"""Every run gets its own subdirectory of --output-dir, and continuing one is explicit.

The defect this closes: artifacts are named by KIND (``qc_result.json``,
``report_run_end.pdf``, ``latest_snapshot.json``), not by run, so a second run against the
same ``--output-dir`` silently landed on top of the first one's results.

The reason the continue paths exist at all: the pod5->BAM cache lives INSIDE the run
directory, so "a fresh directory every time" would make every restart re-basecall from
scratch — on a 20 h run, the whole GPU cost again.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest

from mrnaspect.tests.conftest import run_dir_of
from mrnaspect.main import (
    DEFAULT_STATE_NAME,
    RUN_DIR_PREFIX,
    announce_run_dir,
    main,
    resolve_run_dir,
)


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------

def test_default_is_a_timestamped_subdirectory(tmp_path: Path):
    run_dir = resolve_run_dir(tmp_path)
    assert run_dir.parent == tmp_path
    assert run_dir.name.startswith(RUN_DIR_PREFIX)
    assert not run_dir.exists(), "resolution must not create it; the driver does"


def test_two_resolutions_never_collide(tmp_path: Path):
    """Two starts inside the same second would otherwise share a directory."""
    first = resolve_run_dir(tmp_path)
    first.mkdir()
    second = resolve_run_dir(tmp_path)
    assert second != first


def test_an_explicit_name_is_used_as_given(tmp_path: Path):
    assert resolve_run_dir(tmp_path, output_name="qk_batch_3") == tmp_path / "qk_batch_3"


def test_an_existing_name_is_continued_not_refused(tmp_path: Path):
    """Reusing the name IS the continue mechanism — it must not error."""
    (tmp_path / "again").mkdir()
    assert resolve_run_dir(tmp_path, output_name="again") == tmp_path / "again"


def test_continue_latest_picks_the_most_recent(tmp_path: Path):
    old = tmp_path / f"{RUN_DIR_PREFIX}20260101_000000"
    new = tmp_path / f"{RUN_DIR_PREFIX}20260202_000000"
    for d in (old, new):
        d.mkdir()
    import os
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    assert resolve_run_dir(tmp_path, continue_latest=True) == new


def test_continue_latest_with_nothing_to_continue_says_so(tmp_path: Path):
    with pytest.raises(ValueError, match="no run_. directory"):
        resolve_run_dir(tmp_path, continue_latest=True)


def test_the_two_selectors_are_mutually_exclusive(tmp_path: Path):
    with pytest.raises(ValueError, match="both given"):
        resolve_run_dir(tmp_path, output_name="x", continue_latest=True)


# ---------------------------------------------------------------------------
# what continuing inherits, said out loud
# ---------------------------------------------------------------------------

def test_continuing_warns_and_counts_the_cache_it_inherits(tmp_path: Path, caplog):
    run_dir = tmp_path / "prev"
    run_dir.mkdir()
    (run_dir / DEFAULT_STATE_NAME).write_text("a.pod5\t/x/a.bam\nb.pod5\t/x/b.bam\n")
    with caplog.at_level(logging.WARNING):
        announce_run_dir(run_dir, existed=True)
    assert "CONTINUING" in caplog.text
    assert "2 cached basecall" in caplog.text


def test_a_new_directory_is_announced_without_a_warning(tmp_path: Path, caplog):
    with caplog.at_level(logging.WARNING):
        announce_run_dir(tmp_path / "fresh", existed=False)
    assert "CONTINUING" not in caplog.text


# ---------------------------------------------------------------------------
# the defect itself, end to end
# ---------------------------------------------------------------------------

def test_two_runs_into_one_output_dir_do_not_overwrite_each_other(
    tmp_path: Path, bam_input_dir: Path, curlcake_ref: Path, capsys
):
    out = tmp_path / "out"
    codes = []
    for _ in range(2):
        codes.append(main(["--input-dir", str(bam_input_dir), "--output-dir", str(out),
                           "--reference", str(curlcake_ref), "--mode", "bam",
                           "--min-reads", "1"]))
        time.sleep(1.05)  # distinct timestamps; the suffix path is covered separately
    assert codes == [0, 0]
    runs = sorted(d for d in out.glob(f"{RUN_DIR_PREFIX}*") if d.is_dir())
    assert len(runs) == 2, f"expected one directory per run, got {[d.name for d in runs]}"
    for d in runs:
        assert (d / "qc_result.json").exists()
        assert (d / "run.log").exists()
    # The run dir now travels in the JSON result object on stdout, not a `run_dir=` line
    # (A1, 2026-09-19). Both runs emit one; the last is the one capsys holds.
    emitted = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert len(emitted) == 2 and all(e["run_dir"] for e in emitted), emitted


def test_a_named_run_continues_in_place(
    tmp_path: Path, bam_input_dir: Path, curlcake_ref: Path
):
    out = tmp_path / "out"
    argv = ["--input-dir", str(bam_input_dir), "--output-dir", str(out),
            "--reference", str(curlcake_ref), "--mode", "bam", "--min-reads", "1",
            "--output-name", "fixed"]
    assert main(argv) == 0
    assert main(argv) == 0
    assert [d.name for d in out.iterdir() if d.is_dir()] == ["fixed"]


# `caplog` cannot be used for these: `_setup_logging` REPLACES every root handler, pytest's
# capture handler included, so nothing logged after it reaches caplog. A "not in caplog.text"
# assertion would then pass for an empty string — green for the wrong reason. run.log is the
# artifact an operator actually reads, and the file handler is always at INFO.

def test_a_fresh_run_is_not_announced_as_a_continued_one(
    tmp_path: Path, bam_input_dir: Path, curlcake_ref: Path
):
    """`_setup_logging` creates the run directory to put run.log in it.

    So a check made after that point always finds the directory present, and every fresh
    run was announced as "CONTINUING an existing one — 0 cached basecall(s)". The caller
    has to sample existence BEFORE logging is set up.
    """
    out = tmp_path / "out"
    code = main(["--input-dir", str(bam_input_dir), "--output-dir", str(out),
                 "--reference", str(curlcake_ref), "--mode", "bam", "--min-reads", "1"])
    assert code == 0
    log = (run_dir_of(out) / "run.log").read_text(encoding="utf-8")
    assert "Run directory (new)" in log
    assert "CONTINUING" not in log


def test_a_reused_run_name_IS_announced_as_continued(
    tmp_path: Path, bam_input_dir: Path, curlcake_ref: Path
):
    """The other half — the warning must still fire when it is true."""
    out = tmp_path / "out"
    argv = ["--input-dir", str(bam_input_dir), "--output-dir", str(out),
            "--reference", str(curlcake_ref), "--mode", "bam", "--min-reads", "1",
            "--output-name", "same"]
    assert main(argv) == 0
    assert main(argv) == 0
    log = (out / "same" / "run.log").read_text(encoding="utf-8")
    assert "CONTINUING" in log
