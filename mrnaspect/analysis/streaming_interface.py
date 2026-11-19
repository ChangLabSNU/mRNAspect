"""The streaming QC module interface, and the accumulators the modules share."""

import bisect
import itertools
import logging
import math
from abc import ABC, abstractmethod
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np
import pysam

logger = logging.getLogger(__name__)

# Share of a histogram's values outside its range at which a one-time warning is
# logged. Log only: no reported value depends on it.
_SATURATION_WARN_FRAC = 0.01


class StreamingModule(ABC):
    """Base class for streaming QC modules.

    Lifecycle: ``update(read)`` for each read, ``snapshot()`` at any time (it never
    changes state), ``finalize()`` once. After ``finalize()``, ``update()`` raises
    RuntimeError and ``snapshot()`` still works.

    ``update()`` must use O(1) memory: statistics are computed incrementally, never by
    keeping the reads.
    """

    def __init__(self) -> None:
        self._finalized = False
        self._read_count = 0

    @abstractmethod
    def update(self, read: pysam.AlignedSegment) -> None:
        """Add one read; raises RuntimeError after ``finalize()``."""
        ...

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """The current metrics. Changes no state; works after ``finalize()`` too."""
        ...

    @abstractmethod
    def finalize(self) -> dict[str, Any]:
        """Freeze state and return the final metrics."""
        ...

    def update_batch(self, reads: list[pysam.AlignedSegment]) -> None:
        """Add several reads; by default, ``update()`` on each."""
        for read in reads:
            self.update(read)

    def _check_not_finalized(self) -> None:
        """Raise RuntimeError if ``finalize()`` was already called."""
        if self._finalized:
            raise RuntimeError(
                f"{self.__class__.__name__}: update() called after finalize()"
            )


class WelfordAccumulator:
    """Running mean, variance, min and max in one pass with O(1) memory.

    Welford BP (1962), Technometrics 4(3):419-420; numerically stable.
    """

    def __init__(self) -> None:
        self.n = 0
        self._mean = 0.0
        self._m2 = 0.0  # sum of squared deviations
        self._min = float("inf")
        self._max = float("-inf")

    def update(self, value: float) -> None:
        """Add one value to the running statistics."""
        self.n += 1
        delta = value - self._mean
        self._mean += delta / self.n
        delta2 = value - self._mean
        self._m2 += delta * delta2

        if value < self._min:
            self._min = value
        if value > self._max:
            self._max = value

    @property
    def mean(self) -> float:
        return self._mean if self.n > 0 else 0.0

    @property
    def variance(self) -> float:
        """Population variance."""
        return self._m2 / self.n if self.n > 1 else 0.0

    @property
    def sample_variance(self) -> float:
        """Sample variance (Bessel's correction)."""
        return self._m2 / (self.n - 1) if self.n > 1 else 0.0

    @property
    def std(self) -> float:
        """Population standard deviation."""
        return self.variance ** 0.5

    @property
    def min(self) -> float:
        return self._min if self.n > 0 else 0.0

    @property
    def max(self) -> float:
        return self._max if self.n > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        # With no observations the statistics are None, not 0.0.
        if self.n == 0:
            return {"n": 0, "mean": None, "std": None,
                    "variance": None, "min": None, "max": None}
        return {
            "n": self.n,
            "mean": round(self.mean, 4),
            "std": round(self.std, 4),
            "variance": round(self.variance, 4),
            "min": round(self.min, 4),
            "max": round(self.max, 4),
        }


