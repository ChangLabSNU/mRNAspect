"""Backpressure: admission control and lag reporting for the live POD5 queue.

The lag estimate is always reported. Decimation engages only when ``max_queue_depth``
is set, as a safety net for a run that cannot keep up. It admits every ``stride``-th
arrival by arrival index, so the sample stays uniform in run time: pore health and
throughput drift over a run, and keeping only the first POD5s would over-represent its
healthy start.

What becomes of a decimated POD5 depends on ``sampling``:

* ``True``: it is never analysed. The run finishes in real time and the report
  describes a subsample, named in ``run_accounting.sampled_out`` and in the report's
  banner.
* ``False``: it is re-swept at run-end (``RealtimePipeline.seal``) within the seal
  budget, so decimation costs only interim latency. One still not processed by then is
  named in ``run_accounting.deferred_backpressure``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_STRIDE = 16
DEFAULT_RESUME_FRACTION = 0.5
# Suggested depth when decimation is on. Shallow on purpose: a run that keeps up never
# queues this deep, so decimation engages only when something is wrong.
DEFAULT_MAX_QUEUE_DEPTH = 4
_EWMA_ALPHA = 0.3


@dataclass(frozen=True)
class AdmissionDecision:
    admit: bool
    reason: str
    """``ok`` | ``decimated``"""
    stride: int
    queue_depth: int


@dataclass
class Backpressure:
    """Admission control and lag accounting for the live ingest queue.

    Args:
        max_queue_depth: Queue depth at or above which each arrival doubles the stride.
            ``0`` turns decimation off: nothing is skipped and the run just lags.
        resume_fraction: The stride halves once depth is at or below
            ``max_queue_depth * resume_fraction``, so a queue hovering at the limit does
            not flip the stride on every arrival.
        max_stride: Ceiling on the stride. At the ceiling the run falls further behind
            rather than thinning more.
        sampling: ``True`` leaves a decimated POD5 unanalysed; ``False`` re-sweeps it at
            run-end. See the module docstring.
    """

    max_queue_depth: int = 0
    resume_fraction: float = DEFAULT_RESUME_FRACTION
    max_stride: int = DEFAULT_MAX_STRIDE
    sampling: bool = False

    stride: int = 1
    arrivals: int = 0
    admitted: int = 0
    decimated: int = 0
    max_depth_seen: int = 0
    stride_changes: list = field(default_factory=list)
    _ewma_stage_s: Optional[float] = None

    def __post_init__(self) -> None:
        if self.max_queue_depth < 0:
            raise ValueError("max_queue_depth must be >= 0")
        if not 0 < self.resume_fraction <= 1:
            raise ValueError("resume_fraction must be in (0, 1]")
        if self.max_stride < 1:
            raise ValueError("max_stride must be >= 1")

    # -- admission --------------------------------------------------------

    def on_arrival(self, name: str, queue_depth: int) -> AdmissionDecision:
        """Decide whether to enqueue an arriving POD5, and adjust the stride."""
        index = self.arrivals
        self.arrivals += 1
        self.max_depth_seen = max(self.max_depth_seen, queue_depth)

        if self.max_queue_depth:
            self._retune(queue_depth)

        # Phase on the absolute arrival index, so the admitted sets nest as the stride
        # doubles (every 4th is a subset of every 2nd) and a stride change never
        # re-phases the sample.
        admit = self.stride == 1 or index % self.stride == 0
        if admit:
            self.admitted += 1
            return AdmissionDecision(True, "ok", self.stride, queue_depth)

        self.decimated += 1
        logger.warning(
            "Backpressure: %s %s (queue depth %d >= %d, stride 1/%d). %s",
            "SAMPLING OUT" if self.sampling else "DEFERRING",
            name, queue_depth, self.max_queue_depth, self.stride,
            "It will NOT be analysed; the report says so in its banner."
            if self.sampling else
            "It is recorded in run_accounting and re-swept at run-end — not dropped.")
        return AdmissionDecision(False, "decimated", self.stride, queue_depth)

    def _retune(self, queue_depth: int) -> None:
        old = self.stride
        if queue_depth >= self.max_queue_depth and self.stride < self.max_stride:
            self.stride = min(self.stride * 2, self.max_stride)
        elif queue_depth <= self.max_queue_depth * self.resume_fraction and self.stride > 1:
            self.stride = max(1, self.stride // 2)
        if self.stride != old:
            self.stride_changes.append(
                {"at_arrival": self.arrivals, "from": old, "to": self.stride,
                 "queue_depth": queue_depth})
            level = logger.warning if self.stride > old else logger.info
            level("Backpressure: stride %d -> %d at queue depth %d",
                  old, self.stride, queue_depth)
            if self.stride >= self.max_stride:
                logger.error(
                    "Backpressure at max stride (1/%d) and still behind. Thinning further "
                    "would not help — the run needs a faster basecall model (HAC) or a "
                    "longer batch duration.", self.max_stride)

    # -- lag --------------------------------------------------------------

    def on_completed(self, stage_seconds: float) -> None:
        """Record how long one input took end to end, for the lag estimate."""
        if stage_seconds <= 0:
            return
        self._ewma_stage_s = (
            stage_seconds if self._ewma_stage_s is None
            else _EWMA_ALPHA * stage_seconds + (1 - _EWMA_ALPHA) * self._ewma_stage_s)

    def estimated_lag_s(self, queue_depth: int) -> Optional[float]:
        """Seconds behind: queue depth x the moving average of per-input time.

        None until an input has completed.
        """
        if self._ewma_stage_s is None:
            return None
        return queue_depth * self._ewma_stage_s

    def snapshot(self, queue_depth: int) -> dict:
        lag = self.estimated_lag_s(queue_depth)
        return {
            "enabled": bool(self.max_queue_depth),
            "sampling": bool(self.sampling),
            # Per POD5; None before the first arrival, 1.0 until one is skipped.
            "admitted_fraction": (None if not self.arrivals
                                  else round(self.admitted / self.arrivals, 6)),
            "max_queue_depth": self.max_queue_depth,
            "queue_depth": queue_depth,
            "max_queue_depth_seen": self.max_depth_seen,
            "arrivals": self.arrivals,
            "admitted": self.admitted,
            "decimated": self.decimated,
            "stride": self.stride,
            "stride_changes": list(self.stride_changes),
            "mean_stage_s": (None if self._ewma_stage_s is None
                             else round(self._ewma_stage_s, 2)),
            "estimated_lag_s": None if lag is None else round(lag, 1),
            "keeping_up": queue_depth <= 1,
        }
