"""RealtimePipeline — the live driver, and the gate that says it agrees with the offline one.

:func:`test_realtime_equals_offline` is the headline. Everything else supports it.

No GPU and no dorado server: the basecall and align stages are injected fakes that copy a
real fixture BAM, exactly as ``test_item14a_pod5_cache.py`` already does. The point of
these tests is the *driver* — feed, ordering, restart, sealing, accounting — so removing
basecaller nondeterminism from the comparison is what makes a diff meaningful.
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from mrnaspect.tests.conftest import pod5_ready
from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.realtime.main import RealtimePipeline, State
from mrnaspect.realtime.manifest import IngestManifest, MANIFEST_NAME
from mrnaspect.realtime.mimic import Batch, MinKNOWMimic
from mrnaspect.tests.golden_compare import canonicalize, diff_docs


def _fakes(fixture_bam: Path):
    """basecall -> a ubam dir; align -> a copy of the fixture BAM. No GPU."""
    def fake_basecall(pod5: Path, out_dir: Path) -> Path:
        d = Path(out_dir) / f"basecall_{pod5.stem}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture_bam, d / "chunk_0_0.bam")
        return d

    def fake_align(prefix: str, reference: Path, out_dir: Path) -> Path:
        bam = Path(out_dir) / f"aligned_{prefix}.bam"
        shutil.copy(fixture_bam, bam)
        return bam

    return fake_basecall, fake_align


def _cfg(curlcake_ref: Path, **over):
    base = {"reference": str(curlcake_ref), "mode": "pod5", "min_reads": 1}
    base.update(over)
    return pod5_ready(load_config(None, base))


def _rt_cfg(curlcake_ref: Path, **rt):
    """Realtime config with the clock compressed; the RULES are unchanged."""
    cfg = _cfg(curlcake_ref)
    cfg["realtime"] = {
        **cfg.get("realtime", {}),
        "poll_interval_s": 0.02, "stable_polls": 3,
        "seal_poll_interval_s": 0.02, "seal_stable_polls": 3,
        "pod5_subdir": "pod5", "seal_budget_s": 10.0, "interim_every_n": 0,
        **rt,
    }
    return cfg


def _run_mimic(run_dir: Path, n: int = 4, **over) -> MinKNOWMimic:
    mimic = MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166) for _ in range(n)],
                         "win_same_disk", speed=400.0, **over)
    mimic.run()
    return mimic


def _realtime(run_dir: Path, out: Path, curlcake_ref: Path, fixture_bam: Path, **rt):
    bc, al = _fakes(fixture_bam)
    return RealtimePipeline(run_dir, out, _rt_cfg(curlcake_ref, **rt),
                            basecaller=bc, aligner=al)


# ---------------------------------------------------------------------------
# The headline gate
# ---------------------------------------------------------------------------

def test_realtime_equals_offline(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Same Core, same inputs, two different feeds -> identical QC numbers.

    Comparison is over the canonicalized ``qc_result.json`` with the volatile paths
    stripped, plus ``run_accounting`` (the two drivers keep deliberately different
    bookkeeping — one counts cached basecalls, the other counts dispositions — and that is
    not a QC number).
    """
    run_dir = tmp_path / "run"
    _run_mimic(run_dir, n=4)

    rt_out = tmp_path / "rt"
    _realtime(run_dir, rt_out, curlcake_ref, fixture_bam).run()

    off_out = tmp_path / "off"
    bc, al = _fakes(fixture_bam)
    OfflinePipeline(run_dir / "pod5", off_out, _cfg(curlcake_ref),
                    basecaller=bc, aligner=al).run()

    rt = canonicalize(json.loads((rt_out / "qc_result.json").read_text()))
    off = canonicalize(json.loads((off_out / "qc_result.json").read_text()))
    for doc in (rt, off):
        doc.pop("run_accounting", None)
        doc.pop("n_inputs", None)

    assert diff_docs(off, rt) == [], "real-time and offline disagree on QC numbers"


