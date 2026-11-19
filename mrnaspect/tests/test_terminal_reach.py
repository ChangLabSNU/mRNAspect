"""QC metric #4 — terminal reach, its locked threshold, and its three exclusions.

Validated against an independent route before these tests were written: run over the real
``IVT_260407_train4`` primary BAM the module reproduces a full-pass awk measurement
exactly — QK-01 ``n_admitted`` 146,233 (awk 146,233) with 5' <=10 / <=50 / <=100 and
3' <=10 matching to within 0.004 pp, and the same on HA-L4 (103,448). These unit tests pin
the behaviours that measurement cannot see: the empty case, each exclusion, and the
guards.

The exclusions all push the reported fraction OPTIMISTIC (they remove reads whose
apparent reach is short because an end is a junction, not a terminus), so each one is
counted rather than dropped — measured bound +0.156 pp for ``SA`` and ~0.22 pp for split
children.
"""

from __future__ import annotations

import pysam
import pytest

from mrnaspect.analysis.modules.terminal_reach import (
    DEFAULTS,
    TerminalReachModule,
)

REF_LEN = 1000
_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "ref", "LN": REF_LEN}]}
)


def _read(*, start: int, span: int, flag: int = 0, name: str = "r",
          tags: list[tuple[str, object]] | None = None,
          cigar: object = "auto") -> pysam.AlignedSegment:
    """A read whose alignment covers ``span`` reference bases from ``start``."""
    r = pysam.AlignedSegment(_HEADER)
    r.query_name = name
    r.flag = flag
    r.reference_id = 0
    r.reference_start = start
    r.query_sequence = "A" * span
    r.cigartuples = [(0, span)] if cigar == "auto" else cigar
    if tags:
        r.set_tags(list(tags))
    return r


def _full_span(**kw) -> pysam.AlignedSegment:
    """Reaches both termini exactly."""
    return _read(start=0, span=REF_LEN, **kw)


# --- the measurement -------------------------------------------------------

def test_a_read_covering_the_whole_reference_is_full_length() -> None:
    m = TerminalReachModule(ref_length=REF_LEN)
    m.update(_full_span())
    s = m.snapshot()
    assert s["n_admitted"] == 1
    assert s["full_length_count"] == 1
    assert s["full_length_fraction"] == 1.0
    assert s["five_prime"]["counts"]["<=10"] == 1
    assert s["three_prime"]["counts"]["<=10"] == 1


@pytest.mark.parametrize(
    "start,expected_bin",
    [(0, "<=10"), (10, "<=10"), (11, "11-50"), (50, "11-50"),
     (51, "51-100"), (100, "51-100"), (101, ">100"), (900, ">100")],
)
def test_five_prime_distance_lands_in_the_figure3_bin(start: int, expected_bin: str) -> None:
    """`reference_start` IS the count of uncovered 5' bases, so the edges are exact."""
    m = TerminalReachModule(ref_length=REF_LEN)
    m.update(_read(start=start, span=REF_LEN - start))
    assert m.snapshot()["five_prime"]["counts"][expected_bin] == 1


def test_three_prime_distance_is_measured_from_the_reference_length() -> None:
    m = TerminalReachModule(ref_length=REF_LEN)
    m.update(_read(start=0, span=REF_LEN - 30))     # 30 nt short of the 3' end
    assert m.snapshot()["three_prime"]["counts"]["11-50"] == 1


def test_full_length_uses_the_5_prime_end_not_the_3_prime() -> None:
    """DRS threads 3'->5', so a dying read loses its 5' end; 3' reach is near-guaranteed.

    This read reaches the 3' terminus perfectly and is 200 nt short at the 5' end. It must
    NOT count as full-length — that is the whole orientation decision.
    """
    m = TerminalReachModule(ref_length=REF_LEN)
    m.update(_read(start=200, span=REF_LEN - 200))
    s = m.snapshot()
    assert s["three_prime"]["counts"]["<=10"] == 1
    assert s["full_length_count"] == 0


def test_the_threshold_is_config_sourced_and_travels_with_the_number() -> None:
    m = TerminalReachModule(ref_length=REF_LEN,
                            config={"full_length_5p_max_short_nt": 100})
    m.update(_read(start=75, span=REF_LEN - 75))    # short of 50, inside 100
    s = m.snapshot()
    assert s["full_length_count"] == 1
    assert s["full_length_5p_max_short_nt"] == 100


def test_the_shipped_default_is_the_locked_50() -> None:
    """Pins the locked value. It moves the answer 17x and flips the arm comparison's
    sign, so a change here is a method change and must be deliberate."""
    assert DEFAULTS["full_length_5p_max_short_nt"] == 50
    assert DEFAULTS["bin_edges_nt"] == [10, 50, 100]


