"""delta_pct, the alignment it was defined under, and why the correction moved to snapshot.

`delta_pct` is a MEAN over the construct's reads, so it is not known until the last one.
Applying the correction per read would use a different factor early and late, and the
reported tail length would then depend on the order reads arrived in — which for a
streaming pipeline means on how MinKNOW happened to write its POD5s.

The correction is affine in pt, so the raw statistics can carry the whole run and be
mapped once at snapshot. `test_the_answer_does_not_depend_on_read_order` is the test that
whole change exists for.
"""

from __future__ import annotations

import random

import pysam
import pytest

from mrnaspect.analysis.modules.polya import PolyAModule
from mrnaspect.analysis.polya_calibration import (
    CONSTANTS,
    DELTA_FIT_FLAGS,
    DeltaAccumulator,
    affine_terms,
    correct_pt,
    delta_flags_match,
)

_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "r", "LN": 4000}]})

STOCK = "rna004_130bps_hac@v5.3.0"
V1 = "m1psi_tuned_v1"
FIT_FLAGS = "-ax map-ont -k7 -w5 --secondary=no -y"
REF_LEN, REF_F, REF_U12 = 1183, 0.24598478444632291, 3


def _ref() -> str:
    n_u = round(REF_F * REF_LEN)
    return ("T" * (n_u - REF_U12) + "A" * (REF_LEN - 12 - (n_u - REF_U12))
            + "T" * REF_U12 + "A" * (12 - REF_U12))


def _read(pt, model, *, dele=0, ins=0, span=1000, name="x"):
    r = pysam.AlignedSegment(_HEADER)
    r.query_name, r.flag, r.reference_id = name, 0, 0
    r.reference_start = 0
    r.query_sequence = "A" * (span - dele + ins)
    r.cigartuples = ([(0, span - dele)]
                     + ([(2, dele)] if dele else [])
                     + ([(1, ins)] if ins else []))
    r.set_tag("pt", int(pt), "i")
    r.set_tag("RG", f"0741d988-979b-4fd1-abf8-d040f567a442_{model}", "Z")
    return r


def _module(model=STOCK, arm="m1psi", flags=FIT_FLAGS):
    return PolyAModule({"minimap2_flags": flags}, arm=arm, ref_seq=_ref())


# --- the reason the correction moved to snapshot ----------------------------

def test_the_answer_does_not_depend_on_read_order() -> None:
    """Same reads, shuffled: the same corrected mean, exactly.

    Under a per-read correction the first read would be corrected with delta_pct from one
    read and the last with delta_pct from all of them, so a reordered POD5 set would give
    a different tail length for the same data.
    """
    reads = [(70 + i, 10 + i * 3, i) for i in range(25)]   # pt, deletions, insertions
    answers = set()
    for seed in range(5):
        order = reads[:]
        random.Random(seed).shuffle(order)
        m = _module()
        for i, (pt, d, ins) in enumerate(order):
            m.update(_read(pt, STOCK, dele=d, ins=ins, name=f"r{i}"))
        answers.add(m.snapshot()["polya_length_corrected"]["mean"])
    assert len(answers) == 1, f"order changed the answer: {sorted(answers)}"


# --- delta_pct itself -------------------------------------------------------

def test_delta_is_deletions_minus_insertions_over_reference_span() -> None:
    acc = DeltaAccumulator()
    acc.update(_read(90, STOCK, dele=30, ins=5, span=1000))
    assert acc.value == pytest.approx((30 - 5) / 1000 * 100)


def test_delta_averages_over_reads_not_over_bases() -> None:
    """Each read contributes its own ratio once, whatever its length."""
    acc = DeltaAccumulator()
    acc.update(_read(90, STOCK, dele=10, ins=0, span=100))     # 10%
    acc.update(_read(90, STOCK, dele=0, ins=0, span=10_000))   # 0%
    assert acc.value == pytest.approx(5.0)


def test_insertions_can_make_delta_negative() -> None:
    acc = DeltaAccumulator()
    acc.update(_read(90, STOCK, dele=0, ins=40, span=1000))
    assert acc.value == pytest.approx(-4.0)


def test_a_read_with_no_reference_span_is_skipped_not_counted_as_zero() -> None:
    acc = DeltaAccumulator()
    r = pysam.AlignedSegment(_HEADER)
    r.query_name, r.flag, r.reference_id, r.reference_start = "u", 4, -1, -1
    acc.update(r)
    assert acc.value is None and acc.to_dict()["n_skipped"] == 1


# --- the alignment guard ----------------------------------------------------

def test_the_fitted_flags_are_accepted() -> None:
    assert delta_flags_match(FIT_FLAGS)


def test_spacing_is_not_load_bearing() -> None:
    assert delta_flags_match("-ax map-ont -k 7 -w 5 --secondary=no -y")


