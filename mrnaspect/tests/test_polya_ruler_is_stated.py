"""The report must say which ruler produced the tail length, from the module's OWN block.

The module has recorded `status`, `reason` and the correction's terms since the correction
existed. A corrected tail and dorado's raw one differ by ~10% on a fully m1Psi-substituted
construct -- larger than most specs -- so a row that does not say which it is, is a row a
reader cannot use.

This file was rewritten on 2026-10-28, and why matters more than what. Its first version
built its input by hand: flat keys, `correction_applied`, `correction_reason`. `_ruler` read
exactly those keys, so all ten tests passed -- while the module had always written a NESTED
`correction` block, and every real run printed "UNCORRECTED: not applied", corrected or
not, with the corrected value in no table. A test that supplies its own input cannot see a
producer and a consumer disagree.

So every input here starts as a REAL `PolyAModule` snapshot, and `_vary` may change the
VALUE of a key the module produced but refuses to add one it did not.
"""

from __future__ import annotations

import copy

import pytest

from mrnaspect.analysis.modules.polya import PolyAModule
from mrnaspect.reporting.reporter import CoAReporter

from .test_polya_correction_v2 import V1, _read, _ref

_REF = _ref(1183, 0.24598478444632291, 3)


def _real(model: str = V1, arm="m1psi", ref=_REF) -> dict:
    """A snapshot the module actually produced."""
    m = PolyAModule(arm=arm, ref_seq=ref)
    for i, pt in enumerate((88, 90, 92)):
        m.update(_read(pt, model, name=f"r{i}"))
    return m.snapshot()


def _vary(snap: dict, **values) -> dict:
    """Change values inside `correction`, never invent a key the module does not write."""
    out = copy.deepcopy(snap)
    for k, v in values.items():
        assert k in out["correction"], (
            f"{k!r} is not a key PolyAModule writes; a test that adds it is testing a "
            f"shape nothing produces, which is how this file passed while the report lied")
        out["correction"][k] = v
    return out


APPLIED = _real()


def test_the_fixture_really_is_an_applied_correction() -> None:
    """Guards every test below: if this is not applied, they test the wrong branch."""
    c = APPLIED["correction"]
    assert c["applied"] is True and c["status"] == "applied"
    assert c["factor"] and c["shift_nt"] is not None


def test_an_applied_correction_carries_no_stale_reason() -> None:
    """It used to keep __init__'s "constants are unresolved" beside a factor computed from
    them, in qc_result.json."""
    assert APPLIED["correction"]["reason"] is None


def test_a_corrected_tail_says_so_and_shows_the_arithmetic() -> None:
    c = APPLIED["correction"]
    text = CoAReporter._ruler(APPLIED)
    assert "Corrected to the canonical-equivalent ruler" in text
    assert V1 in text and "modification m1Ψ-substituted (declared)" in text
    assert f"{c['factor']:.4f}" in text and f"{abs(c['shift_nt']):.2f} nt" in text


def test_the_sign_of_the_shift_is_readable() -> None:
    shift = abs(APPLIED["correction"]["shift_nt"])
    # A minus sign, not a hyphen (2026-11-16): the hyphen read as a dash after the factor.
    assert f"− {shift:.2f} nt" in CoAReporter._ruler(_vary(APPLIED, shift_nt=-shift))
    assert f"+ {shift:.2f} nt" in CoAReporter._ruler(_vary(APPLIED, shift_nt=shift))


def test_provisional_constants_are_never_presented_as_settled() -> None:
    """`polya_constants.json` carries status 'provisional'; so must the CoA."""
    assert "The constants are provisional" in CoAReporter._ruler(APPLIED)


def test_delta_appears_only_when_it_was_an_input() -> None:
    """v1 is form 'f', so the real block has no delta; the stock model's would."""
    assert APPLIED["correction"]["delta_pct"] is None
    assert "delta" not in CoAReporter._ruler(APPLIED)
    assert "delta=2.928%" in CoAReporter._ruler(_vary(APPLIED, delta_pct=2.928))


def test_an_out_of_range_input_is_shouted_not_whispered() -> None:
    text = CoAReporter._ruler(
        _vary(APPLIED, range_flags=["U12=9 is outside the measured range 2-6"]))
    assert "OUTSIDE THE FITTED RANGE" in text and "U12=9" in text


@pytest.mark.parametrize("why,snap,status,needle", [
    ("no arm", lambda: _real(arm=None), "withheld", "sample.arm"),
    ("no reference", lambda: _real(ref=None), "withheld", "no reference sequence"),
    ("unregistered model", lambda: _real(model="rna004_130bps_sup@v5.1.0"),
     "uncalibrated", "no registered poly(A) constants"),
])
def test_an_uncorrected_tail_says_it_is_uncorrected_and_why(why, snap, status, needle):
    s = snap()
    assert s["correction"]["applied"] is False, why
    text = CoAReporter._ruler(s)
    assert text.startswith(f"Not corrected ({status}):"), text
    assert needle in text, f"{why}: the reason did not reach the sentence: {text}"


def test_the_run_level_row_says_as_called_and_where_the_correction_is() -> None:
    """The run-level module pools every construct, so it never has one correction."""
    row = CoAReporter._polya_method({**_real(ref=None), "pt_coverage": 0.9,
                                     "n_with_pt": 90, "reads_processed": 100})
    assert "90/100" in row
    assert "Uncorrected" in row and "Per-construct results" in row
    assert "Corrected to" not in row


def test_a_run_with_no_polya_snapshot_still_gets_a_sentence() -> None:
    """Absent keys must not produce silence, which reads as "nothing to say"."""
    assert CoAReporter._ruler({}).startswith("Not corrected")
    assert "Uncorrected" in CoAReporter._polya_method({})
