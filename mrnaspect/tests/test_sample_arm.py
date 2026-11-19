"""The arm is declared, validated against the reference, and never guessed.

`mrnaspect/analysis/arms.py` carries the reasoning. What is pinned here is the behaviour
that makes it safe to put a corrected tail length on a CoA:

  * undeclared stays undeclared — there is no default in either direction, because the
    additive terminus term applies on m1psi and is zero on U, so a guess is wrong by up
    to ~7 nt whichever way it went;
  * a construct named in `sample.arms` that is not in the reference REFUSES the run.
    This is the one that would otherwise be silent: tracks are created lazily from
    `read.reference_name`, so a typo'd name matches nothing, leaves that construct on the
    default, and produces a corrected tail that looks fine and is wrong;
  * `arms` states its value and a bare list is refused, so that editing the run-level
    default cannot silently invert every exception;
  * the arm reaches the poly(A) output per construct, which is where the v2 correction
    will read it and where a reader can check what the number was judged as.
"""

from __future__ import annotations

import pytest

from mrnaspect.analysis.arms import ARMS, ArmError, normalise_arm, resolve_arms
from mrnaspect.analysis.modules.polya import PolyAModule

CONTIGS = ["QK-01", "HA-L4", "HA-L6", "mod_tmp_u_1"]


# --- the resolver -----------------------------------------------------------

def test_nothing_declared_resolves_to_nothing() -> None:
    """Not {} meaning 'default everywhere' — {} meaning 'nobody said'."""
    assert resolve_arms(None, CONTIGS) == {}
    assert resolve_arms({}, CONTIGS) == {}
    assert resolve_arms({"manufacturer": "X"}, CONTIGS) == {}


def test_the_run_level_default_covers_every_construct() -> None:
    """The production case: one line, every construct."""
    assert resolve_arms({"arm": "m1psi"}, CONTIGS) == {c: "m1psi" for c in CONTIGS}


def test_exceptions_override_the_default_and_state_their_value() -> None:
    got = resolve_arms({"arm": "m1psi", "arms": {"HA-L4": "U", "mod_tmp_u_1": "U"}}, CONTIGS)
    assert got == {"QK-01": "m1psi", "HA-L6": "m1psi", "HA-L4": "U", "mod_tmp_u_1": "U"}


def test_exceptions_alone_leave_the_rest_undeclared() -> None:
    """Declaring only what you know is legitimate; the rest withholds."""
    got = resolve_arms({"arms": {"HA-L4": "U"}}, CONTIGS)
    assert got == {"HA-L4": "U"}


def test_editing_the_default_does_not_move_the_exceptions() -> None:
    """The whole reason `arms` states a value instead of meaning 'the opposite'.

    Re-running the same panel as a canonical control is one character. Under a bare
    exception list every listed construct would flip with it.
    """
    m = resolve_arms({"arm": "m1psi", "arms": {"HA-L4": "U"}}, CONTIGS)
    u = resolve_arms({"arm": "U", "arms": {"HA-L4": "U"}}, CONTIGS)
    assert m["HA-L4"] == u["HA-L4"] == "U"
    assert m["QK-01"] == "m1psi" and u["QK-01"] == "U"


# --- the guard that would otherwise be silent -------------------------------

def test_an_arms_name_that_is_not_in_the_reference_refuses_the_run() -> None:
    with pytest.raises(ArmError, match="not a contig in this reference"):
        resolve_arms({"arm": "m1psi", "arms": {"HA-L04": "U"}}, CONTIGS)


def test_the_refusal_suggests_the_near_miss() -> None:
    """A typo is the realistic case, so the message does the diffing."""
    with pytest.raises(ArmError, match="Did you mean 'HA-L4'"):
        resolve_arms({"arms": {"HA-L04": "U"}}, CONTIGS)


def test_a_bare_list_of_exceptions_is_refused_with_the_reason() -> None:
    with pytest.raises(ArmError, match="opposite"):
        resolve_arms({"arm": "m1psi", "arms": ["HA-L4"]}, CONTIGS)


@pytest.mark.parametrize("bad", ["psi", "m1Y", "", "canonical", "both", 1, None])
def test_an_unknown_arm_value_is_refused(bad) -> None:
    with pytest.raises(ArmError):
        normalise_arm(bad, where="test")


def test_there_is_no_value_for_partially_substituted() -> None:
    """The constants are fitted for two arms. A third needs constants, not a config value."""
    assert set(ARMS) == {"m1psi", "U"}
    with pytest.raises(ArmError, match="partially substituted"):
        normalise_arm("partial", where="test")


@pytest.mark.parametrize("spelling,canonical",
                         [("u", "U"), ("U", "U"), ("M1PSI", "m1psi"), (" m1psi ", "m1psi")])
def test_case_and_whitespace_are_normalised_to_the_canonical_spelling(spelling, canonical) -> None:
    """`U` and `u` are unambiguous; a run refused over a capital letter costs more."""
    assert normalise_arm(spelling, where="test") == canonical


# --- it reaches the output --------------------------------------------------

def test_the_polya_block_reports_the_declared_arm() -> None:
    snap = PolyAModule(arm="m1psi").snapshot()
    assert snap["correction"]["arm"] == "m1psi"
    assert snap["correction"]["arm_source"] == "declared"


def test_the_polya_block_reports_an_undeclared_arm_as_absent_not_as_a_guess() -> None:
    snap = PolyAModule().snapshot()
    assert snap["correction"]["arm"] is None
    assert snap["correction"]["arm_source"] is None


def test_the_arm_is_not_a_module_parameter() -> None:
    """It is a fact about the sample, so it arrives as an argument, not through config.

    Passing it as a module param would put it under `module_params.polya`, where any second
    consumer would have to reach into one module's settings for a sample-level fact.
    """
    with pytest.raises((ValueError, KeyError, TypeError)):
        PolyAModule({"arm": "m1psi"})
