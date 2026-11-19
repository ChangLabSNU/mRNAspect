"""Per-construct designed tail lengths (module_params.polya.target_lengths)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mrnaspect.analysis.modules.polya import PolyAModule, resolve_target_lengths
from mrnaspect.main import main
from mrnaspect.tests.conftest import run_dir_of

CONTIGS = ["QK-09", "HA-WT"]


def test_targets_resolve_per_construct() -> None:
    cfg = {"target_lengths": {"QK-09": 90, "HA-WT": "120"}}
    assert resolve_target_lengths(cfg, CONTIGS) == {"QK-09": 90.0, "HA-WT": 120.0}
    assert resolve_target_lengths({}, CONTIGS) == {}
    assert resolve_target_lengths(None, CONTIGS) == {}


def test_a_name_the_reference_lacks_is_refused_with_a_suggestion() -> None:
    with pytest.raises(ValueError, match="Did you mean 'HA-WT'"):
        resolve_target_lengths({"target_lengths": {"HA-W": 120}}, CONTIGS)


@pytest.mark.parametrize("targets, match", [
    (["QK-09"], "must be a mapping"),
    ({"QK-09": 0}, "must be a length > 0"),
    ({"QK-09": "ninety"}, "must be a length > 0"),
])
def test_a_malformed_target_is_refused(targets, match) -> None:
    with pytest.raises(ValueError, match=match):
        resolve_target_lengths({"target_lengths": targets}, CONTIGS)


def test_a_whole_run_instance_does_not_mix_designs() -> None:
    """Given per-construct targets, the run-level rate is withheld and says why."""
    snap = PolyAModule({"target_length": 120,
                        "target_lengths": {"QK-09": 90}}).snapshot()["short_tail"]
    assert snap["status"] == "not_measured"
    assert "set per construct" in snap["reason"]


def _run(tmp_path: Path, bam: Path, ref: Path, targets: str) -> tuple[int, Path]:
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    shutil.copy(bam, in_dir / bam.name)
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("min_reads: 100\nsample: {arm: m1psi, arms: {HA-WT: U}}\n"
                   f"module_params: {{polya: {{target_lengths: {targets}, short_fraction: 0.8}}}}\n",
                   encoding="utf-8")
    out = tmp_path / "out"
    code = main(["--input-dir", str(in_dir), "--reference", str(ref), "--output-dir",
                 str(out), "--mode", "bam", "--config", str(cfg)])
    return code, out


def test_each_construct_is_judged_against_its_own_design(
    tmp_path: Path, variant_bam: Path, qk_ha_ref: Path
) -> None:
    code, out = _run(tmp_path, variant_bam, qk_ha_ref, "{QK-09: 90, HA-WT: 120}")
    assert code == 0
    run_dir = run_dir_of(out)
    qc = json.loads((run_dir / "qc_result.json").read_text())["qc_metrics"]
    tails = {c: qc["constructs"][c]["polya_tail"] for c in CONTIGS}
    assert tails["QK-09"]["short_tail_threshold"] == 72.0
    assert tails["HA-WT"]["short_tail_threshold"] == 96.0
    assert qc["metrics"]["polya_tail"]["short_tail_status"] == "not_measured"
    html = (run_dir / "report_run_end.html").read_text(encoding="utf-8")
    assert html.count("Short poly(A) tails") == 2, "one per construct, none for the run"
    assert "90 nt × 0.8" in html and "120 nt × 0.8" in html


def test_a_misspelt_construct_stops_the_run(
    tmp_path: Path, variant_bam: Path, qk_ha_ref: Path, capsys
) -> None:
    code, _ = _run(tmp_path, variant_bam, qk_ha_ref, "{HA-W: 120}")
    assert code == 2
    assert "Did you mean 'HA-WT'" in capsys.readouterr().out
