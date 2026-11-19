"""bam mode aligns every BAM itself, aligned or not (2026-11-03; release item B1).

Until 2026-11-10 bam mode ingested a file's own alignment. An unaligned BAM, which is what
MinKNOW and dorado write, failed at open ("file has no sequences defined"), and a BAM
aligned with other flags was trusted: the same 270 reads aligned with minimap2's defaults
got the m1Psi poly(A) correction with QK-09 delta_pct 2.7582 where Core's own -k7 -w5
gives 3.0094, and the guard that should have withheld it read the config, not the BAM
(log 2026-11-03).

The inputs are built here from the QK/HA fixture: its reads as dorado writes them
(unaligned, basecaller tags only, dorado's @PG and @RG), and the same reads aligned with
minimap2's defaults. Every run goes through the real aligner.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import pysam
import pytest

from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.main import main as offline_main
from mrnaspect.wrappers._shell import resolve_tool

ARMS = "sample:\n  arm: m1psi\n  arms:\n    HA-WT: U\n"
# The tags dorado writes on run 1's reads (log 2026-11-03), mv among them.
BASECALLER_TAGS = ("pt", "qs", "du", "ns", "RG", "ch", "rn", "st", "mx", "dx", "fn",
                   "sm", "sd", "sv", "ts", "pa", "mv", "er", "pi", "sp")
DORADO_VERSION = "2.0.0+20e87c8b"
MODEL = "rna004_130bps_hac@v5.3.0"
# Instrument panels read MinKNOW's files, not reads.
_INSTRUMENT = ("pore_activity", "end_reason", "voltage")


def _unaligned(src: Path, dst: Path, *, only: str | None = None) -> Path:
    """``src``'s primary reads as dorado writes them: unaligned, basecaller tags only."""
    with pysam.AlignmentFile(str(src)) as bam:
        reads = [r for r in bam if not (r.is_secondary or r.is_supplementary)
                 and (only is None or r.reference_name == only)]
    groups = sorted({str(r.get_tag("RG")) for r in reads})
    header = {
        "HD": {"VN": "1.6", "SO": "unknown"},
        "PG": [{"ID": "basecaller", "PN": "dorado", "VN": DORADO_VERSION,
                "CL": f'dorado "basecaller" "--estimate-poly-a" "--emit-moves" "{MODEL}"'}],
        "RG": [{"ID": g, "PL": "ONT",
                "DS": f"runid={g.split('_')[0]} basecall_model={MODEL}"} for g in groups],
    }
    with pysam.AlignmentFile(str(dst), "wb", header=header) as out:
        for r in reads:
            seg = pysam.AlignedSegment(out.header)
            seg.query_name = r.query_name
            seg.flag = 4
            seq, qual = r.query_sequence, list(r.query_qualities)
            if r.is_reverse:
                seq = seq[::-1].translate(str.maketrans("ACGTN", "TGCAN"))
                qual = qual[::-1]
            # The sequence first: setting it clears the qualities.
            seg.query_sequence = seq
            seg.query_qualities = pysam.qualitystring_to_array(
                "".join(chr(q + 33) for q in qual))
            for tag, value, kind in r.get_tags(with_value_type=True):
                if tag in BASECALLER_TAGS:
                    # An array (mv) keeps its own element type; pysam refuses "B".
                    seg.set_tag(tag, value, None if kind == "B" else kind)
            out.write(seg)
    return dst


def _align_upstream(ubam: Path, ref: Path, dst: Path, flags: str) -> Path:
    """``ubam`` aligned the way a user might have done it before handing it over."""
    samtools = resolve_tool("samtools", None)
    minimap2 = resolve_tool("minimap2", None)
    script = (f"set -euo pipefail; '{samtools}' fastq -T {','.join(BASECALLER_TAGS)} "
              f"'{ubam}' | '{minimap2}' {flags} '{ref}' - 2>/dev/null "
              f"| '{samtools}' sort -o '{dst}' -")
    subprocess.run(["bash", "-c", script], check=True)
    return dst


def _run(tmp_path: Path, inputs: list[Path], ref: Path) -> Path:
    src = tmp_path / "in"
    src.mkdir(parents=True)
    for bam in inputs:
        (src / bam.name).symlink_to(bam)
    cfg = tmp_path / "arms.yaml"
    cfg.write_text(ARMS)
    assert offline_main([
        "--input-dir", str(src), "--reference", str(ref), "--output-dir",
        str(tmp_path / "out"), "--output-name", "r", "--mode", "bam",
        "--min-reads", "1", "--config", str(cfg)]) == 0
    return tmp_path / "out" / "r"


