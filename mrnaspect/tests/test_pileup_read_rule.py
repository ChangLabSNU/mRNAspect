"""Identity and error use the read population poly(A) and full length use.

Reverse-strand, chimeric (SA) and dorado-split (pi) reads are counted and left out of the
pileup. On the OCT run, 97% of SA reads carried a second construct; their primary piece
read higher than the other reads on m1psi.
"""

from __future__ import annotations

import pysam

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.analysis.modules.consensus import ConsensusIdentityModule
from mrnaspect.analysis.shared_pileup import SharedPileup
from mrnaspect.reporting.reporter import CoAReporter

_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "c1", "LN": 8}]})


def _read(seq: str, name: str, *, reverse=False, tag=None) -> pysam.AlignedSegment:
    r = pysam.AlignedSegment(_HEADER)
    r.query_name, r.reference_id, r.reference_start = name, 0, 0
    r.flag = 16 if reverse else 0
    r.query_sequence, r.cigartuples = seq, [(0, len(seq))]
    if tag:
        r.set_tag(*tag)
    return r


def test_the_three_are_counted_and_left_out() -> None:
    p = SharedPileup("c1", "ACGTACGT")
    p.update(_read("ACGTACGT", "ok"))
    p.update(_read("ACGTACGT", "rev", reverse=True))
    p.update(_read("ACGTACGT", "sa", tag=("SA", "c2,1,+,8M,60,0;", "Z")))
    p.update(_read("ACGTACGT", "pi", tag=("pi", "parent", "Z")))
    snap = p.snapshot()
    assert snap["reads_processed"] == 1 and int(p.depth[0]) == 1
    assert snap["excluded"] == {"reverse_strand": 1, "split_mapped_sa": 1,
                                "split_child_pi": 1}


def test_a_chimera_does_not_move_identity() -> None:
    clean = SharedPileup("c1", "ACGTACGT")
    clean.update(_read("ACGTACGT", "ok"))
    mixed = SharedPileup("c1", "ACGTACGT")
    mixed.update(_read("ACGTACGT", "ok"))
    mixed.update(_read("TTTTTTTT", "sa", tag=("SA", "c2,1,+,8M,60,0;", "Z")))
    ident = [ConsensusIdentityModule(p, config={"min_depth": 1}).snapshot()[
        "consensus_identity_pct"] for p in (clean, mixed)]
    assert ident == [100.0, 100.0]


def test_left_out_reads_are_not_reported_as_malformed(
    tmp_path, variant_bam, qk_ha_ref
) -> None:
    """n_not_in_pileup is the malformed count; the read rule has its own."""
    reporter = CoAReporter(tmp_path / "rep", min_reads=1)
    engine = AnalysisEngine(reporter, reference=qk_ha_ref)
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    snaps = {name: track.snapshot() for name, track in engine._constructs.items()}
    for name, snap in snaps.items():
        sa = snap["pileup"]["excluded"]["split_mapped_sa"]
        assert snap["n_not_in_pileup"] == 0, name
        assert snap["n_reads"] == snap["pileup"]["reads_processed"] + sa, name
    assert {n: s["pileup"]["excluded"]["split_mapped_sa"]
            for n, s in snaps.items()} == {"QK-09": 2, "HA-WT": 4}
