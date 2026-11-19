"""The fitted range: a margin on f, and the tail flag, per model.

Decided 2026-10-28 (content audit, item 9; release_work.yaml H9):

* f: the fitted range's ends are observed extremes -- the upper one IS QK-10 on the fit's
  1,183-nt reference, and run 1's 1,200-nt reference of the same construct reads
  f = 0.249167, which printed OUTSIDE THE FITTED RANGE. Within 0.005 of an end the
  report now says "at the edge" instead; beyond it, the flag as before.
* the tail: judged on the construct's corrected MEDIAN against the range the fit's cells
  covered -- which differs by model (29.98-122.55 nt stock, 29.98-122.45 v1). Until now
  the flag existed in `range_flags` and nothing passed it a tail, so it never fired.
  Within 3 nt of an end it is said quietly too (same day): on run 1 under v1,
  HA-L0's 122.04 nt was 0.14 nt past v1's then 121.90 and printed OUTSIDE.

v1's range moved to 122.45 with the 2026-11-02 refit on Core's read rule, so the values
below that sat between the two models' ends were re-chosen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pysam
import pytest

from mrnaspect.analysis.modules.polya import PolyAModule
from mrnaspect.analysis.polya_calibration import (
    ALIASES,
    CONSTANTS,
    DELTA_FIT_FLAGS,
    F_EDGE_MARGIN,
    F_SUPPORT,
    TAIL_EDGE_MARGIN_NT,
    range_edges,
    range_flags,
)
from mrnaspect.reporting.reporter import CoAReporter

from .conftest import lab_artifact
from .test_polya_correction_v2 import _HEADER, STOCK, V1, _read, _ref
from .test_polya_ruler_is_stated import _real, _vary

QK10_RUN1 = 0.249167


# --- f: the margin ---------------------------------------------------------------------

def test_qk10_on_run_1_s_reference_is_at_the_edge_not_outside() -> None:
    assert range_flags(f=QK10_RUN1, u12=3) == []
    assert range_edges(f=QK10_RUN1) == [
        "f=0.2492 is 0.0006 past the fitted range's upper end, 0.2485"]


@pytest.mark.parametrize("f", [F_SUPPORT[1] + F_EDGE_MARGIN + 1e-4,
                               F_SUPPORT[0] - F_EDGE_MARGIN - 1e-4])
def test_beyond_the_margin_is_flagged_and_not_called_an_edge(f) -> None:
    flags = range_flags(f=f, u12=3)
    assert len(flags) == 1 and flags[0].startswith(f"f={f:.4f} is outside")
    assert range_edges(f=f) == []


def test_the_lower_end_has_a_margin_too() -> None:
    f = F_SUPPORT[0] - 0.002
    assert range_flags(f=f, u12=3) == []
    assert "lower end" in range_edges(f=f)[0]


def test_inside_the_range_nothing_is_said() -> None:
    assert range_flags(f=0.2, u12=3) == [] and range_edges(f=0.2) == []
    assert range_edges(f=F_SUPPORT[1]) == [], "the end itself is inside"


# --- the tail: per model, on the corrected median -------------------------------------

def test_each_model_carries_its_own_tail_range() -> None:
    assert CONSTANTS[STOCK]["tail_nt_support"] == (29.98, 122.55)
    assert CONSTANTS[V1]["tail_nt_support"] == (29.98, 122.45)


def test_each_model_s_range_is_its_own() -> None:
    """122.50 nt is inside stock's range and 0.05 nt past v1's; 125.50 is 2.95 past stock's
    -- at the edge -- and 3.05 past v1's -- outside."""
    stock, v1 = CONSTANTS[STOCK]["tail_nt_support"], CONSTANTS[V1]["tail_nt_support"]
    assert range_edges(f=None, tail_nt=122.50, tail_support=stock) == []
    assert "0.05 nt past" in range_edges(f=None, tail_nt=122.50, tail_support=v1)[0]
    assert range_flags(f=0.2, u12=3, tail_nt=125.50, tail_support=stock) == []
    assert "2.95 nt past" in range_edges(f=None, tail_nt=125.50, tail_support=stock)[0]
    assert "125.50 nt" in range_flags(f=0.2, u12=3, tail_nt=125.50, tail_support=v1)[0]


def test_ha_l0_on_run_1_is_inside_the_refit_range() -> None:
    """The case the 3-nt edge was made for: HA-L0 on run 1 read 122.04 nt under the 5-nt
    histogram median (until Core 00141be), 0.14 past the pre-registered v1 fit's 121.90.
    With exact quartiles it reads 121.83 nt (run1_v1_fullref, 2026-11-02), and the refit
    ends v1's range at 122.45: inside either way, so nothing is said."""
    v1 = CONSTANTS[V1]["tail_nt_support"]
    assert TAIL_EDGE_MARGIN_NT == 3.0
    for tail in (122.04, 121.83):
        assert range_flags(f=0.2289, u12=6, tail_nt=tail, tail_support=v1) == []
        assert range_edges(f=0.2289, tail_nt=tail, tail_support=v1) == []


