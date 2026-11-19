"""MinKNOW mimic — the harness the watcher will be tested against, tested itself.

A mimic that is wrong is worse than no mimic: it makes a broken watcher look correct.
So these tests pin the mimic to the *recorded* run logs under
``data/IVT_260407/results/`` rather than to the mimic's own implementation.

The load-bearing test here is :func:`test_marker_gap_is_inside_the_stability_window`.
Everything else is scaffolding.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from mrnaspect.realtime import mimic as mimic_mod
from mrnaspect.realtime.mimic import (
    Batch,
    Fault,
    MinKNOWMimic,
    MimicProfile,
    PROFILES,
)

# The locked detection parameters (step3 D42). Duplicated here on purpose: if someone
# changes the watcher's defaults, this test must fail and force the conversation, rather
# than importing the new value and silently agreeing with it.
LOCKED_POLL_INTERVAL_S = 5.0
LOCKED_STABLE_POLLS = 7
STABILITY_WINDOW_S = (LOCKED_STABLE_POLLS - 1) * LOCKED_POLL_INTERVAL_S  # 30.0


def _tiny(n: int = 5, *, reads: int = 3) -> list[Batch]:
    """Synthetic batches — no POD5 library, no disk read, no GPU."""
    return [Batch(size_bytes=1024, n_reads=reads) for _ in range(n)]


# ---------------------------------------------------------------------------
# The one that matters
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile_name", sorted(PROFILES))
def test_marker_gap_is_inside_the_stability_window(profile_name: str) -> None:
    """The run-end marker lands long before the last POD5 can be declared stable.

    This is the dropped-final-batch trap, stated as an assertion. A watcher needs 30 s of
    unchanging size to call a file ready, but MinKNOW writes ``final_summary_*.txt`` 1.1 s
    (atomic topologies) to 5.4 s (cross-disk) after the final POD5 appears. Any loop that
    treats the marker as "stop scanning" therefore loses the last batch on every run — and
    the last batch is the end-of-run pores, the most degraded reads in the dataset.

    If this test ever fails because a profile's ``marker_gap_s`` grew past 30 s, do not
    relax the assertion: it would mean the recorded timings were re-derived, and the
    watcher's sealing logic should be revisited against the new data.
    """
    profile = PROFILES[profile_name]
    assert profile.marker_gap_s < STABILITY_WINDOW_S, (
        f"{profile_name}: marker_gap_s={profile.marker_gap_s} is no longer inside the "
        f"{STABILITY_WINDOW_S}s stability window")
    # And the summary never arrives after the marker — the ordering that makes
    # "at marker time the POD5 set is closed" safe to rely on.
    assert profile.summary_gap_s <= profile.marker_gap_s


# ---------------------------------------------------------------------------
# Schedule — happy path
# ---------------------------------------------------------------------------

def test_schedule_reproduces_recorded_structure() -> None:
    """Steady intervals, a short final window, then summary and marker."""
    profile = PROFILES["win_same_disk"]
    mimic = MinKNOWMimic("/tmp/unused-schedule-only", _tiny(6), profile)
    events = mimic.schedule()

    pod5s = [e for e in events if e[1] == "pod5"]
    assert len(pod5s) == 6
    gaps = [round(b[0] - a[0], 6) for a, b in zip(pod5s, pod5s[1:])]
    # All but the last gap are the steady batch interval...
    assert gaps[:-1] == pytest.approx([profile.batch_interval_s] * 4)
    # ...and the final window is truncated by run-stop (measured 115.4 s of 180.6 s).
    assert gaps[-1] == pytest.approx(
        profile.batch_interval_s * profile.last_batch_interval_fraction)

    t_last = pod5s[-1][0]
    summary = next(e for e in events if e[1] == "summary")
    marker = next(e for e in events if e[1] == "marker")
    assert summary[0] - t_last == pytest.approx(profile.summary_gap_s)
    assert marker[0] - t_last == pytest.approx(profile.marker_gap_s)
    # Ordering is the property the sweep depends on.
    assert marker[0] >= summary[0] > t_last


def test_speed_factor_is_a_uniform_scaling() -> None:
    """Compressing a 20 h run into seconds must not distort relative timing."""
    batches = _tiny(4)
    slow = MinKNOWMimic("/tmp/unused-a", batches, "linux_mk1d", speed=1.0).schedule()
    fast = MinKNOWMimic("/tmp/unused-b", batches, "linux_mk1d", speed=500.0).schedule()
    assert [e[1:] for e in slow] == [e[1:] for e in fast]
    for s, f in zip(slow, fast):
        assert f[0] == pytest.approx(s[0] / 500.0)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def test_atomic_run_publishes_everything(tmp_path: Path) -> None:
    result = MinKNOWMimic(
        tmp_path / "run", _tiny(4), "win_same_disk", speed=10_000.0,
    ).run()

    assert result.published == [f"SIM41175_7c428dc3_9cffaacf_{i}.pod5" for i in range(4)]
    # POD5s nest one level down; the marker and summary sit in the run root. A watcher
    # pointed only at pod5/ would never see run-end — that is why the mimic reproduces
    # the real two-level layout rather than flattening it.
    assert result.pod5_dir == tmp_path / "run" / "pod5"
    assert result.marker_path is not None and result.marker_path.parent == tmp_path / "run"
    assert result.summary_path is not None
    assert sorted(p.name for p in result.pod5_dir.glob("*.pod5")) == sorted(result.published)
    assert all(p.stat().st_size == 1024 for p in result.pod5_dir.glob("*.pod5"))

    # sequencing_summary is the completeness oracle: one row per read, keyed by POD5 name.
    rows = result.summary_path.read_text().strip().splitlines()
    assert rows[0].split("\t")[0] == "filename_pod5"
    assert len(rows) - 1 == 4 * 3 == result.total_reads
    assert f"pod5_files_in_final_dest={len(result.published)}" in result.marker_path.read_text()


def test_atomic_delivery_is_never_observably_partial(tmp_path: Path) -> None:
    """A concurrent observer must only ever see a POD5 at its full size.

    This is the property that makes the production (intra-filesystem rename) topology
    forgiving. If it did not hold, the mimic would be understating the difficulty and the
    watcher would be tested against an easier world than it ships into.
    """
    run_dir = tmp_path / "run"
    batches = [Batch(size_bytes=4 << 20) for _ in range(3)]  # 4 MiB: big enough to catch a copy
    mimic = MinKNOWMimic(run_dir, batches, "win_same_disk", speed=2000.0)

    sizes: set[int] = set()
    stop = threading.Event()

    def observe() -> None:
        pod5_dir = run_dir / "pod5"
        while not stop.is_set():
            if pod5_dir.is_dir():
                for p in pod5_dir.glob("*.pod5"):
                    try:
                        sizes.add(p.stat().st_size)
                    except FileNotFoundError:
                        pass
            time.sleep(0.001)

    watcher = threading.Thread(target=observe, daemon=True)
    watcher.start()
    try:
        mimic.run()
        time.sleep(0.05)  # let the observer poll once more after the last publish
    finally:
        stop.set()
        watcher.join(timeout=5)

    assert sizes, "the observer never sampled anything — the test proves nothing"
    assert sizes == {4 << 20}, f"observed partial sizes: {sorted(sizes - {4 << 20})}"


def test_grow_delivery_transits_through_incomplete(tmp_path: Path) -> None:
    """The cross-filesystem case: the file IS observably partial inside the watched dir.

    Forced to several slow steps so the transit is reliably observable; the recorded
    hardware does it in one ~3 ms step, which a 5 s poller would never catch. That is a
    real finding, not a gap in the test — on measured hardware the cross-disk case looks
    almost atomic to a poller, and only the ``mid_copy_stall`` fault makes it pathological.
    """
    profile = PROFILES["win_cross_disk"]
    slow = MimicProfile(**{**profile.__dict__, "grow_steps": 4, "grow_step_s": 0.05,
                           "grow_tail_s": 0.0})
    run_dir = tmp_path / "run"
    mimic = MinKNOWMimic(run_dir, [Batch(size_bytes=4096)], slow, speed=1.0,
                         write_summaries=False)

    sizes: set[int] = set()
    stop = threading.Event()

    def observe() -> None:
        pod5_dir = run_dir / "pod5"
        while not stop.is_set():
            if pod5_dir.is_dir():
                for p in pod5_dir.glob("*.pod5"):
                    try:
                        sizes.add(p.stat().st_size)
                    except FileNotFoundError:
                        pass
            time.sleep(0.001)

    watcher = threading.Thread(target=observe, daemon=True)
    watcher.start()
    try:
        mimic.run()
        time.sleep(0.05)  # let the observer poll once more after the last write
    finally:
        stop.set()
        watcher.join(timeout=5)

    published = run_dir / "pod5" / "SIM41175_7c428dc3_9cffaacf_0.pod5"
    assert published.stat().st_size == 4096, "grow delivery must still finish complete"
    assert sizes - {4096}, "grow delivery never exposed an intermediate size"


# ---------------------------------------------------------------------------
# Faults
# ---------------------------------------------------------------------------

def test_stalled_tmp_never_reaches_the_final_name(tmp_path: Path) -> None:
    result = MinKNOWMimic(tmp_path / "run", _tiny(4), "win_same_disk", speed=10_000.0,
                          faults={2: Fault.STALLED_TMP}).run()
    pod5_dir = result.pod5_dir
    assert not (pod5_dir / "SIM41175_7c428dc3_9cffaacf_2.pod5").exists()
    assert (pod5_dir / "SIM41175_7c428dc3_9cffaacf_2.pod5.tmp").exists()
    assert "SIM41175_7c428dc3_9cffaacf_2.pod5" not in result.published
    # The instrument still recorded the reads — the gap between the summary and what QC
    # can ingest is the coverage signal, so it must not be silently reconciled away.
    assert len(result.summary_path.read_text().strip().splitlines()) - 1 == 4 * 3


def test_zero_byte_and_truncated_stabilize_but_are_unusable(tmp_path: Path) -> None:
    result = MinKNOWMimic(tmp_path / "run", _tiny(4), "win_same_disk", speed=10_000.0,
                          faults={1: Fault.ZERO_BYTE, 3: Fault.TRUNCATED}).run()
    assert (result.pod5_dir / "SIM41175_7c428dc3_9cffaacf_1.pod5").stat().st_size == 0
    assert (result.pod5_dir / "SIM41175_7c428dc3_9cffaacf_3.pod5").stat().st_size == 512
    # Both stabilize at the final name — a size-stability watcher CANNOT reject these.
    # Rejecting them is the basecall stage's job, which is exactly what this fault proves.
    assert "SIM41175_7c428dc3_9cffaacf_1.pod5" in result.published


def test_post_marker_straggler_lands_after_the_marker(tmp_path: Path) -> None:
    batches = _tiny(3)
    result = MinKNOWMimic(tmp_path / "run", batches, "win_same_disk", speed=10_000.0,
                          faults={3: Fault.POST_MARKER_STRAGGLER}).run()
    straggler = result.pod5_dir / "SIM41175_7c428dc3_9cffaacf_3.pod5"
    assert straggler.exists()
    # The marker was written when only 3 files existed — so a loop that trusts the
    # marker's own count without re-globbing would miss the straggler.
    assert "pod5_files_in_final_dest=3" in result.marker_path.read_text()
    assert straggler.stat().st_mtime >= result.marker_path.stat().st_mtime


def test_duplicate_name_republishes_an_earlier_file(tmp_path: Path) -> None:
    batches = [Batch(size_bytes=100), Batch(size_bytes=999)]
    result = MinKNOWMimic(tmp_path / "run", batches, "win_same_disk", speed=10_000.0,
                          faults={1: Fault.DUPLICATE_NAME}).run()
    first = result.pod5_dir / "SIM41175_7c428dc3_9cffaacf_0.pod5"
    assert first.stat().st_size == 999, "the republish must change the bytes on disk"
    assert not (result.pod5_dir / "SIM41175_7c428dc3_9cffaacf_1.pod5").exists()


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def test_refuses_a_non_empty_run_dir(tmp_path: Path) -> None:
    """The general rule: the mimic writes junk, so it only gets a folder it owns.

    Deliberately a plain file, not a `pod5/` subdir — this is exactly the case the CLI's
    `_dirty_mimic_target` misses (it looks in `run_dir/pod5/` and for run-end markers),
    and it is the shape the recorded replay set actually has: POD5s at the top level.
    """
    run = tmp_path / "run"
    run.mkdir()
    (run / "irreplaceable_0.pod5").write_bytes(b"x")
    with pytest.raises(ValueError, match="non-empty directory"):
        MinKNOWMimic(run, _tiny(2))


def test_an_absent_or_empty_run_dir_is_accepted(tmp_path: Path) -> None:
    """Both normal cases: the mimic creates the folder, or `main()` pre-created it empty."""
    MinKNOWMimic(tmp_path / "not_yet", _tiny(2))
    (tmp_path / "made_empty").mkdir()
    MinKNOWMimic(tmp_path / "made_empty", _tiny(2))


def test_allow_dirty_is_the_escape_for_your_own_leftovers(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "stale.pod5").write_bytes(b"x")
    MinKNOWMimic(run, _tiny(2), allow_dirty=True)


def test_a_protected_root_is_refused_even_with_allow_dirty(tmp_path: Path, monkeypatch) -> None:
    """The second tier exists precisely because the first one has an escape hatch.

    `PROTECTED_ROOTS` ships EMPTY (it used to hardcode two paths on one lab machine, which
    protected nobody else), so it is set here the way an operator sets it: through
    `QCRNA_PROTECTED_ROOTS`. Reloading is what makes the env var observable — the module
    reads it at import.
    """
    import importlib

    vault = tmp_path / "recorded_runs"
    (vault / "run_a").mkdir(parents=True)
    monkeypatch.setenv("QCRNA_PROTECTED_ROOTS", str(vault))
    reloaded = importlib.reload(mimic_mod)
    try:
        assert reloaded.PROTECTED_ROOTS == (str(vault),)
        with pytest.raises(ValueError, match="protected tree"):
            reloaded.MinKNOWMimic(vault / "run_a" / "sub", _tiny(2), allow_dirty=True)
    finally:
        monkeypatch.delenv("QCRNA_PROTECTED_ROOTS")
        importlib.reload(mimic_mod)


def test_protected_roots_ships_empty(monkeypatch) -> None:
    """With nothing in the environment the list is empty — no path is baked into Core.

    Asserting the module attribute as-is would be worthless here: `pixi.toml` sets
    QCRNA_PROTECTED_ROOTS for this machine, so the attribute is populated whenever the
    suite runs under `pixi run`. The claim under test is about the SHIPPED default, so
    the variable has to be cleared and the module re-imported.
    """
    import importlib

    monkeypatch.delenv("QCRNA_PROTECTED_ROOTS", raising=False)
    reloaded = importlib.reload(mimic_mod)
    try:
        assert reloaded.PROTECTED_ROOTS == (), (
            f"a path is hardcoded into Core: {reloaded.PROTECTED_ROOTS}. It would ship to "
            f"every user as a default that protects one machine.")
    finally:
        monkeypatch.undo()
        importlib.reload(mimic_mod)


def test_straggler_fault_must_be_keyed_one_past_the_end() -> None:
    with pytest.raises(ValueError, match="one past the last batch"):
        MinKNOWMimic("/tmp/unused", _tiny(3), faults={1: Fault.POST_MARKER_STRAGGLER})


def test_unknown_fault_and_out_of_range_index_fail_loudly() -> None:
    with pytest.raises(ValueError, match="unknown fault"):
        MinKNOWMimic("/tmp/unused", _tiny(3), faults={0: "explode"})
    with pytest.raises(ValueError, match="out of range"):
        MinKNOWMimic("/tmp/unused", _tiny(3), faults={7: Fault.ZERO_BYTE})


def test_batch_rejects_a_missing_source_and_a_shapeless_spec() -> None:
    with pytest.raises(FileNotFoundError):
        Batch(source=Path("/nonexistent/x.pod5"))
    with pytest.raises(ValueError, match="either a source path or"):
        Batch()


def test_from_dir_sorts_numerically_not_lexicographically(tmp_path: Path) -> None:
    """``_10.pod5`` sorts before ``_2.pod5`` under a string sort.

    Invisible on the 9-file replay set, certain on a 400-POD5 production run. The mimic
    must deliver batches in acquisition order or every downstream ordering assumption is
    tested against the wrong sequence.
    """
    for i in (0, 2, 10, 11, 3):
        (tmp_path / f"FC_run_acq_{i}.pod5").write_bytes(b"x")
    names = [b.source.name for b in Batch.from_dir(tmp_path)]
    assert names == [f"FC_run_acq_{i}.pod5" for i in (0, 2, 3, 10, 11)]


def test_speed_must_be_positive_and_batches_non_empty() -> None:
    with pytest.raises(ValueError, match="speed"):
        MinKNOWMimic("/tmp/unused", _tiny(2), speed=0)
    with pytest.raises(ValueError, match="no batches"):
        MinKNOWMimic("/tmp/unused", [])
