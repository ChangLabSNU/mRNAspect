"""The v2 ruler correction: which model, which arm, and when it refuses.

The load-bearing test is `test_reproduces_the_exported_worked_examples`. Everything else
checks that the correction is withheld rather than guessed; that one checks it is the
RIGHT correction when it is applied. A formula that withholds correctly and computes the
wrong number is worse than one that does neither, because the refusals read as care.
"""

from __future__ import annotations

import json

import pysam
import pytest

from mrnaspect.analysis.modules.polya import PolyAModule
from mrnaspect.analysis.polya_calibration import (
    ALIASES,
    CONSTANTS,
    SHARED,
    correct_pt,
    resolve_model,
)

_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "r", "LN": 100}]})

V1 = "m1psi_tuned_v1"
STOCK = "rna004_130bps_hac@v5.3.0"

#: Verbatim from `polya_constants_admit.json`'s worked examples — the fit's own record of
#: what these constants produce. Reference lengths are the OCT panel's. The v1 rows are the
#: 2026-11-02 refit's (Core's read rule); the stock rows equal polya_constants.json's.
EXAMPLES = [
    # model, construct_len, f, u12, arm, pt, expected
    (STOCK, 1183, 0.24598478444632291, 3, "U", 90.0, 89.96204530592286),
    (STOCK, 1520, 0.22894736842105262, 6, "U", 90.0, 90.61868637648267),
    (V1, 1183, 0.24598478444632291, 3, "U", 90.0, 89.96096244273474),
    (V1, 1183, 0.24598478444632291, 3, "m1psi", 90.0, 99.71883193301605),
    (V1, 1520, 0.22894736842105262, 6, "U", 90.0, 90.63633775580136),
    (V1, 1520, 0.22894736842105262, 6, "m1psi", 90.0, 96.57405299327588),
]


def _ref(length: int, f: float, u12: int) -> str:
    """A reference with exactly this f and this terminus U count."""
    n_u = round(f * length)
    seq = "T" * (n_u - u12) + "A" * (length - 12 - (n_u - u12)) + "T" * u12 + "A" * (12 - u12)
    assert len(seq) == length and seq.count("T") == n_u and seq[-12:].count("T") == u12
    return seq


def _read(pt, model, name="x"):
    r = pysam.AlignedSegment(_HEADER)
    r.query_name, r.flag, r.reference_id = name, 0, 0
    r.reference_start, r.query_sequence, r.cigartuples = 0, "ACGT", [(0, 4)]
    r.set_tag("pt", int(pt), "i")
    r.set_tag("RG", f"0741d988-979b-4fd1-abf8-d040f567a442_{model}", "Z")
    return r


def _run(model, arm, ref, pt=90.0):
    m = PolyAModule(arm=arm, ref_seq=ref)
    m.update(_read(pt, model))
    return m.snapshot()


# --- the one that matters ---------------------------------------------------

@pytest.mark.parametrize("model,length,f,u12,arm,pt,expected", EXAMPLES)
def test_reproduces_the_exported_worked_examples(model, length, f, u12, arm, pt, expected):
    got = correct_pt(pt, arm=arm, f=f, u12=u12, constants=resolve_model(model)[1])
    assert got == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("model,length,f,u12,arm,pt,expected", EXAMPLES)
def test_the_same_numbers_survive_the_whole_module(model, length, f, u12, arm, pt, expected):
    """Composition -> model resolution -> correction, not just the arithmetic.

    Tolerance is 1e-3 because the accumulator rounds what it reports; the formula itself
    is exact above.
    """
    snap = _run(model, arm, _ref(length, f, u12), pt)
    assert snap["correction"]["status"] == "applied"
    assert snap["polya_length_corrected"]["mean"] == pytest.approx(expected, abs=1e-3)


def test_raw_pt_is_never_replaced() -> None:
    snap = _run(V1, "m1psi", _ref(1183, 0.24598478444632291, 3))
    assert snap["polya_length"]["mean"] == pytest.approx(90.0)


# --- aliases are mandatory, not a convenience -------------------------------

def test_both_earlier_names_of_the_released_model_resolve() -> None:
    """A BAM carries the directory it was basecalled with; a rename does not reach it.

    Without these, every BAM made before 2026-10-23 would read as unregistered and
    silently lose its correction.
    """
    for old in ("qcrna_m1psi_v1_ov2500", "train8s_hac_5e-6_ep2_w2_ov2500"):
        name, constants = resolve_model(old)
        assert name == V1 and constants is CONSTANTS[V1]


