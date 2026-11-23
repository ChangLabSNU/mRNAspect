"""The real-time CLI exits non-zero when every input failed.

The offline CLI already did. The real-time one returned 0 with `status="report"` and
`inputs_processed=0`, so a workflow that reads the exit code went on with nothing
analysed. Total failure is judged by what became of the inputs, never by how many reads
mapped: an input that was processed and holds no mapped read is a result.

No GPU and no dorado-server: the server is a stand-in and the two stages are injected,
as in ``test_realtime_pipeline.py``. Everything else is the real entry point.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path

import pysam
import yaml

from mrnaspect.realtime import main as rt
from mrnaspect.tests.conftest import dorado_stubs, run_dir_of


def _run(tmp_path: Path, curlcake_ref: Path, monkeypatch, capsys, basecaller, aligner):
    run = tmp_path / "run"
    (run / "pod5").mkdir(parents=True)
    (run / "pod5" / "a.pod5").write_bytes(b"the stand-ins do not read this")
    (run / "final_summary_test.txt").write_text("the run has ended\n")
    stubs = dorado_stubs()
    cfg = tmp_path / "c.yaml"
    cfg.write_text(yaml.safe_dump({
        "mode": "pod5", "min_reads": 1, "reference": str(curlcake_ref),
        "basecall": {"model": stubs["model"], "server": {"client": stubs["client"]}},
        "realtime": {"poll_interval_s": 0.02, "stable_polls": 3,
                     "seal_poll_interval_s": 0.02, "seal_stable_polls": 3,
                     "seal_budget_s": 10.0, "interim_every_n": 0}}))

    @contextlib.contextmanager
    def no_server(*_args, **_kwargs):
        yield None

    real = rt.RealtimePipeline
    monkeypatch.setattr(rt, "dorado_server", no_server)
    # **kw: what the command line passes besides (continues=, since 2026-11-23).
    monkeypatch.setattr(rt, "RealtimePipeline", lambda run_dir, out, config, **kw: real(
        run_dir, out, config, basecaller=basecaller, aligner=aligner, **kw))
    out = tmp_path / "out"
    code = rt.main(["--config", str(cfg), "--input-dir", str(run), "--output-dir", str(out)])
    emitted = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return code, emitted, out


def _basecall_from(bam: Path):
    def basecall(pod5: Path, out_dir: Path) -> Path:
        d = Path(out_dir) / f"basecall_{pod5.stem}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(bam, d / "chunk_0_0.bam")
        return d
    return basecall


def _align_to(bam: Path):
    def align(prefix: str, reference: Path, out_dir: Path) -> Path:
        dst = Path(out_dir) / f"aligned_{prefix}.bam"
        shutil.copy(bam, dst)
        return dst
    return align


def _unmapped_bam(path: Path, reference: Path, n: int = 3) -> Path:
    """A BAM against ``reference`` in which no read is mapped."""
    with pysam.FastaFile(str(reference)) as fa:
        header = {"HD": {"VN": "1.6"},
                  "SQ": [{"SN": name, "LN": fa.get_reference_length(name)}
                         for name in fa.references]}
    with pysam.AlignmentFile(str(path), "wb", header=header) as out:
        for i in range(n):
            read = pysam.AlignedSegment()
            read.query_name, read.flag = f"unmapped-{i}", 4
            read.query_sequence = "ACGTACGTACGTACGTACGT"
            read.query_qualities = pysam.qualitystring_to_array("I" * 20)
            out.write(read)
    return path


def test_every_input_failing_exits_one(tmp_path, fixture_bam, curlcake_ref, monkeypatch,
                                       capsys) -> None:
    def failing_basecall(pod5: Path, out_dir: Path) -> Path:
        raise RuntimeError("the basecall server refused the connection")

    code, emitted, out = _run(tmp_path, curlcake_ref, monkeypatch, capsys,
                              failing_basecall, _align_to(fixture_bam))
    assert code == 1
    assert emitted["exit_code"] == 1 and emitted["inputs_processed"] == 0
    # The accounting still names the input, so the operator can find it.
    accounting = json.loads((run_dir_of(out) / "qc_result.json").read_text())["run_accounting"]
    assert "a.pod5" in json.dumps(accounting)


def test_an_input_processed_with_no_mapped_read_is_not_a_failure(
        tmp_path, fixture_bam, curlcake_ref, monkeypatch, capsys) -> None:
    """The control: total failure is not "zero mapped reads"."""
    unmapped = _unmapped_bam(tmp_path / "unmapped.bam", curlcake_ref)
    code, emitted, out = _run(tmp_path, curlcake_ref, monkeypatch, capsys,
                              _basecall_from(unmapped), _align_to(unmapped))
    assert emitted["inputs_processed"] == 1
    assert code == emitted["exit_code"] == 0
    mapping = json.loads((run_dir_of(out) / "qc_result.json").read_text())[
        "snapshots"]["run_summary"]["mapping"]
    assert mapping["mapped"] == 0 and mapping["total"] == 3


def test_a_processed_input_exits_zero(tmp_path, fixture_bam, curlcake_ref, monkeypatch,
                                      capsys) -> None:
    code, emitted, _ = _run(tmp_path, curlcake_ref, monkeypatch, capsys,
                            _basecall_from(fixture_bam), _align_to(fixture_bam))
    assert code == emitted["exit_code"] == 0 and emitted["inputs_processed"] == 1
