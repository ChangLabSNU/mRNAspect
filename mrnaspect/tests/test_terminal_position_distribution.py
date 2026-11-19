"""Where along the reference alignments begin and end — the truncation figure's data.

The 4-bucket distance ladder in `terminal_reach` answers "did the read reach the
terminus?". It cannot answer "where did it stop?", which is the shape a manufacturer
reads for truncation. These guards cover the two histograms that do, and in particular
the off-by-one that would delete the most important population from the figure.
"""

from __future__ import annotations

import pysam
import pytest

from mrnaspect.analysis.modules.terminal_reach import TerminalReachModule

REF_LEN = 1000
_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "ref", "LN": REF_LEN}]}
)


def _read(*, start: int, span: int, flag: int = 0, name: str = "r",
          tags: list[tuple[str, object]] | None = None) -> pysam.AlignedSegment:
    r = pysam.AlignedSegment(_HEADER)
    r.query_name = name
    r.flag = flag
    r.reference_id = 0
    r.reference_start = start
    r.query_sequence = "A" * span
    r.cigartuples = [(0, span)]
    if tags:
        r.set_tags(list(tags))
    return r


def _mod(**over) -> TerminalReachModule:
    return TerminalReachModule(ref_length=REF_LEN, config=over or None)


def test_full_length_reads_are_in_the_last_bin_not_overflow():
    """The defect this design exists to avoid.

    pysam's `reference_end` is EXCLUSIVE, so a read covering the final base reports
    end == ref_length. Fed straight into a half-open [0, ref_length) histogram that value
    is out of range, and every full-length read — the population the figure is for —
    would vanish into `overflow` while the figure showed only the truncated ones.
    """
    m = _mod(position_histogram_bins=10)          # 100 nt per bin
    m.update(_read(start=0, span=REF_LEN, name="full"))

    d = m.snapshot()["end_position_distribution"]
    assert d["overflow"] == 0
    assert d["bin_counts"][-1] == 1               # last bin (900-999), not escaped
    assert d["n"] == 1


def test_positions_land_in_the_right_bins_at_both_ends():
    """start = 5'-most covered position, end = 3'-most. A read 200..599 inclusive."""
    m = _mod(position_histogram_bins=10)          # 100 nt per bin
    m.update(_read(start=200, span=400, name="mid"))

    start_d = m.snapshot()["start_position_distribution"]
    end_d = m.snapshot()["end_position_distribution"]
    assert start_d["bin_counts"][2] == 1          # position 200 -> bin 2
    assert end_d["bin_counts"][5] == 1            # position 599 -> bin 5
    assert sum(start_d["bin_counts"]) == sum(end_d["bin_counts"]) == 1


def test_denominator_is_n_admitted_and_exclusions_never_reach_the_figure():
    """A coordinate from an excluded read is not a molecule terminus. If any of the three
    exclusions leaked in, the histograms would out-count the ladder beside them."""
    m = _mod()
    m.update(_read(start=0, span=500, name="ok"))
    m.update(_read(start=0, span=500, flag=16, name="reverse"))
    m.update(_read(start=0, span=500, name="split", tags=[("SA", "x", "Z")]))
    m.update(_read(start=0, span=500, name="child", tags=[("pi", "parent", "Z")]))

    snap = m.snapshot()
    assert snap["n_admitted"] == 1
    assert snap["start_position_distribution"]["n"] == 1
    assert snap["end_position_distribution"]["n"] == 1
    assert snap["excluded"] == {"reverse_strand": 1, "split_mapped_sa": 1,
                                "split_child_pi": 1, "no_alignment_end": 0}


def test_geometry_follows_the_reference_not_a_shipped_constant():
    """Range is [0, ref_length) taken from the loaded reference, so a 1.2 kb construct and
    a 2.5 kb one each get a figure spanning their own length."""
    short = TerminalReachModule(ref_length=1200).snapshot()["end_position_distribution"]
    long_ = TerminalReachModule(ref_length=2500).snapshot()["end_position_distribution"]
    assert short["bin_max"] == 1200.0 and long_["bin_max"] == 2500.0
    assert short["bin_width"] == pytest.approx(6.0)     # 1200 / 200
    assert long_["bin_width"] == pytest.approx(12.5)    # 2500 / 200
    assert short["n"] == 0 and len(short["bin_counts"]) == 200   # drawable before read 1


def test_bin_count_is_validated():
    with pytest.raises(ValueError, match="position_histogram_bins"):
        TerminalReachModule(ref_length=REF_LEN, config={"position_histogram_bins": 0})


