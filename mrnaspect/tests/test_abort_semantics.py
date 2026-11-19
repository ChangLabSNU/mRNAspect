"""Abort semantics: Ctrl-C stops the run, and an abort is not a failure.

Both halves are regressions from a real drill on 2026-09-16:

* the abort flag was checked by the live loop but NOT by the drain that follows it, so a
  Ctrl-C on a backlogged run went on to basecall the whole queue — 382 inputs, ~30 s each;
* the inputs it did not get to were recorded as ``failed_basecall``, so ``qc_result.json``
  blamed the data for a decision the operator made (measured: 376 of them).
"""

from __future__ import annotations

import os
import signal
import threading
from pathlib import Path

import pytest

from mrnaspect.realtime.main import RealtimePipeline
from mrnaspect.realtime.sealing import build_run_accounting
from mrnaspect.realtime.watcher import FileReadyEvent
from mrnaspect.runtime.lifecycle import (
    AbortFlag,
    install_signal_handlers,
    restore_signal_handlers,
)
from mrnaspect.tests.test_realtime_pipeline import _realtime


def _queue_events(pipe: RealtimePipeline, names: list[str]) -> None:
    for name in names:
        pipe._queue.put(FileReadyEvent(path=pipe.pod5_dir / name, size=1, detected_at=0.0))


def _record_handled(pipe: RealtimePipeline) -> list[str]:
    handled: list[str] = []
    pipe._handle = lambda path: handled.append(Path(path).name)  # type: ignore[assignment]
    return handled


# ---------------------------------------------------------------------------
# the drain
# ---------------------------------------------------------------------------

def test_abort_does_not_process_the_queued_backlog(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
):
    run_dir = tmp_path / "run"
    (run_dir / "pod5").mkdir(parents=True)
    pipe = _realtime(run_dir, tmp_path / "out", curlcake_ref, fixture_bam)
    handled = _record_handled(pipe)
    names = [f"q_{i}.pod5" for i in range(5)]
    _queue_events(pipe, names)

    pipe._abort.request()      # as if Ctrl-C had already landed
    pipe.live()

    assert handled == [], "an aborted run must not keep basecalling its backlog"
    assert pipe.progress.aborted == set(names)
    # ...and recorded as observed, or build_run_accounting would refuse the partition
    assert set(names) <= pipe.progress.observed


def test_run_end_still_drains_the_queue(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
):
    """The other reason the loop ends: those inputs DO belong in the report."""
    run_dir = tmp_path / "run"
    (run_dir / "pod5").mkdir(parents=True)
    pipe = _realtime(run_dir, tmp_path / "out", curlcake_ref, fixture_bam)
    handled = _record_handled(pipe)
    names = [f"q_{i}.pod5" for i in range(3)]
    _queue_events(pipe, names)

    pipe._run_end.set()
    pipe.live()

    assert handled == names
    assert pipe.progress.aborted == set()


# ---------------------------------------------------------------------------
# the accounting
# ---------------------------------------------------------------------------

def test_aborted_inputs_are_not_reported_as_failures():
    acc = build_run_accounting(
        observed={"a.pod5", "b.pod5", "c.pod5"},
        ingested={"a.pod5"},
        failed={"b.pod5"},
        unstable=set(),
        aborted={"c.pod5"},
    )
    assert acc["failed_basecall"] == ["b.pod5"]
    assert acc["aborted_unprocessed"] == ["c.pod5"]


def test_a_real_failure_keeps_its_disposition_over_abort():
    """Precedence: something that reached a stage and broke is not merely 'not reached'."""
    acc = build_run_accounting(
        observed={"x.pod5"}, ingested=set(), failed={"x.pod5"}, unstable=set(),
        aborted={"x.pod5"},
    )
    assert acc["failed_basecall"] == ["x.pod5"]
    assert acc["aborted_unprocessed"] == []


def test_an_unaccounted_input_still_fails_loudly():
    """The partition check must not have been widened by the new bucket."""
    with pytest.raises(RuntimeError, match="does not partition"):
        build_run_accounting(observed={"ghost.pod5"}, ingested=set(), failed=set(),
                             unstable=set())


# ---------------------------------------------------------------------------
# the notice
# ---------------------------------------------------------------------------

