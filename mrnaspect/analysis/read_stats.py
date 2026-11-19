"""Run summary over primary reads: read length, mapping rate and quality score.

Means and spreads are exact (Welford); quartiles and figures come from fixed-bin
histograms. Secondary and supplementary records are counted, not analysed.
"""

from typing import Any, Mapping, Optional

import pysam

from .streaming_interface import (
    StreamingModule,
    WelfordAccumulator,
    HistogramAccumulator,
    resolve_params,
    validate_histogram,
)

# `histogram` is the qscore histogram (the name is kept for existing configs);
# `length_histogram` is the read-length one, in 10 nt bins. Reads at or past bin_max are
# counted as overflow, not binned: raise `length_histogram.bin_max` for long constructs,
# or pass `ref_length` (see LENGTH_HISTOGRAM_REF_MULTIPLE).
DEFAULTS = {
    "histogram": {"bin_min": 0.0, "bin_max": 30.0, "n_bins": 100},
    "length_histogram": {"bin_min": 0.0, "bin_max": 5000.0, "n_bins": 500},
}

#: With a known reference length L the read-length range is [0, 3L) instead, so bin
#: width scales with the construct. 3L rather than 2L keeps concatemer dimers (at 2L)
#: inside the range.
LENGTH_HISTOGRAM_REF_MULTIPLE = 3.0


def _round_or_none(value: Optional[float], ndigits: int = 4) -> Optional[float]:
    """Round, passing None through: a histogram quantile can be None even when n > 0."""
    return None if value is None else round(value, ndigits)


class RunSummaryModule(StreamingModule):
    """Run summary over a stream of reads, O(1) memory.

    Snapshot:
        - read_length: {n, mean, std, variance, min, max}
        - read_length_distribution: {n, Q1, median, Q3, underflow, overflow, bin
          geometry, bin_counts}
        - mapping: {total, mapped, unmapped, mapping_rate}, primary reads only
        - non_primary: {secondary, supplementary}
        - quality_score: {n, mean, std, variance, min, max, Q1, median, Q3, missing_qs}
        - quality_score_distribution: as read_length_distribution
    """

    def __init__(self, config: Optional[Mapping[str, Any]] = None, *,
                 ref_length: Optional[int] = None) -> None:
        """Create empty accumulators.

        Args:
            config: per-key overrides onto :data:`DEFAULTS`.
            ref_length: the reference length the reads are measured against (the
                construct's, or the longest contig's for the run-level summary). Sets
                the read-length range (see :data:`LENGTH_HISTOGRAM_REF_MULTIPLE`);
                ``None`` keeps the default range.
        """
        super().__init__()
        cfg = resolve_params(config, DEFAULTS)
        # Any `length_histogram` in config turns off the range derived from ref_length.
        if ref_length and not (config or {}).get("length_histogram"):
            cfg["length_histogram"] = {
                **cfg["length_histogram"],
                "bin_max": float(ref_length) * LENGTH_HISTOGRAM_REF_MULTIPLE,
            }

        # Read length: exact mean/variance (Welford) and the shape (histogram), both fed
        # the same reads.
        self._length_acc = WelfordAccumulator()
        len_min, len_max, len_bins = validate_histogram(cfg["length_histogram"])
        self._length_hist = HistogramAccumulator(
            bin_min=len_min, bin_max=len_max, n_bins=len_bins)

        # Mapping counters (primary reads only)
        self._total = 0
        self._mapped = 0
        self._unmapped = 0

        # Secondary and supplementary records: counted apart, never in the totals above.
        self._secondary = 0
        self._supplementary = 0

        # Quality score (qs tag)
        self._qs_acc = WelfordAccumulator()
        qs_min, qs_max, qs_bins = validate_histogram(cfg["histogram"])
        self._qs_hist = HistogramAccumulator(bin_min=qs_min, bin_max=qs_max, n_bins=qs_bins)
        self._missing_qs = 0  # primary reads without a qs tag

    def update(self, read: pysam.AlignedSegment) -> None:
        """Add one record; secondary and supplementary records are only counted."""
        self._check_not_finalized()

        # Each flag is counted on its own; the record is then skipped.
        if read.is_supplementary or read.is_secondary:
            if read.is_supplementary:
                self._supplementary += 1
            if read.is_secondary:
                self._secondary += 1
            return

        self._read_count += 1

        # --- Read length ---
        # A read without SEQ ('*') still counts in mapping but adds no length.
        qlen = read.query_length
        if qlen:
            self._length_acc.update(qlen)
            self._length_hist.update(qlen)

        # --- Mapping ---
        self._total += 1
        if read.is_unmapped:
            self._unmapped += 1
        else:
            self._mapped += 1

        # --- Quality score (qs tag) ---
        try:
            qs = read.get_tag("qs")
        except KeyError:
            self._missing_qs += 1
        else:
            self._qs_acc.update(float(qs))
            self._qs_hist.update(float(qs))

    def snapshot(self) -> dict[str, Any]:
        """Return current metrics without altering state."""
        mapping_rate = self._mapped / self._total if self._total > 0 else 0.0

        return {
            "read_length": self._length_acc.to_dict(),
            # With bin geometry and counts, so the figure can be drawn from the JSON.
            "read_length_distribution": self._length_hist.to_dict(),
            "mapping": {
                "total": self._total,
                "mapped": self._mapped,
                "unmapped": self._unmapped,
                "mapping_rate": round(mapping_rate, 6),
            },
            # Not included in `mapping`.
            "non_primary": {
                "secondary": self._secondary,
                "supplementary": self._supplementary,
            },
            # The qscore histogram, in the same form as read_length_distribution.
            "quality_score_distribution": self._qs_hist.to_dict(),
            "quality_score": {
                **self._qs_acc.to_dict(),
                # None-safe: a quantile is None with no qs tags, and also when it falls
                # among out-of-range values, so n > 0 does not guarantee a number.
                "Q1": _round_or_none(self._qs_hist.q1),
                "median": _round_or_none(self._qs_hist.median),
                "Q3": _round_or_none(self._qs_hist.q3),
                "missing_qs": self._missing_qs,
            },
        }

    def finalize(self) -> dict[str, Any]:
        """Freeze state and return final metrics."""
        self._finalized = True
        return self.snapshot()
