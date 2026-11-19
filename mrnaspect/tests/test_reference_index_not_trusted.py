"""A ``.fai`` beside the reference is never read: Core indexes the FASTA afresh, privately.

Found 2026-11-04, from a reference edited in a text editor. htslib trusts an existing
``.fai`` without checking it against the FASTA, so after an edit the old index keeps the
old names and offsets: with contig A deleted from an A, B reference, ``fetch("A")``
returned B's bases with no error, and ``fetch("B")`` failed. In Core that is a
construct's pileup built against another construct's sequence -- identity, mismatch and
the flags all wrong, and nothing saying so. An index built from the file as it stands
cannot be stale, and writing it outside the reference's directory also works when that
directory is read-only.
"""

from __future__ import annotations

import gc
import os
import stat
from pathlib import Path

import pysam
import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.reporting.reporter import CoAReporter


def _engine(tmp_path: Path, reference: Path) -> AnalysisEngine:
    return AnalysisEngine(CoAReporter(tmp_path / "report", min_reads=1), reference=reference)


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_an_edited_reference_is_read_as_it_stands_not_through_its_old_index(tmp_path):
    ref = _write(tmp_path / "ref.fa", ">A\nAAAAACCCCC\n>B\nGGGGGTTTTT\n")
    pysam.faidx(str(ref))                               # the sibling index, now current
    _write(ref, ">B\nGGGGGTTTTT\n")                     # A deleted, the .fai left behind
    engine = _engine(tmp_path, ref)
    fasta = engine._fasta_handle()
    assert list(fasta.references) == ["B"]
    assert fasta.fetch("B") == "GGGGGTTTTT"
    assert engine._construct_for("A") is None
    assert engine._construct_for("B").pileup.ref_seq == "GGGGGTTTTT"
    # The stale sibling was not rewritten either: Core neither read nor touched it.
    assert (tmp_path / "ref.fa.fai").read_text().startswith("A\t")


def test_a_rewrapped_reference_with_the_same_names_and_lengths_is_read_correctly(tmp_path):
    """Names and lengths unchanged, line width changed: the old offsets point mid-line.
    A check of names and lengths alone would pass this; a fresh index reads it right."""
    ref = _write(tmp_path / "ref.fa", ">A\nAAAAACCCCC\n>B\nGGGGGTTTTT\n")
    pysam.faidx(str(ref))
    _write(ref, ">A\nAAAAA\nCCCCC\n>B\nGGGGG\nTTTTT\n")
    fasta = _engine(tmp_path, ref)._fasta_handle()
    assert fasta.fetch("A") == "AAAAACCCCC" and fasta.fetch("B") == "GGGGGTTTTT"


def test_a_reference_in_a_read_only_directory_still_opens(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    folder = tmp_path / "ro"
    folder.mkdir()
    ref = _write(folder / "ref.fa", ">A\nAAAAACCCCC\n")
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert _engine(tmp_path, ref)._fasta_handle().fetch("A") == "AAAAACCCCC"
        assert not (folder / "ref.fa.fai").exists()
    finally:
        folder.chmod(stat.S_IRWXU)


def test_the_private_index_goes_with_the_engine(tmp_path):
    ref = _write(tmp_path / "ref.fa", ">A\nAAAAACCCCC\n")
    engine = _engine(tmp_path, ref)
    engine._fasta_handle()
    index_dir = Path(engine._fasta_index_dir)
    assert index_dir.is_dir() and not index_dir.is_relative_to(tmp_path)
    engine._fasta.close()
    del engine
    gc.collect()
    assert not index_dir.exists()
