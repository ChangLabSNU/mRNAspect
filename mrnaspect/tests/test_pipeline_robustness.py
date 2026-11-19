"""OfflinePipeline robustness — genuine gaps ported from the old driver's own test script.

The old-driver script (`sequential_pipeline.py`, retiring) covered several behaviours the
package suite did not yet re-cover. This ports only the still-valid ones, retargeted to the
package driver (`OfflinePipeline`):

  - input-guard fail-loud: empty dir -> FileNotFoundError; non-dir path -> NotADirectoryError.
  - corrupt-input isolation: a bad BAM is skipped+recorded (on_error="skip"); on_error="raise" aborts.
  - pod5 basecall-failure isolation: a basecall raise on one shard is isolated, the rest ingest.
  - low_reads banner end-to-end: below min_reads the report STILL renders, with the banner in the CoA.
  - interim cadence + timing block: interims fire past the floor; per-input timing carries the
    two-stage split (basecall_s/align_s/ingest_s).

NOT ported: the `processed_inputs.txt` restart skip-set (retired by H2 — the checkpoint is a
pod5->ubam cache now, covered by test_item14a_pod5_cache.py), and the pod5 wiring / determinism
smoke (covered by test_item14a and test_golden respectively).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mrnaspect.tests.conftest import pod5_ready
from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.reporting.reporter import LOW_READS_FINAL


from .conftest import write_distinct_bam


def _bam_dir(tmp_path: Path, fixture_bam: Path, names: list[str]) -> Path:
    d = tmp_path / "in"
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        # Distinct read IDs per input — a real run's POD5s do not share reads,
        # and the engine now drops a primary read ID it has already seen.
        write_distinct_bam(fixture_bam, d / name, Path(name).stem)
    return d


def _pod5_dir(tmp_path: Path, names: list[str]) -> Path:
    d = tmp_path / "in"
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        (d / name).write_bytes(b"")  # content unused; the fake basecaller ignores it
    return d


def _cfg(curlcake_ref: Path, **over) -> dict:
    """pod5 overrides get the dorado stubs — client and model have no defaults (A-3)."""
    base = {"reference": str(curlcake_ref), "mode": "bam", "min_reads": 1}
    base.update(over)
    cfg = load_config(None, base)
    return pod5_ready(cfg) if cfg.get("mode") == "pod5" else cfg


# --- input guards (seq #3) ---------------------------------------------------

def test_empty_input_dir_raises(tmp_path: Path, curlcake_ref: Path) -> None:
    empty = tmp_path / "in"
    empty.mkdir()
    pipe = OfflinePipeline(empty, tmp_path / "out", _cfg(curlcake_ref))
    with pytest.raises(FileNotFoundError):
        pipe.run()


def test_non_directory_input_raises(tmp_path: Path, curlcake_ref: Path) -> None:
    not_a_dir = tmp_path / "in"
    not_a_dir.write_text("i am a file")
    pipe = OfflinePipeline(not_a_dir, tmp_path / "out", _cfg(curlcake_ref))
    with pytest.raises(NotADirectoryError):
        pipe.run()


# --- corrupt-input isolation (seq #5) ----------------------------------------

def test_corrupt_bam_skipped_then_raise_aborts(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    in_dir = _bam_dir(tmp_path, fixture_bam, ["a_good.bam"])
    (in_dir / "b_corrupt.bam").write_bytes(b"not a bam file at all")  # sorts after a_
    out = tmp_path / "out"

    # Default on_error="skip": run completes, the corrupt input is recorded as failed.
    OfflinePipeline(in_dir, out, _cfg(curlcake_ref)).run()
    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert acc["n_processed"] == 1, acc
    assert acc["n_failed"] == 1 and acc["failed_inputs"] == ["b_corrupt.bam"], acc

    # on_error="raise": the same corrupt input aborts the run.
    with pytest.raises(Exception):
        OfflinePipeline(in_dir, tmp_path / "out2",
                        _cfg(curlcake_ref, on_error="raise")).run()


# --- pod5 basecall-failure isolation (seq #9, two-stage) ---------------------

def test_pod5_basecall_failure_isolated(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    in_dir = _pod5_dir(tmp_path, ["good.pod5", "zzz_bad.pod5"])  # zzz_ sorts last
    out = tmp_path / "out"

    def flaky_basecall(pod5_path: Path, out_dir: Path) -> Path:
        if pod5_path.name == "zzz_bad.pod5":
            raise RuntimeError("simulated dorado-server timeout")
        ubam_dir = Path(out_dir) / f"basecall_{pod5_path.stem}"
        ubam_dir.mkdir(parents=True, exist_ok=True)
        (ubam_dir / "chunk_0.bam").write_bytes(b"stub-ubam")
        return ubam_dir

    def fake_align(prefix: str, reference: Path, out_dir: Path) -> Path:
        bam = Path(out_dir) / f"aligned_{prefix}.bam"
        shutil.copy(fixture_bam, bam)  # a real BAM the engine can ingest
        return bam

    OfflinePipeline(in_dir, out, _cfg(curlcake_ref, mode="pod5"),
                    basecaller=flaky_basecall, aligner=fake_align).run()
    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert acc["n_processed"] == 1 and acc["failed_inputs"] == ["zzz_bad.pod5"], acc


# --- low_reads banner end-to-end (seq #7) ------------------------------------

def test_low_reads_banner_renders(tmp_path: Path, fixture_bam: Path, curlcake_ref: Path) -> None:
    in_dir = _bam_dir(tmp_path, fixture_bam, ["a.bam"])
    out = tmp_path / "out"
    # Fixture is 166 reads; demo default threshold 1000 -> the report renders WITH a banner.
    pipe = OfflinePipeline(in_dir, out, _cfg(curlcake_ref, min_reads=1000))
    assert pipe.min_reads == 1000
    result = pipe.run()
    assert result["status"] == "report"          # always renders now
    assert result["low_reads"] is True
    # C3: this is a *terminal* render (finalize), so it must carry the finished-below
    # -threshold wording, not the "still accumulating" wording.
    assert result["low_reads_state"] == LOW_READS_FINAL
    assert (out / "report_run_end.pdf").exists()
    html = (out / "report_run_end.html").read_text()
    assert "This run finished with" in html, "banner missing from CoA"
    assert "final result" in html
    qc = json.loads((out / "qc_result.json").read_text())
    assert qc["report"]["low_reads"] is True
    assert qc["report"]["low_reads_state"] == LOW_READS_FINAL


# --- interim cadence + timing block (seq #10) --------------------------------

def test_interim_cadence_and_timing(tmp_path: Path, fixture_bam: Path, curlcake_ref: Path) -> None:
    in_dir = _pod5_dir(tmp_path, [f"b{i:02d}.pod5" for i in range(4)])  # 4 x 166 reads
    out = tmp_path / "out"

    def fake_basecall(pod5_path: Path, out_dir: Path) -> Path:
        ubam_dir = Path(out_dir) / f"basecall_{pod5_path.stem}"
        ubam_dir.mkdir(parents=True, exist_ok=True)
        (ubam_dir / "chunk_0.bam").write_bytes(b"stub-ubam")
        return ubam_dir

    def fake_align(prefix: str, reference: Path, out_dir: Path) -> Path:
        bam = Path(out_dir) / f"aligned_{prefix}.bam"
        # Distinct reads per POD5, as a real aligner produces. Copying one BAM verbatim
        # would hand the engine the same 166 read IDs four times, and they would be
        # dropped as duplicates — so the cumulative total would never cross min_reads
        # and no interim would render.
        write_distinct_bam(fixture_bam, bam, prefix)
        return bam

    # min_reads=300 floor, interim on every input once crossed (interim_every_n=1):
    #   input1=166 (below floor), 2=332 (interim_1), 3=498 (interim_2), 4=664 (interim_3).
    cfg = _cfg(curlcake_ref, mode="pod5", min_reads=300,
               cadence={"interim_every_n": 1, "interim_interval_s": None})
    OfflinePipeline(in_dir, out, cfg, basecaller=fake_basecall, aligner=fake_align).run()

    interims = sorted(out.glob("interim_*_snapshot.json"))
    assert len(interims) == 3, [p.name for p in interims]
    first = json.loads(interims[0].read_text())
    assert first["total_reads"] == 332
    assert first["qc_metrics"]["metrics"]["sequence_identity"]["status"] == "measured"

    qc = json.loads((out / "qc_result.json").read_text())
    assert qc["run_accounting"]["n_interim_reports"] == 3
    t = qc["timing"]
    assert t["total_s"] >= 0 and len(t["per_input"]) == 4
    # Two-stage timing split (15c/driver split): each per-input carries basecall + align + ingest.
    assert all({"basecall_s", "align_s", "ingest_s"} <= set(pi) for pi in t["per_input"])
