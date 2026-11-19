"""Poly(A) Q1/median/Q3 are exact order statistics of the integer pt, not read off bins.

Decided 2026-10-30, after a parity check against the ruler's fitting analysis: on the October run Core's poly(A) median
sat above the exact median of the same reads in 65 of 65 cells, by +0.6 nt on average,
because the 5-nt histogram spreads each bin's count across the bin. The ruler constants
were fitted on exact medians, so the corrected tail carried the whole offset. The bins
stay, for the figure.
"""

from __future__ import annotations

import numpy as np
import pytest

from mrnaspect.analysis.modules.polya import PolyAModule
from mrnaspect.analysis.polya_calibration import (
    affine_terms,
    reference_composition,
    resolve_model,
)
from mrnaspect.analysis.streaming_interface import (
    HistogramAccumulator,
    IntegerQuantileAccumulator,
)

from .test_polya_correction_v2 import V1, _read, _ref


def _counted(values) -> IntegerQuantileAccumulator:
    acc = IntegerQuantileAccumulator()
    for v in values:
        acc.update(float(v))
    return acc


#: The module's default geometry: 1 nt bins centred on the integers (since 2026-11-09).
FIGURE_BINS = (-0.5, 499.5, 500)
#: The 5 nt bins Core read its quartiles from until 2026-10-30.
OLD_BINS = (0.0, 500.0, 100)


def _binned(values, geometry=FIGURE_BINS) -> HistogramAccumulator:
    h = HistogramAccumulator(*geometry)
    for v in values:
        h.update(float(v))
    return h


def _snapshot(pts, *, arm: str = "U", ref: str | None = None) -> dict:
    m = PolyAModule(arm=arm, ref_seq=ref)
    for i, pt in enumerate(pts):
        m.update(_read(pt, V1, name=f"r{i}"))
    return m.snapshot()


# --- the accumulator -------------------------------------------------------------------

@pytest.mark.parametrize("n", [1, 2, 3, 4, 10, 101, 1000, 1001])
def test_the_quartiles_are_numpys_on_the_same_values(n) -> None:
    values = np.random.default_rng(n).integers(11, 400, size=n)
    acc = _counted(values)
    # Equal, not approximately: at q = 1/4, 1/2, 3/4 both sides land on multiples of 0.25.
    assert acc.median == np.median(values)
    assert [acc.q1, acc.q3] == list(np.percentile(values, [25, 75]))


@pytest.mark.parametrize("q", [0.0, 0.01, 0.1, 0.33, 0.9, 0.999, 1.0])
def test_any_quantile_is_numpys_linear_rule(q) -> None:
    values = np.random.default_rng(7).integers(11, 400, size=257)
    assert _counted(values).quantile(q) == pytest.approx(np.quantile(values, q), abs=1e-9)


def test_every_read_on_one_value_reads_that_value_not_the_bins_interpolation() -> None:
    """The plainest case of the bias: 1,001 reads at 56 nt."""
    values = [56] * 1001
    assert _binned(values, OLD_BINS).median == 57.5, "what Core reported until 2026-10-30"
    assert _counted(values).median == 56.0
    assert _snapshot(values)["polya_distribution"]["median"] == 56.0


def test_empty_is_none_and_a_q_outside_the_unit_interval_raises() -> None:
    d = IntegerQuantileAccumulator().to_dict()
    assert d["n"] == 0 and d["Q1"] is None and d["median"] is None and d["Q3"] is None
    for q in (-0.01, 1.01, 50):
        with pytest.raises(ValueError, match="must be in"):
            _counted([1, 2, 3]).quantile(q)


def test_a_non_integer_pt_stops_the_run_before_any_length_is_counted() -> None:
    with pytest.raises(ValueError, match="expected an integer"):
        IntegerQuantileAccumulator().update(56.5)
    r = _read(56, V1)
    r.set_tag("pt", 56.5, "f")
    m = PolyAModule(arm="U")
    with pytest.raises(ValueError, match="expected an integer"):
        m.update(r)
    assert m.snapshot()["polya_length"]["n"] == 0


def test_beyond_the_figure_s_range_the_median_is_still_measured() -> None:
    """The bins end at 500 nt and a count per value does not. All-overflow used to be None."""
    values = [600, 610, 620]
    assert _binned(values).median is None
    dist = _snapshot(values)["polya_distribution"]
    assert dist["median"] == 610.0 and dist["overflow"] == 3


# --- the module ------------------------------------------------------------------------

def test_the_module_reports_exact_quartiles_and_keeps_the_figure_s_bins() -> None:
    pts = np.random.default_rng(3).integers(20, 200, size=301)
    dist = _snapshot(pts)["polya_distribution"]
    assert dist["median"] == np.median(pts)
    assert [dist["Q1"], dist["Q3"]] == list(np.percentile(pts, [25, 75]))
    assert dist["quantile_method"] == "exact" and dist["n"] == 301
    assert dist["bin_counts"] == _binned(pts).counts, "the figure keeps the same bins"


@pytest.mark.parametrize("arm", ["U", "m1psi"])
def test_the_corrected_quartiles_are_the_exact_ones_through_the_ruler(arm) -> None:
    ref = _ref(1183, 0.24598478444632291, 3)
    comp = reference_composition(ref)
    F, c = affine_terms(arm=arm, f=comp["f"], u12=comp["u12"],
                        constants=resolve_model(V1)[1])
    pts = np.random.default_rng(11).integers(20, 200, size=400)
    snap = _snapshot(pts, arm=arm, ref=ref)
    assert snap["correction"]["applied"]
    corr = snap["polya_distribution_corrected"]
    exact = {"median": np.median(pts),
             **dict(zip(("Q1", "Q3"), np.percentile(pts, [25, 75])))}
    for key, value in exact.items():
        assert corr[key] == round(F * value + c, 2), key