def _result(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "qc_result.json").read_text())


def _qc(run_dir: Path) -> dict[str, Any]:
    """Every module's numbers, without the instrument panels."""
    snaps = _result(run_dir)["snapshots"]
    return {k: v for k, v in snaps.items() if k not in _INSTRUMENT}


def _same(a: Any, b: Any, where: str = "") -> None:
    """Equal, floats to 1e-12 relative: a mean summed in another order may differ in the
    last bits."""
    if isinstance(a, dict) and isinstance(b, dict):
        assert a.keys() == b.keys(), f"{where}: keys {sorted(a)} vs {sorted(b)}"
        for k in a:
            _same(a[k], b[k], f"{where}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), f"{where}: length {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, f"{where}[{i}]")
    elif isinstance(a, float) and isinstance(b, float) and not (
            math.isnan(a) or math.isnan(b)):
        assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12), f"{where}: {a} vs {b}"
    else:
        assert a == b, f"{where}: {a!r} vs {b!r}"


@pytest.fixture(scope="module")
def ubam(tmp_path_factory, variant_bam) -> Path:
    return _unaligned(variant_bam, tmp_path_factory.mktemp("ubam") / "reads.bam")


@pytest.fixture(scope="module")
def k15_bam(tmp_path_factory, ubam, qk_ha_ref) -> Path:
    """The same reads aligned with minimap2's map-ont defaults (k15 w10)."""
    return _align_upstream(ubam, qk_ha_ref, tmp_path_factory.mktemp("k15") / "k15.bam",
                           "-ax map-ont --secondary=no -y")


@pytest.fixture(scope="module")
def ubam_run(tmp_path_factory, ubam, qk_ha_ref) -> Path:
    return _run(tmp_path_factory.mktemp("run_ubam"), [ubam], qk_ha_ref)


@pytest.fixture(scope="module")
def k15_run(tmp_path_factory, k15_bam, qk_ha_ref) -> Path:
    return _run(tmp_path_factory.mktemp("run_k15"), [k15_bam], qk_ha_ref)


def test_the_inputs_are_what_they_claim_to_be(ubam, k15_bam) -> None:
    with pysam.AlignmentFile(str(ubam), check_sq=False) as bam:
        assert not bam.header.references
        flags = {r.flag for r in bam}
    assert flags == {4}
    with pysam.AlignmentFile(str(k15_bam)) as bam:
        (pg,) = [p for p in bam.header.to_dict()["PG"] if p["PN"] == "minimap2"]
    assert "-k7" not in pg["CL"] and "-w5" not in pg["CL"]


def test_an_unaligned_bam_is_aligned_and_reported(ubam_run) -> None:
    mapping = _qc(ubam_run)["run_summary"]["mapping"]
    assert mapping["total"] == 270
    assert mapping["mapped"] == 270


def test_a_bam_aligned_elsewhere_gives_the_numbers_of_its_reads_unaligned(
        ubam_run, k15_run) -> None:
    _same(_qc(k15_run), _qc(ubam_run))


def test_the_fixtures_own_alignment_is_replaced_too(tmp_path, variant_bam, qk_ha_ref,
                                                    ubam_run) -> None:
    _same(_qc(_run(tmp_path, [variant_bam], qk_ha_ref)), _qc(ubam_run))


def test_the_polya_correction_follows_cores_alignment_not_the_inputs(
        k15_run, ubam_run) -> None:
    """The 11-03 defect: a k15 BAM got delta_pct 2.7582 from its own alignment.

    Core's alignment of the same reads gives 2.9937 against this two-contig reference
    (3.0094 against run 1's fifteen, the 11-03 -k7 -w5 value: which minimizers are too
    frequent to seed with depends on the reference).
    """
    k15 = _qc(k15_run)["construct:QK-09"]["polya"]["correction"]
    own = _qc(ubam_run)["construct:QK-09"]["polya"]["correction"]
    assert k15["status"] == own["status"] == "applied"
    # To the last bits only: the reads reach the engine in another order.
    assert k15["delta_pct"] == pytest.approx(own["delta_pct"], rel=1e-12)
    assert abs(k15["delta_pct"] - 2.7582) > 0.1


