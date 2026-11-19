"""The progress heartbeat: proof that a working run does not look like a hung one.

Measured on 2026-09-16: after one interim warning the console said nothing for minutes
while the pipeline was basecalling normally. "It looks stopped" is the correct reaction to
a terminal that has gone silent, and the operator's next move — Ctrl-C, or kill -9 — costs
a report.

Two design choices are what these tests pin, because both are easy to undo by accident:

* progress has its OWN logger, so it shows without ``-v`` while the console stays at
  WARNING. Raising the whole console to INFO is not the fix — a fast feed emits a "Ready:"
  line per POD5 and buries everything.
* the heartbeat runs on its OWN thread, because the main loop is blocked inside a basecall
  for tens of seconds at a time — exactly when the reassurance is needed.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pytest

from mrnaspect.main import PROGRESS_LOGGER, RUN_LOG_NAME, _setup_logging
from mrnaspect.tests.test_realtime_pipeline import _realtime


@pytest.fixture
def clean_logging():
    """Snapshot and restore global logging state — _setup_logging mutates it."""
    root, prog = logging.getLogger(), logging.getLogger(PROGRESS_LOGGER)
    saved = (list(root.handlers), root.level,
             list(prog.handlers), prog.level, prog.propagate)
    yield
    root.handlers, root.level = saved[0], saved[1]
    prog.handlers, prog.level, prog.propagate = saved[2], saved[3], saved[4]


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


# ---------------------------------------------------------------------------
# the line
# ---------------------------------------------------------------------------

def test_the_progress_line_says_where_the_run_is(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
):
    run_dir = tmp_path / "run"
    (run_dir / "pod5").mkdir(parents=True)
    pipe = _realtime(run_dir, tmp_path / "out", curlcake_ref, fixture_bam)
    pipe.progress.observed.update({"a.pod5", "b.pod5"})
    pipe.progress.ingested.add("a.pod5")
    pipe._current_input = "b.pod5"

    line = pipe._progress_line()
    assert "1/2 inputs done" in line
    assert "queue" in line
    assert "primary reads" in line
    assert "b.pod5" in line


def test_the_line_survives_having_nothing_to_report(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
):
    """At BOOT there is no current input and no lag estimate; it must not blow up."""
    run_dir = tmp_path / "run"
    (run_dir / "pod5").mkdir(parents=True)
    pipe = _realtime(run_dir, tmp_path / "out", curlcake_ref, fixture_bam)
    assert "0/0 inputs done" in pipe._progress_line()


# ---------------------------------------------------------------------------
# the thread — the whole reason it is not a timer in the main loop
# ---------------------------------------------------------------------------

def test_it_reports_while_the_main_thread_is_blocked(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
):
    run_dir = tmp_path / "run"
    (run_dir / "pod5").mkdir(parents=True)
    pipe = _realtime(run_dir, tmp_path / "out", curlcake_ref, fixture_bam,
                     progress_interval_s=0.05)
    collector = _Collect()
    prog = logging.getLogger(PROGRESS_LOGGER)
    prog.addHandler(collector)
    try:
        beat = threading.Thread(target=pipe._heartbeat, daemon=True)
        beat.start()
        time.sleep(0.35)          # the main thread is busy, as it is during a basecall
        pipe._heartbeat_stop.set()
        beat.join(timeout=2.0)
    finally:
        prog.removeHandler(collector)

    assert len(collector.lines) >= 2, (
        "a blocked main thread must not silence the heartbeat — that is the case it is for")
    assert all("[QC]" in line for line in collector.lines)


def test_zero_disables_it(tmp_path: Path, fixture_bam: Path, curlcake_ref: Path):
    run_dir = tmp_path / "run"
    (run_dir / "pod5").mkdir(parents=True)
    pipe = _realtime(run_dir, tmp_path / "out", curlcake_ref, fixture_bam,
                     progress_interval_s=0)
    collector = _Collect()
    prog = logging.getLogger(PROGRESS_LOGGER)
    prog.addHandler(collector)
    try:
        beat = threading.Thread(target=pipe._heartbeat, daemon=True)
        beat.start()
        beat.join(timeout=2.0)
        assert not beat.is_alive(), "interval 0 must return, not spin"
    finally:
        prog.removeHandler(collector)
    assert collector.lines == []


# ---------------------------------------------------------------------------
# the wiring
# ---------------------------------------------------------------------------

def test_progress_is_visible_without_verbose_and_not_doubled_with_it(
    tmp_path: Path, clean_logging
):
    """The point of a separate logger: shown either way, printed once either way."""
    for verbose in (False, True):
        out = tmp_path / f"out_{verbose}"
        _setup_logging(out, verbose)
        prog = logging.getLogger(PROGRESS_LOGGER)

        assert prog.propagate is False, (
            "propagation would print every progress line twice under -v")
        streams = [h for h in prog.handlers if isinstance(h, logging.StreamHandler)
                   and not isinstance(h, logging.FileHandler)]
        assert len(streams) == 1 and streams[0].level == logging.INFO, (
            "progress needs exactly one console handler, always at INFO")

        # the console the REST of the pipeline logs to is untouched
        root_streams = [h for h in logging.getLogger().handlers
                        if isinstance(h, logging.StreamHandler)
                        and not isinstance(h, logging.FileHandler)]
        assert root_streams[0].level == (logging.INFO if verbose else logging.WARNING)


def test_progress_also_reaches_run_log(tmp_path: Path, clean_logging):
    """run.log is the forensic record; a status channel that skipped it would be a gap."""
    out = tmp_path / "out"
    _setup_logging(out, verbose=False)
    logging.getLogger(PROGRESS_LOGGER).info("[QC] a heartbeat")
    for h in logging.getLogger(PROGRESS_LOGGER).handlers:
        h.flush()
    assert "[QC] a heartbeat" in (out / RUN_LOG_NAME).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The OFFLINE driver, which this file did not cover until 2026-09-23
# ---------------------------------------------------------------------------
# Everything above drives the real-time pipeline. The offline driver set the progress
# channel up in `_setup_logging` and then never emitted on it — every line went to the
# module logger, which the console filters to WARNING without `-v`. So `mrnaspect` printed
# NOTHING for the whole run.
#
# It surfaced on the first macOS pod5 run: a 280 MB POD5 basecalling on Metal, and a
# terminal that had said nothing since the pixi banner. The comment beside the call site
# already explained why the announcement had to come BEFORE the basecall — "three minutes
# of silence during the longest step — indistinguishable from a hang" — so the intent was
# right and only the delivery was missing.

def test_the_offline_driver_talks_to_the_console_without_v(tmp_path, fixture_bam,
                                                           curlcake_ref, capsys):
    """Asserted on what reaches the CONSOLE, not on the logger's records.

    A records-based check would pass even if the handler were filtered back out, and being
    filtered out is exactly the failure. `_setup_logging` builds its StreamHandler inside
    the call, so under capsys it binds the captured stream and this sees what an operator
    sees.
    """
    from mrnaspect.main import main as offline_main
    from mrnaspect.tests.conftest import write_distinct_bam

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for i in range(2):
        write_distinct_bam(fixture_bam, in_dir / f"in_{i}.bam", f"i{i}")

    code = offline_main(["--input-dir", str(in_dir), "--output-dir", str(tmp_path / "out"),
                         "--reference", str(curlcake_ref), "--mode", "bam",
                         "--min-reads", "1"])          # NOTE: no -v
    assert code == 0
    err = capsys.readouterr().err
    assert "2 input(s)" in err, f"the run never said how much work there was:\n{err}"
    # One line per input, so a silent gap means a stuck input rather than a quiet run.
    assert err.count("-> 166 reads") == 2, err
    assert "[1/2]" in err and "[2/2]" in err, err


def test_the_progress_logger_is_named_for_the_package():
    """It was `phase2.progress` for six days after the package left the prototype's directory, and
    that string was printed into every run.log."""
    assert PROGRESS_LOGGER == "mrnaspect.progress"
