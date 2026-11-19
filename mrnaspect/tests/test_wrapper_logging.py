"""A wrapper's tool output belongs in a file, and a FAILURE still belongs on the console.

The noise this pins down: ``run_align.sh`` invokes samtools five times and the pinned
samtools emits three dynamic-linker warnings per invocation, so one POD5 put ~20 junk
lines on the operator's console and a 402-POD5 replay put ~8,000 there. The wrappers
inherited stdout/stderr, so none of it was the driver's to route.

The trap in fixing that is over-correcting into silence. These tests pin BOTH halves:
routine output leaves the console, and a non-zero exit still reaches it.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from mrnaspect.wrappers._shell import run_logged


def test_routine_output_goes_to_the_log_and_not_the_console(
    tmp_path: Path, capfd: pytest.CaptureFixture
) -> None:
    """Happy path: both streams land in the file, nothing reaches the terminal."""
    log = tmp_path / "stage.log"
    returned = run_logged(
        ["bash", "-c", "echo to-stdout; echo to-stderr >&2"], log, stage="align"
    )

    assert returned == log
    body = log.read_text()
    # Combined, because a tool's complaint is only interpretable next to the step it
    # was complaining about — splitting the streams reorders them.
    assert "to-stdout" in body and "to-stderr" in body

    captured = capfd.readouterr()          # capfd, not capsys: this is a CHILD process
    assert captured.out == "" and captured.err == ""


def test_failure_reaches_the_console_and_still_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Edge case: redirecting output must not be why a failure goes unexplained.

    A wrapper that swallowed its tool's stderr would turn a crashed alignment into a
    silently short BAM — a wrong number rather than a crash, which is the one outcome
    the paper-grade rules refuse.
    """
    log = tmp_path / "stage.log"
    with caplog.at_level(logging.ERROR):
        with pytest.raises(subprocess.CalledProcessError):
            run_logged(
                ["bash", "-c", "echo 'samtools: truncated file' >&2; exit 3"],
                log, stage="align",
            )

    assert "samtools: truncated file" in caplog.text   # the tool's own words survive
    assert "exit 3" in caplog.text
    assert str(log) in caplog.text                     # and where to read the rest
    assert "samtools: truncated file" in log.read_text()


def test_empty_output_is_not_an_error(tmp_path: Path) -> None:
    """A silent tool leaves an empty log, not a crash in the tail reader."""
    log = tmp_path / "stage.log"
    run_logged(["bash", "-c", "true"], log, stage="align")
    assert log.read_text() == ""


def test_log_is_written_live_not_buffered_until_exit(tmp_path: Path) -> None:
    """The reason for a file handle over ``capture_output``.

    A 20 h basecall must be inspectable with ``tail -f`` while it runs; a pipe would
    hold the whole stream in the parent's memory and reveal it only at exit. The child
    here blocks until it sees its own line already on disk, so this deadlocks (and the
    test fails on timeout) if the output is buffered away from the file.
    """
    log = tmp_path / "stage.log"
    script = (
        f"echo started; "
        f"for i in $(seq 100); do grep -q started {log} && exit 0; sleep 0.05; done; exit 1"
    )
    run_logged(["bash", "-c", script], log, stage="basecall")
    assert "started" in log.read_text()
