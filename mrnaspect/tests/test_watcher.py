"""DirectoryWatcher — polling + size stabilization (step3 §3d).

Driven by :meth:`scan_once` rather than a thread: the watcher is a deterministic state
machine on purpose (§3d-10), so its rules are tested by stepping it, not by sleeping and
hoping. The one genuinely time-dependent property — that the marker arrives inside the
stability window — is tested against the *mimic*, which carries the recorded timings.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mrnaspect.realtime.mimic import Batch, MinKNOWMimic, PROFILES
from mrnaspect.realtime.watcher import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_STABLE_POLLS,
    DirectoryWatcher,
    FileReadyEvent,
)


def _watcher(run_dir: Path, ready: list, **over) -> DirectoryWatcher:
    kwargs = dict(
        pod5_subdir="pod5", on_ready=ready.append,
        poll_interval_s=0.01, stable_polls=3,
    )
    kwargs.update(over)
    return DirectoryWatcher(run_dir, **kwargs)


def _make(run_dir: Path, name: str, size: int = 100) -> Path:
    d = run_dir / "pod5"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"\0" * size)
    return p


# ---------------------------------------------------------------------------
# The stability rule
# ---------------------------------------------------------------------------

def test_first_sighting_never_emits(tmp_path: Path) -> None:
    """There is no baseline to compare against, so the first scan can only remember."""
    ready: list[FileReadyEvent] = []
    w = _watcher(tmp_path, ready)
    _make(tmp_path, "a.pod5")
    assert w.scan_once() == []
    assert ready == []


def test_emits_after_stable_polls_consecutive_matches(tmp_path: Path) -> None:
    ready: list[FileReadyEvent] = []
    w = _watcher(tmp_path, ready, stable_polls=3)
    _make(tmp_path, "a.pod5", size=42)

    assert w.scan_once() == []      # baseline
    assert w.scan_once() == []      # match 2
    events = w.scan_once()          # match 3 == stable_polls -> ready
    assert len(events) == 1
    assert events[0].path.name == "a.pod5" and events[0].size == 42
    assert [e.path.name for e in ready] == ["a.pod5"]

    assert w.scan_once() == [], "an emitted file must never be emitted twice"


def test_a_growing_file_resets_the_count(tmp_path: Path) -> None:
    """The cross-filesystem case: the file is visibly incomplete inside the watched dir."""
    ready: list[FileReadyEvent] = []
    w = _watcher(tmp_path, ready, stable_polls=3)
    p = _make(tmp_path, "a.pod5", size=10)

    w.scan_once()
    w.scan_once()                       # count would be 2...
    p.write_bytes(b"\0" * 20)           # ...but it grew
    assert w.scan_once() == []          # reset to 1
    assert w.scan_once() == []          # 2
    assert len(w.scan_once()) == 1      # 3 -> ready, at the FINAL size
    assert ready[0].size == 20


def test_verified_silence_formula_is_off_by_one(tmp_path: Path) -> None:
    """(stable_polls - 1) x poll_interval, NOT stable_polls x poll_interval.

    Pinned because the intuitive reading is wrong and a caller tuning these numbers on the
    intuitive model would under-verify.
    """
    w = _watcher(tmp_path, [], poll_interval_s=5.0, stable_polls=7)
    assert w.verified_silence_s == 30.0
    assert DEFAULT_POLL_INTERVAL_S * (DEFAULT_STABLE_POLLS - 1) == 30.0


def test_stable_polls_below_two_is_refused(tmp_path: Path) -> None:
    """stable_polls=1 would emit on first sighting — the 0-byte bug, by construction."""
    with pytest.raises(ValueError, match="must be >= 2"):
        _watcher(tmp_path, [], stable_polls=1)


def test_a_file_stable_at_zero_bytes_is_not_emitted(tmp_path: Path) -> None:
    """MinKNOW's cross-fs path creates at 0 and fills; a 0-byte file that never grows is a
    failed write. Emitting it hands an empty POD5 to the basecaller."""
    ready: list[FileReadyEvent] = []
    w = _watcher(tmp_path, ready, stable_polls=2)
    _make(tmp_path, "empty.pod5", size=0)
    for _ in range(5):
        assert w.scan_once() == []
    assert ready == []


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def test_temp_suffixes_are_never_emitted(tmp_path: Path) -> None:
    """A stalled `.pod5.tmp` must be ignored forever without blocking the run."""
    ready: list[FileReadyEvent] = []
    w = _watcher(tmp_path, ready, stable_polls=2)
    _make(tmp_path, "a.pod5.tmp", size=50)
    _make(tmp_path, "b.pod5.partial", size=50)
    _make(tmp_path, "good.pod5", size=50)
    for _ in range(4):
        w.scan_once()
    assert [e.path.name for e in ready] == ["good.pod5"]


def test_emit_preexisting_false_suppresses_the_backlog(tmp_path: Path) -> None:
    ready: list[FileReadyEvent] = []
    w = _watcher(tmp_path, ready, stable_polls=2, emit_preexisting=False)
    _make(tmp_path, "old.pod5")
    for _ in range(4):
        w.scan_once()
    assert ready == []

    _make(tmp_path, "new.pod5")           # arrivals AFTER the first scan still emit
    for _ in range(3):
        w.scan_once()
    assert [e.path.name for e in ready] == ["new.pod5"]


def test_flat_layout_is_supported(tmp_path: Path) -> None:
    """Our replay set is a flat directory of POD5s; MinKNOW's real layout is nested."""
    ready: list[FileReadyEvent] = []
    w = DirectoryWatcher(tmp_path, pod5_subdir="", on_ready=ready.append,
                         poll_interval_s=0.01, stable_polls=2)
    (tmp_path / "a.pod5").write_bytes(b"\0" * 10)
    w.scan_once()
    w.scan_once()
    assert [e.path.name for e in ready] == ["a.pod5"]


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_missing_watch_dir_is_survivable(tmp_path: Path) -> None:
    """pod5/ does not exist until MinKNOW's first batch lands — the normal start state."""
    ready: list[FileReadyEvent] = []
    w = _watcher(tmp_path / "not_yet", ready, stable_polls=2)
    for _ in range(5):
        assert w.scan_once() == []      # no exception, no event

    _make(tmp_path / "not_yet", "a.pod5")
    w.scan_once()
    w.scan_once()
    assert [e.path.name for e in ready] == ["a.pod5"]


