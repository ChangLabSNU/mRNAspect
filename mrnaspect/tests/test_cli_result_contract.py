"""Every exit path of both CLIs emits ONE JSON object with the SAME keys.

This is the guard that stops the drift A1 fixed. Before 2026-09-19 the two entry points
hand-rolled their own final line and had grown three different shapes — a normal offline
run, a `--resume-from-snapshot` run (no mapped_reads, no run_dir) and every failure
(`ERROR: ...` prose, also on stdout). None of it was pinned, which is why nobody noticed.

The contract, in one sentence: **stdout is one JSON object per invocation, always the
same keys; stderr is the human sentence; the exit code is 0 for a verdict, 1 for a run
that could not produce one, 2 for a command that was never going to work.**

The key set is checked as an EXACT set, not a subset. A subset assertion would pass while
a new field appeared on only the success path, which is precisely how the old format
drifted apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mrnaspect.main import main as offline_main
from mrnaspect.realtime.main import main as realtime_main
from mrnaspect.runtime.cli_result import RESULT_KEYS
from mrnaspect.tests.conftest import write_distinct_bam


def _emitted(capsys) -> dict:
    """The single JSON object on stdout, with its key set checked."""
    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout must carry exactly one object, got {lines!r}"
    obj = json.loads(lines[0])
    assert set(obj) == set(RESULT_KEYS), (
        f"key set drifted: missing {set(RESULT_KEYS) - set(obj)}, "
        f"extra {set(obj) - set(RESULT_KEYS)}")
    # The human sentence belongs on stderr; anything there is fine except emptiness,
    # which would mean an operator running by hand sees nothing at all.
    assert captured.err.strip(), "nothing was said to the person running the command"
    return obj


def _bam_dir(tmp_path: Path, fixture_bam: Path, name: str = "a.bam") -> Path:
    d = tmp_path / "in"
    d.mkdir(parents=True, exist_ok=True)
    write_distinct_bam(fixture_bam, d / name, Path(name).stem)
    return d


# --- the five exit paths -----------------------------------------------------

def test_a_normal_run_emits_a_verdict(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, capsys
) -> None:
    code = offline_main(["--input-dir", str(_bam_dir(tmp_path, fixture_bam)),
                         "--output-dir", str(tmp_path / "out"),
                         "--reference", str(curlcake_ref),
                         "--mode", "bam", "--min-reads", "1"])
    obj = _emitted(capsys)
    assert code == 0 and obj["exit_code"] == 0 and obj["status"] == "report"
    assert obj["mapped_reads"] == 166 and obj["error"] is None
    # What a GUI actually reads. Both must be absolute and on disk — a relative path
    # would resolve against the GUI's cwd, not the run's.
    for key in ("report", "qc_result", "run_dir"):
        assert Path(obj[key]).is_absolute() and Path(obj[key]).exists(), (key, obj[key])


def test_too_few_reads_is_a_verdict_not_a_failure(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, capsys
) -> None:
    """`insufficient` exits 0: the run finished, and "not enough reads" IS the answer."""
    code = offline_main(["--input-dir", str(_bam_dir(tmp_path, fixture_bam)),
                         "--output-dir", str(tmp_path / "out"),
                         "--reference", str(curlcake_ref),
                         "--mode", "bam", "--min-reads", "100000"])
    obj = _emitted(capsys)
    assert code == 0 and obj["exit_code"] == 0
    assert obj["status"] in ("report", "insufficient") and obj["error"] is None


def test_a_bad_reference_exits_one_with_the_reason_in_the_object(
    tmp_path: Path, fixture_bam: Path, capsys
) -> None:
    bad = tmp_path / "not_a_reference.fasta"
    bad.write_text("this is not a FASTA\n", encoding="utf-8")
    code = offline_main(["--input-dir", str(_bam_dir(tmp_path, fixture_bam)),
                         "--output-dir", str(tmp_path / "out"),
                         "--reference", str(bad), "--mode", "bam"])
    obj = _emitted(capsys)
    assert code == 1 and obj["exit_code"] == 1 and obj["status"] == "error"
    assert obj["error"] and obj["report"] is None and obj["mapped_reads"] is None


def test_a_usage_mistake_exits_two(tmp_path: Path, curlcake_ref: Path, capsys) -> None:
    """No --input-dir and no --resume-from-snapshot: nothing to run.

    This used to `raise SystemExit(str)`, which exits 1 and puts prose on stderr with no
    object at all — the one path a caller could not parse. It is a command that was never
    going to work, so it joins the other 2s.
    """
    code = offline_main(["--output-dir", str(tmp_path / "out"),
                         "--reference", str(curlcake_ref), "--mode", "bam"])
    obj = _emitted(capsys)
    assert code == 2 and obj["exit_code"] == 2 and obj["status"] == "error"


def test_every_input_failing_exits_one_although_a_report_rendered(
    tmp_path: Path, curlcake_ref: Path, capsys
) -> None:
    """A rendered shell over no data is not a verdict — see the override in main()."""
    d = tmp_path / "in"
    d.mkdir()
    (d / "broken.bam").write_bytes(b"not a bam at all")
    code = offline_main(["--input-dir", str(d), "--output-dir", str(tmp_path / "out"),
                         "--reference", str(curlcake_ref),
                         "--mode", "bam", "--min-reads", "1"])
    obj = _emitted(capsys)
    assert code == 1 and obj["exit_code"] == 1
    # The status still reflects what the reporter did; the exit code carries the verdict.
    assert obj["run_dir"] and obj["qc_result"]


# --- both entry points, one contract -----------------------------------------

def test_the_realtime_cli_refuses_with_the_same_object(tmp_path: Path, capsys) -> None:
    """The realtime driver had its own copy of this logic, which is how they drifted."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("reference: /placeholder/ref.fasta\n", encoding="utf-8")
    (tmp_path / "run").mkdir()
    code = realtime_main(["--input-dir", str(tmp_path / "run"),
                          "--output-dir", str(tmp_path / "out"), "--config", str(cfg)])
    obj = _emitted(capsys)
    assert code == 2 and obj["exit_code"] == 2 and obj["status"] == "error"
    assert "needs to be told what to follow" in obj["error"]


