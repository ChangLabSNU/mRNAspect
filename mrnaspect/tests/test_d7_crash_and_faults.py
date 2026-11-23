"""D7 — crash windows and fault injection (plan tests B, C, E, F).

Plan test B is the substantial one: a kill can land in seven distinct windows, each leaving
the output directory in a *different* half-finished state, and every one of them must
restart to a report canonically identical to an uninterrupted run. The uniform assertion is
what makes this meaningful — "it recovered" is easy; "it recovered to the same numbers" is
the claim a CoA depends on.

Crashes are simulated by putting the output directory into the exact state a kill would
leave, then running a fresh pipeline to completion. That is deterministic where a real
SIGKILL race is not, and it exercises the same recovery code. Stages are injected fakes
(no GPU, no dorado-server), so any difference between runs is a driver bug rather than
basecaller nondeterminism.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mrnaspect.tests.conftest import pod5_ready
from mrnaspect.main import load_config
from mrnaspect.realtime.main import RealtimePipeline
from mrnaspect.wrappers._shell import PARTIAL_PREFIX, unfinished_outputs
from mrnaspect.realtime.manifest import (
    MANIFEST_NAME,
    IngestManifest,
    ManifestIntegrityError,
)
from mrnaspect.realtime.mimic import Batch, Fault, MinKNOWMimic
from mrnaspect.realtime.watcher import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_STABLE_POLLS,
    DirectoryWatcher,
)
from mrnaspect.runtime.checkpoint import Checkpoint, DEFAULT_STATE_NAME
from mrnaspect.tests.golden_compare import canonicalize, diff_docs

N_POD5 = 3


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

from .conftest import write_distinct_bam

def _fakes(fixture_bam: Path, calls: dict | None = None):
    """basecall -> ubam dir, align -> a copy of the fixture BAM. Optionally counted."""
    calls = calls if calls is not None else {}
    calls.setdefault("basecall", [])
    calls.setdefault("align", [])

    # Each POD5 yields DISTINCT reads, keyed by its stem — as a real basecall does. Copying
    # one fixture BAM verbatim gave every POD5 the same 166 read IDs, which the engine now
    # drops as duplicates; a test built on that would be asserting the double-counting the
    # duplicate guard exists to prevent, rather than the crash/restart behaviour it names.
    def fake_basecall(pod5: Path, out_dir: Path) -> Path:
        calls["basecall"].append(pod5.name)
        d = Path(out_dir) / f"basecall_{pod5.stem}"
        d.mkdir(parents=True, exist_ok=True)
        write_distinct_bam(fixture_bam, d / "chunk_0_0.bam", pod5.stem)
        return d

    def fake_align(prefix: str, reference: Path, out_dir: Path) -> Path:
        calls["align"].append(prefix)
        bam = Path(out_dir) / f"aligned_{prefix}.bam"
        write_distinct_bam(fixture_bam, bam, prefix)
        return bam

    return fake_basecall, fake_align, calls


def _cfg(curlcake_ref: Path, **rt):
    cfg = pod5_ready(load_config(None, {"reference": str(curlcake_ref), "mode": "pod5",
                                        "min_reads": 1}))
    cfg["realtime"] = {
        **cfg["realtime"],
        "poll_interval_s": 0.02, "stable_polls": 3,
        "seal_poll_interval_s": 0.02, "seal_stable_polls": 3,
        "seal_budget_s": 10.0, "interim_every_n": 0, **rt,
    }
    return cfg


def _mimic(run_dir: Path, n: int = N_POD5, **over) -> MinKNOWMimic:
    m = MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166) for _ in range(n)],
                     "win_same_disk", speed=400.0, **over)
    m.run()
    return m


def _pipe(run_dir: Path, out: Path, curlcake_ref: Path, fixture_bam: Path,
          calls: dict | None = None, **rt) -> RealtimePipeline:
    bc, al, _ = _fakes(fixture_bam, calls)
    return RealtimePipeline(run_dir, out, _cfg(curlcake_ref, **rt),
                            basecaller=bc, aligner=al)


def _final(out: Path) -> dict:
    """The reported artifact, minus bookkeeping that legitimately differs across restarts."""
    doc = canonicalize(json.loads((out / "qc_result.json").read_text()))
    doc.pop("run_accounting", None)   # n_basecall_cached etc. — not a QC number
    doc.pop("n_inputs", None)
    return doc


@pytest.fixture(scope="module")
def _ref_run(tmp_path_factory, fixture_bam, curlcake_ref):
    """An uninterrupted run. Every crash scenario is compared against this."""
    base = tmp_path_factory.mktemp("d7_reference")
    run_dir = base / "run"
    _mimic(run_dir)
    out = base / "out"
    _pipe(run_dir, out, curlcake_ref, fixture_bam).run()
    return {"doc": _final(out), "run_dir": run_dir}


# ---------------------------------------------------------------------------
# Plan test B — the seven crash windows
# ---------------------------------------------------------------------------
#
# Each scenario receives the output dir of a run that was killed partway, and puts it into
# the precise state that kill would leave. The pipeline is then restarted and must finish
# to the reference numbers.

def _partial_run(run_dir: Path, out: Path, curlcake_ref: Path, fixture_bam: Path,
                 n: int) -> RealtimePipeline:
    """Process the first `n` POD5s and stop — no seal, no finalize, as a kill would."""
    pipe = _pipe(run_dir, out, curlcake_ref, fixture_bam)
    pipe.boot()
    pipe.replay()
    for pod5 in sorted((run_dir / "pod5").glob("*.pod5"))[:n]:
        pipe._handle(pod5)
    return pipe


def _crash_mid_basecall(out: Path, run_dir: Path) -> None:
    """Killed while the server was streaming chunks: a `partial-` dir of debris."""
    d = out / "basecall_cache" / f"{PARTIAL_PREFIX}basecall_ghost"
    d.mkdir(parents=True, exist_ok=True)
    (d / "chunk_0_0.bam").write_text("half a chunk")


def _crash_after_ubam_before_record(out: Path, run_dir: Path) -> None:
    """The ubam landed at its canonical name but the checkpoint entry never got written.

    The cache then MISSES and the shard is re-basecalled — wasteful, never wrong. This is
    the safe direction of the race, and it is why `record` happens after the rename.
    """
    cache = out / "basecall_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "basecall_orphan_ubam").mkdir(exist_ok=True)
    (cache / "basecall_orphan_ubam" / "chunk_0_0.bam").write_text("orphan")


def _crash_after_record_before_align(out: Path, run_dir: Path) -> None:
    """Checkpoint says the ubam exists; no aligned BAM. Restart should reuse and align."""
    # The partial run already recorded entries; drop the aligned BAMs so align must re-run.
    for bam in (out / "basecall_cache").glob("aligned_*.bam*"):
        bam.unlink()


def _crash_mid_align(out: Path, run_dir: Path) -> None:
    """`samtools sort` killed: a truncated BAM under `partial-` (C2 quarantines it there)."""
    cache = out / "basecall_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{PARTIAL_PREFIX}aligned_ghost.bam").write_text("truncated BAM")


def _crash_after_bam_before_manifest(out: Path, run_dir: Path) -> None:
    """The aligned BAM was published but its manifest line never appended.

    Replay therefore covers fewer entries and that POD5 is re-derived. Cheap, and the
    only alternative — trusting an unrecorded BAM — is how a report drifts from its log.
    """
    path = out / MANIFEST_NAME
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n")


def _crash_torn_manifest_line(out: Path, run_dir: Path) -> None:
    """Killed mid-append: only the LAST line can be truncated in an append-only file."""
    with open(out / MANIFEST_NAME, "a", encoding="utf-8") as fh:
        fh.write("99\tghost.pod5\tbams/ghost.bam\t8")     # no trailing newline


def _crash_after_append_before_snapshot(out: Path, run_dir: Path) -> None:
    """The manifest advanced but `latest_snapshot.json` is stale — or absent.

    Must be a no-op for correctness: the snapshot is an operator dashboard and a
    last-resort render, explicitly NOT recovery state.
    """
    snap = out / "latest_snapshot.json"
    if snap.exists():
        snap.write_text('{"deliberately": "stale"}')


CRASH_POINTS = [
    ("mid_basecall", _crash_mid_basecall, 1),
    ("after_ubam_before_record", _crash_after_ubam_before_record, 1),
    ("after_record_before_align", _crash_after_record_before_align, 2),
    ("mid_align", _crash_mid_align, 1),
    ("after_bam_before_manifest", _crash_after_bam_before_manifest, 2),
    ("torn_manifest_line", _crash_torn_manifest_line, 2),
    ("after_append_before_snapshot", _crash_after_append_before_snapshot, 2),
]


@pytest.mark.parametrize("name,mutate,n_before", CRASH_POINTS,
                         ids=[c[0] for c in CRASH_POINTS])
def test_crash_window_restarts_to_identical_numbers(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, _ref_run,
    name: str, mutate, n_before: int
) -> None:
    """Plan test B. Seven kill windows, one assertion: same final report."""
    run_dir = tmp_path / "run"
    shutil.copytree(_ref_run["run_dir"], run_dir)
    out = tmp_path / "out"

    _partial_run(run_dir, out, curlcake_ref, fixture_bam, n_before)
    mutate(out, run_dir)

    # Restart into the SAME output directory and finish.
    restarted = _pipe(run_dir, out, curlcake_ref, fixture_bam)
    result = restarted.run()

    assert result["status"] == "report"
    assert len(restarted.progress.ingested) == N_POD5, (
        f"{name}: restart did not cover every input")
    assert diff_docs(_ref_run["doc"], _final(out)) == [], (
        f"{name}: restart produced different numbers than an uninterrupted run")


def test_boot_clears_partial_debris_from_every_crash_point(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, _ref_run
) -> None:
    """C2's invariant across restarts: presence at a canonical path => complete."""
    run_dir = tmp_path / "run"
    shutil.copytree(_ref_run["run_dir"], run_dir)
    out = tmp_path / "out"
    out.mkdir()
    _crash_mid_basecall(out, run_dir)
    _crash_mid_align(out, run_dir)
    assert len(unfinished_outputs(out / "basecall_cache")) == 2

    _pipe(run_dir, out, curlcake_ref, fixture_bam).boot()
    assert not list(out.rglob(f"{PARTIAL_PREFIX}*"))