# --- the exclusions, each counted -----------------------------------------

def test_reverse_strand_is_excluded_and_counted() -> None:
    """On a sense-strand reference a reverse read's start is biologically the 3' end."""
    m = TerminalReachModule(ref_length=REF_LEN)
    m.update(_full_span(flag=16, name="rev"))
    s = m.snapshot()
    assert s["n_admitted"] == 0
    assert s["excluded"]["reverse_strand"] == 1
    assert s["reverse_strand_rate"] == 1.0


def test_sa_carrying_primary_is_excluded_and_counted() -> None:
    """Split-mapped: at least one alignment end is a junction, not a terminus."""
    m = TerminalReachModule(ref_length=REF_LEN)
    m.update(_full_span(tags=[("SA", "ref,1,+,100M,60,0;")], name="chim"))
    s = m.snapshot()
    assert s["n_admitted"] == 0
    assert s["excluded"]["split_mapped_sa"] == 1


def test_split_child_is_excluded_and_counted() -> None:
    """dorado read-split children carry DISTINCT read ids, so read-id dedup misses them,
    and each child's ends are internal to the parent molecule."""
    m = TerminalReachModule(ref_length=REF_LEN)
    m.update(_full_span(tags=[("pi", "parent-uuid")], name="child"))
    s = m.snapshot()
    assert s["n_admitted"] == 0
    assert s["excluded"]["split_child_pi"] == 1


def test_exclusions_do_not_contaminate_the_bins() -> None:
    """Edge case: an excluded read must not also be binned.

    Double-counting here would inflate the denominator with reads the metric refused,
    which is the failure the counters exist to make visible.
    """
    m = TerminalReachModule(ref_length=REF_LEN)
    m.update(_full_span(name="ok"))
    m.update(_full_span(flag=16, name="rev"))
    m.update(_full_span(tags=[("SA", "x")], name="chim"))
    m.update(_full_span(tags=[("pi", "p")], name="child"))
    s = m.snapshot()
    assert s["n_admitted"] == 1
    assert sum(s["five_prime"]["counts"].values()) == 1
    assert s["full_length_fraction"] == 1.0
    assert sum(s["excluded"].values()) == 3


def test_a_read_with_no_alignment_end_is_counted_not_binned() -> None:
    """No walkable CIGAR: `reference_end` is None. Binning it would enter distance 0."""
    m = TerminalReachModule(ref_length=REF_LEN)
    m.update(_read(start=0, span=10, cigar=None))
    s = m.snapshot()
    assert s["n_admitted"] == 0
    assert s["excluded"]["no_alignment_end"] == 1


# --- the empty case and the guards ----------------------------------------

def test_no_reads_reports_none_not_zero() -> None:
    """A fabricated 0.0 would read as 'no read reached the terminus'."""
    s = TerminalReachModule(ref_length=REF_LEN).snapshot()
    assert s["n_admitted"] == 0
    assert s["full_length_fraction"] is None
    assert all(v is None for v in s["five_prime"]["fractions"].values())
    assert s["reverse_strand_rate"] is None


def test_the_denominator_is_named_in_the_snapshot() -> None:
    """The number must not travel without the population it is over."""
    assert TerminalReachModule(ref_length=REF_LEN).snapshot()["denominator"] == (
        "reads_assigned_to_construct_admitted")


def test_an_alignment_longer_than_the_reference_clamps_to_zero() -> None:
    """Deletions consume reference, so a span can exceed the recorded length. A negative
    distance would silently land in the nearest bin."""
    m = TerminalReachModule(ref_length=REF_LEN)
    m.update(_read(start=0, span=REF_LEN + 50))
    assert m.snapshot()["three_prime"]["counts"]["<=10"] == 1


@pytest.mark.parametrize("bad", [0, -1])
def test_a_nonpositive_reference_length_raises(bad: int) -> None:
    """It is the denominator of every terminal distance, and a bad one is silent."""
    with pytest.raises(ValueError, match="ref_length"):
        TerminalReachModule(ref_length=bad)


@pytest.mark.parametrize("edges", [[], [50, 10], [10, 10], [-5, 50]])
def test_bad_bin_edges_raise(edges: list[int]) -> None:
    with pytest.raises(ValueError, match="bin_edges_nt"):
        TerminalReachModule(ref_length=REF_LEN, config={"bin_edges_nt": edges})


def test_update_after_finalize_raises() -> None:
    m = TerminalReachModule(ref_length=REF_LEN)
    m.finalize()
    with pytest.raises(RuntimeError):
        m.update(_full_span())
