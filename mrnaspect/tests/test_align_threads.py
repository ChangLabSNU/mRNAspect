"""minimap2's thread count is a setting, align.threads, and every CPU by default.

B1 step 3 (2026-11-10: "as recommended"). The shipped flags set no -t, so minimap2
ran on its default 3 threads; once bam mode aligned every input, alignment became about
40% of a bam-mode run (24.3 of 57.5 s on run 1's 28,599 held-out reads). align.threads
null means every CPU this process may use; a -t in align.minimap2_flags decides instead,
and setting both is refused. The thread count does not change an alignment.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pysam
import pytest

from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.wrappers.aligner import (available_cpus, minimap2_sets_threads,
                                        resolve_align_threads, shell_align)

SHIPPED = "-ax map-ont -k7 -w5 --secondary=no -y"


@pytest.mark.parametrize("flags, sets", [
    (SHIPPED, False),
    (SHIPPED + " -t 8", True),
    (SHIPPED + " -t8", True),
    ("-yt8 -ax map-ont", True),
    ("-ax map-ont -k7 -w5", False),
    ("-x map-ont -a", False),
    ("", False),
    (None, False),
])
def test_a_t_in_the_flags_is_found(flags, sets) -> None:
    assert minimap2_sets_threads(flags) is sets


def test_null_is_every_cpu_this_process_may_use() -> None:
    assert resolve_align_threads(None, SHIPPED) == available_cpus() >= 1


def test_a_number_is_taken_as_given() -> None:
    assert resolve_align_threads(4, SHIPPED) == 4


def test_a_t_in_the_flags_decides_when_threads_is_null() -> None:
    assert resolve_align_threads(None, SHIPPED + " -t 8") is None


def test_both_is_refused() -> None:
    with pytest.raises(ValueError, match="both set minimap2's threads"):
        resolve_align_threads(4, SHIPPED + " -t 8")


@pytest.mark.parametrize("bad", [0, -2, 2.5, "4", True])
def test_anything_but_a_whole_number_of_one_or_more_is_refused(bad) -> None:
    with pytest.raises(ValueError, match="align.threads"):
        resolve_align_threads(bad, SHIPPED)


def _staged(tmp_path: Path, bam: Path, prefix: str) -> Path:
    out = tmp_path / prefix
    (out / f"basecall_{prefix}").mkdir(parents=True)
    (out / f"basecall_{prefix}" / bam.name).symlink_to(bam)
    return out


def _minimap2_line(bam: Path) -> str:
    with pysam.AlignmentFile(str(bam)) as fh:
        (pg,) = [p for p in fh.header.to_dict()["PG"] if p["PN"] == "minimap2"]
    return pg["CL"]


def _records(bam: Path) -> list[str]:
    with pysam.AlignmentFile(str(bam)) as fh:
        return [r.to_string() for r in fh.fetch(until_eof=True)]


def test_the_thread_count_reaches_minimap2_and_changes_no_record(
        tmp_path, variant_bam, qk_ha_ref) -> None:
    one = shell_align("a", qk_ha_ref, _staged(tmp_path, variant_bam, "a"),
                      align_flags=SHIPPED, threads=1)
    four = shell_align("b", qk_ha_ref, _staged(tmp_path, variant_bam, "b"),
                       align_flags=SHIPPED, threads=4)
    assert " -t 1 " in _minimap2_line(one) and " -t 4 " in _minimap2_line(four)
    assert _records(one) == _records(four)


def test_without_a_count_minimap2_keeps_its_own(tmp_path, variant_bam, qk_ha_ref) -> None:
    bam = shell_align("a", qk_ha_ref, _staged(tmp_path, variant_bam, "a"),
                      align_flags=SHIPPED)
    assert " -t " not in _minimap2_line(bam)


def _pipeline(tmp_path: Path, ref: Path, **align) -> OfflinePipeline:
    cfg = load_config(None, {"reference": str(ref), "mode": "bam", "min_reads": 1,
                             **{f"align.{k}": v for k, v in align.items()}})
    return OfflinePipeline(tmp_path / "in", tmp_path / "out", cfg)


def test_the_pipeline_binds_the_resolved_count(tmp_path, qk_ha_ref) -> None:
    assert _pipeline(tmp_path, qk_ha_ref).aligner.keywords["threads"] == available_cpus()
    assert _pipeline(tmp_path, qk_ha_ref, threads=2).aligner.keywords["threads"] == 2


def test_the_pipeline_refuses_both_before_it_starts(tmp_path, qk_ha_ref) -> None:
    with pytest.raises(ValueError, match="keep one"):
        _pipeline(tmp_path, qk_ha_ref, threads=2, minimap2_flags=SHIPPED + " -t 8")


def test_the_shipped_config_leaves_it_null() -> None:
    cfg = load_config(None, {"reference": "x.fa"}, require_reference=False)
    assert cfg["align"]["threads"] is None


def test_the_script_still_parses() -> None:
    """What it does with ALIGN_THREADS is checked above, through minimap2's own @PG."""
    script = Path(__file__).resolve().parents[1] / "wrappers" / "run_align.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
