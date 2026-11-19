"""`latest_snapshot.json` says how many inputs are done.

The Overview's *Inputs* value is Core's count, read from the snapshot. An offline run
globbed its inputs before the first one, so it also knows the total and the GUI can say
"n of N". A live MinKNOW run does not: its POD5 files are still being written, so
``inputs_total`` is ABSENT rather than null or a count that would read as final.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator
from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.reporting import summary_writer as SW
from mrnaspect.tests.test_realtime_pipeline import _realtime, _run_mimic

from .conftest import write_distinct_bam


def _snapshot(out_dir: Path) -> dict:
    return json.loads((out_dir / SW.LATEST_SNAPSHOT_NAME).read_text(encoding="utf-8"))


def _offline(tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, names: list[str],
             *, broken: tuple[str, ...] = ()) -> Path:
    """Run the offline driver over distinct copies of the fixture; return its output."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for name in names:
        write_distinct_bam(fixture_bam, in_dir / name, Path(name).stem)
    for name in broken:
        (in_dir / name).write_text("not a bam\n")
    out = tmp_path / "out"
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "min_reads": 1})
    OfflinePipeline(in_dir, out, cfg).run()
    return out


def test_an_offline_run_records_processed_and_total(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    snap = _snapshot(_offline(tmp_path, fixture_bam, curlcake_ref,
                              ["a.bam", "b.bam", "c.bam"]))
    assert snap["inputs_processed"] == 3
    assert snap["inputs_total"] == 3
    assert snap["last_input"] == "c.bam"


def test_a_failed_input_counts_toward_the_total_not_the_processed(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """`zz.bam` sorts last and fails, so the last snapshot is the one after `b.bam`."""
    snap = _snapshot(_offline(tmp_path, fixture_bam, curlcake_ref,
                              ["a.bam", "b.bam"], broken=("zz.bam",)))
    assert snap["inputs_processed"] == 2
    assert snap["inputs_total"] == 3


def test_a_live_run_records_processed_and_no_total(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    run_dir = tmp_path / "run"
    _run_mimic(run_dir, n=3)
    out = tmp_path / "out"
    pipe = _realtime(run_dir, out, curlcake_ref, fixture_bam)
    pipe.run()

    snap = _snapshot(out)
    assert len(pipe.progress.ingested) == 3
    assert snap["inputs_processed"] == 3
    assert "inputs_total" not in snap, "a live run has no fixed input total"


def test_the_writer_leaves_the_total_out_when_it_is_unknown(tmp_path: Path) -> None:
    SW.dump_latest_snapshot(tmp_path, {}, QCResultIntegrator(), total_reads=0,
                            last_input="x.pod5", inputs_processed=1)
    snap = _snapshot(tmp_path)
    assert snap["inputs_processed"] == 1
    assert "inputs_total" not in snap


@pytest.mark.parametrize("processed, total", [(-1, None), (4, 3)])
def test_the_writer_refuses_an_impossible_count(
    tmp_path: Path, processed, total
) -> None:
    with pytest.raises(ValueError, match="inputs_processed"):
        SW.dump_latest_snapshot(tmp_path, {}, QCResultIntegrator(), total_reads=0,
                                last_input="x.pod5", inputs_processed=processed,
                                inputs_total=total)
    assert not (tmp_path / SW.LATEST_SNAPSHOT_NAME).exists()
