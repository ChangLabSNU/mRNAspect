"""C4 — the per-input body is shared, not duplicated.

``OfflinePipeline.run()`` used to be one method mixing input discovery, the abort check,
stage execution, timing, durability, interim cadence and finalize. The real-time driver
needs the *middle* of that and none of the ends, and the tempting move is to copy it. Two
copies of a stage sequence stay identical exactly until one of them is edited, and the
thing they would silently disagree about is whether offline and real-time produce the same
numbers — which is the headline gate of the whole real-time effort.

So the body lives in ``_process_one_input`` and both drivers call it. These tests pin the
two properties that makes true: it is callable without ``run()``, and driving it by hand
reaches the same engine state that ``run()`` does.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mrnaspect.tests.conftest import pod5_ready
from mrnaspect.main import InputResult, OfflinePipeline, load_config


from .conftest import write_distinct_bam


def _bam_dir(tmp_path: Path, fixture_bam: Path, names: list[str]) -> Path:
    d = tmp_path / "in"
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        # Distinct read IDs per input — a real run's POD5s do not share reads,
        # and the engine now drops a primary read ID it has already seen.
        write_distinct_bam(fixture_bam, d / name, Path(name).stem)
    return d


def _cfg(reference: Path, **over):
    """pod5 overrides get the dorado stubs — client and model have no defaults (A-3)."""
    _c = load_config(None, {"reference": str(reference), "mode": "bam",
                            "min_reads": 1, **over})
    return pod5_ready(_c) if _c.get("mode") == "pod5" else _c


def test_process_one_input_works_without_calling_run(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The real-time driver never calls run(); the counters must still exist.

    This is why ``_reset_run_state()`` is invoked from ``__init__`` — before C4 the
    counters were created inside ``run()``, so calling the body directly raised
    AttributeError on the first timing update.
    """
    in_dir = _bam_dir(tmp_path, fixture_bam, ["a.bam"])
    pipe = OfflinePipeline(in_dir, tmp_path / "out", _cfg(curlcake_ref))

    res = pipe._process_one_input(in_dir / "a.bam")

    assert isinstance(res, InputResult)
    assert res.n_records > 0
    assert res.primary_total == pipe.engine.primary_total
    # bam mode aligns its input too (2026-11-10): what was ingested is Core's alignment.
    assert res.bam == tmp_path / "out" / "basecall_cache" / "aligned_a.bam"
    assert res.align_s > 0
    assert res.basecall_cached is False      # bam mode never touches the GPU cache
    assert pipe._n_processed == 1
    assert pipe._failed_inputs == []