def test_ctrl_c_says_something_on_every_press(tmp_path: Path, capfd):
    """Silence is what makes an operator press again, or reach for kill -9.

    The handler cannot use logging (it may run mid-lock), so the message goes to fd 2 via
    os.write. capfd captures at the fd level, which is exactly what that needs.
    """
    flag = AbortFlag()
    prev = install_signal_handlers(flag, notice=b"[QCRNA] Abort requested.\n")
    try:
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)
    finally:
        restore_signal_handlers(prev)

    assert flag.is_set()
    assert capfd.readouterr().err.count("[QCRNA] Abort requested.") == 2


def test_the_notice_names_the_hard_exit_and_the_recovery(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
):
    run_dir = tmp_path / "run"
    (run_dir / "pod5").mkdir(parents=True)
    out = tmp_path / "out"
    pipe = _realtime(run_dir, out, curlcake_ref, fixture_bam)
    notice = pipe._abort_notice().decode()
    assert str(os.getpid()) in notice          # which process to kill
    assert "--resume-from-snapshot" in notice  # how to get the report back afterwards
    assert str(out) in notice


def test_a_handler_with_no_notice_stays_silent(capfd):
    """The notice is opt-in: a programmatic caller must not have stderr written under it."""
    flag = AbortFlag()
    prev = install_signal_handlers(flag)
    try:
        signal.raise_signal(signal.SIGINT)
    finally:
        restore_signal_handlers(prev)
    assert flag.is_set()
    assert capfd.readouterr().err == ""


# ---------------------------------------------------------------------------
# signal isolation of the stage children
# ---------------------------------------------------------------------------

def test_align_is_isolated_from_the_terminal_signal(monkeypatch, tmp_path: Path):
    """A Ctrl-C must not kill the align child and turn a good input into a FAILED one.

    Measured before the fix: the align subprocess `died with <Signals.SIGINT: 2>`, the
    driver's `on_error: skip` recorded the input as failed, and the report then said
    "INCOMPLETE: 1 input(s) failed". A signal is a fact about the operator, not the data.
    """
    from mrnaspect.wrappers import aligner

    seen: dict = {}

    def fake_run_logged(cmd, log_path, *, stage, **kw):
        seen["stage"], seen["kw"] = stage, kw
        (tmp_path / "aligned_p.bam").write_bytes(b"")
        return log_path

    monkeypatch.setattr(aligner, "run_logged", fake_run_logged)
    aligner.shell_align("p", tmp_path / "ref.fa", tmp_path)
    assert seen["stage"] == "align"
    assert seen["kw"].get("start_new_session") is True


def test_basecall_is_isolated_too(monkeypatch, tmp_path: Path):
    """The half that was already protected — pinned so the pair cannot drift apart."""
    from mrnaspect.wrappers import basecaller

    seen: dict = {}

    def fake_run_logged(cmd, log_path, *, stage, **kw):
        seen["kw"] = kw
        (tmp_path / "basecall_x").mkdir(exist_ok=True)
        return log_path

    monkeypatch.setattr(basecaller, "run_logged", fake_run_logged)
    # A client path is now required: shell_basecall resolves it BEFORE starting the child,
    # so a failure is one message rather than one per POD5. The fake never runs it, but it
    # must resolve — and since 2026-09-23 resolving means an EXECUTABLE file, not merely an
    # existing one, so the stub is chmod'd. A non-executable "client" was never a thing a
    # real bundle contains; this fixture had been describing a state that cannot occur.
    fake_client = tmp_path / "ont_basecall_client"
    fake_client.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_client.chmod(0o755)
    basecaller.shell_basecall(tmp_path / "x.pod5", tmp_path, client=str(fake_client))
    assert seen["kw"].get("start_new_session") is True


# ---------------------------------------------------------------------------
# the OTHER backlog path: sealing
# ---------------------------------------------------------------------------

