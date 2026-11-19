"""A run takes one basecaller model: a second one stops it, whatever skips bad reads."""

from __future__ import annotations

from pathlib import Path

import pysam
import pytest

from mrnaspect.analysis.modules.polya import MixedModelError, PolyAModule
from mrnaspect.main import main

from .test_polya_correction_v2 import _read

SUP = "rna004_130bps_sup@v5.3.0"


def test_a_second_model_raises() -> None:
    m = PolyAModule()
    m.update(_read(90, "rna004_130bps_hac@v5.3.0", name="a"))
    with pytest.raises(MixedModelError, match="two basecaller models"):
        m.update(_read(90, SUP, name="b"))


def test_two_names_of_one_model_are_one_model() -> None:
    """A BAM carries the folder name it was basecalled with; aliases are the same model."""
    m = PolyAModule()
    m.update(_read(90, "qcrna_m1psi_v1_ov2500", name="a"))
    m.update(_read(90, "m1psi_tuned_v1", name="b"))
    assert m.snapshot()["reads_processed"] == 2


def _relabel(src: Path, dst: Path, *, every: int, suffix: str = "") -> None:
    """Copy `src`, giving every `every`-th read (1 = all) another model's read group.

    ``suffix`` renames every read, so a second copy is not skipped as duplicates.
    """
    with pysam.AlignmentFile(str(src)) as fin, \
            pysam.AlignmentFile(str(dst), "wb", template=fin) as fout:
        for i, r in enumerate(fin):
            if i % every == 0 and r.has_tag("RG"):
                r.set_tag("RG", str(r.get_tag("RG")).split("_", 1)[0] + "_" + SUP, "Z")
            r.query_name += suffix
            fout.write(r)
    pysam.index(str(dst))


def _run(tmp_path: Path, ref: Path) -> int:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("min_reads: 100\nsample: {arm: m1psi, arms: {HA-WT: U}}\n")
    return main(["--input-dir", str(tmp_path / "in"), "--reference", str(ref),
                 "--output-dir", str(tmp_path / "out"), "--mode", "bam",
                 "--config", str(cfg)])


def test_a_bam_mixing_models_stops_the_run(
    tmp_path: Path, variant_bam: Path, qk_ha_ref: Path, capsys
) -> None:
    """The per-read guard used to swallow it: half the reads dropped, exit 0."""
    (tmp_path / "in").mkdir()
    _relabel(variant_bam, tmp_path / "in" / "mixed.bam", every=2)
    assert _run(tmp_path, qk_ha_ref) == 2
    assert "two basecaller models" in capsys.readouterr().out


def test_two_bams_of_two_models_stop_the_run_under_on_error_skip(
    tmp_path: Path, variant_bam: Path, qk_ha_ref: Path, capsys
) -> None:
    """on_error: skip skips a bad input; a second model is not one."""
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "a.bam").write_bytes(variant_bam.read_bytes())
    pysam.index(str(tmp_path / "in" / "a.bam"))
    _relabel(variant_bam, tmp_path / "in" / "b.bam", every=1, suffix="_b")
    assert _run(tmp_path, qk_ha_ref) == 2
    assert "two basecaller models" in capsys.readouterr().out
