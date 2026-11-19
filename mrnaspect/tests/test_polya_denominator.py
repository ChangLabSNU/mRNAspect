"""poly(A) denominator + spec-derived short threshold.

Two defects these cover, both of them "the number is fine, the denominator is missing":

  * every length statistic is conditional on dorado having produced a ``pt`` estimate, and
    dorado omits ``pt`` where the estimate FAILED — on truncated/degraded reads, i.e. the
    population the metric exists to catch. The conditioning must be visible next to the
    MEASURED numbers, not only in the no-data state.
  * "short" is undefined without the construct's designed tail length, so with no target
    configured the metric abstains instead of scoring against an invented cutoff.
"""

from __future__ import annotations

import pysam
import pytest

from mrnaspect.analysis.modules.polya import PolyAModule
from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator

_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "ref", "LN": 100}]}
)


def _read(*, pt=None, flag=0, name="r"):
    """A minimal primary read, with or without a pt (poly-A estimate) tag."""
    r = pysam.AlignedSegment(_HEADER)
    r.query_name = name
    r.flag = flag
    r.reference_id = 0
    r.reference_start = 0
    r.query_sequence = "ACGT"
    r.cigartuples = [(0, 4)]
    if pt is not None:
        r.set_tag("pt", int(pt), "i")
    return r


def _feed(module, *, with_pt, missing):
    for i, pt in enumerate(with_pt):
        module.update(_read(pt=pt, name=f"hit{i}"))
    for i in range(missing):
        module.update(_read(name=f"miss{i}"))


# --- the denominator --------------------------------------------------------

def test_pt_coverage_reports_the_conditioning():
    # 6 reads estimated, 4 not: every length stat below rests on 6/10, not on 10.
    m = PolyAModule(config={"target_length": 100, "short_fraction": 0.8})
    _feed(m, with_pt=[120, 130, 140, 20, 30, 150], missing=4)
    snap = m.snapshot()

    assert snap["reads_processed"] == 10
    assert snap["n_with_pt"] == 6
    assert snap["missing_pt"] == 4
    assert snap["pt_coverage"] == 0.6
    assert snap["missing_pt_ratio"] == 0.4
    # The length stats are over the 6 — that is correct and unavoidable, and is exactly
    # what pt_coverage exists to disclose.
    assert snap["polya_length"]["n"] == 6


def test_short_tail_bounds_bracket_the_conditional_estimate():
    # target 100 * 0.8 => cutoff 80 nt. Short calls: 20, 30 => s=2, k=6, m=4, N=10.
    m = PolyAModule(config={"target_length": 100, "short_fraction": 0.8})
    _feed(m, with_pt=[120, 130, 140, 20, 30, 150], missing=4)
    st = m.snapshot()["short_tail"]

    assert st["status"] == "measured"
    assert st["threshold"] == 80.0
    assert st["count"] == 2
    assert st["ratio"] == pytest.approx(2 / 6)     # conditional on a pt call (MAR only)
    assert st["ratio_lower"] == pytest.approx(2 / 10)   # all 4 missing assumed LONG
    assert st["ratio_upper"] == pytest.approx(6 / 10)   # all 4 missing assumed SHORT
    # The assumption-free interval must bracket the conditional point estimate, and its
    # width is exactly the missing fraction.
    assert st["ratio_lower"] <= st["ratio"] <= st["ratio_upper"]
    assert st["ratio_upper"] - st["ratio_lower"] == pytest.approx(0.4)


def test_denominator_swap_alone_would_have_been_optimistic():
    """Guards the reasoning, not just the value: s/N < s/k, i.e. the naive 'use total
    reads' fix moves the short rate DOWN — it silently assumes every unmeasured read had
    a long tail, which is the pass-direction error a QC system must not make."""
    m = PolyAModule(config={"target_length": 100, "short_fraction": 0.8})
    _feed(m, with_pt=[120, 20], missing=8)
    st = m.snapshot()["short_tail"]
    assert st["ratio_lower"] < st["ratio"]


# --- no fabricated zero -----------------------------------------------------

