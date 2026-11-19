"""MAPQ distribution: how confidently reads were assigned to their construct.

With several constructs in the reference, a read's assignment is itself a QC result.
The default alignment uses ``--secondary=no``, so a read that fits two constructs
almost equally is emitted once, like a clean assignment; only its low MAPQ (0 for a
tie) shows it.

Counts are kept per MAPQ value (minimap2 emits 0-60), so the median is exact. Unmapped
reads are excluded and counted in ``skipped_unmapped``: their MAPQ of 0 would read as
ties.
"""

from typing import Any, Mapping, Optional

import pysam

from ..streaming_interface import StreamingModule, resolve_params

# MAPQ below this is a weak assignment (low_mapq_*, MAPQ 0 included); MAPQ 0 alone, a
# tie, is reported as mapq0_*.
DEFAULTS = {"low_mapq_threshold": 10}


class MapqModule(StreamingModule):
    """Per-construct MAPQ distribution over primary mapped reads.

    Snapshot:
        - n, mean, median (exact), min, max
        - mapq0_count / mapq0_fraction: ties
        - low_mapq_count / low_mapq_fraction: below ``low_mapq_threshold`` (also
          reported), MAPQ 0 included
        - distribution: {str(mapq): count}, the full table
        - skipped_unmapped
    """

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__()
        cfg = resolve_params(config, DEFAULTS)
        self._low_threshold = int(cfg["low_mapq_threshold"])
        self._counts: dict[int, int] = {}
        self._sum = 0
        self._skipped_unmapped = 0

    def update(self, read: pysam.AlignedSegment) -> None:
        """Record one read's MAPQ. Non-primary and unmapped reads are excluded."""
        self._check_not_finalized()

        if read.is_supplementary or read.is_secondary:
            return
        if read.is_unmapped:
            self._skipped_unmapped += 1
            return

        mapq = int(read.mapping_quality)
        self._read_count += 1
        self._counts[mapq] = self._counts.get(mapq, 0) + 1
        self._sum += mapq

    def _median(self) -> Optional[float]:
        """Exact median over the count table (no bin interpolation)."""
        n = self._read_count
        if n == 0:
            return None
        # Mean of the values at 0-based ranks lo and hi (the same rank when n is odd).
        lo_rank, hi_rank = (n - 1) // 2, n // 2
        seen = 0
        lo_val = hi_val = None
        for value in sorted(self._counts):
            seen += self._counts[value]
            if lo_val is None and seen > lo_rank:
                lo_val = value
            if seen > hi_rank:
                hi_val = value
                break
        return (lo_val + hi_val) / 2.0

    def snapshot(self) -> dict[str, Any]:
        """Return current metrics without altering state."""
        n = self._read_count
        mapq0 = self._counts.get(0, 0)
        low = sum(c for v, c in self._counts.items() if v < self._low_threshold)
        return {
            "n": n,
            # None, not 0.0, when no read was measured.
            "mean": round(self._sum / n, 4) if n else None,
            "median": self._median(),
            "min": min(self._counts) if self._counts else None,
            "max": max(self._counts) if self._counts else None,
            "mapq0_count": mapq0,
            "mapq0_fraction": round(mapq0 / n, 6) if n else None,
            "low_mapq_count": low,
            "low_mapq_fraction": round(low / n, 6) if n else None,
            "low_mapq_threshold": self._low_threshold,
            # Keyed by str, so it is the same before and after a JSON round-trip.
            "distribution": {str(v): self._counts[v] for v in sorted(self._counts)},
            "skipped_unmapped": self._skipped_unmapped,
        }

    def finalize(self) -> dict[str, Any]:
        """Freeze state and return final metrics."""
        self._finalized = True
        return self.snapshot()