def test_vanished_files_are_pruned_from_stability_state(tmp_path: Path) -> None:
    """Otherwise _sizes leaks for the process lifetime, and a reused name would resume a
    stale count instead of starting fresh."""
    w = _watcher(tmp_path, [], stable_polls=5)
    p = _make(tmp_path, "a.pod5")
    w.scan_once()
    w.scan_once()
    assert p in w._sizes
    p.unlink()
    w.scan_once()
    assert p not in w._sizes


def test_callback_exceptions_are_not_swallowed(tmp_path: Path) -> None:
    """A caller bug must not be invisible (§3d-8c)."""
    def boom(_event: FileReadyEvent) -> None:
        raise RuntimeError("consumer exploded")

    w = DirectoryWatcher(tmp_path, on_ready=boom, poll_interval_s=0.01, stable_polls=2)
    _make(tmp_path, "a.pod5")
    w.scan_once()
    with pytest.raises(RuntimeError, match="consumer exploded"):
        w.scan_once()


# ---------------------------------------------------------------------------
# Run-end — the correctness point
# ---------------------------------------------------------------------------

def test_marker_is_reported_once_and_does_not_stop_scanning(tmp_path: Path) -> None:
    """The regression. The marker means "the input set is closed", not "stop looking".

    The prototype's event loop returned from
    `_poll_once` on seeing the marker, BEFORE scanning for POD5s. Since the marker lands
    ~1.1 s after the final POD5 and that POD5 needs 30 s of silence, the last batch of
    every run is lost. This test fails against that behaviour, which is the point.
    """
    ready: list[FileReadyEvent] = []
    markers: list[Path] = []
    w = _watcher(tmp_path, ready, stable_polls=3, on_run_end=markers.append)

    # The final POD5 lands, then the marker ~1 s later — long before the POD5 is ready.
    last = _make(tmp_path, "final.pod5", size=99)
    w.scan_once()                                     # baseline; not ready yet
    (tmp_path / "final_summary_FC_run_acq.txt").write_text("pod5_files_in_final_dest=1\n")

    w.scan_once()                                     # marker seen, count -> 2
    assert [p.name for p in markers] == ["final_summary_FC_run_acq.txt"]
    assert w.run_end_seen
    assert ready == [], "not ready yet — but the watcher must still be looking"

    events = w.scan_once()                            # count -> 3: ready, AFTER the marker
    assert [e.path.name for e in events] == ["final.pod5"], (
        "the final POD5 was dropped because the marker stopped the scan")
    assert last.name in w.emitted_names

    w.scan_once()
    assert len(markers) == 1, "run-end must be announced exactly once"


