"""Both drivers say how far they have got from the first moment (GUI round 2, 2026-11-12).

Nothing said whether a run's input had been recognised until its first result,
because the only progress file, latest_snapshot.json, is written when an input finishes.
The drivers now write run_status.json (mrnaspect.runtime.run_status) before the first
input and as the count moves; and `--describe-input` answers the same question before a
run exists at all. No GPU: the basecall and align stages are the fakes
test_realtime_pipeline.py uses.
"""

from __future__ import annotations

import json
from pathlib import Path

from mrnaspect.main import OfflinePipeline
from mrnaspect.runtime.run_status import RUN_STATUS_NAME
from mrnaspect.tests.test_realtime_pipeline import _cfg, _fakes, _realtime, _run_mimic


def _status(out: Path) -> dict:
    return json.loads((out / RUN_STATUS_NAME).read_text())


def test_the_offline_driver_says_n_of_n_before_the_first_input(
        tmp_path: Path, fixture_bam: Path, curlcake_ref: Path) -> None:
    in_dir, out = tmp_path / "pod5", tmp_path / "out"
    in_dir.mkdir()
    for i in range(3):
        (in_dir / f"r_{i}.pod5").write_bytes(b"x")
    bc, al = _fakes(fixture_bam)
    seen: list[dict] = []

    def basecall_and_look(pod5: Path, out_dir: Path) -> Path:
        seen.append(_status(out))          # what a surface reads while this one runs
        return bc(pod5, out_dir)

    OfflinePipeline(in_dir, out, _cfg(curlcake_ref), basecaller=basecall_and_look,
                    aligner=al).run()
    assert seen[0] == {**seen[0], "inputs_found": 3, "inputs_processed": 0,
                       "inputs_total": 3, "current_input": "r_0.pod5"}
    assert [s["inputs_processed"] for s in seen] == [0, 1, 2]
    end = _status(out)
    assert (end["inputs_processed"], end["inputs_total"], end["current_input"]) == (3, 3, None)


def test_the_live_driver_counts_what_minknow_has_written(
        tmp_path: Path, fixture_bam: Path, curlcake_ref: Path) -> None:
    run_dir, out = tmp_path / "run", tmp_path / "out"
    _run_mimic(run_dir, n=4)
    _realtime(run_dir, out, curlcake_ref, fixture_bam).run()
    end = _status(out)
    assert end["inputs_total"] is None, "a live run has no fixed total"
    assert end["inputs_found"] == end["inputs_processed"] == 4
    assert end["current_input"] is None


def test_the_live_driver_writes_before_any_input_arrives(
        tmp_path: Path, fixture_bam: Path, curlcake_ref: Path) -> None:
    """MinKNOW may take minutes to write its first POD5; the surface must still know the
    run is up and watching."""
    run_dir, out = tmp_path / "run", tmp_path / "out"
    (run_dir / "pod5").mkdir(parents=True)
    (run_dir / "final_summary_x.txt").write_text("ended")   # ends at once, no input
    _realtime(run_dir, out, curlcake_ref, fixture_bam).run()
    s = _status(out)
    assert (s["inputs_found"], s["inputs_processed"], s["inputs_total"]) == (0, 0, None)


# --- --describe-input --------------------------------------------------------------------------

def test_describe_input_prints_describe_s_answer(tmp_path, capsys, curlcake_ref) -> None:
    from mrnaspect.main import main
    from mrnaspect.runtime.input_folder import describe

    run = tmp_path / "data" / "exp" / "s" / "20261112_1434_MS00000_SIM06556_b4ed6268"
    (run / "pod5").mkdir(parents=True)
    (run / "pod5" / "a.pod5").write_bytes(b"xy")
    rc = main(["--describe-input", "--live", "--mode", "pod5",
               "--input-dir", str(run / "pod5")])
    assert rc == 0
    got = json.loads(capsys.readouterr().out)
    assert got == describe(str(run / "pod5"), "pod5", live=True)
    assert got["use"] == str(run) and got["moved"] == "up" and got["inputs"] == 1


def test_describe_input_needs_no_reference_and_writes_nothing(tmp_path, capsys) -> None:
    from mrnaspect.main import main

    before = set(tmp_path.rglob("*"))
    rc = main(["--describe-input", "--mode", "bam", "--input-dir", str(tmp_path)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["use"] is None
    assert set(tmp_path.rglob("*")) == before