@pytest.mark.parametrize("entry", [offline_main, realtime_main], ids=["offline", "realtime"])
def test_both_entry_points_agree_on_the_key_set(entry, tmp_path: Path, capsys) -> None:
    """The same refusal through both drivers: --continue-latest with nothing to continue.

    Chosen because `resolve_run_dir` raises it and both `main()`s call that first and
    identically, so any divergence in how they report a refusal surfaces here. It also
    fires BEFORE a run directory exists, which is the one case where `run_dir` is
    legitimately null — the key must still be present and the object still parseable.

    (My first attempt used a non-directory as the output parent. That does not reach
    `emit_error` at all: `resolve_run_dir` raises ValueError only for the two selector
    mistakes, and a bad parent surfaces later as NotADirectoryError out of `mkdir`. Worth
    a finding of its own — see the log entry — not worth smuggling into this test.)
    """
    code = entry(["--input-dir", str(tmp_path), "--output-dir", str(tmp_path / "out"),
                  "--reference", "/placeholder/ref.fasta", "--continue-latest"])
    obj = _emitted(capsys)
    assert code == 2 and obj["exit_code"] == 2 and obj["status"] == "error"
    assert obj["run_dir"] is None and obj["error"]


def test_every_path_in_the_object_agrees_with_the_others(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, capsys
) -> None:
    """One run must not be described under two different roots.

    Driven through a SYMLINKED output directory, because that is how this was found:
    macOS answers `/tmp` as `/private/tmp`, and the first real GUI run printed
    `run_dir` and `qc_result` under `/tmp/...` while `report` came back
    `/private/tmp/...` — `report` was the only field being resolved. A consumer that
    compares or displays them together sees one run in two places.

    The check is that `report` and `qc_result` live UNDER `run_dir` as strings. That
    holds only if all three are canonicalised the same way, and it fails for any future
    field that forgets to.
    """
    real = tmp_path / "real_out"
    real.mkdir()
    link = tmp_path / "linked_out"
    link.symlink_to(real, target_is_directory=True)

    code = offline_main(["--input-dir", str(_bam_dir(tmp_path, fixture_bam)),
                         "--output-dir", str(link),          # the symlinked spelling
                         "--reference", str(curlcake_ref),
                         "--mode", "bam", "--min-reads", "1"])
    obj = _emitted(capsys)
    assert code == 0, obj

    run_dir = obj["run_dir"]
    assert Path(run_dir).resolve() == Path(run_dir), f"run_dir is not canonical: {run_dir}"
    for key in ("report", "qc_result"):
        assert obj[key].startswith(run_dir + "/"), (
            f"{key} is not under run_dir as a string — the fields disagree about how to "
            f"spell the same directory.\n  run_dir = {run_dir}\n  {key} = {obj[key]}")


# ---------------------------------------------------------------------------
# Why the run ended, and what the numbers cover (2026-09-23)
# ---------------------------------------------------------------------------
# A cancelled run used to emit {"status": "report", "mapped_reads": 166, "message": null}
# and a GUI showed "report - 166 mapped reads". Nothing said the operator had stopped it
# after 1 of 60 inputs; the only carrier was the file being named report_user_abort.pdf.
# The fact was on disk the whole time (`report.reason`, `run_accounting`) — it was absent
# from the one surface a consumer is told to read.

def test_a_normal_run_says_it_reached_the_end(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, capsys
) -> None:
    code = offline_main(["--input-dir", str(_bam_dir(tmp_path, fixture_bam)),
                         "--output-dir", str(tmp_path / "out"),
                         "--reference", str(curlcake_ref),
                         "--mode", "bam", "--min-reads", "1"])
    obj = _emitted(capsys)
    assert code == 0
    assert obj["reason"] == "run_end"
    assert obj["inputs_processed"] == obj["inputs_total"] == 1


