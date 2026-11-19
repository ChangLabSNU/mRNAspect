"""f and U12: derived from the reference, checked against the constants they feed.

The strongest test here is the first one. `f` and `U12` are inputs to a correction fitted
elsewhere, so the thing that matters is not that this code computes *something* but that
it computes what the fit assumed — the exported constants carry worked examples, and this
reproduces them bit for bit. An off-by-one terminus window or a `U`/`T` slip would still
produce plausible numbers.
"""

from __future__ import annotations

import pytest

from mrnaspect.analysis.modules.polya import PolyAModule
from mrnaspect.analysis.polya_calibration import (
    F_SUPPORT,
    U12_SUPPORT,
    U12_WINDOW_NT,
    range_flags,
    reference_composition,
)

#: Taken verbatim from `polya_constants.json`'s `examples`, which is the fit's own record
#: of what it assumed. QK-01 and HA-L0 are the two constructs it works through.
EXPORTED = {
    "QK-01": {"f": 0.24598478444632291, "u12": 3, "length": 1183},
    "HA-L0": {"f": 0.22894736842105262, "u12": 6, "length": 1520},
}


def _seq(length: int, n_u: int, terminus: str) -> str:
    """A reference with a known U count and a chosen last-12 terminus."""
    body_len = length - len(terminus)
    assert 0 <= n_u <= body_len
    return ("T" * n_u + "A" * (body_len - n_u)) + terminus


# --- the one that matters ---------------------------------------------------

@pytest.mark.parametrize("name", sorted(EXPORTED))
def test_reproduces_the_exported_worked_examples(name) -> None:
    """Bit-identical to what the constants were fitted against.

    Built rather than read from a FASTA: the real references are not part of Core,
    which must not depend on them. The U count and terminus are the published ones, so
    the arithmetic under test is the same arithmetic.
    """
    want = EXPORTED[name]
    n_u = round(want["f"] * want["length"])
    # The published f must be exactly n_u / length — if it is not, the definition moved.
    assert n_u / want["length"] == want["f"], (
        f"{name}: f is not a clean U-count ratio, so the definition this test assumes "
        f"('U fraction of the whole reference') is not the one that produced it")

    terminus = "T" * want["u12"] + "A" * (U12_WINDOW_NT - want["u12"])
    got = reference_composition(_seq(want["length"], n_u - want["u12"], terminus))
    assert got["f"] == want["f"]
    assert got["u12"] == want["u12"]


# --- the definitions -------------------------------------------------------

def test_u12_counts_only_the_last_twelve() -> None:
    """A U at position -13 is outside the window; one at -12 is inside."""
    assert reference_composition("T" + "A" * 12)["u12"] == 0
    assert reference_composition("A" + "T" + "A" * 11)["u12"] == 1


def test_rna_and_dna_spellings_agree() -> None:
    """References arrive as DNA from a FASTA and as RNA from nobody — accept both."""
    assert reference_composition("ACGT" * 25)["f"] == reference_composition("ACGU" * 25)["f"]
    assert reference_composition("acgt" * 25)["u12"] == reference_composition("ACGT" * 25)["u12"]


def test_ambiguous_bases_count_in_the_denominator_but_are_not_u() -> None:
    c = reference_composition("N" * 50 + "T" * 50)
    assert c["f"] == 0.5


def test_a_sequence_shorter_than_the_window_keeps_f_and_withholds_u12() -> None:
    """Withhold only what cannot be computed, and say which."""
    c = reference_composition("ACGT")
    assert c["f"] == 0.25
    assert c["u12"] is None
    assert "shorter than" in c["reason"]


def test_an_empty_record_withholds_both_with_a_reason() -> None:
    c = reference_composition("")
    assert c["f"] is None and c["u12"] is None and c["reason"]


# --- range flags ------------------------------------------------------------

STOCK_TAILS = (29.98, 122.55)


def test_inside_the_fitted_ranges_flags_nothing() -> None:
    assert range_flags(f=0.2, u12=3, tail_nt=90.0, tail_support=STOCK_TAILS) == []


def test_each_input_flags_independently_and_names_its_range() -> None:
    assert any("f=" in m for m in range_flags(f=0.9, u12=3))
    assert any("U12=" in m for m in range_flags(f=0.2, u12=9))
    assert any("tail" in m for m in range_flags(f=0.2, u12=3, tail_nt=200.0,
                                                tail_support=STOCK_TAILS))
    assert len(range_flags(f=0.9, u12=9, tail_nt=200.0, tail_support=STOCK_TAILS)) == 3


def test_the_u12_range_is_what_was_measured_not_what_is_plausible() -> None:
    """Three termini were measured: U12 = 2, 3 and 6. Outside that the line extrapolates."""
    assert U12_SUPPORT == (2, 6)
    assert range_flags(f=0.2, u12=2) == [] and range_flags(f=0.2, u12=6) == []
    assert range_flags(f=0.2, u12=1) and range_flags(f=0.2, u12=7)


def test_flags_do_not_refuse() -> None:
    """Out of range is reported, not fatal — the reader decides what to do with it."""
    snap = PolyAModule(arm="m1psi", ref_seq="T" * 100).snapshot()
    assert snap["correction"]["range_flags"]
    assert snap["correction"]["f"] == 1.0


def test_the_support_constants_are_the_exported_ones() -> None:
    assert F_SUPPORT == (0.0546448087431694, 0.2485207100591716)
    assert U12_WINDOW_NT == 12


# --- it reaches the output --------------------------------------------------

def test_the_polya_block_carries_f_u12_and_flags() -> None:
    snap = PolyAModule(arm="m1psi", ref_seq="ACGT" * 300).snapshot()
    c = snap["correction"]
    assert c["f"] == pytest.approx(0.25)
    assert c["u12"] == 3
    assert "range_flags" in c


def test_a_module_with_no_reference_reports_absence_not_zero() -> None:
    """The run-level instance has no single construct, so it has no f to report."""
    c = PolyAModule().snapshot()["correction"]
    assert c["f"] is None and c["u12"] is None and c["range_flags"] is None