def test_irrelevant_differences_do_not_block_it() -> None:
    """Thread count, reference path and minimap2 build do not change D or I."""
    assert delta_flags_match(FIT_FLAGS + " -t 16 /some/other/ref.fa")


@pytest.mark.parametrize("flags", [
    "-ax map-ont -k14 -w10 --secondary=no -y",   # the production primary pass
    "-ax map-ont -k7 -w5 -y",                    # secondary alignments allowed
    "", None,
])
def test_anything_else_withholds_a_delta_form_correction(flags) -> None:
    m = _module(flags=flags)
    m.update(_read(90, STOCK, dele=30, ins=5))
    snap = m.snapshot()
    assert snap["correction"]["status"] == "withheld"
    assert "delta_pct" in snap["correction"]["reason"]
    assert snap["polya_length_corrected"]["mean"] is None


def test_the_guard_does_not_touch_the_U_arm() -> None:
    """delta only enters the m1psi factor."""
    m = _module(arm="U", flags="-ax map-ont -k14 -w10 --secondary=no -y")
    m.update(_read(90, STOCK))
    assert m.snapshot()["correction"]["status"] == "applied"


def test_the_guard_does_not_touch_a_model_that_does_not_need_delta() -> None:
    """v1 is form 'f': m_delta_prime_per_pct is 0, so the flags are irrelevant to it."""
    assert CONSTANTS[V1]["m_delta_prime_per_pct"] == 0.0
    m = _module(model=V1, flags="-ax map-ont -k14 -w10 --secondary=no -y")
    m.update(_read(90, V1))
    assert m.snapshot()["correction"]["status"] == "applied"


def test_the_fitted_flag_set_is_the_ones_that_change_indels() -> None:
    assert set(DELTA_FIT_FLAGS) == {"-k7", "-w5", "--secondary=no"}


# --- the affine map is the same arithmetic ----------------------------------

@pytest.mark.parametrize("pt", [0.0, 45.0, 90.0, 428.0])
def test_affine_terms_reproduce_the_direct_formula(pt) -> None:
    c = CONSTANTS[V1]
    F, k = affine_terms(arm="m1psi", f=REF_F, u12=REF_U12, constants=c)
    direct = correct_pt(pt, arm="m1psi", f=REF_F, u12=REF_U12, constants=c)
    assert F * pt + k == pytest.approx(direct, abs=1e-9)


def test_the_corrected_spread_scales_and_does_not_shift() -> None:
    """std -> F*std, not F*std + c. A shift would invent variance."""
    m = _module(model=V1)
    for i, pt in enumerate([80, 90, 100]):
        m.update(_read(pt, V1, name=f"r{i}"))
    snap = m.snapshot()
    F, _ = affine_terms(arm="m1psi", f=REF_F, u12=REF_U12, constants=CONSTANTS[V1])
    assert snap["polya_length_corrected"]["std"] == pytest.approx(
        F * snap["polya_length"]["std"], abs=1e-3)


def test_the_corrected_histogram_moves_its_edges_not_its_counts() -> None:
    """Nothing is re-binned, so no value lands in a different overflow than it did raw."""
    m = _module(model=V1)
    for i, pt in enumerate([80, 90, 100, 110]):
        m.update(_read(pt, V1, name=f"r{i}"))
    snap = m.snapshot()
    raw, corr = snap["polya_distribution"], snap["polya_distribution_corrected"]
    assert corr["bin_counts"] == raw["bin_counts"]
    assert corr["underflow"] == raw["underflow"] and corr["overflow"] == raw["overflow"]
    assert corr["bin_min"] != raw["bin_min"]


def test_the_spec_verdict_names_the_ruler_it_used() -> None:
    """With a correction applied, the verdict is on the corrected length, and says so."""
    m = PolyAModule({"minimap2_flags": FIT_FLAGS, "target_length": 100, "short_fraction": 0.8},
                    arm="m1psi", ref_seq=_ref())
    m.update(_read(90, V1))
    assert m.snapshot()["short_tail"]["judged_on"] == "corrected"


def test_a_short_tail_is_judged_on_the_corrected_length() -> None:
    """The cutoff is a designed length, so it meets the corrected tail, not dorado's."""
    pts = [70, 75, 78, 80, 85, 90]
    m = PolyAModule({"minimap2_flags": FIT_FLAGS, "target_length": 100, "short_fraction": 0.8},
                    arm="m1psi", ref_seq=_ref())
    for i, pt in enumerate(pts):
        m.update(_read(pt, V1, name=f"r{i}"))
    snap = m.snapshot()
    F, c = snap["correction"]["factor"], snap["correction"]["shift_nt"]
    cutoff = snap["short_tail"]["threshold"]
    corrected_short = sum(F * pt + c < cutoff for pt in pts)
    assert snap["short_tail"]["count"] == corrected_short == 1
    assert sum(pt < cutoff for pt in pts) == 3, "raw pt would have called three"