def test_manual_driving_matches_run(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Feeding inputs one at a time reaches the same engine state as run().

    A miniature of the real-time/offline equivalence gate: same Core, same inputs, two
    different feeds. Any divergence here is a driver bug, since the BAMs are identical.
    """
    names = ["a.bam", "b.bam", "c.bam"]
    in_dir = _bam_dir(tmp_path, fixture_bam, names)

    looped = OfflinePipeline(in_dir, tmp_path / "out_loop", _cfg(curlcake_ref))
    looped.run()

    manual = OfflinePipeline(in_dir, tmp_path / "out_manual", _cfg(curlcake_ref))
    results = [manual._process_one_input(in_dir / n) for n in names]

    assert all(r is not None for r in results)
    assert manual.engine.primary_total == looped.engine.primary_total
    assert manual._n_processed == looped._n_processed == 3
    # And the QC substance, not just the counters.
    assert manual._bundle().keys() == looped._bundle().keys()
    assert (manual._bundle()["run_summary"]["mapping"]
            == looped._bundle()["run_summary"]["mapping"])


def test_primary_total_is_cumulative_and_monotone(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """``primary_total`` is the value the real-time manifest asserts against on replay,
    so it must be the running engine total — not this input's own count."""
    names = ["a.bam", "b.bam", "c.bam"]
    in_dir = _bam_dir(tmp_path, fixture_bam, names)
    pipe = OfflinePipeline(in_dir, tmp_path / "out", _cfg(curlcake_ref))

    totals = [pipe._process_one_input(in_dir / n).primary_total for n in names]
    assert totals == sorted(totals), "primary_total must never decrease"
    assert len(set(totals)) == 3, "each input must advance the cumulative total"
    assert totals[-1] == pipe.engine.primary_total


def test_failure_returns_none_and_is_recorded(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    in_dir = _bam_dir(tmp_path, fixture_bam, ["good.bam"])
    (in_dir / "broken.bam").write_text("not a bam\n")
    pipe = OfflinePipeline(in_dir, tmp_path / "out", _cfg(curlcake_ref))

    assert pipe._process_one_input(in_dir / "broken.bam") is None
    assert pipe._failed_inputs == ["broken.bam"]
    assert pipe._n_processed == 0
    # A failed input must not poison the ones after it.
    assert pipe._process_one_input(in_dir / "good.bam") is not None
    assert pipe._n_processed == 1


def test_on_error_raise_propagates(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    in_dir = _bam_dir(tmp_path, fixture_bam, ["good.bam"])
    (in_dir / "broken.bam").write_text("not a bam\n")
    pipe = OfflinePipeline(in_dir, tmp_path / "out",
                           _cfg(curlcake_ref, on_error="raise"))
    with pytest.raises(Exception):
        pipe._process_one_input(in_dir / "broken.bam")


def test_label_only_affects_logging(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, caplog
) -> None:
    """The offline loop knows "3 of 400"; a live watcher does not. The progress prefix is
    therefore a caller concern, and must not leak into the result."""
    in_dir = _bam_dir(tmp_path, fixture_bam, ["a.bam"])
    pipe = OfflinePipeline(in_dir, tmp_path / "out", _cfg(curlcake_ref))
    with caplog.at_level("INFO"):
        res = pipe._process_one_input(in_dir / "a.bam", label="[7/99] ")
    assert "[7/99] a.bam" in caplog.text
    assert res.path == in_dir / "a.bam"


# ---------------------------------------------------------------------------
# realtime config section
# ---------------------------------------------------------------------------

def test_realtime_config_section_loads_with_locked_defaults(curlcake_ref: Path) -> None:
    """The locked detection parameters live in config, not in code.

    30 s of verified silence = (stable_polls - 1) * poll_interval_s. Pinned because a
    "harmless" tweak to make a test run faster would silently change what "ready" means.
    """
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam"})
    rt = cfg["realtime"]
    assert rt["poll_interval_s"] == 5.0
    assert rt["stable_polls"] == 7
    assert (rt["stable_polls"] - 1) * rt["poll_interval_s"] == 30.0
    # The two-level MinKNOW layout: POD5s nest, the run-end marker does not.
    assert rt["pod5_subdir"] == "pod5"
    assert rt["run_end_glob"] == "final_summary_*.txt"


def test_realtime_section_is_shape_guarded(curlcake_ref: Path, tmp_path: Path) -> None:
    """A scalar where a mapping belongs must fail at load, not opaquely mid-run."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("realtime: 5\n")
    with pytest.raises(ValueError, match="'realtime' must be a mapping"):
        load_config(bad, {"reference": str(curlcake_ref), "mode": "bam"})


def test_offline_pipeline_exposes_realtime_settings(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Both drivers share one config object rather than growing a second one."""
    in_dir = _bam_dir(tmp_path, fixture_bam, ["a.bam"])
    pipe = OfflinePipeline(in_dir, tmp_path / "out", _cfg(curlcake_ref))
    assert pipe.realtime["stable_polls"] == 7


# ---------------------------------------------------------------------------
# Stage-start logging: the operator must not face silence during the long step
# ---------------------------------------------------------------------------

def _pod5_dir(tmp_path: Path, names: list[str]) -> Path:
    d = tmp_path / "pod5in"
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        (d / name).write_bytes(b"not really a pod5")
    return d


def _fakes(fixture_bam: Path):
    """basecall -> ubam dir; align -> a copy of the fixture BAM. No GPU."""
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


def test_basecall_and_align_announce_before_they_start(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, caplog
) -> None:
    """Basecall is ~190 s and ~98 % of the stage. Logging only on
    completion leaves three minutes of silence during the longest step, which on a 20 h
    run is indistinguishable from a hang."""
    in_dir = _pod5_dir(tmp_path, ["x.pod5"])
    bc, al = _fakes(fixture_bam)
    pipe = OfflinePipeline(in_dir, tmp_path / "out", _cfg(curlcake_ref, mode="pod5"),
                           basecaller=bc, aligner=al)
    with caplog.at_level("INFO"):
        pipe._process_one_input(in_dir / "x.pod5")

    lines = [r.message for r in caplog.records]
    started = next(i for i, m in enumerate(lines) if "x.pod5: basecalling" in m)
    aligned = next(i for i, m in enumerate(lines) if "x.pod5: aligning" in m)
    finished = next(i for i, m in enumerate(lines) if "-> 166 reads" in m)
    # Order is the whole point: announce, then do, then summarize.
    assert started < aligned < finished


def test_a_cached_basecall_says_so_instead_of_claiming_to_basecall(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, caplog
) -> None:
    """A restart that reuses the ubam cache must not print 'basecalling…' — the operator
    would then read a 0.0 s basecall as a GPU that silently did nothing."""
    in_dir = _pod5_dir(tmp_path, ["x.pod5"])
    bc, al = _fakes(fixture_bam)
    out = tmp_path / "out"
    OfflinePipeline(in_dir, out, _cfg(curlcake_ref, mode="pod5"),
                    basecaller=bc, aligner=al)._process_one_input(in_dir / "x.pod5")

    pipe2 = OfflinePipeline(in_dir, out, _cfg(curlcake_ref, mode="pod5"),
                            basecaller=bc, aligner=al)
    # The claim below is about the SECOND, cached call, but caplog accumulates across the
    # whole test — and the first call legitimately logs "basecalling…". That only went
    # unnoticed while the line sat on a logger caplog was not capturing at INFO outside
    # the `at_level` block; moving it to the progress channel (INFO at import) made the
    # first call's line visible and the assertion started reading it. Clearing scopes the
    # assertion to what it says it tests.
    caplog.clear()
    with caplog.at_level("INFO"):
        res = pipe2._process_one_input(in_dir / "x.pod5")

    assert res.basecall_cached is True
    assert any("basecall served from cache" in m for m in
               (r.message for r in caplog.records))
    assert not any("basecalling" in m for m in (r.message for r in caplog.records))