# --- the joint (5' short, 3' short) plane ----------------------------------

def test_joint_cell_indices_follow_the_two_distances():
    """One read 200..599 on a 1000 nt reference at 10 bins/axis (100 nt per cell):
    d5 = 200 -> i = 2, d3 = 1000 - 600 = 400 -> j = 4."""
    m = _mod(joint_histogram_bins=10)
    m.update(_read(start=200, span=400))

    j = m.snapshot()["joint_shortfall_distribution"]
    assert j["cells"] == [[2, 4, 1]]
    assert j["n"] == 1 and j["out_of_range"] == 0
    assert j["axes"] == ["five_prime_short_nt", "three_prime_short_nt"]
    assert j["bin_width"] == 100.0


def test_joint_is_sparse_and_deterministically_ordered():
    """Empty cells are absent, not zeros, and the same reads always serialise identically
    — a provenance stamp over this block must not move because a dict iterated
    differently."""
    m = _mod(joint_histogram_bins=10)
    # Insertion order is deliberately NEITHER sorted NOR its reverse: with only two cells
    # a reversal coincides with sorting, and the guard passes while the sort is gone.
    for k in range(3):
        m.update(_read(start=500, span=200, name=f"a{k}"))   # (5, 3), all one cell
    m.update(_read(start=100, span=800, name="b"))            # (1, 1)
    m.update(_read(start=300, span=100, name="c"))            # (3, 6)

    j = m.snapshot()["joint_shortfall_distribution"]
    assert j["cells"] == [[1, 1, 1], [3, 6, 1], [5, 3, 3]]    # sorted, 3 of 100 cells
    assert j["n"] == 5


def test_joint_carries_alignment_length_on_the_anti_diagonal():
    """d5 + d3 == ref_length - aligned_span, which is what makes an anti-diagonal an
    iso-length contour. Two reads of the SAME span sit on one anti-diagonal; a shorter
    one sits further out."""
    m = _mod(joint_histogram_bins=10)
    m.update(_read(start=100, span=400, name="span400a"))     # d5=100, d3=500
    m.update(_read(start=400, span=400, name="span400b"))     # d5=400, d3=200
    m.update(_read(start=400, span=100, name="span100"))      # d5=400, d3=500

    cells = {(i, j): c for i, j, c in
             m.snapshot()["joint_shortfall_distribution"]["cells"]}
    same = [(i, j) for (i, j) in cells if (i + j) == 6]       # (1,5) and (4,2)
    assert sorted(same) == [(1, 5), (4, 2)]                   # equal span, one diagonal
    assert (4, 5) in cells                                    # shorter span, further out


def test_joint_counts_only_admitted_reads():
    m = _mod(joint_histogram_bins=10)
    m.update(_read(start=0, span=500, name="ok"))
    m.update(_read(start=0, span=500, flag=16, name="reverse"))
    m.update(_read(start=0, span=500, name="split", tags=[("SA", "x", "Z")]))
    m.update(_read(start=0, span=500, name="child", tags=[("pi", "p", "Z")]))

    snap = m.snapshot()
    assert snap["joint_shortfall_distribution"]["n"] == snap["n_admitted"] == 1


def test_a_reference_mismatch_is_counted_not_clamped():
    """If the BAM was aligned against a LONGER reference than the one this process
    loaded, a read can start past `ref_length`. Clamping would fold that into the edge
    cell, where it would read as a real population of fully-truncated reads."""
    m = TerminalReachModule(ref_length=300, config={"joint_histogram_bins": 10})
    big = pysam.AlignmentHeader.from_dict(
        {"HD": {"VN": "1.6"}, "SQ": [{"SN": "ref", "LN": 5000}]})
    r = pysam.AlignedSegment(big)
    r.query_name = "beyond"
    r.flag = 0
    r.reference_id = 0
    r.reference_start = 4000          # past the loaded reference entirely
    r.query_sequence = "A" * 100
    r.cigartuples = [(0, 100)]
    m.update(r)

    j = m.snapshot()["joint_shortfall_distribution"]
    assert j["out_of_range"] == 1
    assert j["cells"] == []           # nothing invented at the edge
    assert j["n"] == 0


def test_joint_bin_count_is_validated():
    with pytest.raises(ValueError, match="joint_histogram_bins"):
        TerminalReachModule(ref_length=REF_LEN, config={"joint_histogram_bins": 0})
