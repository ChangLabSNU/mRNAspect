"""A resumed real-time run removes only what a killed stage left unfinished.

Startup used to delete every path under the output folder whose name contained
``.partial``. A finished BAM is named after its POD5, so an input called
``batch.partial-01.pod5`` lost its finished BAM, its index and its basecalls on resume;
with the POD5 archived by then, the report came back with no reads and exit code 0. An
operator's own notes went the same way.

The unfinished outputs are now named with a prefix, ``partial-``, in the cache folder
only. A finished output starts with ``basecall_`` or ``aligned_``, so no input name can
make one look like the other.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from contextlib import closing
from pathlib import Path

import pysam
import pytest

from mrnaspect.main import load_config
from mrnaspect.realtime.main import RealtimePipeline
from mrnaspect.realtime.manifest import IngestManifest, MANIFEST_NAME
from mrnaspect.realtime.mimic import Batch, MinKNOWMimic
from mrnaspect.tests.conftest import pod5_ready
from mrnaspect.wrappers import aligner, basecaller
from mrnaspect.wrappers._shell import PARTIAL_PREFIX, resolve_tool, unfinished_outputs

_WRAPPERS = Path(basecaller.__file__).resolve().parent
_NAME = "batch.partial-01.pod5"


def _cfg(reference: Path) -> dict:
    cfg = pod5_ready(load_config(
        None, {"reference": str(reference), "mode": "pod5", "min_reads": 1}))
    cfg["realtime"] = {
        **cfg.get("realtime", {}),
        "poll_interval_s": 0.02, "stable_polls": 3,
        "seal_poll_interval_s": 0.02, "seal_stable_polls": 3,
        "pod5_subdir": "pod5", "seal_budget_s": 10.0, "interim_every_n": 0,
    }
    return cfg


def _pipe(run_dir: Path, out: Path, reference: Path, fixture_bam: Path,
          calls: list[str]) -> RealtimePipeline:
    """The real driver with stand-in stages that copy a fixture BAM and count calls."""
    def basecall(pod5: Path, out_dir: Path) -> Path:
        calls.append(pod5.name)
        d = Path(out_dir) / f"basecall_{pod5.stem}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture_bam, d / "chunk_0_0.bam")
        return d

    def align(prefix: str, ref: Path, out_dir: Path) -> Path:
        bam = Path(out_dir) / f"aligned_{prefix}.bam"
        shutil.copy(fixture_bam, bam)
        pysam.index(str(bam))
        return bam

    return RealtimePipeline(run_dir, out, _cfg(reference),
                            basecaller=basecall, aligner=align)


def _finished_run(tmp_path: Path, reference: Path, fixture_bam: Path,
                  calls: list[str]) -> tuple[Path, Path, int]:
    """One finished real-time run on a POD5 named ``batch.partial-01.pod5``."""
    run_dir = tmp_path / "run"
    mimic = MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166)],
                         "win_same_disk", speed=400.0)
    mimic.run()
    (run_dir / "pod5" / mimic.pod5_name(0)).rename(run_dir / "pod5" / _NAME)
    out = tmp_path / "out"
    _pipe(run_dir, out, reference, fixture_bam, calls).run()
    reads = _mapped(out)
    assert calls == [_NAME] and reads > 0
    return run_dir, out, reads


def _mapped(out: Path) -> int:
    return json.loads((out / "qc_result.json").read_text())[
        "snapshots"]["run_summary"]["mapping"]["mapped"]


# ---------------------------------------------------------------------------
# The reported case: a finished run, the POD5 archived, then a resume
# ---------------------------------------------------------------------------

def test_a_resume_keeps_a_finished_input_whose_name_contains_partial(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    calls: list[str] = []
    run_dir, out, reads = _finished_run(tmp_path, curlcake_ref, fixture_bam, calls)
    cache = out / "basecall_cache"
    finished = [cache / "aligned_batch.partial-01.bam",
                cache / "aligned_batch.partial-01.bam.bai",
                cache / "basecall_batch.partial-01" / "chunk_0_0.bam"]
    assert all(p.exists() for p in finished)

    # The operator archives the raw data before looking at the report again.
    archive = tmp_path / "archive"
    archive.mkdir()
    (run_dir / "pod5" / _NAME).rename(archive / _NAME)
    stamp = (out / "qc_result.json").stat().st_mtime_ns
    os.utime(out / "qc_result.json", ns=(stamp - 10**9, stamp - 10**9))

    result = _pipe(run_dir, out, curlcake_ref, fixture_bam, calls).run()

    assert all(p.exists() for p in finished), "the resume deleted a finished output"
    assert calls == [_NAME], "the resume needed the basecaller again"
    assert result["status"] == "report"
    assert _mapped(out) == reads
    entries, torn = IngestManifest(out / MANIFEST_NAME, root=out).read()
    assert [e.pod5_name for e in entries] == [_NAME] and not torn
    # The report on disk is the resume's, not the first run's left in place.
    assert (out / "qc_result.json").stat().st_mtime_ns > stamp - 10**9


def test_a_resume_keeps_the_operator_s_own_files(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    calls: list[str] = []
    run_dir, out, _ = _finished_run(tmp_path, curlcake_ref, fixture_bam, calls)
    mine = [out / "operator.partial-notes.txt",
            out / "notes.partial-review" / "note.txt",
            out / "basecall_cache" / "operator.partial-notes.txt",
            out / "partial-notes.txt"]
    for p in mine:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("kept by the operator")

    _pipe(run_dir, out, curlcake_ref, fixture_bam, calls).boot()

    assert [p for p in mine if not p.exists()] == []


# ---------------------------------------------------------------------------
# What a killed stage leaves is still removed, and only that
# ---------------------------------------------------------------------------

def test_what_a_killed_stage_left_is_removed(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    run_dir = tmp_path / "run"
    MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166)],
                 "win_same_disk", speed=400.0).run()
    out = tmp_path / "out"
    cache = out / "basecall_cache"
    debris = [cache / f"{PARTIAL_PREFIX}aligned_x.bam",
              cache / f"{PARTIAL_PREFIX}aligned_x.bam.bai",
              # samtools sort's own temporary files, named after its output
              cache / f"{PARTIAL_PREFIX}aligned_x.bam.tmp.0000.bam",
              cache / f"{PARTIAL_PREFIX}basecall_x" / "chunk_0_0.bam"]
    # A finished basecall of x.partial.pod5: under the old suffix scheme this was also
    # the name of x.pod5's unfinished one.
    finished = cache / "basecall_x.partial" / "chunk_0_0.bam"
    for p in [*debris, finished]:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    assert {p.name for p in unfinished_outputs(cache)} == {
        "partial-aligned_x.bam", "partial-aligned_x.bam.bai",
        "partial-aligned_x.bam.tmp.0000.bam", "partial-basecall_x"}

    _pipe(run_dir, out, curlcake_ref, fixture_bam, []).boot()

    assert [p for p in debris if p.exists()] == []
    assert not (cache / f"{PARTIAL_PREFIX}basecall_x").exists()
    assert finished.exists()


def test_a_folder_that_does_not_exist_holds_nothing_unfinished(tmp_path: Path) -> None:
    assert unfinished_outputs(tmp_path / "absent") == []


# ---------------------------------------------------------------------------
# The wrappers build under the names the cleanup removes
# ---------------------------------------------------------------------------

_STUB_CLIENT = """#!/bin/bash
# Leaves a chunk BAM where -s points, as the real client would, and records the folder.
while [[ $# -gt 0 ]]; do
    if [[ "$1" == "-s" ]]; then cp "$FIXTURE_BAM" "$2/bam_0_0.bam"; echo "$2" > "$SEEN"; fi
    shift
done
"""


def _run_basecall(tmp_path: Path, out: Path, stem: str, fixture_bam: Path) -> Path:
    """run_basecall.sh for ``<stem>.pod5`` against a stand-in client; the folder it used."""
    client = tmp_path / "ont_basecall_client"
    client.write_text(_STUB_CLIENT, encoding="utf-8")
    client.chmod(0o755)
    pod5 = tmp_path / f"{stem}.pod5"
    pod5.write_bytes(b"")
    model = tmp_path / "model"
    model.mkdir(exist_ok=True)
    seen = tmp_path / "seen.txt"
    with closing(socket.socket()) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        env = {**os.environ, "DORADO_CLIENT": str(client), "SEEN": str(seen),
               "FIXTURE_BAM": str(fixture_bam),
               "DORADO_PORT": str(srv.getsockname()[1])}
        subprocess.run(["bash", str(_WRAPPERS / "run_basecall.sh"), str(pod5), stem,
                        str(out), str(model)], env=env, check=True, capture_output=True)
    return Path(seen.read_text().strip())


def test_basecalling_one_input_leaves_another_s_finished_output_alone(
    tmp_path: Path, fixture_bam: Path
) -> None:
    """``x.pod5`` and ``x.partial.pod5`` in one run: neither removes the other's output."""
    out = tmp_path / "cache"
    _run_basecall(tmp_path, out, "x.partial", fixture_bam)
    first = out / "basecall_x.partial" / "bam_0_0.bam"
    assert first.exists()

    used = _run_basecall(tmp_path, out, "x", fixture_bam)

    assert used.name == f"{PARTIAL_PREFIX}basecall_x" and used.parent == out
    assert first.exists(), "basecalling x.pod5 removed x.partial.pod5's finished output"
    assert (out / "basecall_x" / "bam_0_0.bam").exists()
    assert unfinished_outputs(out) == []


def _tools_missing() -> bool:
    try:
        resolve_tool("minimap2")
        resolve_tool("samtools")
    except FileNotFoundError:
        return True
    return False


@pytest.mark.skipif(_tools_missing(), reason="run_align.sh needs minimap2 and samtools")
def test_a_failed_alignment_is_left_under_a_name_the_cleanup_removes(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    ubams = tmp_path / "basecall_broken"
    ubams.mkdir()
    data = fixture_bam.read_bytes()
    (ubams / "chunk_0_0.bam").write_bytes(data[: len(data) // 2])  # cut mid-record

    with pytest.raises(subprocess.CalledProcessError):
        aligner.shell_align("broken", curlcake_ref, tmp_path)

    assert not (tmp_path / "aligned_broken.bam").exists()
    left = {p.name for p in tmp_path.iterdir()} - {
        "basecall_broken", "align_broken.log", "minimap2_broken.log"}
    assert left <= {p.name for p in unfinished_outputs(tmp_path)}, (
        f"a failed alignment left something the cleanup would not remove: {sorted(left)}")


def test_the_wrappers_name_their_unfinished_output_by_prefix() -> None:
    align = (_WRAPPERS / "run_align.sh").read_text()
    call = (_WRAPPERS / "run_basecall.sh").read_text()
    assert f'PARTIAL_BAM="$RESULTS_DIR/{PARTIAL_PREFIX}aligned_${{PREFIX}}.bam"' in align
    assert f'PARTIAL_OUT="$RESULTS_DIR/{PARTIAL_PREFIX}basecall_${{PREFIX}}"' in call
    # No line of either script still builds a name with the old suffix.
    for text in (align, call):
        code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        assert [ln for ln in code if ".partial" in ln] == []
