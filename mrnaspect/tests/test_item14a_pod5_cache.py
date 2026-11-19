"""Item 14a/14b integration — pod5-mode: checkpoint the ubam, skip re-basecall on restart.

Drives the real OfflinePipeline in pod5 mode with injected fakes (no GPU/server). Basecall and
align are two separate stages: the fake basecaller writes a ubam dir and the fake aligner copies the
fixture BAM out of it. The driver checkpoints the UBAM (the expensive GPU output): the first run
basecalls + records; a restart into the same output dir reuses the cached ubam (basecaller NOT called
again) but ALWAYS re-aligns (aligner runs both times). Ingestion always runs (H2), so the report is
complete either way.
"""

from __future__ import annotations

import functools
import shutil
from pathlib import Path

from mrnaspect.tests.conftest import pod5_ready
from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.wrappers.basecaller import shell_basecall
from mrnaspect.wrappers.aligner import available_cpus, shell_align


def _pod5_input_dir(tmp_path: Path) -> Path:
    d = tmp_path / "input"
    d.mkdir()
    (d / "sample_0.pod5").write_bytes(b"")  # content unused; the glob just needs the file
    return d


def _make_fakes(fixture_bam: Path, bc_calls: list[str], al_calls: list[str]):
    """Return (fake_basecaller, fake_aligner). Basecaller writes a ubam dir; aligner copies the
    fixture BAM to aligned_<prefix>.bam. Both log their calls so the test can assert cache behaviour."""

    def fake_basecall(pod5_path: Path, out_dir: Path) -> Path:
        bc_calls.append(pod5_path.name)
        ubam_dir = Path(out_dir) / f"basecall_{pod5_path.stem}"
        ubam_dir.mkdir(parents=True, exist_ok=True)
        (ubam_dir / "chunk_0.bam").write_bytes(b"stub-ubam")  # stand-in for the server chunks
        return ubam_dir

    def fake_align(prefix: str, reference: Path, out_dir: Path) -> Path:
        al_calls.append(prefix)
        bam = Path(out_dir) / f"aligned_{prefix}.bam"
        shutil.copy(fixture_bam, bam)  # a real BAM the engine can ingest
        return bam

    return fake_basecall, fake_align


def test_pod5_restart_reuses_ubam_but_realigns(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    in_dir = _pod5_input_dir(tmp_path)
    out_dir = tmp_path / "out"
    cfg = pod5_ready(load_config(
        None, {"reference": str(curlcake_ref), "mode": "pod5", "min_reads": 1}))

    # First run: cache miss -> basecall once (records the ubam) and align once.
    bc1: list[str] = []
    al1: list[str] = []
    bc, al = _make_fakes(fixture_bam, bc1, al1)
    OfflinePipeline(in_dir, out_dir, cfg, basecaller=bc, aligner=al).run()
    assert bc1 == ["sample_0.pod5"] and al1 == ["sample_0"]

    # Restart into the SAME out_dir: cached ubam exists -> basecaller must NOT run again, but the
    # aligner DOES (align always re-runs from the ubam).
    bc2: list[str] = []
    al2: list[str] = []
    bc, al = _make_fakes(fixture_bam, bc2, al2)
    pipe2 = OfflinePipeline(in_dir, out_dir, cfg, basecaller=bc, aligner=al)
    pipe2.run()
    assert bc2 == []                          # no re-basecall (ubam served from cache)
    assert al2 == ["sample_0"]                # but align re-ran
    assert pipe2._n_basecall_cached == 1      # counted as a basecall-cache hit (15d)
    assert pipe2._n_processed == 1            # input still ingested (H2)


def test_default_stages_bind_config_model_and_flags(
    tmp_path: Path, curlcake_ref: Path
) -> None:
    """14c/14d: with no injected stages, the defaults are shell_basecall / shell_align partial-bound
    with the config's basecall.model and align.minimap2_flags (so neither is hardcoded)."""
    # pod5 mode has no defaults for the dorado client or model (bucket A-3); stubs keep
    # this hermetic, since what is under test is the BINDING, not where dorado lives.
    cfg = pod5_ready(load_config(
        None, {"reference": str(curlcake_ref), "mode": "pod5", "min_reads": 1}))
    pipe = OfflinePipeline(tmp_path / "in", tmp_path / "out", cfg)
    assert isinstance(pipe.basecaller, functools.partial) and pipe.basecaller.func is shell_basecall
    # `client` / `server_binary` joined in 2026-09-19 (bucket A-3): dorado cannot be
    # bundled, so its location is configuration too. `client` is the RESOLVED absolute path
    # because pod5 mode checks it at construction rather than at the first POD5.
    assert pipe.basecaller.keywords == {
        "model": cfg["basecall"]["model"],
        "client": pipe.basecaller.keywords["client"],
        "server_binary": cfg["basecall"]["server"]["binary"],
        "emit_moves": cfg["basecall"]["emit_moves"],
    }
    assert Path(pipe.basecaller.keywords["client"]).exists(), "client must be resolved"
    assert isinstance(pipe.aligner, functools.partial) and pipe.aligner.func is shell_align
    assert pipe.aligner.keywords == {"align_flags": cfg["align"]["minimap2_flags"],
                                     "emit_moves": cfg["basecall"]["emit_moves"],
                                     "threads": available_cpus()}
