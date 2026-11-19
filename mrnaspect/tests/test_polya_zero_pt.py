"""`pt == 0` is a failure sentinel, not a zero-length tail.

dorado-server does not omit the tag when its poly-A estimate fails — it writes `pt:i:0`
and sets the `pa` sample boundaries to -1. Measured over 30,626 rt180 reads the split was
total (3,104 zeros, ALL with pa = -1; 27,522 non-zeros, ALL with real boundaries), so
taking the zero at face value counted a failed measurement as a zero-length tail.

What that cost was not just a low median. `missing_pt` read 0 and `pt_coverage` read 1.00
on data where 10.1 % of reads had no measurement, and the short-tail bounds — whose width
IS `missing_pt_ratio` — collapsed to a point. The denominator disclosure asserted
certainty it did not have, which is worse than saying nothing.

These tests pin the adjudication: boundaries present and non-negative => a real zero that
counts; anything else => missing, with the reason named.
"""

from __future__ import annotations

import array

import pysam
import pytest

from mrnaspect.analysis.modules.polya import PolyAModule

_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "ref", "LN": 100}]}
)

# Shapes taken from real rt180 reads: a measured tail vs a failed estimate.
PA_FOUND = [3300, 3399, 5898, -1, -1]
PA_NONE = [3300, -1, -1, -1, -1]


MODEL = "rna004_130bps_hac@v5.3.0"
RG = f"0741d988-979b-4fd1-abf8-d040f567a442_{MODEL}"


def _read(pt=None, pa=None, *, name="r", rg=RG):
    """A read whose RG names a model in DORADO_POLYA_CONSTANTS, so the model's
    offset_correction (10.88 for hac@v5.3.0) is known."""
    r = pysam.AlignedSegment(_HEADER)
    r.query_name = name
    if rg is not None:
        r.set_tag("RG", rg, "Z")
    r.flag = 0
    r.reference_id = 0
    r.reference_start = 0
    r.query_sequence = "ACGT"
    r.cigartuples = [(0, 4)]
    if pt is not None:
        r.set_tag("pt", int(pt), "i")
    if pa is not None:
        r.set_tag("pa", array.array("i", pa))
    return r


def _snap(reads, **cfg):
    m = PolyAModule(cfg or None)
    for i, r in enumerate(reads):
        r.query_name = f"r{i}"
        m.update(r)
    return m


# --- the four cases ---------------------------------------------------------

def test_zero_without_boundaries_is_missing_not_a_length():
    snap = _snap([_read(80, PA_FOUND), _read(0, PA_NONE)]).snapshot()

    assert snap["missing_pt_breakdown"]["no_border"] == 1
    assert snap["missing_pt"] == 1
    # The zero never reached the accumulators, so the distribution is the one real tail.
    assert snap["polya_length"]["n"] == 1
    assert snap["polya_length"]["mean"] == pytest.approx(80.0)
    # And the denominator now tells the truth: 1 of 2 reads was measured.
    assert snap["n_with_pt"] == 1
    assert snap["pt_coverage"] == pytest.approx(0.5)


def test_zero_with_boundaries_is_not_a_measured_zero():
    """Boundaries found + pt 0 means the FORMULA clamped, so the tail is <= 10.88 nt.

    `pt = window/(spb*speed_correction) - offset_correction` is clamped at zero and the
    offset is a subtraction, so any tail below it comes out negative and ships as 0. That
    is a left-censored observation, not a length: putting 0 into the distribution would be
    a fabricated value (the true tail lies in (0, 10.88]).
    """
    snap = _snap([_read(80, PA_FOUND), _read(0, PA_FOUND)]).snapshot()

    assert snap["n_zero_with_border"] == 1
    assert snap["missing_pt"] == 0                 # not missing — something IS known
    assert snap["polya_length"]["n"] == 1          # ...but it is not a length
    assert snap["polya_length"]["mean"] == pytest.approx(80.0)
    assert snap["model_offset_correction_nt"] == pytest.approx(10.88)
    # pt_coverage is the fraction the DISTRIBUTION speaks for, so these are excluded.
    assert snap["pt_coverage"] == pytest.approx(0.5)
    assert snap["zero_with_border_ratio"] == pytest.approx(0.5)


