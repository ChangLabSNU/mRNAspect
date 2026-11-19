"""The consensus flag list: positions where the reads leave the reference.

Decided 2026-11-04. At depth >= min_depth and a reference base other than N,
a position is flagged when its mismatch rate -- the fraction of covering reads that do not
carry the reference base, a deletion counting -- is at or above ``mismatch_threshold``
(default 0.5: at most half the reads carry the reference). It replaces two lists that were
one quantity cut twice: the low-consensus sites (purity < 0.9 or consensus != reference,
which is exactly mismatch > 0.1) and the variants (one alternative >= 0.5, always inside
this list). A position flags on how much of the reference is lost, however the loss is
split between bases and deletions. No null. U is not masked.
"""

from __future__ import annotations

import pytest

from mrnaspect.analysis.modules.consensus import ConsensusIdentityModule, pooled_identity
from mrnaspect.analysis.shared_pileup import _COL_DEL, SharedPileup


def _pileup() -> SharedPileup:
    """Ref 'ACGTA', depth 10 at every position.

    pos0 ref A: 10 A              -> mismatch 0.0
    pos1 ref C: 5 C, 5 G          -> mismatch 0.5  flagged: at the threshold counts
    pos2 ref G: 10 T              -> mismatch 1.0  flagged: every read agrees, on T
    pos3 ref T: 4 T, 3 C, 3 del   -> mismatch 0.6  flagged, though no alternative has half
    pos4 ref A: 6 A, 4 G          -> mismatch 0.4
    """
    p = SharedPileup("synthetic", "ACGTA")
    p.counts[0, 0] = 10
    p.counts[1, 1], p.counts[1, 2] = 5, 5
    p.counts[2, 3] = 10
    p.counts[3, 3], p.counts[3, 1], p.counts[3, _COL_DEL] = 4, 3, 3
    p.counts[4, 0], p.counts[4, 2] = 6, 4
    p.depth[:] = 10
    p._read_count = 10
    return p


def _snap(config=None) -> dict:
    return ConsensusIdentityModule(
        _pileup(), config={"min_depth": 1, **(config or {})}).snapshot()


def test_the_default_flags_where_at_most_half_the_reads_carry_the_reference() -> None:
    snap = _snap()
    assert snap["mismatch_threshold"] == 0.5
    assert [s["pos"] for s in snap["mismatch_sites"]] == [1, 2, 3]
    assert snap["mismatch_site_count"] == 3


def test_a_loss_split_between_a_base_and_deletions_still_flags() -> None:
    """pos3: 30% read as C and 30% deleted, so no alternative reaches half and the old
    variant rule (one alternative >= 0.5) passed it -- the stock m1Ψ miscall in miniature.
    The entry says how the loss is split."""
    site = {s["pos"]: s for s in _snap()["mismatch_sites"]}[3]
    assert site["mismatch_rate"] == pytest.approx(0.6)
    assert site["ref_base"] == "T" and site["depth"] == 10
    assert site["base_counts"] == {"A": 0, "C": 3, "G": 0, "T": 4, "del": 3, "ins": 0}


@pytest.mark.parametrize("threshold", [0.1, 0.4, 0.5, 0.6, 1.0])
def test_the_list_is_the_curve_at_or_above_the_threshold(threshold) -> None:
    """The list and the per-position curve are one quantity, so they cannot disagree."""
    snap = _snap({"mismatch_threshold": threshold})
    curve = snap["mismatch_rate_by_position"]
    assert [s["pos"] for s in snap["mismatch_sites"]] == [
        i for i, v in enumerate(curve) if v is not None and v >= threshold]
    assert all(s["mismatch_rate"] == curve[s["pos"]] for s in snap["mismatch_sites"])


def test_a_rate_exactly_at_the_threshold_is_flagged() -> None:
    """5 of 50 reads off the reference is a rate of exactly 0.1. Computed as 1 - 45/50 it
    came out 0.09999999999999998, and a 0.1 threshold passed it."""
    p = SharedPileup("synthetic", "A")
    p.counts[0, 0], p.counts[0, 2] = 45, 5
    p.depth[:] = 50
    p._read_count = 50
    snap = ConsensusIdentityModule(p, config={"mismatch_threshold": 0.1}).snapshot()
    assert snap["mismatch_rate_by_position"] == [0.1]
    assert snap["mismatch_site_count"] == 1


def test_the_depth_gate_and_an_unknown_reference_base() -> None:
    gated = ConsensusIdentityModule(_pileup(), config={"min_depth": 50}).snapshot()
    assert gated["mismatch_sites"] == [] and gated["mismatch_site_count"] == 0
    # An N matches no read, so every read would be "off" it; it is never flagged.
    p = SharedPileup("synthetic", "N")
    p.counts[0, 2] = 10
    p.depth[:] = 10
    p._read_count = 10
    snap = ConsensusIdentityModule(p, config={"min_depth": 1}).snapshot()
    assert snap["mismatch_sites"] == []


def test_a_reference_u_is_assessed_like_any_other_base() -> None:
    """pos3's reference base is T (a U in the transcript). It used to drop out under the
    stock U-mask."""
    assert 3 in {s["pos"] for s in _snap()["mismatch_sites"]}


def test_the_two_old_lists_and_their_settings_are_gone() -> None:
    snap = _snap()
    for key in ("variants", "variant_count", "variant_frequencies", "variant_freq_threshold",
                "low_consensus_sites", "low_consensus_count", "low_consensus_threshold"):
        assert key not in snap, key


@pytest.mark.parametrize("old", ["variant_freq_threshold", "low_consensus_threshold"])
def test_a_config_that_still_sets_an_old_key_stops(old) -> None:
    """Refused, not ignored. low_consensus_threshold was a purity cut (larger flags more)
    and mismatch_threshold is a mismatch cut (larger flags fewer), so an old value read as
    the new one would mean the opposite."""
    with pytest.raises(ValueError, match="unknown parameter key"):
        ConsensusIdentityModule(_pileup(), config={old: 0.5})


def test_pooling_adds_the_counts_and_states_the_threshold() -> None:
    pooled = pooled_identity([_snap(), _snap()])
    assert pooled["mismatch_site_count"] == 6
    assert pooled["mismatch_threshold"] == 0.5
    # Constructs that used different thresholds have no single one to state.
    mixed = pooled_identity([_snap(), _snap({"mismatch_threshold": 0.6})])
    assert mixed["mismatch_threshold"] is None
