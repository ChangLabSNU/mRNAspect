"""Run-end sealing — sweep, coverage, and the no-silent-drops accounting.

The sweep is driven with an injected clock and sleep so budget-exhaustion and
stability timing are exercised deterministically rather than by waiting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mrnaspect.realtime.sealing import (
    DEFAULT_SEAL_STABLE_POLLS,
    RunSealer,
    build_run_accounting,
    read_sequencing_summary,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _pod5(d: Path, name: str, size: int = 100) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"\0" * size)
    return p


def _sealer(d: Path, clock: FakeClock, **over) -> RunSealer:
    kwargs = dict(poll_interval_s=2.0, stable_polls=2, budget_s=60.0,
                  clock=clock, sleep=clock.sleep)
    kwargs.update(over)
    return RunSealer(d, **kwargs)


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------

def test_sweep_finds_the_file_the_watcher_was_still_verifying(tmp_path: Path) -> None:
    """The whole reason sealing sweeps the DIRECTORY and not a queue.

    The final POD5 arrives ~1.1 s before the marker and is still mid-verification when the
    live watcher stops. It was never enqueued, so only a directory scan can recover it —
    and it is the end-of-run pores, the reads whose loss biases every metric optimistically.
    """
    pod5_dir = tmp_path / "pod5"
    _pod5(pod5_dir, "run_0.pod5")
    _pod5(pod5_dir, "run_1.pod5")     # the one still being verified at marker time

    clock = FakeClock()
    handed: list[Path] = []
    report = _sealer(pod5_dir, clock).sweep(
        already_handled={"run_0.pod5"}, on_ready=handed.append)

    assert [p.name for p in handed] == ["run_1.pod5"]
    assert report.unstable_at_seal == []
    assert not report.budget_exceeded


def test_sweep_does_nothing_when_everything_was_already_ingested(tmp_path: Path) -> None:
    pod5_dir = tmp_path / "pod5"
    _pod5(pod5_dir, "run_0.pod5")
    clock = FakeClock()
    report = _sealer(pod5_dir, clock).sweep(
        already_handled={"run_0.pod5"}, on_ready=lambda p: pytest.fail("nothing to do"))
    assert report.handed_over == [] and report.unstable_at_seal == []


def test_a_still_growing_file_is_not_handed_over_early(tmp_path: Path) -> None:
    """Sealing uses a shorter window than the live watcher, not no window."""
    pod5_dir = tmp_path / "pod5"
    p = _pod5(pod5_dir, "growing.pod5", size=10)

    clock = FakeClock()
    handed: list[Path] = []
    sealer = _sealer(pod5_dir, clock, stable_polls=3)

    # Grow it on the first sleep so the count resets at least once.
    original_sleep = clock.sleep
    grown = {"done": False}

    def grow_then_sleep(seconds: float) -> None:
        if not grown["done"]:
            p.write_bytes(b"\0" * 999)
            grown["done"] = True
        original_sleep(seconds)

    sealer._sleep = grow_then_sleep
    sealer.sweep(already_handled=set(), on_ready=handed.append)

    assert [q.name for q in handed] == ["growing.pod5"]
    # Handed over at the FINAL size: the growth reset the stability count, so it could not
    # be released until it had stopped changing.
    assert handed[0].stat().st_size == 999


def test_budget_exhaustion_records_rather_than_drops(tmp_path: Path) -> None:
    """A file that never stabilizes must be NAMED, not silently omitted."""
    pod5_dir = tmp_path / "pod5"
    p = _pod5(pod5_dir, "never_settles.pod5", size=10)

    clock = FakeClock()
    sealer = _sealer(pod5_dir, clock, budget_s=10.0, stable_polls=3)
    size = {"n": 10}

    def grow_forever(seconds: float) -> None:
        size["n"] += 10
        p.write_bytes(b"\0" * size["n"])   # never stable
        clock.sleep(seconds)

    sealer._sleep = grow_forever
    report = sealer.sweep(already_handled=set(), on_ready=lambda q: None)

    assert report.budget_exceeded
    assert report.unstable_at_seal == ["never_settles.pod5"]
    assert report.handed_over == []


def test_a_pod5_appearing_after_the_marker_is_flagged_and_still_ingested(
    tmp_path: Path, caplog
) -> None:
    """The design takes the input set to be closed at marker time. If a deployment violates that, the
    file must still be processed AND the falsification logged — never quietly absorbed."""
    pod5_dir = tmp_path / "pod5"
    _pod5(pod5_dir, "run_0.pod5")

    clock = FakeClock()
    sealer = _sealer(pod5_dir, clock, stable_polls=3)
    handed: list[Path] = []
    added = {"done": False}

    def add_straggler(seconds: float) -> None:
        if not added["done"]:
            _pod5(pod5_dir, "straggler.pod5")
            added["done"] = True
        clock.sleep(seconds)

    sealer._sleep = add_straggler
    with caplog.at_level("ERROR"):
        report = sealer.sweep(already_handled=set(), on_ready=handed.append)

    assert "straggler.pod5" in report.appeared_after_marker
    assert "appeared AFTER the run-end marker" in caplog.text
    assert {p.name for p in handed} == {"run_0.pod5", "straggler.pod5"}


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def _summary(run_dir: Path, rows: list[tuple[str, str]]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / "sequencing_summary_FC_run_acq.txt"
    lines = ["filename_pod5\tread_id\trun_id"]
    lines += [f"{pod5}\t{rid}\trun" for pod5, rid in rows]
    p.write_text("\n".join(lines) + "\n")
    return p


def test_coverage_counts_reads_per_pod5(tmp_path: Path) -> None:
    _summary(tmp_path, [("a.pod5", "r1"), ("a.pod5", "r2"), ("b.pod5", "r3")])
    cov = read_sequencing_summary(tmp_path)
    assert cov is not None
    assert cov.reads_per_pod5 == {"a.pod5": 2, "b.pod5": 1}
    assert cov.total_reads == 3
    assert cov.expected_pod5s == {"a.pod5", "b.pod5"}


def test_coverage_is_read_weighted_not_file_weighted(tmp_path: Path) -> None:
    """POD5s are not equal-sized — the first and last batches are partial windows — so a
    file-count fraction would misstate how much of the run a report actually saw."""
    rows = [("big.pod5", f"r{i}") for i in range(90)] + [("small.pod5", "r90")]
    _summary(tmp_path, rows)
    cov = read_sequencing_summary(tmp_path)
    assert cov.fraction_covered(["big.pod5"]) == pytest.approx(90 / 91)
    assert cov.fraction_covered(["small.pod5"]) == pytest.approx(1 / 91)
    assert cov.fraction_covered(["big.pod5", "small.pod5"]) == 1.0


def test_a_dropped_final_batch_shows_up_as_less_than_one(tmp_path: Path) -> None:
    """The check that would have caught the prototype's bug from the outside."""
    rows = [("a.pod5", f"r{i}") for i in range(997)]
    rows += [("final.pod5", f"f{i}") for i in range(3)]
    _summary(tmp_path, rows)
    cov = read_sequencing_summary(tmp_path)
    assert cov.fraction_covered(["a.pod5"]) == pytest.approx(0.997)