def test_the_ingested_bam_is_cores_own_alignment(k15_run, qk_ha_ref) -> None:
    aligned = k15_run / "basecall_cache" / "aligned_k15.bam"
    with pysam.AlignmentFile(str(aligned)) as bam:
        (pg,) = [p for p in bam.header.to_dict()["PG"] if p["PN"] == "minimap2"]
    assert "-k7 -w5" in pg["CL"] and str(qk_ha_ref) in pg["CL"]
    tools = _result(k15_run)["provenance"]["tools"]
    assert tools["minimap2"]["command_line"] == pg["CL"]


def test_the_basecaller_is_recorded_from_the_input(ubam_run) -> None:
    """Core's alignment writes a new header, so dorado's lines are read from the input.

    (A BAM aligned through ``samtools fastq | minimap2``, as k15.bam was, has already lost
    them upstream.)
    """
    tools = _result(ubam_run)["provenance"]["tools"]
    assert tools["dorado"]["version"] == DORADO_VERSION
    assert f"basecall_model={MODEL}" in tools["basecall_model_from_bam"]["description"]


def test_a_folder_mixing_aligned_and_unaligned_bams(tmp_path, variant_bam, qk_ha_ref,
                                                    ubam_run) -> None:
    qk = _unaligned(variant_bam, tmp_path / "qk.bam", only="QK-09")
    ha = _align_upstream(_unaligned(variant_bam, tmp_path / "ha_u.bam", only="HA-WT"),
                         qk_ha_ref, tmp_path / "ha.bam", "-ax map-ont --secondary=no -y")
    run = _run(tmp_path / "mixed", [qk, ha], qk_ha_ref)
    assert _result(run)["run_accounting"]["n_processed"] == 2
    _same(_qc(run), _qc(ubam_run))


def _pipeline(in_dir: Path, out: Path, ref: Path,
              overrides: dict[str, Any] | None = None) -> OfflinePipeline:
    cfg = load_config(None, {"reference": str(ref), "mode": "bam", "min_reads": 1,
                             "on_error": "raise", **(overrides or {})})
    return OfflinePipeline(in_dir, out, cfg)


def _input_dir(tmp_path: Path, bam: Path) -> Path:
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / bam.name).symlink_to(bam)
    return in_dir


@pytest.mark.parametrize("emit_moves", [False, True])
def test_the_move_table_is_carried_only_when_asked_for(tmp_path, ubam, qk_ha_ref,
                                                       emit_moves) -> None:
    """Every input read carries mv; it about doubles the align time and BAM size."""
    out = tmp_path / "out"
    _pipeline(_input_dir(tmp_path, ubam), out, qk_ha_ref,
              {"basecall.emit_moves": emit_moves}).run()
    with pysam.AlignmentFile(str(out / "basecall_cache" / "aligned_reads.bam")) as bam:
        carried = {r.has_tag("mv") for r in bam}
    assert carried == {emit_moves}


def test_a_rerun_in_the_same_folder_aligns_each_read_once(tmp_path, ubam,
                                                          qk_ha_ref) -> None:
    in_dir, out = _input_dir(tmp_path, ubam), tmp_path / "out"
    for _ in range(2):
        pipe = _pipeline(in_dir, out, qk_ha_ref)
        pipe.run()
        assert pipe.engine.run_summary.snapshot()["mapping"]["total"] == 270


def test_a_staging_folder_holding_another_bam_is_refused(tmp_path, ubam,
                                                         qk_ha_ref) -> None:
    """Its other BAM would be aligned together with the input."""
    out = tmp_path / "out"
    stray = out / "basecall_cache" / "basecall_reads" / "chunk_0_0.bam"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"")
    with pytest.raises(FileExistsError, match="chunk_0_0.bam"):
        _pipeline(_input_dir(tmp_path, ubam), out, qk_ha_ref).run()


def test_bam_mode_checks_the_minimap2_flags_too(tmp_path, qk_ha_ref) -> None:
    with pytest.raises(ValueError, match="lacks -y"):
        _pipeline(tmp_path / "in", tmp_path / "out", qk_ha_ref,
                  {"align.minimap2_flags": "-ax map-ont -k7 -w5 --secondary=no"})
