"""M7 regression — resume_from_snapshot must not overwrite a completed run.

Bug (UPDATE_PLAN M7): resume_from_snapshot had no already-finalized guard and always
finalized reason=user_abort, so re-running it on a normally-completed run silently
flipped report.reason run_end -> user_abort and clobbered a good terminal report. The
guard refuses when qc_result.json already exists with reason=run_end, while still
recovering a hard-killed run (snapshot present, no terminal report).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mrnaspect.main import OfflinePipeline, load_config, resume_from_snapshot
from mrnaspect.reporting import summary_writer as SW


def _completed_run(bam_input_dir: Path, curlcake_ref: Path, out_dir: Path) -> None:
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam", "min_reads": 1})
    OfflinePipeline(bam_input_dir, out_dir, cfg).run()


def test_resume_refuses_completed_run(
    bam_input_dir: Path, curlcake_ref: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    _completed_run(bam_input_dir, curlcake_ref, out_dir)
    # A normal completion left both artifacts; report.reason == run_end.
    prior = json.loads((out_dir / SW.RESULT_JSON_NAME).read_text(encoding="utf-8"))
    assert prior["report"]["reason"] == "run_end"

    with pytest.raises(RuntimeError, match="completed normally"):
        resume_from_snapshot(out_dir, min_reads=1)

    # The good terminal record is untouched — still run_end, not rewritten to user_abort.
    after = json.loads((out_dir / SW.RESULT_JSON_NAME).read_text(encoding="utf-8"))
    assert after["report"]["reason"] == "run_end"


def test_resume_recovers_hard_killed_run(
    bam_input_dir: Path, curlcake_ref: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    _completed_run(bam_input_dir, curlcake_ref, out_dir)
    # Simulate a hard kill: the durable snapshot survives, the terminal report does not.
    (out_dir / SW.RESULT_JSON_NAME).unlink()

    resume_from_snapshot(out_dir, min_reads=1)

    recovered = json.loads((out_dir / SW.RESULT_JSON_NAME).read_text(encoding="utf-8"))
    assert recovered["report"]["reason"] == "user_abort"


def test_the_command_printed_on_ctrl_c_recovers_the_report(
    bam_input_dir: Path, curlcake_ref: Path, tmp_path: Path
) -> None:
    """The real-time driver prints a resume command on Ctrl-C; run as printed, it works.

    It names no reference (resume never uses one) and no --output-name (the directory it
    names is the run), so neither may be required.
    """
    from types import SimpleNamespace

    from mrnaspect.main import main
    from mrnaspect.realtime.main import RealtimePipeline

    run_dir = tmp_path / "out" / "run_20261103_120000"
    _completed_run(bam_input_dir, curlcake_ref, run_dir)
    (run_dir / SW.RESULT_JSON_NAME).unlink()              # the hard kill
    notice = RealtimePipeline._abort_notice(SimpleNamespace(output_dir=run_dir)).decode()
    line = next(l for l in notice.splitlines() if "--resume-from-snapshot" in l)
    assert main(line.split("python -m mrnaspect.main", 1)[1].split()) == 0
    recovered = json.loads((run_dir / SW.RESULT_JSON_NAME).read_text(encoding="utf-8"))
    assert recovered["report"]["reason"] == "user_abort"
    assert not [p for p in run_dir.iterdir() if p.is_dir() and p.name.startswith("run_")]


def test_resume_without_a_snapshot_is_an_error_not_a_traceback(
    tmp_path: Path, capsys
) -> None:
    """Pointed at the parent of runs, it names the runs that can be resumed."""
    from mrnaspect.main import main

    parent = tmp_path / "out"
    (parent / "run_a").mkdir(parents=True)
    (parent / "run_a" / SW.LATEST_SNAPSHOT_NAME).write_text("{}", encoding="utf-8")
    assert main(["--output-dir", str(parent), "--resume-from-snapshot"]) == 2
    out = capsys.readouterr().out
    assert "run_a" in out and "--output-name" in out
    assert [p.name for p in parent.iterdir() if p.is_dir()] == ["run_a"]
