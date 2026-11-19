"""run_align.sh stops when the alignment does not give back every primary read once.

B1 step 5 (2026-11-10). minimap2 -a writes every read it is given as one primary
record, mapped or not, so the primary reads going in and coming out must match; a read
lost between them would be missing from every number without a word. The script counts
the primary records of its input BAMs and of the aligned BAM, before publishing it.

What it does not catch, measured 2026-11-10: a record with no sequence (``*``) is not
lost, since samtools fastq writes it as an empty read and minimap2 2.30 writes it back
unmapped; and a primary that another aligner hard-clipped comes back shortened, not lost.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pysam
import pytest

from mrnaspect.wrappers._shell import resolve_tool
from mrnaspect.wrappers.aligner import shell_align

SHIPPED = "-ax map-ont -k7 -w5 --secondary=no -y"


def _staged(tmp_path: Path, bam: Path, prefix: str = "x") -> Path:
    out = tmp_path / "cache"
    (out / f"basecall_{prefix}").mkdir(parents=True)
    (out / f"basecall_{prefix}" / bam.name).symlink_to(bam)
    return out


def _primaries(bam: Path) -> list[pysam.AlignedSegment]:
    with pysam.AlignmentFile(str(bam), check_sq=False) as fh:
        return [r for r in fh.fetch(until_eof=True)
                if not (r.is_secondary or r.is_supplementary)]


def test_every_read_in_comes_out_once_and_the_log_says_so(tmp_path, variant_bam,
                                                          qk_ha_ref) -> None:
    out = _staged(tmp_path, variant_bam)
    bam = shell_align("x", qk_ha_ref, out, align_flags=SHIPPED, threads=2)
    assert len(_primaries(bam)) == 270
    assert "primary reads: 270 in, 270 out" in (out / "align_x.log").read_text()


def test_a_read_lost_on_the_way_stops_the_input(tmp_path, variant_bam, qk_ha_ref,
                                                monkeypatch) -> None:
    """A minimap2 that drops its first record: the input fails, nothing is published."""
    lossy = tmp_path / "lossy_minimap2"
    lossy.write_text('#!/bin/bash\n"$REAL_MINIMAP2" "$@" | '
                     "awk '/^@/ {print; next} !gone {gone = 1; next} {print}'\n")
    lossy.chmod(0o755)
    monkeypatch.setenv("REAL_MINIMAP2", resolve_tool("minimap2", None))
    out = _staged(tmp_path, variant_bam)
    with pytest.raises(subprocess.CalledProcessError):
        shell_align("x", qk_ha_ref, out, align_flags=SHIPPED, minimap2=str(lossy))
    log = (out / "align_x.log").read_text()
    assert "270 primary read(s) went into the alignment and 269 came out" in log
    assert not (out / "aligned_x.bam").exists()
    assert not list(out.glob("aligned_x.bam*"))


def test_a_record_with_no_sequence_comes_out_unmapped(tmp_path, variant_bam,
                                                      qk_ha_ref) -> None:
    with pysam.AlignmentFile(str(variant_bam)) as fh:
        reads = [r for r in fh.fetch(until_eof=True)][:5]
    ubam = tmp_path / "noseq.bam"
    with pysam.AlignmentFile(str(ubam), "wb",
                             header={"HD": {"VN": "1.6", "SO": "unknown"}}) as fh:
        for i, r in enumerate(reads):
            seg = pysam.AlignedSegment(fh.header)
            seg.query_name, seg.flag = r.query_name, 4
            if i != 2:
                seg.query_sequence = r.query_sequence
                seg.query_qualities = r.query_qualities
            fh.write(seg)
    bam = shell_align("x", qk_ha_ref, _staged(tmp_path, ubam), align_flags=SHIPPED)
    out = {r.query_name: r for r in _primaries(bam)}
    assert set(out) == {r.query_name for r in reads}
    assert out[reads[2].query_name].is_unmapped
