"""H1 regression — a fully-deleted reference position must NOT count as identity.

Confirmed bug (UPDATE_PLAN H1): the old ``argmax(counts[:, :4]) == ref`` rule ignored the
deletion column, so an all-deleted position whose ref base is 'A' argmax'd to 'A' (index 0
on all-zero ACGT) and scored as a 100% match. The fix makes ``consensus_identity_pct`` the
pooled reference-agreement fraction over coverage that *includes* deletions, so the deleted
position drags identity down to the true value.
"""

from __future__ import annotations

import numpy as np

from mrnaspect.analysis.shared_pileup import SharedPileup, _COL_DEL
from mrnaspect.analysis.modules.consensus import ConsensusIdentityModule


def _synthetic_pileup() -> SharedPileup:
    """4-position construct: 3 positions perfectly matched, the 4th fully deleted.

    Reference 'CGTA' — the deleted position (index 3) is ref 'A', which is exactly the
    base the old all-zero argmax returned, so the old code false-matched it.
    """
    depth = 100
    pileup = SharedPileup("synthetic", "CGTA")
    # pos0 ref C, pos1 ref G, pos2 ref T: all reads agree with the reference base.
    for pos, base_col in ((0, 1), (1, 2), (2, 3)):  # C=1, G=2, T=3
        pileup.counts[pos, base_col] = depth
        pileup.depth[pos] = depth
    # pos3 ref A (col 0): every read deletes here. ACGT columns stay 0.
    pileup.counts[3, _COL_DEL] = depth
    pileup.depth[3] = depth
    pileup._read_count = depth
    return pileup


def test_fully_deleted_position_not_counted_as_match() -> None:
    snap = ConsensusIdentityModule(_synthetic_pileup(), config={"min_depth": 1}).snapshot()
    # 3 of 4 positions fully agree, 1 fully deleted -> 75.0, NOT the old buggy 100.0.
    assert snap["consensus_identity_pct"] == 75.0
    assert snap["covered_positions"] == 4


def test_all_match_is_100() -> None:
    pileup = SharedPileup("synthetic", "CGTA")
    for pos, base_col in ((0, 1), (1, 2), (2, 3), (3, 0)):  # all match ref
        pileup.counts[pos, base_col] = 100
        pileup.depth[pos] = 100
    pileup._read_count = 100
    snap = ConsensusIdentityModule(pileup, config={"min_depth": 1}).snapshot()
    assert snap["consensus_identity_pct"] == 100.0


def test_partial_deletion_weighted_by_coverage() -> None:
    """Pooled fraction: half-deleted position contributes its ref share, not a binary."""
    pileup = SharedPileup("synthetic", "CG")  # 2 positions
    pileup.counts[0, 1] = 100                  # pos0 ref C: fully matched
    pileup.depth[0] = 100
    pileup.counts[1, 2] = 40                   # pos1 ref G: 40 match
    pileup.counts[1, _COL_DEL] = 60            #            60 deleted
    pileup.depth[1] = 100
    pileup._read_count = 100
    snap = ConsensusIdentityModule(pileup, config={"min_depth": 1}).snapshot()
    # pooled = (100 + 40) / (100 + 100) = 70.0
    assert snap["consensus_identity_pct"] == 70.0
