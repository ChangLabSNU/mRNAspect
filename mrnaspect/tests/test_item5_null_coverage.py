"""Item 5 regression — uncovered positions emit JSON null, not a fabricated 0.0.

Bug (UPDATE_PLAN item 5): a zero-coverage position has an UNDEFINED per-position rate (0/0),
but both ConsensusIdentityModule and ErrorHotspotModule wrote 0.0 there — indistinguishable
from a measured, error-free position. The fix emits None at depth==0, and reconciles the two
modules onto the same boundary, which is now their shared default gate (depth 50): they null
the identical set of positions. A covered position below that depth is not reported either.
"""

from __future__ import annotations

from mrnaspect.analysis.shared_pileup import SharedPileup
from mrnaspect.analysis.modules.consensus import ConsensusIdentityModule
from mrnaspect.analysis.modules.error_hotspot import ErrorHotspotModule


def _pileup() -> SharedPileup:
    """4-position construct 'ACGT' with a mix of coverage depths.

    pos0 (ref A): depth 100, all match.  pos1 (ref C): UNCOVERED (depth 0).
    pos2 (ref G): depth 5 — covered but below ErrorHotspotModule's default min_depth (10).
    pos3 (ref T): depth 100, all match.
    """
    pileup = SharedPileup("synthetic", "ACGT")
    pileup.counts[0, 0] = 100          # pos0 ref A (col 0)
    pileup.depth[0] = 100
    # pos1 left entirely at zero → uncovered
    pileup.counts[2, 2] = 3            # pos2 ref G (col 2): 3 match ...
    pileup.counts[2, 1] = 2            # ... 2 miscalled to C → mismatch 2/5 = 0.4
    pileup.depth[2] = 5
    pileup.counts[3, 3] = 100          # pos3 ref T (col 3)
    pileup.depth[3] = 100
    pileup._read_count = 100
    return pileup


def test_uncovered_position_is_null_not_zero_in_both_modules() -> None:
    pileup = _pileup()
    cons = ConsensusIdentityModule(pileup, config={"min_depth": 1}).snapshot()
    err = ErrorHotspotModule(pileup).snapshot()

    # The uncovered position (index 1) is None in every per-position array, never 0.0.
    assert cons["mismatch_rate_by_position"][1] is None
    for key in ("substitution_rate", "deletion_rate", "insertion_rate", "total_error_rate"):
        assert err[key][1] is None, f"{key}[1] should be null at depth==0"

    # Covered positions are real floats, not None.
    assert isinstance(cons["mismatch_rate_by_position"][0], float)
    assert isinstance(err["substitution_rate"][0], float)


def test_masks_reconciled_same_null_set() -> None:
    """At their defaults both modules null the IDENTICAL set: uncovered and below 50."""
    pileup = _pileup()
    cons = ConsensusIdentityModule(pileup).snapshot()
    err = ErrorHotspotModule(pileup).snapshot()

    cons_null = {i for i, v in enumerate(cons["mismatch_rate_by_position"]) if v is None}
    err_null = {i for i, v in enumerate(err["substitution_rate"]) if v is None}
    assert cons_null == err_null == {1, 2}


def test_a_position_below_min_depth_is_not_reported() -> None:
    """pos2 is covered at depth 5, under the default 50: null, not its noisy 2/5.

    Lowering min_depth reports it, with its true rate, not a zero.
    """
    err = ErrorHotspotModule(_pileup()).snapshot()
    assert err["substitution_rate"][2] is None
    assert err["min_depth_threshold"] == 50
    low = ErrorHotspotModule(_pileup(), config={"min_depth": 5}).snapshot()
    assert low["substitution_rate"][2] == 0.4


def test_n_reference_base_is_null_not_a_full_mismatch() -> None:
    """An N in the reference has no base to agree with: its rate is null, not 1.0.

    Identity, variant calls and low-consensus sites already skip N positions.
    """
    pileup = SharedPileup("synthetic", "ANGT")
    for pos, col in ((0, 0), (1, 0), (2, 2), (3, 3)):
        pileup.counts[pos, col] = 100
        pileup.depth[pos] = 100
    pileup._read_count = 100
    rates = ConsensusIdentityModule(pileup, config={"min_depth": 1}).snapshot()[
        "mismatch_rate_by_position"]
    assert rates[1] is None
    assert rates[0] == rates[2] == rates[3] == 0.0
