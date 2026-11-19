"""Full length: how far each read's alignment reached toward the reference's two ends.

Direct RNA sequencing reads a molecule 3' to 5' (the adapter anneals to the poly(A)
tail), so a read that stops early loses its 5' end: 5' reach measures integrity, and 3'
reach is a sanity check of the chemistry and the reference. A read is full length when
it stops at most ``full_length_5p_max_short_nt`` short of the 5' end; the fraction
depends strongly on that value, so it is reported with it.

Fractions are over the construct's admitted reads. These are excluded, and counted,
because their alignment ends are not molecule ends (excluding them tends to raise the
fraction):

* ``SA`` (split-mapped, a candidate concatemer): an end of the primary is a junction.
* ``pi`` (a dorado read-split child): its ends lie inside the parent molecule.
* reverse strand: on a sense reference its ``reference_start`` is the 3' end. A high
  rate usually means the reference is reverse-complemented.

Unmapped reads have no construct and are not seen here; ``pooled_envelope`` bounds the
run-level fraction over them. A molecule without a poly(A) tail is never sequenced, so
no fraction covers it.

The 5' and 3' shortfalls are also counted jointly, since the two marginals cannot tell
truncation at one end (a cross in the plane) from fragmentation (the diagonal).
``d5 + d3 = ref_length - aligned span``, so anti-diagonals are lines of equal alignment
length. Where the reference extends past what any read covers, the densest cell sits at
that floor rather than at the origin.
"""

from typing import Any, Mapping, Optional

import pysam

from ..streaming_interface import (
    HistogramAccumulator,
    StreamingModule,
    resolve_params,
)

DEFAULTS = {
    # nt the 5' end may fall short and still count as full length.
    "full_length_5p_max_short_nt": 50,
    # Distance bins, nt short of an end: inclusive upper edges, ascending, plus an
    # implicit ">last" bin. The first edge is 10, not 0, because basecalling can drop
    # a read's last few nucleotides.
    "bin_edges_nt": [10, 50, 100],
    # Bins of the start/end position histograms, which span [0, ref_length).
    "position_histogram_bins": 200,
    # Bins per axis of the joint (5' short x 3' short) histogram.
    "joint_histogram_bins": 60,
}