def test_a_refusal_has_no_reason_and_covers_nothing(tmp_path: Path, curlcake_ref: Path,
                                                    capsys) -> None:
    offline_main(["--output-dir", str(tmp_path / "out"),
                  "--reference", str(curlcake_ref), "--mode", "bam"])
    obj = _emitted(capsys)
    assert obj["reason"] is None
    assert obj["inputs_processed"] is None and obj["inputs_total"] is None


def test_a_cancelled_run_says_so_and_says_how_much_it_covered(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Driven as a SUBPROCESS with a real SIGTERM — the only faithful form.

    In-process this cannot be tested: the abort flag is checked BETWEEN inputs
    (`main.py:626`), so it needs several of them and a signal delivered mid-loop. The
    signal goes once `latest_snapshot.json` exists, i.e. once at least one input is done,
    rather than after a fixed sleep — a timing-based version would be flaky on a loaded
    host and would pass vacuously if the signal landed after the last input.
    """
    import os
    import signal
    import subprocess
    import sys
    import time

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    n = 40
    for i in range(n):
        write_distinct_bam(fixture_bam, in_dir / f"in_{i:03d}.bam", f"i{i}")
    out = tmp_path / "out"
    script = Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin") / "mrnaspect"

    proc = subprocess.Popen(
        [str(script), "--input-dir", str(in_dir), "--output-dir", str(out),
         "--output-name", "probe", "--reference", str(curlcake_ref),
         "--mode", "bam", "--min-reads", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    snap = out / "probe" / "latest_snapshot.json"
    try:
        deadline = time.monotonic() + 120
        while not snap.exists() and time.monotonic() < deadline:
            assert proc.poll() is None, "the run exited before any input finished"
            time.sleep(0.1)
        assert snap.exists(), "no input finished within 120 s"
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=300)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()

    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, lines
    obj = json.loads(lines[0])
    assert set(obj) == set(RESULT_KEYS)
    assert obj["reason"] == "user_abort", obj
    assert obj["inputs_total"] == n
    assert 0 < obj["inputs_processed"] < n, (
        f"the run covered {obj['inputs_processed']} of {n} — if it covered all of them "
        f"the signal arrived too late and this test proved nothing")
    # The person running it must be told too, not just the caller.
    assert "stopped by operator" in stderr and "covers" in stderr, stderr


# ---------------------------------------------------------------------------
# Startup configuration errors stay INSIDE the contract (2026-09-23)
# ---------------------------------------------------------------------------
# `FileNotFoundError` from OfflinePipeline.__init__ / _discover_inputs escaped main()'s
# except clause, which named only ReferenceError and DoradoServerError. The process died
# with a traceback and **nothing on stdout** — the one thing the contract promises never
# happens. Found while adding --basecall-model: a pod5 run with no model printed a
# traceback where a consumer expected an object. Same class as `--input-dir` missing,
# a level deeper.
#
# The library still RAISES for these (test_reference_and_exit_code.py constructs
# OfflinePipeline directly and asserts that); only the CLI turns them into a result.

def test_pod5_without_a_model_still_emits_the_object(tmp_path: Path, curlcake_ref: Path,
                                                     capsys) -> None:
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "x.pod5").write_bytes(b"")
    code = offline_main(["--input-dir", str(in_dir), "--mode", "pod5",
                         "--reference", str(curlcake_ref),
                         "--output-dir", str(tmp_path / "out")])
    obj = _emitted(capsys)
    assert code == 2 and obj["exit_code"] == 2 and obj["status"] == "error"
    assert "basecall.model" in obj["error"]
    # The fix must be in the message, including the flag that now exists for it.
    assert "--basecall-model" in obj["error"], obj["error"]


def test_an_empty_input_directory_still_emits_the_object(tmp_path: Path,
                                                         curlcake_ref: Path, capsys) -> None:
    """Raised from _discover_inputs, i.e. after construction — the other escape route."""
    empty = tmp_path / "in"
    empty.mkdir()
    code = offline_main(["--input-dir", str(empty), "--mode", "bam",
                         "--reference", str(curlcake_ref),
                         "--output-dir", str(tmp_path / "out")])
    obj = _emitted(capsys)
    assert code == 2 and obj["status"] == "error" and obj["error"]


def test_the_model_flag_reaches_the_config(tmp_path: Path, curlcake_ref: Path,
                                           capsys) -> None:
    """--basecall-model must land on `basecall.model`, not merely be accepted.

    Checked by the error CHANGING: without the flag the complaint is that no model was
    given; with it, the complaint names the path that was given. A test that only asserted
    a non-zero exit would pass either way.
    """
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "x.pod5").write_bytes(b"")
    offline_main(["--input-dir", str(in_dir), "--mode", "pod5",
                  "--reference", str(curlcake_ref), "--output-dir", str(tmp_path / "out"),
                  "--basecall-model", "/no/such/model_dir"])
    obj = _emitted(capsys)
    assert "/no/such/model_dir" in obj["error"], obj["error"]