@pytest.mark.parametrize("setup", [
    lambda d: None,                                             # no summary at all
    lambda d: (d / "sequencing_summary_x.txt").write_text("read_id\tchannel\nr1\t1\n"),
])
def test_missing_or_columnless_summary_is_not_measured_not_zero(tmp_path: Path, setup) -> None:
    """`not_measured` is an honest third state. "We don't know what the run produced" is
    not the same claim as "we covered none of it"."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    setup(tmp_path)
    assert read_sequencing_summary(tmp_path) is None


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------

def test_every_pod5_lands_in_exactly_one_bucket(tmp_path: Path) -> None:
    acc = build_run_accounting(
        observed={"a.pod5", "b.pod5", "c.pod5", "d.pod5"},
        ingested={"a.pod5"}, failed={"b.pod5"},
        unstable={"c.pod5"}, deferred={"d.pod5"})
    assert acc["ingested"] == ["a.pod5"]
    assert acc["failed_basecall"] == ["b.pod5"]
    assert acc["unstable_at_seal"] == ["c.pod5"]
    assert acc["deferred_backpressure"] == ["d.pod5"]
    assert acc["coverage"]["status"] == "not_measured"


def test_an_unaccounted_pod5_is_fatal() -> None:
    """A partial report is allowed; an unexplained one is not."""
    with pytest.raises(RuntimeError, match="does not partition"):
        build_run_accounting(observed={"a.pod5", "ghost.pod5"}, ingested={"a.pod5"},
                             failed=set(), unstable=set())


def test_buckets_are_deduplicated_by_severity() -> None:
    """An input that failed and was then ingested on retry is ingested, counted once."""
    acc = build_run_accounting(
        observed={"a.pod5"}, ingested={"a.pod5"}, failed={"a.pod5"},
        unstable={"a.pod5"}, deferred={"a.pod5"})
    assert acc["ingested"] == ["a.pod5"]
    assert acc["failed_basecall"] == []
    assert acc["unstable_at_seal"] == []
    assert acc["deferred_backpressure"] == []


def test_a_pod5_the_instrument_made_but_we_never_saw_is_lost_before_processing(
    tmp_path: Path
) -> None:
    """The one bucket sourced from outside our own pipeline — and the reason coverage
    exists. If a file never reached the watched directory, nothing we record could show it."""
    _summary(tmp_path, [("a.pod5", "r1"), ("vanished.pod5", "r2")])
    cov = read_sequencing_summary(tmp_path)

    acc = build_run_accounting(
        observed={"a.pod5"}, ingested={"a.pod5"}, failed=set(), unstable=set(),
        coverage=cov)
    assert acc["lost_before_processing"] == ["vanished.pod5"]
    assert acc["coverage"]["status"] == "measured"
    assert acc["coverage"]["fraction_of_run_reads"] == pytest.approx(0.5)


def test_full_coverage_reports_one(tmp_path: Path) -> None:
    _summary(tmp_path, [("a.pod5", "r1"), ("b.pod5", "r2")])
    acc = build_run_accounting(
        observed={"a.pod5", "b.pod5"}, ingested={"a.pod5", "b.pod5"},
        failed=set(), unstable=set(), coverage=read_sequencing_summary(tmp_path))
    assert acc["coverage"]["fraction_of_run_reads"] == 1.0
    assert acc["lost_before_processing"] == []


def test_seal_window_is_shorter_than_the_live_window() -> None:
    """Justified: at marker time the set is CLOSED, so sealing guards only against a file
    still flushing (2-4 s measured), not against one not yet written (30 s)."""
    from mrnaspect.realtime.watcher import (
        DEFAULT_POLL_INTERVAL_S, DEFAULT_STABLE_POLLS)
    from mrnaspect.realtime.sealing import DEFAULT_SEAL_POLL_INTERVAL_S

    live = (DEFAULT_STABLE_POLLS - 1) * DEFAULT_POLL_INTERVAL_S
    seal = (DEFAULT_SEAL_STABLE_POLLS - 1) * DEFAULT_SEAL_POLL_INTERVAL_S
    assert seal < live
    assert seal >= 4.0, "must still cover the 2-4 s post-arrival flush"