def test_an_aliased_name_gets_the_same_number_as_the_canonical_one() -> None:
    ref = _ref(1183, 0.24598478444632291, 3)
    old = _run("qcrna_m1psi_v1_ov2500", "m1psi", ref)
    new = _run(V1, "m1psi", ref)
    assert (old["polya_length_corrected"]["mean"]
            == new["polya_length_corrected"]["mean"])


def test_the_report_says_what_the_rg_name_resolved_to() -> None:
    snap = _run("qcrna_m1psi_v1_ov2500", "m1psi", _ref(1183, 0.24598478444632291, 3))
    c = snap["correction"]
    assert c["run_model"] == "qcrna_m1psi_v1_ov2500"
    assert c["run_model_resolved"] == V1


def test_every_alias_points_at_a_registered_model() -> None:
    assert all(v in CONSTANTS for v in ALIASES.values())


# --- when it refuses, and why -----------------------------------------------

def _reason(snap) -> str:
    return snap["correction"]["reason"] or ""


def test_an_unregistered_model_gets_raw_pt_not_a_borrowed_constant() -> None:
    snap = _run("rna004_130bps_sup@v5.2.0", "m1psi", _ref(1183, 0.2, 3))
    assert snap["correction"]["status"] == "uncalibrated"
    assert snap["correction"]["applied"] is False
    assert snap["polya_length_corrected"]["n"] == 0
    assert "no registered poly(A) constants" in _reason(snap)


def test_an_undeclared_arm_withholds_and_says_how_to_fix_it() -> None:
    m = PolyAModule(ref_seq=_ref(1183, 0.2, 3))
    m.update(_read(90, V1))
    snap = m.snapshot()
    assert snap["correction"]["status"] == "withheld"
    assert "sample.arm" in _reason(snap)


def test_a_delta_form_model_withholds_on_m1psi_until_delta_is_measured() -> None:
    """Stock hac@v5.3.0 is `f+delta`. Defaulting delta to 0 would assert D == I."""
    snap = _run(STOCK, "m1psi", _ref(1183, 0.2, 3))
    assert snap["correction"]["status"] == "withheld"
    assert "delta_pct" in _reason(snap)


def test_the_same_delta_form_model_still_corrects_the_U_arm() -> None:
    """delta only enters the m1psi factor, so the U arm is unaffected."""
    snap = _run(STOCK, "U", _ref(1183, 0.2, 3))
    assert snap["correction"]["status"] == "applied"


def test_passing_delta_as_zero_is_refused_rather_than_accepted() -> None:
    with pytest.raises(ValueError, match="delta_pct"):
        correct_pt(90.0, arm="m1psi", f=0.2, u12=3,
                   constants=CONSTANTS[STOCK], delta_pct=None)


def test_a_missing_u12_withholds_on_m1psi_but_not_on_U() -> None:
    """E_nt needs the terminus; F does not."""
    short = "ACGT"
    assert PolyAModule(arm="m1psi", ref_seq=short).snapshot
    m = PolyAModule(arm="m1psi", ref_seq=short); m.update(_read(90, V1))
    assert m.snapshot()["correction"]["status"] == "withheld"
    u = PolyAModule(arm="U", ref_seq=short); u.update(_read(90, V1))
    assert u.snapshot()["correction"]["status"] == "applied"


def test_no_reference_withholds() -> None:
    m = PolyAModule(arm="m1psi")
    m.update(_read(90, V1))
    assert m.snapshot()["correction"]["status"] == "withheld"


# --- the constants are the exported ones ------------------------------------

def test_the_shared_offset_is_dorado_hac_v530s() -> None:
    """The fit used one offset for both models; the FT's own [poly_a] block carries it too."""
    from mrnaspect.analysis.modules.polya import DORADO_POLYA_CONSTANTS
    assert SHARED["offset_correction"] == (
        DORADO_POLYA_CONSTANTS["rna004_130bps_hac@v5.3.0"]["offset_correction"])
    assert SHARED["f_ref"] == 0.245


def test_the_retired_lab_model_is_not_registered() -> None:
    """`FT_test_train_4_hac_5e-5_ep8` is train #4, not what is released -- in EVERY table.
    This checked only the ruler registry until 2026-10-28, while dorado's own constants
    table kept the key; a guard on one table of a pair."""
    from mrnaspect.analysis.modules.polya import DORADO_POLYA_CONSTANTS
    for table in (CONSTANTS, ALIASES, DORADO_POLYA_CONSTANTS):
        assert "FT_test_train_4_hac_5e-5_ep8" not in table