class TerminalReachModule(StreamingModule):
    """How far each read's alignment reached toward the 5' and 3' ends of one construct.

    Every fraction is over ``n_admitted``: 
    the construct's reads less those counted under ``excluded``.
    """

    def __init__(
        self,
        ref_length: int,
        config: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__()
        if ref_length <= 0:
            raise ValueError(
                f"ref_length must be positive, got {ref_length!r} — the reference length "
                "is the denominator of every terminal distance and a bad one is silent"
            )
        cfg = resolve_params(config, DEFAULTS)
        self.ref_length = int(ref_length)

        edges = [int(e) for e in cfg["bin_edges_nt"]]
        if not edges or any(b <= a for a, b in zip(edges, edges[1:])) or edges[0] < 0:
            raise ValueError(
                f"bin_edges_nt must be positive and strictly ascending, got {edges!r}"
            )
        self.bin_edges = edges

        self.full_length_max_short = int(cfg["full_length_5p_max_short_nt"])
        if self.full_length_max_short < 0:
            raise ValueError(
                "full_length_5p_max_short_nt must be >= 0, got "
                f"{self.full_length_max_short!r}"
            )

        # One counter per bin plus the ">last edge" overflow bin.
        self._n_bins = len(edges) + 1
        self._bins_5p = [0] * self._n_bins
        self._bins_3p = [0] * self._n_bins

        self._n_admitted = 0
        self._full_length = 0

        # Where admitted alignments start (5'-most covered position) and end (3'-most),
        # as 0-based inclusive reference positions in [0, ref_length). Named by
        # reference coordinate: the read itself ends at the 5' end. pysam's exclusive
        # reference_end would put every full-length read at ref_length, out of range.
        n_pos_bins = int(cfg["position_histogram_bins"])
        if n_pos_bins < 1:
            raise ValueError(
                f"position_histogram_bins must be >= 1, got {n_pos_bins!r}")
        self.position_histogram_bins = n_pos_bins
        self._start_hist = HistogramAccumulator(
            bin_min=0.0, bin_max=float(self.ref_length), n_bins=n_pos_bins)
        self._end_hist = HistogramAccumulator(
            bin_min=0.0, bin_max=float(self.ref_length), n_bins=n_pos_bins)

        # Joint (d5, d3) counts, sparse: most cells are empty, and the snapshot is
        # re-emitted after every POD5.
        n_joint = int(cfg["joint_histogram_bins"])
        if n_joint < 1:
            raise ValueError(
                f"joint_histogram_bins must be >= 1, got {n_joint!r}")
        self.joint_histogram_bins = n_joint
        self._joint: dict[tuple[int, int], int] = {}
        # Pairs outside the plane: the BAM's coordinates disagree with the reference
        # loaded here (a different FASTA than the aligner used).
        self._joint_out_of_range = 0

        # Exclusions, each counted.
        self._n_reverse = 0
        self._n_split_mapped = 0        # SA-carrying: an end is a junction
        self._n_split_child = 0         # pi-carrying: dorado read-split child
        self._n_no_alignment_end = 0    # reference_end is None (no usable CIGAR)

    # --- accumulation -------------------------------------------------------

    def _bin_index(self, distance: int) -> int:
        """Index of the bin holding ``distance`` nt short of a terminus."""
        for i, edge in enumerate(self.bin_edges):
            if distance <= edge:
                return i
        return self._n_bins - 1

    def update(self, read: pysam.AlignedSegment) -> None:
        """Record one read's 5' and 3' shortfall, or count why it was excluded.

        The caller passes only primary mapped reads assigned to this construct.
        """
        self._check_not_finalized()

        if read.is_reverse:
            self._n_reverse += 1
            return
        if read.has_tag("SA"):
            self._n_split_mapped += 1
            return
        if read.has_tag("pi"):
            self._n_split_child += 1
            return

        ref_end = read.reference_end        # 0-based, EXCLUSIVE (pysam)
        ref_start = read.reference_start    # 0-based, INCLUSIVE
        if ref_end is None or ref_start is None:
            # No walkable CIGAR: counted, never folded into a bin as a zero distance.
            self._n_no_alignment_end += 1
            return

        # Reference bases left uncovered at the 5' and 3' ends, never below 0.
        d5 = max(0, ref_start)
        d3 = max(0, self.ref_length - ref_end)

        self._n_admitted += 1
        self._bins_5p[self._bin_index(d5)] += 1
        self._bins_3p[self._bin_index(d3)] += 1
        # The same reads as the ladders; ref_end - 1 is the last covered position.
        self._start_hist.update(ref_start)
        self._end_hist.update(ref_end - 1)

        # The joint cell of (d5, d3), a pairing the two ladders do not keep.
        i = (d5 * self.joint_histogram_bins) // self.ref_length
        j = (d3 * self.joint_histogram_bins) // self.ref_length
        if 0 <= i < self.joint_histogram_bins and 0 <= j < self.joint_histogram_bins:
            self._joint[(i, j)] = self._joint.get((i, j), 0) + 1
        else:
            self._joint_out_of_range += 1
        if d5 <= self.full_length_max_short:
            self._full_length += 1

    # --- reporting ----------------------------------------------------------

    def _bin_labels(self) -> list[str]:
        labels = []
        prev = -1
        for edge in self.bin_edges:
            labels.append(f"<={edge}" if prev < 0 else f"{prev + 1}-{edge}")
            prev = edge
        labels.append(f">{prev}")
        return labels

    def _bin_block(self, bins: list[int]) -> dict[str, Any]:
        n = self._n_admitted
        labels = self._bin_labels()
        return {
            "counts": dict(zip(labels, bins)),
            # None when no read was admitted, not a 0 that would read as a measurement.
            "fractions": (
                {label: round(c / n, 6) for label, c in zip(labels, bins)}
                if n else {label: None for label in labels}
            ),
        }

    def _joint_block(self) -> dict[str, Any]:
        """Sparse (5' short, 3' short) cell counts, plus the geometry to place them.

        ``cells`` is ``[bin_5p, bin_3p, count]`` triples, sorted, so the output does not
        depend on the order the reads arrived in.
        """
        n_bins = self.joint_histogram_bins
        return {
            "axes": ["five_prime_short_nt", "three_prime_short_nt"],
            "n_bins": n_bins,
            "bin_width": round(self.ref_length / n_bins, 6),
            # Both axes span [0, ref_length), so cell (i, j) covers
            # [i*bin_width, (i+1)*bin_width) nt short of the 5' terminus and likewise for j.
            "n": sum(self._joint.values()),
            "cells": [[i, j, c] for (i, j), c in sorted(self._joint.items())],
            "out_of_range": self._joint_out_of_range,
        }

    def snapshot(self) -> dict[str, Any]:
        n = self._n_admitted
        return {
            "ref_length": self.ref_length,
            # The threshold behind full_length_count.
            "full_length_5p_max_short_nt": self.full_length_max_short,
            "bin_edges_nt": list(self.bin_edges),

            "n_admitted": n,
            "full_length_count": self._full_length,
            # Over admitted reads only, so unmapped reads are left out: the optimistic
            # end of the run-level bounds (see pooled_envelope).
            "full_length_fraction": round(self._full_length / n, 6) if n else None,
            "denominator": "reads_assigned_to_construct_admitted",

            "five_prime": self._bin_block(self._bins_5p),
            "three_prime": self._bin_block(self._bins_3p),

            # Bin geometry plus counts, over admitted reads, in 0-based inclusive
            # reference positions (see __init__).
            "start_position_distribution": self._start_hist.to_dict(),
            "end_position_distribution": self._end_hist.to_dict(),
            "joint_shortfall_distribution": self._joint_block(),

            # Reads not admitted, by cause.
            "excluded": {
                "reverse_strand": self._n_reverse,
                "split_mapped_sa": self._n_split_mapped,
                "split_child_pi": self._n_split_child,
                "no_alignment_end": self._n_no_alignment_end,
            },
            # Over admitted plus reverse-strand reads. A high rate usually means the
            # reference is reverse-complemented; no threshold is applied here.
            "reverse_strand_rate": (
                round(self._n_reverse / (n + self._n_reverse), 6)
                if (n + self._n_reverse) else None
            ),
        }

    def finalize(self) -> dict[str, Any]:
        self._finalized = True
        return self.snapshot()


def pooled_envelope(
    reaches: "list[Mapping[str, Any]]",
    total_primary_reads: Optional[int],
) -> dict[str, Any]:
    """Pool per-construct terminal reach into the run's full-length bounds.

    Shared by the report and the integrated result, so the two agree. The bounds differ
    in an assumption about the reads not admitted, not by sampling error: ``upper``
    treats them as non-analyte and leaves them out of the denominator; ``lower`` divides
    by every primary read in the run, so each counts as a molecule that fell short. A
    bound whose denominator is missing or zero is None.
    """
    hit = sum(int(r.get("full_length_count") or 0) for r in reaches)
    admitted = sum(int(r.get("n_admitted") or 0) for r in reaches)
    excluded = sum(sum((r.get("excluded") or {}).values()) for r in reaches)
    thresholds = {r.get("full_length_5p_max_short_nt") for r in reaches}
    return {
        "full_length_count": hit,
        "n_admitted": admitted,
        "n_excluded_from_reach": excluded,
        "primary_reads": total_primary_reads,
        "fraction_upper": round(hit / admitted, 6) if admitted else None,
        "fraction_lower": (round(hit / total_primary_reads, 6)
                           if total_primary_reads else None),
        "denominator_upper": "reads_admitted_to_terminal_reach",
        "denominator_lower": "all_primary_reads_in_the_run",
        # Named so the pair is never read as a confidence interval.
        "bound_type": "assumption_envelope_not_confidence_interval",
        # A molecule without a poly(A) tail is never sequenced; neither bound covers it.
        "population": "reads from molecules that carried a poly-A tail and entered a pore",
        "five_prime_max_short_nt": thresholds.pop() if len(thresholds) == 1 else None,
        "n_constructs": len(reaches),
    }
