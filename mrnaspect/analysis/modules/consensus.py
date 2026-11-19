"""Per-base identity and the positions where the reads leave the reference, for one
construct.

Reads a ``SharedPileup``'s arrays and processes no reads itself: it has ``snapshot`` and
``finalize`` but no ``update``, and stores only its settings and the pileup. Every
reference position is scored, U included: a U read as C at an m1Ψ site counts as a
mismatch.
"""

from typing import Any, Mapping, Optional

import numpy as np

from ..shared_pileup import SharedPileup, _BASE_TO_IDX, _COL_DEL, _COL_INS
from ..streaming_interface import resolve_params, single_value


# min_depth gates the flagged positions and the per-position rates, but not identity,
# which is scored wherever depth > 0. The config overrides any key.
DEFAULTS = {
    "min_depth": 50,
    # A position is flagged when its mismatch rate -- the fraction of covering reads that
    # do not carry the reference base, a deletion counting -- is at or above this. The
    # default flags where at most half the reads carry the reference, since below a
    # majority nanopore basecalling error dominates. The report states the value used.
    "mismatch_threshold": 0.5,
}


def _pct_of(part: int, whole: int) -> Optional[float]:
    """``part`` per 100 of ``whole``, to 4 places; None when ``whole`` is 0."""
    return round(part / whole * 100, 4) if whole > 0 else None


class ConsensusIdentityModule:
    """Per-base identity and the flagged positions from a ``SharedPileup``.

    Args:
        pileup: SharedPileup instance to consume from.
        config: overrides of ``DEFAULTS``.

    Raises:
        ValueError: a mismatch_threshold outside (0, 1].
    """

    def __init__(
        self,
        pileup: SharedPileup,
        config: Optional[Mapping[str, Any]] = None,
    ) -> None:
        cfg = resolve_params(config, DEFAULTS)

        self.pileup = pileup
        self.min_depth = int(cfg["min_depth"])
        self.mismatch_threshold = float(cfg["mismatch_threshold"])
        # 0 would flag every position; above 1, none.
        if not 0 < self.mismatch_threshold <= 1:
            raise ValueError(
                f"consensus mismatch_threshold must be in (0, 1], a fraction of the "
                f"covering reads; got {self.mismatch_threshold}")
        self._finalized = False

    def snapshot(self) -> dict[str, Any]:
        """Identity and the flagged positions from the pileup as it stands.

        Recomputed on every call; nothing is stored.

        Returns:
            Summary scalars, the flagged positions and the per-position mismatch rates.
        """
        pileup = self.pileup
        ref_length = pileup.ref_length
        depth = pileup.depth  # (ref_length,)
        counts = pileup.counts  # (ref_length, 7)

        # --- Compute per-position consensus and mismatch rate ---

        # Reference base index at each position (-1 for N)
        ref_indices = np.array(
            [_BASE_TO_IDX.get(b, -1) for b in pileup.ref_seq], dtype=np.int64
        )

        # depth = A+C+G+T+deletion counts, so a deleted position is covered but agrees
        # with nothing. Insertions are not in depth.
        has_depth = depth >= self.min_depth  # (ref_length,) — min-depth gate
        has_any_depth = depth > 0            # sequenced at all — the BREADTH question

        # Reference-base agreement count per position (0 where the ref base is N).
        ref_base_counts = np.zeros(ref_length, dtype=np.int64)
        valid_ref = ref_indices >= 0
        ref_base_counts[valid_ref] = counts[
            np.arange(ref_length)[valid_ref], ref_indices[valid_ref]
        ]

        # --- Per-base identity = pooled reference-agreement fraction ---
        # Labelled "Per-base identity" in the report (2026-11-06), not "consensus":
        # it averages over read bases, so basecalling error lowers it, whereas a
        # consensus sequence would match the reference wherever most reads do.
        # identity = sum of reference-base agreements / sum of depth, over positions
        # with depth > 0 and a reference base other than N. Deletions lower it;
        # insertions are excluded (they have no reference position).
        # Scored at depth > 0, not min_depth: thinly covered positions tend to be the
        # error-prone ends, so a gate would bias identity up and shift it as positions
        # cross the gate mid-run. positions_above_min_depth shows how thin coverage is.
        scored = has_any_depth & valid_ref
        total_depth = int(depth[scored].sum())
        total_agree = int(ref_base_counts[scored].sum())
        # None when nothing was scorable; a real 0% has total_depth > 0.
        consensus_identity_pct = _pct_of(total_agree, total_depth)
        # The rest of the scored depth, over the same positions and weighting, so
        # agree + substituted + deleted = scored and the three percentages add to 100.
        # Insertions are not in depth, so they are counted beside it: events per 100
        # scored bases.
        total_deleted = int(counts[scored, _COL_DEL].sum())
        total_substituted = total_depth - total_agree - total_deleted
        total_inserted = int(counts[scored, _COL_INS].sum())
        # Breadth: positions with any coverage (cf. positions_above_min_depth).
        covered_count = int(np.sum(has_any_depth))

        # Per-position mismatch rate = (depth - ref_base_count) / depth, the share of
        # covering reads that do not carry the reference base. One division of counts, so a
        # rate exactly at the threshold compares as equal: 1 - 45/50 is 0.09999999999999998.
        safe_depth = np.maximum(depth, 1)  # avoid division by zero
        mismatch_rates = (depth - ref_base_counts) / safe_depth
        # Emitted only at depth >= min_depth (null below), so a rate from a few reads is
        # not drawn beside a well-covered one, and never at an N, which no base matches.
        # null thus means uncovered, below min_depth or N; covered_positions and
        # positions_above_min_depth tell the first two apart.
        # (The error-hotspot module emits its rates wherever depth > 0.)
        measured = has_depth & valid_ref

        # --- Flagged positions ---
        # At depth >= min_depth and a reference base other than N, a position is flagged
        # when its mismatch rate is at or above mismatch_threshold: at the default 0.5,
        # where at most half the covering reads carry the reference base. A position flags
        # on how much of the reference is lost, however the loss is split between bases and
        # deletions; base_counts says how. The rate is the one emitted per position, so the
        # list and the curve cannot disagree. No null: under a basecaller that miscalls a
        # modified base, its positions flag too. pos is 0-based, in reference order.
        flagged = measured & (mismatch_rates >= self.mismatch_threshold)
        mismatch_sites = [
            {
                "pos": int(pos),
                "ref_base": pileup.ref_seq[pos],
                "depth": int(depth[pos]),
                "mismatch_rate": float(mismatch_rates[pos]),
                "base_counts": {
                    "A": int(counts[pos, 0]), "C": int(counts[pos, 1]),
                    "G": int(counts[pos, 2]), "T": int(counts[pos, 3]),
                    "del": int(counts[pos, _COL_DEL]), "ins": int(counts[pos, _COL_INS]),
                },
            }
            for pos in np.flatnonzero(flagged)
        ]

        # --- Assemble result ---

        positions_above_min_depth = int(np.sum(has_depth))

        return {
            "ref_name": pileup.ref_name,
            "consensus_identity_pct": consensus_identity_pct,
            # Identity's numerator and denominator, in base observations (summed depth),
            # so identity can be pooled across constructs (``pooled_identity``).
            "identity_agreeing_depth": total_agree,
            "identity_scored_depth": total_depth,
            # What the rest of that depth is, and the insertions beside it, over the same
            # positions: agreeing + substitution + deletion depth = scored depth.
            "identity_substitution_depth": total_substituted,
            "identity_deletion_depth": total_deleted,
            "identity_insertion_events": total_inserted,
            "substitution_pct": _pct_of(total_substituted, total_depth),
            "deletion_pct": _pct_of(total_deleted, total_depth),
            "insertions_per_100_bases": _pct_of(total_inserted, total_depth),
            "total_positions": ref_length,
            "covered_positions": covered_count,
            "positions_above_min_depth": positions_above_min_depth,
            "min_depth_threshold": self.min_depth,
            # The positions at mismatch rate >= mismatch_threshold, and the rule used.
            "mismatch_threshold": self.mismatch_threshold,
            "mismatch_site_count": len(mismatch_sites),
            "mismatch_sites": mismatch_sites,
            "reads_in_pileup": pileup._read_count,
            # One entry per reference position: the mismatch rate, or null below
            # min_depth.
            "mismatch_rate_by_position": [
                v if m else None
                for v, m in zip(mismatch_rates.tolist(), measured.tolist())
            ],
        }

    def finalize(self) -> dict[str, Any]:
        """Freeze and return final metrics."""
        self._finalized = True
        return self.snapshot()