def test_no_pt_at_all_gives_none_not_zero():
    # The bonito path emits no pt at all. A 0.0 short ratio here would read as
    # "no short tails found" when in truth nothing was measured.
    m = PolyAModule(config={"target_length": 100, "short_fraction": 0.8})
    _feed(m, with_pt=[], missing=5)
    st = m.snapshot()["short_tail"]
    assert st["ratio"] is None
    assert st["ratio_lower"] == 0.0 and st["ratio_upper"] == 1.0  # fully uninformative
    assert m.snapshot()["pt_coverage"] == 0.0


def test_no_reads_at_all_leaves_coverage_none():
    snap = PolyAModule(config={"target_length": 100, "short_fraction": 0.8}).snapshot()
    assert snap["pt_coverage"] is None and snap["missing_pt_ratio"] is None


# --- the threshold is a spec, not a default ---------------------------------

def test_without_a_target_the_metric_abstains():
    m = PolyAModule()          # shipped defaults: no target
    _feed(m, with_pt=[10, 20, 300], missing=0)
    snap = m.snapshot()
    st = snap["short_tail"]

    assert st["status"] == "not_measured"
    assert st["count"] is None and st["ratio"] is None
    assert st["threshold"] is None
    assert "target_length" in st["reason"]
    # The DISTRIBUTION still reports — it needs no target.
    assert snap["polya_distribution"]["median"] is not None
    assert snap["polya_length"]["n"] == 3


def test_threshold_derives_from_target_and_fraction():
    """Both are needed: the fraction is a spec, so it has no default."""
    assert PolyAModule(config={"target_length": 150})._short_threshold is None
    assert PolyAModule(
        config={"target_length": 150, "short_fraction": 0.8})._short_threshold == 120.0
    assert PolyAModule(
        config={"target_length": 150, "short_fraction": 0.5})._short_threshold == 75.0


def test_absolute_threshold_overrides_the_derived_one():
    m = PolyAModule(config={"target_length": 150, "short_threshold": 42})
    assert m._short_threshold == 42.0


@pytest.mark.parametrize("cfg", [
    {"target_length": 0},
    {"target_length": -5},
    {"target_length": 100, "short_fraction": 0},
    {"target_length": 100, "short_fraction": 1.5},
    {"short_threshold": -1},
])
def test_nonsense_spec_fails_loud(cfg):
    with pytest.raises(ValueError):
        PolyAModule(config=cfg)


# --- the integrator carries it out ------------------------------------------

def test_integrator_surfaces_coverage_on_the_measured_branch():
    m = PolyAModule(config={"target_length": 100, "short_fraction": 0.8})
    _feed(m, with_pt=[120, 130, 20], missing=7)
    entry = QCResultIntegrator().integrate(
        {"polya": m.snapshot()})["metrics"]["polya_tail"]

    assert entry["status"] == "measured"
    # The regression: these used to exist ONLY in the no_data branch, so a median could be
    # read off a CoA with no idea it rested on 3 of 10 reads.
    assert entry["pt_coverage"] == 0.3
    assert entry["n_with_pt"] == 3 and entry["reads_processed"] == 10
    assert entry["missing_pt"] == 7
    assert entry["short_tail_ratio_lower"] is not None
    assert entry["short_tail_ratio_upper"] is not None
    assert entry["short_tail_target_length"] == 100


def test_integrator_reports_short_tail_not_measured_without_a_target():
    m = PolyAModule()
    _feed(m, with_pt=[120, 130], missing=0)
    entry = QCResultIntegrator().integrate(
        {"polya": m.snapshot()})["metrics"]["polya_tail"]
    assert entry["status"] == "measured"          # lengths ARE measured
    assert entry["short_tail_status"] == "not_measured"   # the spec judgement is not
    assert entry["short_tail_ratio"] is None


def test_a_target_without_a_cutoff_is_not_measured_and_says_so():
    """No fraction is assumed: a target alone names the design, not what counts as short."""
    st = PolyAModule(config={"target_length": 120}).snapshot()["short_tail"]
    assert st["status"] == "not_measured" and st["threshold"] is None
    assert "short_fraction" in st["reason"] and "short_threshold" in st["reason"]