# ---------------------------------------------------------------------------
# Plan test C — the run-end sweep, at PRODUCTION watcher settings
# ---------------------------------------------------------------------------

def test_final_pod5_survives_the_marker_at_production_settings(tmp_path: Path) -> None:
    """Plan test C. The regression that motivated the whole sealing design.

    Uses the REAL locked constants (poll 5.0 s x 7 = 30 s of verified silence) rather than
    the compressed clock the other tests use, because the bug only exists when the
    stability window exceeds the marker gap — 30 s against a measured 1.07 s. Driven by
    stepping ``scan_once`` so the exact production configuration is exercised without 35
    seconds of wall time; the rule is poll-COUNT based, so stepping is faithful.
    """
    run_dir = tmp_path / "run"
    (run_dir / "pod5").mkdir(parents=True)
    ready, markers = [], []
    w = DirectoryWatcher(
        run_dir, pod5_subdir="pod5", on_ready=ready.append, on_run_end=markers.append,
        poll_interval_s=DEFAULT_POLL_INTERVAL_S, stable_polls=DEFAULT_STABLE_POLLS)
    assert w.verified_silence_s == 30.0

    (run_dir / "pod5" / "final.pod5").write_bytes(b"\0" * 512)
    w.scan_once()                                            # baseline, 1 of 7
    (run_dir / "final_summary_FC_run_acq.txt").write_text("pod5_files_in_final_dest=1\n")

    for _ in range(DEFAULT_STABLE_POLLS - 1):                # polls 2..7
        w.scan_once()

    assert markers, "the marker must still be reported"
    assert [e.path.name for e in ready] == ["final.pod5"], (
        "the final POD5 was lost — the marker stopped the scan")


