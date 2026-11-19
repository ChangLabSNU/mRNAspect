"""The short-tail rate in the report: a row only when a cutoff is configured."""

from __future__ import annotations

import shutil
from pathlib import Path

from mrnaspect.main import main
from mrnaspect.reporting.reporter import CoAReporter
from mrnaspect.tests.conftest import run_dir_of


def _measured(**over) -> dict:
    st = {"status": "measured", "ratio": 0.25, "ratio_lower": 0.2, "ratio_upper": 0.3,
          "threshold": 96.0, "target_length": 120, "short_fraction": 0.8,
          "judged_on": "corrected"}
    return {"short_tail": {**st, **over}}


def test_the_row_gives_the_rate_its_bounds_and_its_ruler() -> None:
    (row,) = CoAReporter._short_tail_rows(_measured())
    assert row["name"] == "Short poly(A) tails"
    assert row["value"] == "25.0% (20.0%–30.0%)"
    assert "shorter than 96 nt (120 nt × 0.8)" in row["method"]
    assert "on the corrected length" in row["method"]


def test_a_cutoff_set_directly_and_a_raw_verdict_are_named_so() -> None:
    (row,) = CoAReporter._short_tail_rows(_measured(threshold=100.0, judged_on="raw_pt"))
    assert "shorter than 100 nt (set directly)" in row["method"]
    assert "on the uncorrected length" in row["method"]


def test_without_a_cutoff_there_is_no_row() -> None:
    assert CoAReporter._short_tail_rows({"short_tail": {"status": "not_measured"}}) == []
    assert CoAReporter._short_tail_rows({}) == []


def test_a_run_with_a_target_prints_the_row(
    tmp_path: Path, variant_bam: Path, qk_ha_ref: Path
) -> None:
    """End to end: the run-level table and each construct's carry it."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    shutil.copy(variant_bam, in_dir / variant_bam.name)
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("min_reads: 100\nsample: {arm: m1psi, arms: {HA-WT: U}}\n"
                   "module_params: {polya: {target_length: 120, short_fraction: 0.8}}\n", encoding="utf-8")
    out = tmp_path / "out"
    assert main(["--input-dir", str(in_dir), "--reference", str(qk_ha_ref),
                 "--output-dir", str(out), "--mode", "bam", "--config", str(cfg)]) == 0
    html = (run_dir_of(out) / "report_run_end.html").read_text(encoding="utf-8")
    assert html.count("Short poly(A) tails") == 3, "run level, QK-09 and HA-WT"
    assert "on the corrected length" in html and "on the uncorrected length" in html