def test_zero_with_border_is_not_scored_short_and_widens_the_bound():
    """The class mixes short tails with FAILED basecalls, inseparably.

    Against `pt > 0` reads it is half the length and unmapped 59.2% vs 7.5%, and its
    high-spb fraction is 11-56 bases and essentially all unmapped. Scoring it short would
    import that contamination into QC #2; scoring it long would hide real short tails. So
    it is scored neither and the interval carries it.
    """
    reads = [_read(80, PA_FOUND), _read(0, PA_FOUND), _read(0, PA_FOUND)]
    snap = _snap(reads, target_length=90, short_fraction=0.8).snapshot()
    st = snap["short_tail"]

    assert snap["n_zero_with_border"] == 2
    assert st["count"] == 0                                # not short
    assert st["count_zero_with_border"] == 2               # but visible
    # Point estimate is over reads that HAVE a length: the single 80 nt tail, not short.
    assert st["ratio"] == pytest.approx(0.0)
    assert st["ratio_denominator"] == "reads_with_a_length"
    # ...and the interval opens to cover them: 0/3 known short, up to 2/3 if all were.
    assert st["ratio_lower"] == pytest.approx(0.0)
    assert st["ratio_upper"] == pytest.approx(2 / 3)


def test_zero_with_border_stays_its_own_counter_not_folded_into_missing_pt():
    """Same arithmetic, different reasons — and the reasons must stay readable.

    Both widen the upper bound, but `missing_pt` means "dorado found nothing" while
    `zero_with_border` means "it found the region and the formula clamped". Collapsing
    them would lose which one a run is suffering from.
    """
    snap = _snap([_read(80, PA_FOUND), _read(0, PA_FOUND), _read(0, PA_NONE)],
                 target_length=90, short_fraction=0.8).snapshot()
    st = snap["short_tail"]

    assert snap["missing_pt"] == 1                          # only the no-border read
    assert snap["missing_pt_breakdown"]["no_border"] == 1
    assert snap["n_zero_with_border"] == 1                  # counted separately
    # Both unmeasured reads widen the bound, and neither is counted short.
    assert st["count"] == 0
    assert st["ratio_lower"] == pytest.approx(0.0)
    assert st["ratio_upper"] == pytest.approx(2 / 3)


def test_zero_without_pa_tag_is_missing_and_warns_once(caplog):
    m = PolyAModule()
    for i in range(3):
        m.update(_read(0, None, name=f"r{i}"))   # pt but no pa to adjudicate with
    snap = m.snapshot()

    assert snap["missing_pt_breakdown"]["no_pa"] == 3
    assert snap["polya_length"]["n"] == 0
    assert snap["pt_coverage"] == pytest.approx(0.0)
    # Warned, but only once — three reads must not become three log lines.
    assert sum("no pa tag" in r.message for r in caplog.records) <= 1


def test_absent_pt_tag_still_counts_as_no_tag():
    snap = _snap([_read(80, PA_FOUND), _read(None, PA_FOUND)]).snapshot()
    assert snap["missing_pt_breakdown"] == {"no_tag": 1, "no_border": 0, "no_pa": 0}


def test_breakdown_sums_to_missing_pt():
    snap = _snap([
        _read(80, PA_FOUND), _read(None, PA_FOUND), _read(0, PA_NONE), _read(0, None),
    ]).snapshot()
    b = snap["missing_pt_breakdown"]
    assert (b["no_tag"], b["no_border"], b["no_pa"]) == (1, 1, 1)
    assert snap["missing_pt"] == 3
    assert snap["reads_processed"] == 4
    assert snap["n_with_pt"] == 1


# --- the consequence that matters on a CoA ----------------------------------

def test_failed_estimates_widen_the_short_tail_bounds_instead_of_counting_as_short():
    """Five real 80 nt tails and five failures, against a 90 nt design (threshold 72).

    Old behaviour: the five zeros were each < 72, so short = 5, ratio = 0.5, missing = 0,
    and the bounds pinned at [0.5, 0.5] — 'half this run is short, and we are sure'.
    Now: none of the five is called short, and the bounds open to [0.0, 0.5] — 'none of
    what we measured is short, and we could not measure half of it'. Same data, and the
    second statement is the one the data supports.
    """
    reads = [_read(80, PA_FOUND) for _ in range(5)] + [_read(0, PA_NONE) for _ in range(5)]
    snap = _snap(reads, target_length=90, short_fraction=0.8).snapshot()
    st = snap["short_tail"]

    assert snap["missing_pt_breakdown"]["no_border"] == 5
    assert st["count"] == 0
    assert st["ratio"] == pytest.approx(0.0)          # of what was measured
    assert st["ratio_lower"] == pytest.approx(0.0)
    assert st["ratio_upper"] == pytest.approx(0.5)    # width == missing_pt_ratio
    assert snap["missing_pt_ratio"] == pytest.approx(0.5)