class HistogramAccumulator:
    """Fixed-bin histogram with quantiles interpolated inside a bin; O(1) per value.

    Values outside ``[bin_min, bin_max)`` are counted as underflow or overflow, never
    put in a bin.
    """

    def __init__(self, bin_min: float, bin_max: float, n_bins: int = 100) -> None:
        """A histogram of ``n_bins`` equal-width bins over ``[bin_min, bin_max)``.

        Args:
            bin_min: Left edge of the first bin; smaller values are underflow.
            bin_max: Right edge of the last bin; values ``>=`` it are overflow.
            n_bins: Number of bins.
        """
        self.bin_min = bin_min
        self.bin_max = bin_max
        self.n_bins = n_bins
        self.bin_width = (bin_max - bin_min) / n_bins
        self.counts = [0] * n_bins
        self.n = 0
        self._underflow = 0  # values < bin_min
        self._overflow = 0   # values >= bin_max
        # Set once the saturation warning is logged. It is the only state snapshot()
        # changes, and it changes no reported value.
        self._saturation_warned = False

    def update(self, value: float) -> None:
        """Add one value to the histogram."""
        self.n += 1
        if value < self.bin_min:
            self._underflow += 1
        elif value >= self.bin_max:
            self._overflow += 1
        else:
            idx = int((value - self.bin_min) / self.bin_width)
            # Float rounding can give n_bins for a value just below bin_max.
            idx = min(idx, self.n_bins - 1)
            self.counts[idx] += 1

    def quantile(self, q: float) -> Optional[float]:
        """The q-th quantile (0 <= q <= 1), interpolated inside the bin it falls in.

        None when there are no values, or when the quantile falls among the underflow
        or overflow: it then lies outside the bins and is unknown.
        """
        if not 0.0 <= q <= 1.0:
            raise ValueError(f"quantile q must be in [0, 1], got {q!r}")
        if self.n == 0:
            return None

        target = q * self.n
        cumulative = self._underflow

        # A quantile among the escaped values is None, not a range edge: underflow is
        # caught here, overflow by running off the end of the loop below.
        if cumulative >= target and self._underflow > 0:
            return None

        for i, count in enumerate(self.counts):
            cumulative += count
            if cumulative >= target and count > 0:
                # Linear interpolation within the bin
                prev_cumulative = cumulative - count
                fraction = (target - prev_cumulative) / count if count > 0 else 0.5
                bin_left = self.bin_min + i * self.bin_width
                return bin_left + fraction * self.bin_width

        return None

    @property
    def median(self) -> Optional[float]:
        return self.quantile(0.5)

    @property
    def q1(self) -> Optional[float]:
        return self.quantile(0.25)

    @property
    def q3(self) -> Optional[float]:
        return self.quantile(0.75)

    def to_dict(self) -> dict[str, Any]:
        # Bin i spans [bin_min + i*bin_width, +bin_width). Underflow and overflow are in
        # no bin, so bin_counts sums to n - underflow - overflow.
        geometry = {
            "bin_min": self.bin_min,
            "bin_max": self.bin_max,
            "n_bins": self.n_bins,
            "bin_width": self.bin_width,
            "bin_counts": list(self.counts),
        }
        # No observations: None quantiles, not bin_min.
        if self.n == 0:
            return {"n": 0, "Q1": None, "median": None, "Q3": None,
                    "underflow": 0, "overflow": 0, **geometry}
        self._warn_if_saturated()
        def _r(v: Optional[float]) -> Optional[float]:
            return None if v is None else round(v, 2)

        return {
            "n": self.n,
            "Q1": _r(self.q1),
            "median": _r(self.median),
            "Q3": _r(self.q3),
            "underflow": self._underflow,
            "overflow": self._overflow,
            **geometry,
        }

    def _warn_if_saturated(self) -> None:
        """Log once when under/overflow reaches ``_SATURATION_WARN_FRAC`` of the values.

        Escaped values are in no bin, so the shape misses them and a quantile among them
        is None: the configured range is too narrow for the data.
        """
        if self._saturation_warned:
            return
        escaped = self._underflow + self._overflow
        if escaped >= _SATURATION_WARN_FRAC * self.n:
            self._saturation_warned = True
            logger.warning(
                "Histogram saturation: %d/%d values (%.1f%%) fell outside [%g, %g) "
                "(underflow=%d, overflow=%d); the tail/shape is unreliable — widen the "
                "bins for this metric.",
                escaped, self.n, 100.0 * escaped / self.n,
                self.bin_min, self.bin_max, self._underflow, self._overflow)


class IntegerQuantileAccumulator:
    """Exact quantiles of an integer-valued measurement, from one count per value.

    For integer data such as dorado's poly(A) length (``pt:i``): memory is bounded by
    the range of values, not the number of reads, and the quantiles are those of the
    sorted values. ``HistogramAccumulator`` would spread each bin's count across the bin
    and so shift them.

    Quantiles follow numpy's default (``method="linear"``, Hyndman & Fan type 7), so the
    median is ``np.median`` of the same values.
    """

    def __init__(self) -> None:
        self.n = 0
        self._counts: dict[int, int] = {}

    def update(self, value: float) -> None:
        """Count one value. A non-integer raises: exactness and bounded memory rest on it."""
        iv = int(value)
        if iv != value:
            raise ValueError(
                f"expected an integer value, got {value!r}: counting distinct non-integer "
                "values is neither bounded in memory nor the measurement this was built for")
        self._counts[iv] = self._counts.get(iv, 0) + 1
        self.n += 1

    def counts(self) -> dict[int, int]:
        """A copy of the count per value."""
        return dict(self._counts)

    def quantile(self, q: float) -> Optional[float]:
        """The q-th quantile (0 <= q <= 1) by numpy's ``linear`` rule; None when empty.

        Position ``h = (n - 1) * q`` in the sorted values, interpolated between the order
        statistics either side of it, which are found from the cumulative counts.
        """
        if not 0.0 <= q <= 1.0:
            raise ValueError(f"quantile q must be in [0, 1], got {q!r}")
        if self.n == 0:
            return None
        values = sorted(self._counts)
        cumulative = list(itertools.accumulate(self._counts[v] for v in values))

        def kth(k: int) -> int:
            # 0-based: the first value whose cumulative count exceeds k.
            return values[bisect.bisect_right(cumulative, k)]

        h = (self.n - 1) * q
        lo = math.floor(h)
        x_lo = kth(lo)
        if h == lo:
            return float(x_lo)
        return x_lo + (h - lo) * (kth(lo + 1) - x_lo)

    @property
    def median(self) -> Optional[float]:
        return self.quantile(0.5)

    @property
    def q1(self) -> Optional[float]:
        return self.quantile(0.25)

    @property
    def q3(self) -> Optional[float]:
        return self.quantile(0.75)

    def to_dict(self) -> dict[str, Any]:
        """``n`` and the quartiles, keyed as in ``HistogramAccumulator.to_dict``.

        ``quantile_method: "exact"`` tells a reader which kind it holds.
        """
        def _r(v: Optional[float]) -> Optional[float]:
            return None if v is None else round(v, 2)

        return {"n": self.n, "Q1": _r(self.q1), "median": _r(self.median),
                "Q3": _r(self.q3), "quantile_method": "exact"}