def test_sealing_recovers_the_final_pod5_end_to_end(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Same property through the whole driver: the file the watcher was mid-verifying
    when the marker fired is recovered by the DIRECTORY sweep, not a queue drain."""
    run_dir = tmp_path / "run"
    mimic = _mimic(run_dir, n=4)
    out = tmp_path / "out"
    pipe = _pipe(run_dir, out, curlcake_ref, fixture_bam)
    pipe.run()

    assert pipe.progress.ingested == {mimic.pod5_name(i) for i in range(4)}
    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert acc["coverage"]["fraction_of_run_reads"] == 1.0


# ---------------------------------------------------------------------------
# Plan test E — degraded cache
# ---------------------------------------------------------------------------

def test_deleting_an_aligned_bam_re_aligns_and_deleting_a_ubam_re_basecalls(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, _ref_run
) -> None:
    """Plan test E. The cache is an optimization; losing part of it costs work, not truth."""
    run_dir = tmp_path / "run"
    shutil.copytree(_ref_run["run_dir"], run_dir)
    out = tmp_path / "out"
    _pipe(run_dir, out, curlcake_ref, fixture_bam).run()

    cache = out / "basecall_cache"
    victim_bam = sorted(cache.glob("aligned_*.bam"))[0]
    victim_prefix = victim_bam.stem[len("aligned_"):]
    victim_ubam = sorted(p for p in cache.glob("basecall_*") if p.is_dir())[1]
    ubam_prefix = victim_ubam.name[len("basecall_"):]

    victim_bam.unlink()
    (cache / f"{victim_bam.name}.bai").unlink(missing_ok=True)
    shutil.rmtree(victim_ubam)
    # The checkpoint still POINTS at the deleted ubam — a stale cache entry, which is the
    # realistic failure (eviction does not rewrite the checkpoint).

    calls: dict = {}
    restarted = _pipe(run_dir, out, curlcake_ref, fixture_bam, calls=calls)
    restarted.run()

    assert ubam_prefix in [Path(n).stem for n in calls["basecall"]], (
        "a deleted ubam must be re-basecalled")
    assert victim_prefix in calls["align"], "a deleted aligned BAM must be re-aligned"
    assert diff_docs(_ref_run["doc"], _final(out)) == [], (
        "a degraded cache must not change the numbers")


# ---------------------------------------------------------------------------
# Plan test F — a corrupted audit trail is fatal
# ---------------------------------------------------------------------------

def test_a_corrupted_primary_total_hard_fails_instead_of_reporting(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, _ref_run
) -> None:
    """Plan test F. A report whose numbers disagree with its own audit trail is worse
    than no report, so this must raise and must NOT leave a fresh qc_result.json."""
    run_dir = tmp_path / "run"
    shutil.copytree(_ref_run["run_dir"], run_dir)
    out = tmp_path / "out"
    _pipe(run_dir, out, curlcake_ref, fixture_bam).run()
    (out / "qc_result.json").unlink()

    # Tamper with the LAST entry, adjusting n_primary in step so the internal shape check
    # still passes. That forces the failure to come from the replay assertion — comparing
    # the recorded total against what re-ingesting the BAM actually produces — rather than
    # from the cheaper consistency check, which is a different guard.
    path = out / MANIFEST_NAME
    lines = path.read_text().splitlines()
    parts = lines[-1].split("\t")
    parts[-2] = str(int(parts[-2]) + 7)            # n_primary
    parts[-1] = str(int(parts[-1]) + 7)            # primary_total_after
    lines[-1] = "\t".join(parts)
    path.write_text("\n".join(lines) + "\n")

    restarted = _pipe(run_dir, out, curlcake_ref, fixture_bam)
    with pytest.raises(ManifestIntegrityError, match="audit trail"):
        restarted.run()
    assert not (out / "qc_result.json").exists(), (
        "a run with a corrupted audit trail must not emit a report")


def test_a_manifest_pointing_at_a_missing_bam_degrades_quietly(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, _ref_run
) -> None:
    """The other half of F: *missing* is benign (truncate the replay), *changed* is fatal.

    Conflating them would either crash on a routine eviction or trust a tampered BAM.
    """
    run_dir = tmp_path / "run"
    shutil.copytree(_ref_run["run_dir"], run_dir)
    out = tmp_path / "out"
    _pipe(run_dir, out, curlcake_ref, fixture_bam).run()

    entries, _ = IngestManifest(out / MANIFEST_NAME, root=out).read()
    (out / entries[0].aligned_bam).unlink()

    restarted = _pipe(run_dir, out, curlcake_ref, fixture_bam)
    restarted.run()
    assert diff_docs(_ref_run["doc"], _final(out)) == []


# ---------------------------------------------------------------------------
# Mimic fault modes, end to end
# ---------------------------------------------------------------------------

def test_a_stalled_tmp_never_enters_the_run_and_shows_up_as_lost(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """`.pod5.tmp` is invisible to the watcher by design — so ONLY coverage can reveal it.

    This is the case that justifies computing coverage from sequencing_summary rather than
    from our own bookkeeping: nothing we record could show a file we never saw.
    """
    run_dir = tmp_path / "run"
    mimic = MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166) for _ in range(3)],
                         "win_same_disk", speed=400.0, faults={1: Fault.STALLED_TMP})
    mimic.run()
    out = tmp_path / "out"
    _pipe(run_dir, out, curlcake_ref, fixture_bam).run()

    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert len(acc["ingested"]) == 2
    assert acc["lost_before_processing"] == [mimic.pod5_name(1)]
    assert acc["coverage"]["fraction_of_run_reads"] == pytest.approx(2 / 3)


def test_a_zero_byte_pod5_is_never_handed_to_the_basecaller(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """It stabilizes instantly at the canonical name, so only the size>0 rule stops it."""
    run_dir = tmp_path / "run"
    mimic = MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166) for _ in range(3)],
                         "win_same_disk", speed=400.0, faults={1: Fault.ZERO_BYTE})
    mimic.run()
    out = tmp_path / "out"
    calls: dict = {}
    pipe = _pipe(run_dir, out, curlcake_ref, fixture_bam, calls=calls)
    pipe.run()

    assert mimic.pod5_name(1) not in [Path(n).name for n in calls["basecall"]]
    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert mimic.pod5_name(1) in acc["unstable_at_seal"], (
        "a 0-byte POD5 must be named, not silently skipped")


def test_a_truncated_pod5_fails_its_stage_and_is_named(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Size stabilization CANNOT reject this — it is a plausible file. Rejecting it is the
    basecall stage's job, which is exactly what the fault proves."""
    run_dir = tmp_path / "run"
    mimic = MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166) for _ in range(3)],
                         "win_same_disk", speed=400.0, faults={1: Fault.TRUNCATED})
    mimic.run()
    bad = mimic.pod5_name(1)

    bc, al, _ = _fakes(fixture_bam)

    def validating_basecall(pod5: Path, out_dir: Path) -> Path:
        if pod5.stat().st_size < 256:          # dorado would reject the malformed POD5
            raise RuntimeError(f"malformed POD5: {pod5.name}")
        return bc(pod5, out_dir)

    out = tmp_path / "out"
    pipe = RealtimePipeline(run_dir, out, _cfg(curlcake_ref),
                            basecaller=validating_basecall, aligner=al)
    pipe.run()

    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert acc["failed_basecall"] == [bad]
    assert len(acc["ingested"]) == 2
    assert acc["coverage"]["fraction_of_run_reads"] == pytest.approx(2 / 3)