def test_the_lower_end_of_the_tail_range_has_a_margin_too() -> None:
    stock = CONSTANTS[STOCK]["tail_nt_support"]
    assert range_flags(f=0.2, u12=3, tail_nt=28.0, tail_support=stock) == []
    assert "lower end, 29.98" in range_edges(f=None, tail_nt=28.0, tail_support=stock)[0]
    assert range_flags(f=0.2, u12=3, tail_nt=26.0, tail_support=stock)
    assert range_edges(f=None, tail_nt=26.0, tail_support=stock) == []


def test_without_a_model_range_a_tail_is_not_judged() -> None:
    assert range_flags(f=0.2, u12=3, tail_nt=500.0) == []


def test_the_tail_ranges_are_the_fit_s_own() -> None:
    """Read from the artifact, not restated from memory: to 0.01 nt. The fit's constants
    file is on the lab's machines only, so this runs there (conftest ``lab_artifact``)."""
    artifact = lab_artifact("ruler_constants")
    if not artifact.exists():
        pytest.skip("the fit's constants file is not here")
    models = json.loads(artifact.read_text())["models"]
    for name, block in models.items():
        lo, hi = block["fit"]["tail_nt_range_corrected_cells"]
        assert CONSTANTS[ALIASES.get(name, name)]["tail_nt_support"] == (
            round(lo, 2), round(hi, 2)), name


# --- wired: the module computes it, the report says it ---------------------------------

def _module(pts, arm="U", model=V1, ref=None) -> dict:
    m = PolyAModule(arm=arm, ref_seq=ref or _ref(1183, 0.24598478444632291, 3))
    for i, pt in enumerate(pts):
        m.update(_read(pt, model, name=f"r{i}"))
    return m.snapshot()


def test_a_corrected_median_outside_the_model_s_range_is_flagged() -> None:
    snap = _module([200] * 3)
    median = snap["polya_distribution_corrected"]["median"]
    assert median > CONSTANTS[V1]["tail_nt_support"][1]
    assert any(f"{median:.2f} nt" in flag for flag in snap["correction"]["range_flags"])


def test_a_typical_tail_is_not() -> None:
    assert _module([90] * 3)["correction"]["range_flags"] is None


def test_the_run_s_own_model_s_range_not_another_s() -> None:
    """122.50 nt: 0.05 past v1's 122.45, inside stock's 122.55. Judged against stock's
    range, nothing would be said at all. f = f_ref makes F_U exactly 1 under any constants,
    so the corrected median is the raw one."""
    snap = _module([122, 123], ref=_ref(1200, 0.245, 3))
    median = snap["polya_distribution_corrected"]["median"]
    assert median == 122.5
    assert CONSTANTS[V1]["tail_nt_support"][1] < median < CONSTANTS[STOCK]["tail_nt_support"][1]
    assert snap["correction"]["range_flags"] is None
    assert any("0.05 nt past" in e and "122.45" in e for e in snap["correction"]["range_edges"])


def test_judged_on_the_corrected_median_not_the_raw_one() -> None:
    """On m1psi the correction stretches: raw 111 nt is inside v1's range, and corrected
    (~123.6 nt under the 2026-11-02 constants) is past its end by under 3 nt -- said, at the
    edge. Stated structurally so a refit does not move the test's premise."""
    snap = _module([110, 111, 112], arm="m1psi")
    upper = CONSTANTS[V1]["tail_nt_support"][1]
    corrected = snap["polya_distribution_corrected"]["median"]
    assert snap["polya_distribution"]["median"] < upper < corrected <= upper + TAIL_EDGE_MARGIN_NT
    assert snap["correction"]["range_flags"] is None
    assert any(f"{corrected:.2f} nt" in e for e in snap["correction"]["range_edges"])


def test_an_uncorrected_construct_has_no_tail_to_judge() -> None:
    snap = _module([200] * 3, arm=None)
    assert snap["correction"]["applied"] is False
    assert snap["correction"]["range_flags"] is None


def test_a_correction_still_waiting_for_delta_judges_no_tail() -> None:
    """Stock hac@v5.3.0 on m1psi needs delta_pct, which only an ALIGNED read supplies. Until
    one has, the constants are known and the correction is not: there is no corrected
    median, and the raw one (200 nt here) is no stand-in for it."""
    r = pysam.AlignedSegment(_HEADER)
    r.query_name, r.flag, r.query_sequence = "u", 4, "ACGT"
    r.set_tag("pt", 200, "i")
    r.set_tag("RG", f"0741d988-979b-4fd1-abf8-d040f567a442_{STOCK}", "Z")
    m = PolyAModule({"minimap2_flags": " ".join(DELTA_FIT_FLAGS)}, arm="m1psi",
                    ref_seq=_ref(1183, 0.24598478444632291, 3))
    m.update(r)
    c = m.snapshot()["correction"]
    assert c["status"] == "applied" and c["applied"] is False, c
    assert c["range_flags"] is None


def test_the_report_says_an_edge_quietly_and_an_extrapolation_loudly() -> None:
    applied = _real()
    edge = range_edges(f=QK10_RUN1)
    quiet = CoAReporter._ruler(_vary(applied, range_edges=edge, range_flags=None))
    assert "At the edge of the fitted range: " + edge[0] in quiet
    assert "OUTSIDE" not in quiet
    loud = CoAReporter._ruler(_vary(applied, range_flags=range_flags(f=0.3, u12=3)))
    assert "OUTSIDE THE FITTED RANGE: f=0.3000" in loud