def test_every_pod5_including_the_last_is_ingested(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The dropped-final-batch regression, end to end through the real driver."""
    run_dir = tmp_path / "run"
    mimic = _run_mimic(run_dir, n=5)

    pipe = _realtime(run_dir, tmp_path / "out", curlcake_ref, fixture_bam)
    pipe.run()

    expected = {mimic.pod5_name(i) for i in range(5)}
    assert pipe.progress.ingested == expected
    assert pipe.state is State.FINALIZE


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------

def test_run_accounting_partitions_and_reports_coverage(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    run_dir = tmp_path / "run"
    _run_mimic(run_dir, n=3)
    out = tmp_path / "out"
    _realtime(run_dir, out, curlcake_ref, fixture_bam).run()

    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert len(acc["ingested"]) == 3
    assert acc["failed_basecall"] == [] and acc["unstable_at_seal"] == []
    assert acc["lost_before_processing"] == []
    # The mimic wrote a sequencing_summary with 166 reads per POD5, so coverage is
    # measurable and complete.
    assert acc["coverage"]["status"] == "measured"
    assert acc["coverage"]["fraction_of_run_reads"] == 1.0


def test_a_failed_input_is_named_not_dropped(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    run_dir = tmp_path / "run"
    mimic = _run_mimic(run_dir, n=3)
    bad = mimic.pod5_name(1)

    bc, al = _fakes(fixture_bam)

    def flaky_basecall(pod5: Path, out_dir: Path) -> Path:
        if pod5.name == bad:
            raise RuntimeError("simulated basecall failure")
        return bc(pod5, out_dir)

    out = tmp_path / "out"
    pipe = RealtimePipeline(run_dir, out, _rt_cfg(curlcake_ref),
                            basecaller=flaky_basecall, aligner=al)
    pipe.run()

    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert acc["failed_basecall"] == [bad]
    assert len(acc["ingested"]) == 2
    assert acc["coverage"]["fraction_of_run_reads"] == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------

def test_restart_replays_the_manifest_and_finishes(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """A restart rebuilds from the manifest, then covers the rest — same final numbers."""
    run_dir = tmp_path / "run"
    _run_mimic(run_dir, n=4)
    out = tmp_path / "out"

    # First attempt: stop after 2 inputs, as a crash would.
    first = _realtime(run_dir, out, curlcake_ref, fixture_bam)
    first.boot()
    first.replay()
    for pod5 in sorted((run_dir / "pod5").glob("*.pod5"))[:2]:
        first._handle(pod5)
    assert len(first.progress.ingested) == 2

    # Restart into the SAME output dir.
    second = _realtime(run_dir, out, curlcake_ref, fixture_bam)
    result = second.run()
    assert result["status"] == "report"
    assert len(second.progress.ingested) == 4

    # And it matches a clean single-pass run.
    clean_out = tmp_path / "clean"
    clean = _realtime(run_dir, clean_out, curlcake_ref, fixture_bam)
    clean.run()
    a = canonicalize(json.loads((out / "qc_result.json").read_text()))
    b = canonicalize(json.loads((clean_out / "qc_result.json").read_text()))
    for doc in (a, b):
        doc.pop("run_accounting", None)
    assert diff_docs(b, a) == [], "a restarted run must equal an uninterrupted one"


def test_manifest_is_not_a_skip_set(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Deleting the manifest costs work, never correctness."""
    run_dir = tmp_path / "run"
    _run_mimic(run_dir, n=3)
    out = tmp_path / "out"
    _realtime(run_dir, out, curlcake_ref, fixture_bam).run()
    total_a = json.loads((out / "qc_result.json").read_text())[
        "snapshots"]["run_summary"]["mapping"]["total"]

    (out / MANIFEST_NAME).unlink()
    out2 = tmp_path / "out2"
    _realtime(run_dir, out2, curlcake_ref, fixture_bam).run()
    total_b = json.loads((out2 / "qc_result.json").read_text())[
        "snapshots"]["run_summary"]["mapping"]["total"]
    assert total_a == total_b


# ---------------------------------------------------------------------------
# BOOT
# ---------------------------------------------------------------------------

def test_boot_clears_stale_partial_artifacts(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Publish-by-rename (C2): a `partial-` output in the cache folder never finished."""
    run_dir = tmp_path / "run"
    _run_mimic(run_dir, n=1)
    out = tmp_path / "out"
    cache = out / "basecall_cache"
    cache.mkdir(parents=True)
    (cache / "partial-aligned_x.bam").write_text("half a BAM")
    stale_dir = cache / "partial-basecall_x"
    stale_dir.mkdir()
    (stale_dir / "chunk_0_0.bam").write_text("half a chunk")

    pipe = _realtime(run_dir, out, curlcake_ref, fixture_bam)
    pipe.boot()
    assert not (cache / "partial-aligned_x.bam").exists()
    assert not stale_dir.exists()


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def test_order_invariance(tmp_path: Path, fixture_bam: Path, curlcake_ref: Path) -> None:
    """The accumulators are commutative, so a shuffled feed must give identical finals.

    Not a hypothetical: under backpressure or a re-drive the arrival order genuinely varies.
    """
    run_dir = tmp_path / "run"
    _run_mimic(run_dir, n=4)
    pod5s = sorted((run_dir / "pod5").glob("*.pod5"))

    finals = []
    for order in (pod5s, list(reversed(pod5s))):
        out = tmp_path / f"out_{len(finals)}"
        pipe = _realtime(run_dir, out, curlcake_ref, fixture_bam)
        pipe.boot()
        pipe.replay()
        for p in order:
            pipe._handle(p)
        from mrnaspect.runtime.lifecycle import FinalizeReason
        pipe.finalize(FinalizeReason.RUN_END)
        doc = canonicalize(json.loads((out / "qc_result.json").read_text()))
        doc.pop("run_accounting", None)
        finals.append(doc)

    assert diff_docs(finals[0], finals[1]) == []


# ---------------------------------------------------------------------------
# Backpressure (D6)
# ---------------------------------------------------------------------------

def test_backpressure_is_off_by_default_and_lag_is_reported(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Lag reporting is unconditional — "did QC keep up" is a property of the run an
    operator needs whether or not decimation is configured."""
    run_dir = tmp_path / "run"
    _run_mimic(run_dir, n=3)
    out = tmp_path / "out"
    _realtime(run_dir, out, curlcake_ref, fixture_bam).run()

    bp = json.loads((out / "qc_result.json").read_text())["run_accounting"]["backpressure"]
    assert bp["enabled"] is False
    assert bp["decimated"] == 0
    # `arrivals` counts WATCHER arrivals only. Here the mimic finished before the pipeline
    # started, so the whole backlog came in through the sealing sweep, which bypasses
    # admission control on purpose — at run-end there is no arrival pressure to control.
    assert bp["mean_stage_s"] is not None, "lag must be estimable once inputs completed"
    assert bp["keeping_up"] is True


def test_deferred_inputs_are_caught_up_at_seal(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """With sampling OFF, decimation degrades the LIVE view and not the final report.

    At run-end there is no arrival pressure left, so the backlog is worked off inside the
    seal budget. Sampling is the shipped default since 2026-10-15, so this mode is now
    requested explicitly — and the explicitness is the point: which of the two a run used
    changes what its CoA claims, so no test of either may rely on the default.
    """
    run_dir = tmp_path / "run"
    out = tmp_path / "out"

    # Real arrival pressure: the mimic publishes CONCURRENTLY while a deliberately slow
    # basecall keeps the queue full. A pre-finished backlog would not exercise this at all,
    # because the sealing sweep bypasses admission control.
    bc, al = _fakes(fixture_bam)

    def slow_basecall(pod5: Path, out_dir: Path) -> Path:
        import time
        time.sleep(0.25)
        return bc(pod5, out_dir)

    mimic = MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166) for _ in range(4)],
                         "win_same_disk", speed=3000.0)
    pipe = RealtimePipeline(
        run_dir, out,
        _rt_cfg(curlcake_ref, max_queue_depth=1, backpressure_sampling=False),
        basecaller=slow_basecall, aligner=al)

    producer = threading.Thread(target=mimic.run, daemon=True)
    producer.start()
    pipe.run()
    producer.join(timeout=5)

    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert acc["backpressure"]["decimated"] > 0, "the test must actually exercise deferral"
    # ...and yet every POD5 is in the final report.
    assert len(acc["ingested"]) == 4
    assert acc["deferred_backpressure"] == []
    assert acc["coverage"]["fraction_of_run_reads"] == 1.0
    assert pipe.progress.ingested == {mimic.pod5_name(i) for i in range(4)}