def test_sealing_stops_between_files_not_just_between_polls(tmp_path: Path):
    """The path that made Ctrl-C look ignored for 30 minutes.

    When QC attaches after a run has ended, ``live()`` returns almost immediately and the
    whole backlog is handed over by the SWEEP instead. Everything outstanding is already
    stable, so one pass walks the entire list calling ``on_ready`` — a ~30 s basecall each —
    and the outer loop's stop check is never reached again. Measured: 68 files, Ctrl-C at
    file 9, still going 4 minutes later.
    """
    import threading

    from mrnaspect.realtime.sealing import RunSealer

    pod5_dir = tmp_path / "pod5"
    pod5_dir.mkdir()
    for i in range(6):
        (pod5_dir / f"f{i}.pod5").write_bytes(b"x" * 100)

    stop = threading.Event()
    handled: list[str] = []

    def on_ready(path: Path) -> None:
        handled.append(path.name)
        if len(handled) == 2:
            stop.set()      # as if the operator pressed Ctrl-C during this handover

    sealer = RunSealer(pod5_dir, poll_interval_s=0.0, stable_polls=2, budget_s=30.0)
    report = sealer.sweep(already_handled=set(), on_ready=on_ready, stop=stop)

    assert len(handled) == 2, f"the sweep kept working after the abort: {handled}"
    assert len(report.aborted) == 4
    assert report.unstable_at_seal == [], (
        "files we never attempted are not 'unstable' — that word claims the INSTRUMENT "
        "was still writing them")


def test_sealing_without_an_abort_is_unchanged(tmp_path: Path):
    """Regression guard: the normal path must still hand over everything."""
    from mrnaspect.realtime.sealing import RunSealer

    pod5_dir = tmp_path / "pod5"
    pod5_dir.mkdir()
    for i in range(4):
        (pod5_dir / f"f{i}.pod5").write_bytes(b"x" * 100)
    handled: list[str] = []
    sealer = RunSealer(pod5_dir, poll_interval_s=0.0, stable_polls=2, budget_s=30.0)
    report = sealer.sweep(already_handled=set(), on_ready=lambda p: handled.append(p.name))
    assert len(handled) == 4
    assert report.aborted == []


def test_an_abort_during_sealing_is_not_filed_as_a_clean_run_end(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
):
    """`reason` is decided before the sweep; without the re-check it stays RUN_END."""
    from mrnaspect.runtime.lifecycle import FinalizeReason

    run_dir = tmp_path / "run"
    (run_dir / "pod5").mkdir(parents=True)
    pipe = _realtime(run_dir, tmp_path / "out", curlcake_ref, fixture_bam)

    seen: dict = {}
    pipe.boot = lambda: None
    pipe.replay = lambda: None
    pipe.live = lambda: None
    pipe.seal = lambda: pipe._abort.request()   # the operator stops mid-sweep
    pipe.finalize = lambda reason: seen.setdefault("reason", reason) or {"status": "report"}

    pipe.run()
    assert seen["reason"] is FinalizeReason.USER_ABORT


# ---------------------------------------------------------------------------
# what the READER of the report is told
# ---------------------------------------------------------------------------

def _notice(failed=(), aborted=(), total=None):
    from mrnaspect.reporting.reporter import CoAReporter
    import tempfile

    rep = CoAReporter(Path(tempfile.mkdtemp()), min_reads=1)
    rep.note_incomplete_inputs(list(failed), aborted_inputs=list(aborted),
                               total_inputs=total)
    return rep._incomplete_notice()


def test_aborted_inputs_are_not_described_as_failures_to_the_reader():
    """The banner argues about BIAS, so the wrong word attaches the wrong mechanism.

    The failure explanation — OOM takes the largest POD5s, basecall failures cluster on
    abnormal signal — is a claim that the loss is data-selected. An abort is selected by
    arrival time instead, and saying "failed" would assert a mechanism that is not there.
    """
    notice = _notice(aborted=["c.pod5", "d.pod5"], total=10)
    assert "run was stopped" in notice
    assert "failed to process" not in notice
    assert "time suffix" in notice          # the skew that IS there
    assert "out-of-memory" not in notice    # the one that is not


def test_real_failures_keep_their_own_explanation():
    notice = _notice(failed=["a.pod5"], total=10)
    assert "Failed to process (1)" in notice
    assert "out-of-memory" in notice
    assert "run was stopped" not in notice


def test_both_kinds_are_reported_separately_and_counted_together():
    notice = _notice(failed=["a.pod5"], aborted=["b.pod5", "c.pod5"], total=10)
    assert "3 inputs are not represented" in notice
    assert "Failed to process (1)" in notice
    assert "run was stopped (2)" in notice


def test_a_complete_run_gets_no_banner():
    assert _notice() is None