def gaussian_smooth(counts: Sequence[float], bin_width: float,
                    bandwidth: float) -> list[float]:
    """``counts`` through a Gaussian kernel with SD ``bandwidth``, on the same bins.

    Each value is the expected count in its bin, in the bars' own unit, so the curve
    overlays them. ``bandwidth`` and ``bin_width`` share a unit (nt for poly(A)). The
    kernel is sampled at whole-bin offsets out to 4 SD and scaled to sum to 1, so the
    total is kept except what the kernel carries past the first or last bin. That part
    is dropped rather than folded back, so no bin is credited with reads from outside the
    range. Rounded to 2 decimals: it is drawn, and never a reported number.

    Raises:
        ValueError: ``bin_width`` or ``bandwidth`` not a positive finite number, or a
            count that is negative or not finite.
    """
    for name, v in (("bin_width", bin_width), ("bandwidth", bandwidth)):
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            raise ValueError(f"gaussian_smooth: {name} must be a positive number, got {v!r}")
    c = np.asarray(counts, dtype=float)
    if c.size and not (np.isfinite(c).all() and (c >= 0).all()):
        raise ValueError("gaussian_smooth: counts must be finite and non-negative")
    if not c.any():
        return [0.0] * c.size
    sigma = bandwidth / bin_width  # in bins
    half = int(math.ceil(4 * sigma))
    offsets = np.arange(-half, half + 1)
    weights = np.exp(-0.5 * (offsets / sigma) ** 2)
    weights /= weights.sum()
    # "full" holds every overlap; the centred slice is the input's own bins. ("same"
    # returns the kernel's length when the input is the shorter of the two.)
    smoothed = np.convolve(c, weights, mode="full")[half:half + c.size]
    return [round(float(v), 2) for v in smoothed]


def resolve_params(
    overrides: Optional[Mapping[str, Any]], defaults: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge a partial ``overrides`` mapping onto ``defaults``, key by key.

    A nested mapping (e.g. a histogram spec) merges recursively, so a partial override
    keeps its other keys. Raises ValueError for a key not in ``defaults`` (a typo would
    otherwise be ignored) or a non-mapping where ``defaults`` has a mapping.
    """
    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - set(defaults))
    if unknown:
        raise ValueError(
            f"unknown parameter key(s) {unknown}; allowed: {sorted(defaults)}")
    resolved: dict[str, Any] = {}
    for key, dval in defaults.items():
        if isinstance(dval, Mapping):
            oval = overrides.get(key)
            if oval is not None and not isinstance(oval, Mapping):
                raise ValueError(
                    f"parameter '{key}' must be a mapping, got {type(oval).__name__}")
            resolved[key] = resolve_params(oval, dval)  # recurse; None -> copy of dval
        elif key in overrides:
            resolved[key] = overrides[key]
        else:
            resolved[key] = dval
    return resolved


def validate_histogram(spec: Mapping[str, Any]) -> tuple[float, float, int]:
    """Check a complete histogram spec; return ``(bin_min, bin_max, n_bins)``.

    Requires ``bin_max > bin_min`` and ``n_bins >= 1``.
    """
    bin_min = float(spec["bin_min"])
    bin_max = float(spec["bin_max"])
    n_bins = int(spec["n_bins"])
    if bin_max <= bin_min:
        raise ValueError(f"histogram bin_max ({bin_max}) must exceed bin_min ({bin_min})")
    if n_bins < 1:
        raise ValueError(f"histogram n_bins must be >= 1, got {n_bins}")
    return bin_min, bin_max, n_bins


def single_value(values: "Iterable[Any]") -> Any:
    """The single distinct value in ``values``, or None when they disagree or are empty.

    For run-level fields repeated on every per-construct snapshot (e.g.
    ``min_depth_threshold``): on disagreement, no one construct's value stands for all.
    """
    seen = set(values)
    return seen.pop() if len(seen) == 1 else None