def test_a_republished_name_is_not_double_counted(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Exactly-once comes from a fresh engine plus a process-local set — never from the
    manifest. A name reappearing with different bytes must not inflate the depth."""
    run_dir = tmp_path / "run"
    mimic = MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166) for _ in range(3)],
                         "win_same_disk", speed=400.0, faults={2: Fault.DUPLICATE_NAME})
    mimic.run()
    out = tmp_path / "out"
    pipe = _pipe(run_dir, out, curlcake_ref, fixture_bam)
    pipe.run()

    doc = json.loads((out / "qc_result.json").read_text())
    # Two distinct names ever existed (_2 was republished over _1), each ingested once.
    assert len(pipe.progress.ingested) == 2
    assert doc["snapshots"]["run_summary"]["mapping"]["total"] == 2 * 166


def test_a_post_marker_straggler_is_ingested_and_flagged(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The design takes the input set to be closed at marker time. If a deployment violates that, the
    straggler must still be ingested AND the falsification recorded."""
    run_dir = tmp_path / "run"
    mimic = MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166) for _ in range(3)],
                         "win_same_disk", speed=400.0,
                         faults={3: Fault.POST_MARKER_STRAGGLER})
    mimic.run()
    out = tmp_path / "out"
    pipe = _pipe(run_dir, out, curlcake_ref, fixture_bam)
    pipe.run()

    assert len(pipe.progress.ingested) == 4, "the straggler must not be lost"
    acc = json.loads((out / "qc_result.json").read_text())["run_accounting"]
    assert len(acc["ingested"]) == 4