def test_sampling_leaves_the_thinned_inputs_out_and_names_them(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The shipped default: a thinned POD5 is never analysed, and the report says so.

    Sealing must not quietly undo the sampling decision. Re-sweeping at run-end would turn
    a uniform-in-time sample into "everything we had time for" — which is a TIME SUFFIX,
    biased toward the degraded end of the run, and the exact shape the fixed stride exists
    to avoid. So the inputs must still be missing after seal, not merely during it.
    """
    run_dir = tmp_path / "run"
    out = tmp_path / "out"

    # Real arrival pressure, as in the census test: the mimic publishes CONCURRENTLY while
    # a deliberately slow basecall keeps the queue full.
    bc, al = _fakes(fixture_bam)

    def slow_basecall(pod5: Path, out_dir: Path) -> Path:
        import time
        time.sleep(0.25)
        return bc(pod5, out_dir)

    mimic = MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166) for _ in range(4)],
                         "win_same_disk", speed=3000.0)
    pipe = RealtimePipeline(run_dir, out, _rt_cfg(curlcake_ref, max_queue_depth=1),
                            basecaller=slow_basecall, aligner=al)
    assert pipe.backpressure.sampling, "sampling is the shipped default"

    producer = threading.Thread(target=mimic.run, daemon=True)
    producer.start()
    pipe.run()
    producer.join(timeout=5)

    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert acc["backpressure"]["decimated"] > 0, "the test must actually exercise thinning"
    assert acc["sampled_out"], "a thinned input must be named"
    assert acc["deferred_backpressure"] == [], (
        "under sampling nothing is merely deferred; the other key must stay empty so a "
        "reader of qc_result.json can tell the two dispositions apart locally")
    assert len(acc["ingested"]) < 4, "sealing must not have caught them up again"
    assert set(acc["sampled_out"]).isdisjoint(acc["ingested"])
    assert acc["coverage"]["fraction_of_run_reads"] < 1.0
    # POD5-level, always available even without a sequencing_summary.
    assert 0.0 < acc["backpressure"]["admitted_fraction"] < 1.0


def test_the_two_dispositions_are_never_both_populated(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """A schema invariant: one bucket, two names, exactly one of them in use.

    The alternative — one key whose meaning depends on a flag stored elsewhere in the
    document — is a disposition the reader cannot resolve where they find it.
    """
    for sampling in (True, False):
        run_dir = tmp_path / f"run_{sampling}"
        out = tmp_path / f"out_{sampling}"
        _run_mimic(run_dir, n=3)
        pipe = _realtime(run_dir, out, curlcake_ref, fixture_bam, max_queue_depth=1,
                         backpressure_sampling=sampling)
        pipe.boot()
        pipe.replay()
        from mrnaspect.runtime.lifecycle import FinalizeReason
        pod5s = sorted((run_dir / "pod5").glob("*.pod5"))
        for q in pod5s[:2]:
            pipe._handle(q)
        pipe.progress.observed.add(pod5s[2].name)
        pipe.progress.deferred.add(pod5s[2].name)
        pipe.finalize(FinalizeReason.USER_ABORT)

        acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
        assert "sampled_out" in acc and "deferred_backpressure" in acc, (
            "both keys always present, so the schema does not change with the mode")
        assert not (acc["sampled_out"] and acc["deferred_backpressure"])
        held = acc["sampled_out"] if sampling else acc["deferred_backpressure"]
        assert held == [pod5s[2].name], f"wrong bucket for sampling={sampling}"


def test_a_deferred_input_that_never_catches_up_is_named(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """If even the seal budget runs out, the report must say which inputs it never saw.

    Sampling OFF: these are inputs the run MEANT to analyse and could not, which is a
    different disposition from one it chose to skip, and they are reported under a
    different key for exactly that reason.
    """
    run_dir = tmp_path / "run"
    _run_mimic(run_dir, n=3)
    out = tmp_path / "out"

    pipe = _realtime(run_dir, out, curlcake_ref, fixture_bam, max_queue_depth=1,
                     backpressure_sampling=False)
    pipe.boot()
    pipe.replay()
    # Simulate a deferral that sealing cannot reach: mark one deferred, then finalize
    # WITHOUT sealing (as an aborted run would).
    from mrnaspect.runtime.lifecycle import FinalizeReason
    pod5s = sorted((run_dir / "pod5").glob("*.pod5"))
    for p in pod5s[:2]:
        pipe._handle(p)
    # Only the LAST one is left deferred. Every observed POD5 must land in some bucket —
    # an earlier draft of this test observed all three while bucketing only two, and the
    # accounting guard correctly refused it.
    pipe.progress.observed.add(pod5s[2].name)
    pipe.progress.deferred.add(pod5s[2].name)
    pipe.finalize(FinalizeReason.USER_ABORT)

    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert acc["deferred_backpressure"] == [pod5s[2].name]
    assert acc["sampled_out"] == [], "nothing was sampled out; this run meant to be a census"
    assert acc["coverage"]["fraction_of_run_reads"] < 1.0, (
        "coverage must reflect what decimation cost")


# ---------------------------------------------------------------------------
# The live progress label
# ---------------------------------------------------------------------------

def test_progress_label_reports_the_backlog_not_the_filename(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The offline prefix is ``[3/400]``, meaningful because the input set is known up
    front. Live it is not, so the useful equivalent is the backlog. The label used to
    repeat the POD5 name, which the very next field of every log line already prints."""
    rt = _realtime(tmp_path / "run", tmp_path / "out", curlcake_ref, fixture_bam)

    # Nothing completed yet: a lag estimate would be fabricated from no data.
    assert rt._progress_label() == "[queue 0] "

    rt.backpressure.on_completed(200.0)          # one input took 200 s
    assert rt._progress_label() == "[queue 0] "  # empty queue IS caught up, not "~0s behind"

    for _ in range(3):
        rt._queue.put(None)
    label = rt._progress_label()
    assert label.startswith("[queue 3, ~")
    assert "min behind" in label                 # 3 x 200 s = 10 min, not "600s"


def test_progress_label_uses_seconds_while_the_backlog_is_small(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    rt = _realtime(tmp_path / "run", tmp_path / "out", curlcake_ref, fixture_bam)
    rt.backpressure.on_completed(5.0)
    rt._queue.put(None)
    assert rt._progress_label() == "[queue 1, ~5s behind] "