def pooled_identity(consensus_snapshots: "list[Mapping[str, Any]]") -> dict[str, Any]:
    """Identity pooled over constructs into one run-level number.

    Agreements and depth are summed across constructs and divided once, so each
    construct weighs by its coverage; a mean of per-construct percentages would weigh a
    thin construct like a deep one. Shared by the report and the integrated result, so
    the two cannot differ.

    ``identity_pct`` is None when nothing was scorable; a real 0% has
    ``scored_depth > 0``. The breakdown pools the same way, so it still adds to 100; it
    is None when a snapshot with scored depth lacks it (an older one), rather than summed
    as zero.
    """
    def total(key: str) -> int:
        return sum(int(s.get(key) or 0) for s in consensus_snapshots)

    agree, depth = total("identity_agreeing_depth"), total("identity_scored_depth")
    parts = all("identity_substitution_depth" in s
                for s in consensus_snapshots if s.get("identity_scored_depth"))
    sub, dele, ins = (total("identity_substitution_depth"),
                      total("identity_deletion_depth"), total("identity_insertion_events"))
    return {
        # False when no snapshot carries the numerator and denominator: an older
        # snapshot holds only the rounded percentage, which cannot be pooled.
        "ingredients_present": any(
            "identity_scored_depth" in s for s in consensus_snapshots),
        "identity_pct": _pct_of(agree, depth),
        "agreeing_depth": agree,
        "scored_depth": depth,
        "substitution_pct": _pct_of(sub, depth) if parts else None,
        "deletion_pct": _pct_of(dele, depth) if parts else None,
        "insertions_per_100_bases": _pct_of(ins, depth) if parts else None,
        "substitution_depth": sub if parts else None,
        "deletion_depth": dele if parts else None,
        "insertion_events": ins if parts else None,
        "denominator": "summed_coverage_over_scored_reference_positions",
        "min_depth_threshold": single_value(
            s.get("min_depth_threshold") for s in consensus_snapshots),
        # The same on every construct; None if they disagree.
        "mismatch_threshold": single_value(
            s.get("mismatch_threshold") for s in consensus_snapshots),
        # Additive across constructs.
        "mismatch_site_count": sum(
            int(s.get("mismatch_site_count") or 0) for s in consensus_snapshots),
        "covered_positions": sum(
            int(s.get("covered_positions") or 0) for s in consensus_snapshots),
        "total_positions": sum(
            int(s.get("total_positions") or 0) for s in consensus_snapshots),
        "n_constructs": len(consensus_snapshots),
    }
