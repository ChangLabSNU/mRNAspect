"""Pore occupancy from MinKNOW's ``pore_activity_*.csv``.

The file has one row per channel state per minute::

    Channel State,Experiment Time (minutes),State Time (samples)
    adapter,0,459797

``State Time`` is channel-time in samples, not a channel count, so a state's occupancy
is its share of the total channel-time. Each time bin is divided by its own observed
total, not a nominal one, because the run's last minute is partial. The full-minute
total (channels x 60 s x sampling rate) is not hardcoded: it is the most common
per-minute sum in the file.

MinKNOW publishes the file complete at run end, and it is small, so it is re-read in
full on each call. Not a :class:`StreamingModule`: its input is not reads.

Which states count as "productive" is left to the acceptance criteria: the module
reports every state's share, plus ``strand_fraction``.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any, Mapping, Optional

from ..minknow import PORE_ACTIVITY_GLOB as POR_GLOB, find_run_root as _find_root
from ..streaming_interface import resolve_params

logger = logging.getLogger(__name__)

_HEADER = ("Channel State", "Experiment Time (minutes)", "State Time (samples)")

DEFAULTS: dict[str, Any] = {
    # Most points in the emitted series; bins are widened to fit.
    "max_series_points": 240,
    # A minute whose total is below this share of a full minute's is listed in
    # partial_minutes. It is kept: its fractions are correct, just noisier.
    "partial_bin_threshold": 0.99,
}


def find_run_root(data_dir: Path | str) -> Optional[Path]:
    """The run root holding a pore-activity file; see ``minknow.find_run_root``."""
    return _find_root(data_dir, POR_GLOB)


class PoreActivityModule:
    """Channel-state occupancy over time, from ``pore_activity_*.csv``.

    ``snapshot`` / ``finalize`` as in the read modules, but the input comes through
    ``update_from_run_dir`` (a MinKNOW run directory), not reads.
    """

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        self.params = resolve_params(config, DEFAULTS)
        self._finalized = False
        self._source: Optional[Path] = None
        self._unavailable: Optional[str] = None
        # minute -> {state: samples}; bounded by run length.
        self._by_minute: dict[int, dict[str, int]] = {}

    # ---------------------------------------------------------------- ingest

    def update_from_run_dir(self, run_dir: Path | str) -> bool:
        """Re-read the pore-activity file in ``run_dir``; return whether one was found.

        No file is not an error (the input may not be a MinKNOW run): the snapshot then
        says ``available: False`` and why. More than one raises ValueError.
        """
        if self._finalized:
            raise RuntimeError("PoreActivityModule: update after finalize()")
        run_dir = Path(run_dir)
        matches = sorted(run_dir.glob(POR_GLOB))
        if not matches:
            # The reported reason names no path: the same run reached by another path
            # must give the same snapshot. The log gets the path.
            logger.info("no %s under %s - not a MinKNOW run directory, or the run has "
                        "not written one yet", POR_GLOB, run_dir)
            self._unavailable = (
                f"no {POR_GLOB} beside the inputs - not a MinKNOW run directory, or the "
                f"run has not yet written one")
            return False
        if len(matches) > 1:
            # One flow-cell position writes one file; merging several would add two
            # runs' channel-time into one denominator.
            raise ValueError(
                f"{len(matches)} pore-activity files under {run_dir}: "
                f"{[m.name for m in matches]}. Expected exactly one.")
        self._source = matches[0]
        self._unavailable = None
        self._parse(self._source)
        return True

    def _parse(self, path: Path) -> None:
        by_minute: dict[int, dict[str, int]] = {}
        with path.open(newline="") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                raise ValueError(f"{path} is empty") from None
            if tuple(h.strip() for h in header) != _HEADER:
                # A renamed or reordered column would otherwise give a plausible, wrong
                # occupancy rather than a crash.
                raise ValueError(
                    f"{path}: unexpected header {header!r}; expected {list(_HEADER)}")
            for lineno, row in enumerate(reader, start=2):
                if not row:
                    continue
                if len(row) != 3:
                    raise ValueError(f"{path}:{lineno}: expected 3 fields, got {len(row)}")
                state, minute_s, samples_s = (c.strip() for c in row)
                try:
                    minute, samples = int(minute_s), int(samples_s)
                except ValueError as exc:
                    raise ValueError(f"{path}:{lineno}: {exc}") from None
                if samples < 0:
                    raise ValueError(f"{path}:{lineno}: negative state time {samples}")
                # Unknown states are kept: dropping one would shrink the denominator.
                slot = by_minute.setdefault(minute, {})
                slot[state] = slot.get(state, 0) + samples
        self._by_minute = by_minute

    # ------------------------------------------------------------- reporting

    @property
    def _nominal(self) -> Optional[int]:
        """The most common per-minute channel-time: the full-minute total."""
        if not self._by_minute:
            return None
        totals: dict[int, int] = {}
        for states in self._by_minute.values():
            t = sum(states.values())
            totals[t] = totals.get(t, 0) + 1
        # Ties go to the larger total: a truncated minute can only be smaller.
        return max(totals, key=lambda t: (totals[t], t))

    def snapshot(self) -> dict[str, Any]:
        if self._unavailable is not None or not self._by_minute:
            return {
                "available": False,
                "unavailable_reason": self._unavailable or "no rows parsed",
                "source_file": self._source.name if self._source else None,
            }

        minutes = sorted(self._by_minute)
        nominal = self._nominal
        per_minute_total = {m: sum(self._by_minute[m].values()) for m in minutes}
        grand_total = sum(per_minute_total.values())

        # Sum samples over minutes and divide once, so a partial minute weighs less.
        agg: dict[str, int] = {}
        for states in self._by_minute.values():
            for state, samples in states.items():
                agg[state] = agg.get(state, 0) + samples

        states_out = {
            state: {
                "samples": samples,
                "fraction": round(samples / grand_total, 6) if grand_total else None,
            }
            for state, samples in sorted(agg.items(), key=lambda kv: -kv[1])
        }

        partial = [m for m in minutes
                   if nominal and per_minute_total[m]
                   < self.params["partial_bin_threshold"] * nominal]

        return {
            "available": True,
            # The name, not the path: it carries the flow cell and run ids and does not
            # change with where the run is read from.
            "source_file": self._source.name,
            "n_minutes": len(minutes),
            "first_minute": minutes[0],
            "last_minute": minutes[-1],
            "samples_per_full_minute": nominal,
            "total_channel_time_samples": grand_total,
            "states": states_out,
            # No "productive" share is computed; see the module docstring.
            "strand_fraction": states_out.get("strand", {}).get("fraction"),
            "partial_minutes": partial,
            "series": self._series(minutes, nominal, agg),
        }

    def _series(self, minutes: list[int], nominal: Optional[int],
                agg: dict[str, int]) -> dict[str, Any]:
        """Occupancy over time in at most ``max_series_points`` bins.

        Each bin sums samples and divides once, so a partial minute weighs less.
        """
        cap = max(1, int(self.params["max_series_points"]))
        span = minutes[-1] - minutes[0] + 1
        bin_minutes = max(1, -(-span // cap))  # ceil division -> at most `cap` bins

        order = [s for s, _ in sorted(agg.items(), key=lambda kv: -kv[1])]
        bins: dict[int, dict[str, int]] = {}
        for m in minutes:
            b = (m - minutes[0]) // bin_minutes
            acc = bins.setdefault(b, {})
            for state, samples in self._by_minute[m].items():
                acc[state] = acc.get(state, 0) + samples

        t, coverage = [], []
        fractions: dict[str, list[Optional[float]]] = {s: [] for s in order}
        for b in sorted(bins):
            acc = bins[b]
            total = sum(acc.values())
            t.append(minutes[0] + b * bin_minutes)
            full = nominal * bin_minutes if nominal else None
            coverage.append(round(total / full, 4) if full else None)
            for state in order:
                fractions[state].append(
                    round(acc.get(state, 0) / total, 6) if total else None)

        return {
            "bin_minutes": bin_minutes,
            "t_minutes": t,
            # Share of a full bin's channel-time present; < 1.0 marks a truncated bin,
            # such as the last. Not a fraction of pores.
            "coverage": coverage,
            "fractions": fractions,
        }

    def finalize(self) -> dict[str, Any]:
        self._finalized = True
        return self.snapshot()
