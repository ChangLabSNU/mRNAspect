"""Backpressure — uniform-in-time decimation, and honest lag.

The property that matters is *which* POD5s get dropped, not how many. Pore health,
translocation speed and occupancy drift monotonically across a 20-hour run, so
"process the first N" reports the healthy early run and omits the degraded end — bias in
exactly the direction that flatters a QC result. Admitting every s-th arrival is unbiased
with respect to run time, and that is what these tests pin.
"""

from __future__ import annotations

import pytest

from mrnaspect.realtime.backpressure import Backpressure


def _drive(bp: Backpressure, depths: list[int]) -> list[bool]:
    """Feed a sequence of queue depths; return the admit decision for each arrival."""
    return [bp.on_arrival(f"p{i}.pod5", d).admit for i, d in enumerate(depths)]


# ---------------------------------------------------------------------------
# Disabled by default
# ---------------------------------------------------------------------------

def test_disabled_by_default_admits_everything(caplog) -> None:
    """The measured headroom (align ~2%, HAC duty cycle ~0.3) makes an unbounded queue
    the right default. Lagging honestly beats thinning data nobody asked to thin."""
    bp = Backpressure()
    assert all(_drive(bp, [0, 5, 50, 500, 5000]))
    assert bp.decimated == 0 and bp.stride == 1
    assert bp.snapshot(5000)["enabled"] is False


# ---------------------------------------------------------------------------
# The bias property
# ---------------------------------------------------------------------------

def test_decimation_is_uniform_in_time_not_a_truncated_prefix() -> None:
    """The load-bearing test. Under sustained pressure the admitted set must be spread
    across the whole run, never the first chunk of it."""
    bp = Backpressure(max_queue_depth=4)
    # Sustained pressure for 40 arrivals.
    admitted = [i for i, ok in enumerate(_drive(bp, [10] * 40)) if ok]

    assert len(admitted) < 40, "pressure must actually decimate"
    # Spread: the admitted set must reach the END of the run, not stop early.
    assert max(admitted) >= 32, f"admitted set truncates early: {admitted}"
    # And be roughly evenly spaced — the gap between consecutive admits is bounded.
    gaps = [b - a for a, b in zip(admitted, admitted[1:])]
    assert max(gaps) <= bp.max_stride, f"uneven admission: {gaps}"


def test_a_prefix_policy_would_fail_the_same_check() -> None:
    """Sanity: the check above is not vacuous — the policy it rejects really is rejected."""
    prefix_admitted = list(range(8))          # "process the first 8, drop the rest"
    assert max(prefix_admitted) < 32


def test_stride_phases_on_absolute_index_so_samples_nest() -> None:
    """When the stride doubles, the admitted set must become a SUBSET of the previous one.

    Phasing on a per-regime counter instead would re-phase the sample at every change,
    which reintroduces the time bias the stride exists to avoid.
    """
    bp = Backpressure(max_queue_depth=2, resume_fraction=0.5)
    # Push hard so the stride climbs, and record which absolute indices were admitted.
    admitted = {i for i, ok in enumerate(_drive(bp, [99] * 32)) if ok}
    assert bp.stride > 1
    # Every admitted index is a multiple of some power of two <= the final stride, so the
    # final stride's multiples are all present.
    assert all(i % bp.stride == 0 for i in admitted if i >= bp.stride)


# ---------------------------------------------------------------------------
# Control behaviour
# ---------------------------------------------------------------------------

def test_stride_backs_off_under_pressure_and_recovers_when_drained() -> None:
    bp = Backpressure(max_queue_depth=4, resume_fraction=0.5)
    _drive(bp, [10] * 12)
    loaded = bp.stride
    assert loaded > 1, "stride must rise under pressure"

    _drive(bp, [0] * 12)              # queue drained
    assert bp.stride < loaded, "stride must recover once the backlog clears"
    assert bp.stride == 1


def test_hysteresis_prevents_flapping() -> None:
    """A queue sitting just under the limit must not retune on every arrival."""
    bp = Backpressure(max_queue_depth=10, resume_fraction=0.5)
    _drive(bp, [9] * 20)              # above resume threshold (5), below the limit (10)
    assert bp.stride_changes == []


def test_stride_is_capped_and_says_why(caplog) -> None:
    """Past the cap, thinning further does not help — the run needs a faster model."""
    bp = Backpressure(max_queue_depth=2, max_stride=4)
    with caplog.at_level("ERROR"):
        _drive(bp, [99] * 40)
    assert bp.stride == 4
    assert "faster basecall model" in caplog.text


@pytest.mark.parametrize("kwargs", [
    {"max_queue_depth": -1}, {"resume_fraction": 0}, {"resume_fraction": 1.5},
    {"max_stride": 0},
])
def test_invalid_configuration_fails_loudly(kwargs) -> None:
    with pytest.raises(ValueError):
        Backpressure(**kwargs)


# ---------------------------------------------------------------------------
# Lag
# ---------------------------------------------------------------------------

def test_lag_is_none_until_something_has_completed() -> None:
    """An estimate from no data is a fabricated number on an operator's dashboard."""
    bp = Backpressure()
    assert bp.estimated_lag_s(10) is None
    assert bp.snapshot(10)["estimated_lag_s"] is None

    bp.on_completed(200.0)
    assert bp.estimated_lag_s(10) == pytest.approx(2000.0)


def test_lag_tracks_recent_stage_time() -> None:
    """EWMA, so a run that speeds up or slows down is reflected rather than averaged away."""
    bp = Backpressure()
    for _ in range(10):
        bp.on_completed(200.0)
    assert bp.estimated_lag_s(1) == pytest.approx(200.0, rel=0.05)
    for _ in range(20):
        bp.on_completed(50.0)
    assert bp.estimated_lag_s(1) == pytest.approx(50.0, rel=0.1)


def test_snapshot_reports_keeping_up_and_the_counts() -> None:
    bp = Backpressure(max_queue_depth=4)
    _drive(bp, [10] * 20)
    bp.on_completed(190.0)
    snap = bp.snapshot(7)

    assert snap["enabled"] is True
    assert snap["arrivals"] == 20
    assert snap["admitted"] + snap["decimated"] == 20
    assert snap["max_queue_depth_seen"] == 10
    assert snap["keeping_up"] is False
    assert snap["estimated_lag_s"] == pytest.approx(7 * 190.0, rel=0.01)
    assert snap["stride_changes"], "stride transitions must be recorded, not just the end state"

    assert Backpressure().snapshot(0)["keeping_up"] is True


def test_zero_and_negative_stage_times_are_ignored() -> None:
    """A clock that goes backwards must not poison the lag estimate."""
    bp = Backpressure()
    bp.on_completed(0.0)
    bp.on_completed(-5.0)
    assert bp.estimated_lag_s(3) is None