def test_a_post_marker_straggler_is_still_emitted(tmp_path: Path) -> None:
    """The design takes the set to be closed at marker time. If a deployment ever violates that, the
    watcher must notice rather than silently ignore the file."""
    ready: list[FileReadyEvent] = []
    w = _watcher(tmp_path, ready, stable_polls=2)
    (tmp_path / "pod5").mkdir(parents=True)
    (tmp_path / "final_summary_x.txt").write_text("done\n")
    w.scan_once()
    assert w.run_end_seen

    _make(tmp_path, "straggler.pod5")
    w.scan_once()
    w.scan_once()
    assert [e.path.name for e in ready] == ["straggler.pod5"]


def test_marker_gap_is_shorter_than_the_stability_window(tmp_path: Path) -> None:
    """Tie the watcher's own parameters to the measured MinKNOW timings.

    If a future edit lengthens the marker gap or shortens the stability window until they
    cross, the dropped-final-batch hazard disappears — and so does the reason for the
    sealing sweep. Until then, every recorded profile sits inside the window.
    """
    w = _watcher(tmp_path, [], poll_interval_s=5.0, stable_polls=7)
    for name, profile in PROFILES.items():
        assert profile.marker_gap_s < w.verified_silence_s, name


# ---------------------------------------------------------------------------
# End to end against the mimic
# ---------------------------------------------------------------------------

def test_watcher_against_the_mimic_sees_every_pod5_including_the_last(
    tmp_path: Path
) -> None:
    """The real shape: a mimic publishing on a compressed clock, a watcher in a thread.

    The mimic's marker gap is scaled with everything else, so the *ratio* that makes the
    final batch hazardous is preserved even though the wall-clock is seconds.
    """
    run_dir = tmp_path / "run"
    batches = [Batch(size_bytes=256) for _ in range(4)]
    # 180.6 s batches / 1.07 s marker gap, compressed 300x -> 0.60 s / 3.6 ms.
    mimic = MinKNOWMimic(run_dir, batches, "win_same_disk", speed=300.0)

    ready: list[FileReadyEvent] = []
    markers: list[Path] = []
    watcher = DirectoryWatcher(
        run_dir, pod5_subdir="pod5", on_ready=ready.append, on_run_end=markers.append,
        poll_interval_s=0.02, stable_polls=3)

    thread = threading.Thread(target=watcher.run, daemon=True)
    thread.start()
    try:
        mimic.run()
        # Let the watcher verify the FINAL file after the marker has already arrived —
        # the exact window the prototype cuts short.
        deadline = 5.0
        waited = 0.0
        while len(ready) < len(batches) and waited < deadline:
            threading.Event().wait(0.05)
            waited += 0.05
    finally:
        watcher.stop()
        thread.join(timeout=5)

    assert [e.path.name for e in ready] == [mimic.pod5_name(i) for i in range(4)], (
        "watcher must see every POD5, in acquisition order, including the last")
    assert len(markers) == 1
