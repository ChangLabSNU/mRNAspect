"""A resumed POD5 run reuses a cached basecall only for the same file and the same model.

The cache was looked up by the POD5's file name alone. A resume after the file had been
replaced under the same name (same size, other contents) reported the earlier file's
reads, and a resume with another model reported the first model's basecalls while the
result named the second. Both exited 0.

Each cache entry now carries the sha256 of the POD5 and the name and digest of the model
folder. A resume that finds either changed stops before any input is processed, names
what changed, and says to start a fresh run.

The command line, the shell wrappers and the alignment run for real. The ONT client is a
stand-in: it writes the BAM that the stand-in "POD5" names, and logs the model it was
given. A socket that only listens stands in for the server.
"""

from __future__ import annotations

import json
import shutil
import socket
from contextlib import closing
from pathlib import Path

import pysam
import pytest

from mrnaspect.main import load_config, main
from mrnaspect.realtime.main import RealtimePipeline
from mrnaspect.realtime.mimic import Batch, MinKNOWMimic
from mrnaspect.runtime.checkpoint import (
    Checkpoint,
    DEFAULT_STATE_NAME,
    StaleBasecallError,
    file_sha256,
    model_fingerprint,
)
from mrnaspect.tests.conftest import pod5_ready
from mrnaspect.wrappers._shell import resolve_tool

_STUB_CLIENT = """#!/bin/bash
while [[ $# -gt 0 ]]; do
    case "$1" in
        -i) IN=$2; shift ;;
        -s) OUT=$2; shift ;;
        --model) MODEL=$2; shift ;;
    esac
    shift
done
echo "$MODEL" >> "$CALLS"
for f in "$IN"/*.pod5; do cp "$(cat "$f")" "$OUT/bam_0_0.bam"; done
"""


def _tools_missing() -> bool:
    try:
        resolve_tool("minimap2")
        resolve_tool("samtools")
    except FileNotFoundError:
        return True
    return False


needs_tools = pytest.mark.skipif(
    _tools_missing(), reason="the alignment needs minimap2 and samtools")


class Bench:
    """One input folder, two stand-in inputs and models, and a way to run the CLI."""

    def __init__(self, tmp_path: Path, fixture_bam: Path, reference: Path,
                 monkeypatch, port: int) -> None:
        self.tmp, self.reference, self.port = tmp_path, reference, port
        fx = tmp_path / "fx"
        fx.mkdir()
        # Two inputs that give different read counts. Their stand-in POD5s are the two
        # paths, which are the same length: a replacement keeps the file's size.
        self.bam_a, self.bam_b = fx / "a.bam", fx / "b.bam"
        shutil.copy(fixture_bam, self.bam_a)
        with pysam.AlignmentFile(fixture_bam, "rb") as src, \
                pysam.AlignmentFile(self.bam_b, "wb", header=src.header) as dst:
            for i, read in enumerate(src):
                if i < 60:
                    dst.write(read)
        self.in_dir = tmp_path / "in"
        self.in_dir.mkdir()
        self.pod5 = self.in_dir / "batch_0.pod5"
        self.pod5.write_text(str(self.bam_a))
        # Two model folders with the same files under different names.
        self.model_a, self.model_b = tmp_path / "models" / "A", tmp_path / "models" / "B"
        for m in (self.model_a, self.model_b):
            m.mkdir(parents=True)
            (m / "config.toml").write_text("[model]\n")
        client = tmp_path / "ont_basecall_client"
        client.write_text(_STUB_CLIENT, encoding="utf-8")
        client.chmod(0o755)
        self.calls_file = tmp_path / "calls.txt"
        monkeypatch.setenv("DORADO_CLIENT", str(client))
        monkeypatch.setenv("CALLS", str(self.calls_file))
        self.out = tmp_path / "out"

    @property
    def calls(self) -> list[str]:
        """The model of every basecaller call so far."""
        if not self.calls_file.exists():
            return []
        return [Path(m).name for m in self.calls_file.read_text().splitlines()]

    def run(self, name: str | None, model: Path, *, continue_latest: bool = False) -> int:
        args = ["--input-dir", str(self.in_dir), "--output-dir", str(self.out),
                "--reference", str(self.reference), "--mode", "pod5",
                "--basecall-model", str(model), "--min-reads", "1",
                "--dorado-port", str(self.port), "--no-dorado-autostart"]
        if name:
            args += ["--output-name", name]
        if continue_latest:
            args += ["--continue-latest"]
        return main(args)

    def mapped(self, name: str) -> int:
        doc = json.loads((self.out / name / "qc_result.json").read_text())
        return doc["snapshots"]["run_summary"]["mapping"]["mapped"]


@pytest.fixture
def bench(tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, monkeypatch):
    with closing(socket.socket()) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(64)
        yield Bench(tmp_path, fixture_bam, curlcake_ref, monkeypatch,
                    srv.getsockname()[1])


# ---------------------------------------------------------------------------
# The three reported cases, each on its own
# ---------------------------------------------------------------------------

@needs_tools
def test_an_unchanged_resume_reuses_the_cached_basecall(bench: Bench) -> None:
    assert bench.run("r1", bench.model_a) == 0
    reads = bench.mapped("r1")
    assert bench.calls == ["A"] and reads > 0

    assert bench.run("r1", bench.model_a) == 0

    assert bench.calls == ["A"], "an unchanged resume basecalled again"
    assert bench.mapped("r1") == reads


@needs_tools
@pytest.mark.parametrize("how", ["output-name", "continue-latest"])
def test_a_replaced_input_stops_the_resume(bench: Bench, capsys, how: str) -> None:
    name = "r1" if how == "output-name" else None
    assert bench.run(name, bench.model_a) == 0
    run_name = name or next(p.name for p in bench.out.iterdir())
    reads_a = bench.mapped(run_name)
    size = bench.pod5.stat().st_size
    report = (bench.out / run_name / "qc_result.json").read_bytes()
    capsys.readouterr()

    bench.pod5.write_text(str(bench.bam_b))  # same name, same size, other contents
    assert bench.pod5.stat().st_size == size
    code = bench.run(name, bench.model_a, continue_latest=(name is None))

    said = capsys.readouterr().out
    assert code != 0
    assert bench.calls == ["A"]
    assert "batch_0.pod5" in said and "changed" in said and "fresh run" in said
    # The report of the first run is left as it was.
    assert (bench.out / run_name / "qc_result.json").read_bytes() == report

    # A fresh run reads the replacement.
    assert bench.run("fresh", bench.model_a) == 0
    assert bench.calls == ["A", "A"]
    assert bench.mapped("fresh") != reads_a


@needs_tools
def test_a_changed_model_stops_the_resume(bench: Bench, capsys) -> None:
    assert bench.run("r1", bench.model_a) == 0
    reads = bench.mapped("r1")
    capsys.readouterr()

    code = bench.run("r1", bench.model_b)

    said = capsys.readouterr().out
    assert code != 0
    assert bench.calls == ["A"]
    assert "model" in said and "fresh run" in said
    assert "'A'" in said and "'B'" in said

    # A fresh run with the other model basecalls with it.
    assert bench.run("fresh", bench.model_b) == 0
    assert bench.calls == ["A", "B"]
    assert bench.mapped("fresh") == reads


# ---------------------------------------------------------------------------
# What counts as the same model, and entries with nothing to check against
# ---------------------------------------------------------------------------

@needs_tools
def test_a_model_folder_whose_files_changed_stops_the_resume(bench: Bench, capsys) -> None:
    assert bench.run("r1", bench.model_a) == 0
    capsys.readouterr()

    (bench.model_a / "config.toml").write_text("[model]\noverlap = 2500\n")
    code = bench.run("r1", bench.model_a)

    said = capsys.readouterr().out
    assert code != 0 and bench.calls == ["A"]
    assert "'A'" in said and "files have changed" in said and "fresh run" in said


@needs_tools
def test_a_model_folder_that_only_moved_is_the_same_model(bench: Bench) -> None:
    assert bench.run("r1", bench.model_a) == 0
    moved = bench.tmp / "elsewhere" / "A"
    moved.parent.mkdir()
    bench.model_a.rename(moved)

    assert bench.run("r1", moved) == 0

    assert bench.calls == ["A"]


@needs_tools
def test_an_entry_with_nothing_to_check_against_is_basecalled_again(
    bench: Bench
) -> None:
    """A cache written before entries carried a fingerprint: neither trusted nor an error."""
    assert bench.run("r1", bench.model_a) == 0
    state = bench.out / "r1" / DEFAULT_STATE_NAME
    name, ubam = state.read_text().splitlines()[0].split("\t")[:2]
    state.write_text(f"{name}\t{ubam}\n")

    assert bench.run("r1", bench.model_a) == 0

    assert bench.calls == ["A", "A"]
    assert "cannot be checked" in (bench.out / "r1" / "run.log").read_text()
    # The new entry can be checked, so a third run reuses it.
    assert bench.run("r1", bench.model_a) == 0
    assert bench.calls == ["A", "A"]


# ---------------------------------------------------------------------------
# The cache itself
# ---------------------------------------------------------------------------

def _entry(tmp_path: Path) -> tuple[Checkpoint, Path, tuple[str, str]]:
    pod5 = tmp_path / "x.pod5"
    pod5.write_bytes(b"signal")
    ubam = tmp_path / "basecall_x"
    ubam.mkdir()
    model = tmp_path / "hac"
    model.mkdir()
    (model / "weights").write_bytes(b"w")
    fp = model_fingerprint(model)
    ckpt = Checkpoint(tmp_path / "cache.tsv")
    ckpt.record("x.pod5", ubam, input_sha256=file_sha256(pod5), model=fp)
    return Checkpoint(tmp_path / "cache.tsv"), pod5, fp


def test_a_matching_entry_is_served(tmp_path: Path) -> None:
    ckpt, pod5, fp = _entry(tmp_path)
    assert ckpt.verified("x.pod5", input_path=pod5, model=fp) == tmp_path / "basecall_x"


def test_a_changed_file_is_named(tmp_path: Path) -> None:
    ckpt, pod5, fp = _entry(tmp_path)
    pod5.write_bytes(b"SIGNAL")
    with pytest.raises(StaleBasecallError, match="x.pod5.*changed"):
        ckpt.verified("x.pod5", input_path=pod5, model=fp)


def test_a_changed_model_is_named(tmp_path: Path) -> None:
    ckpt, pod5, _ = _entry(tmp_path)
    other = tmp_path / "sup"
    other.mkdir()
    with pytest.raises(StaleBasecallError, match="'hac'.*'sup'"):
        ckpt.verified("x.pod5", input_path=pod5, model=model_fingerprint(other))


def test_a_model_given_by_name_is_compared_by_name(tmp_path: Path) -> None:
    """A value that is no folder here is a name the server resolves itself."""
    assert model_fingerprint("rna004_130bps_hac@v5.3.0") == (
        "rna004_130bps_hac@v5.3.0", "-")


def test_the_model_digest_covers_names_and_contents(tmp_path: Path) -> None:
    a, b = tmp_path / "1" / "m", tmp_path / "2" / "m"
    for m in (a, b):
        (m / "sub").mkdir(parents=True)
        (m / "sub" / "w.bin").write_bytes(b"w")
    assert model_fingerprint(a) == model_fingerprint(b)
    (b / "sub" / "w.bin").rename(b / "sub" / "v.bin")
    assert model_fingerprint(a) != model_fingerprint(b)


def test_a_file_manager_s_own_files_do_not_change_the_model(tmp_path: Path) -> None:
    """Finder writes .DS_Store into a folder it has opened. Counted in the digest, that
    made an unchanged model read as changed: a run could not be continued after its
    model folder had been looked at."""
    ckpt, pod5, fp = _entry(tmp_path)
    model = tmp_path / "hac"
    (model / ".DS_Store").write_bytes(b"Bud1")
    (model / "._weights").write_bytes(b"resource fork")
    (model / "Thumbs.db").write_bytes(b"thumbs")
    (model / "__MACOSX").mkdir()
    (model / "__MACOSX" / "._weights").write_bytes(b"x")
    assert model_fingerprint(model) == fp
    assert ckpt.verified("x.pod5", input_path=pod5, model=model_fingerprint(model)) \
        == tmp_path / "basecall_x"
    # One of the model's own files changing is still seen.
    (model / "weights").write_bytes(b"other")
    with pytest.raises(StaleBasecallError, match="files have changed"):
        ckpt.verified("x.pod5", input_path=pod5, model=model_fingerprint(model))


def test_a_line_cut_inside_its_last_field_is_a_miss(tmp_path: Path) -> None:
    """A kill mid-append can leave any prefix of a line: basecalled again, not an error."""
    ckpt, pod5, fp = _entry(tmp_path)
    state = tmp_path / "cache.tsv"
    state.write_text(state.read_text().rstrip("\n")[:-5])
    assert Checkpoint(state).verified("x.pod5", input_path=pod5, model=fp) is None


# ---------------------------------------------------------------------------
# The live driver restores from its manifest, so the model is checked there too
# ---------------------------------------------------------------------------

def _live(run_dir: Path, out: Path, reference: Path, fixture_bam: Path,
          model: Path, calls: list[str]) -> RealtimePipeline:
    def basecall(pod5: Path, out_dir: Path) -> Path:
        calls.append(pod5.name)
        d = Path(out_dir) / f"basecall_{pod5.stem}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture_bam, d / "chunk_0_0.bam")
        return d

    def align(prefix: str, ref: Path, out_dir: Path) -> Path:
        bam = Path(out_dir) / f"aligned_{prefix}.bam"
        shutil.copy(fixture_bam, bam)
        return bam

    cfg = pod5_ready(load_config(
        None, {"reference": str(reference), "mode": "pod5", "min_reads": 1}))
    cfg["basecall"]["model"] = str(model)
    cfg["realtime"] = {
        **cfg.get("realtime", {}),
        "poll_interval_s": 0.02, "stable_polls": 3,
        "seal_poll_interval_s": 0.02, "seal_stable_polls": 3,
        "pod5_subdir": "pod5", "seal_budget_s": 10.0, "interim_every_n": 0,
    }
    return RealtimePipeline(run_dir, out, cfg, basecaller=basecall, aligner=align)


def test_a_live_run_resumed_with_another_model_stops(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    run_dir, out = tmp_path / "run", tmp_path / "out"
    MinKNOWMimic(run_dir, [Batch(size_bytes=256, n_reads=166)],
                 "win_same_disk", speed=400.0).run()
    models = [tmp_path / "A", tmp_path / "B"]
    for m in models:
        m.mkdir()
    calls: list[str] = []
    _live(run_dir, out, curlcake_ref, fixture_bam, models[0], calls).run()
    assert len(calls) == 1

    with pytest.raises(StaleBasecallError, match="'A'.*'B'"):
        _live(run_dir, out, curlcake_ref, fixture_bam, models[1], calls).run()
    assert len(calls) == 1

    # With the model it was started with, the same folder resumes.
    result = _live(run_dir, out, curlcake_ref, fixture_bam, models[0], calls).run()
    assert result["status"] == "report" and len(calls) == 1


# ---------------------------------------------------------------------------
# A cached input that can no longer be read is that input's failure, not the run's
# ---------------------------------------------------------------------------

def test_a_cached_input_that_cannot_be_read_follows_on_error(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The check before the run reads every cached POD5. One that cannot be read is left
    to the input loop, where ``on_error: skip`` names it and carries on."""
    import os

    from mrnaspect.main import OfflinePipeline

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for name in ("a.pod5", "b.pod5"):
        (in_dir / name).write_bytes(name.encode())
    calls: list[str] = []

    def basecall(pod5: Path, out_dir: Path) -> Path:
        calls.append(pod5.name)
        d = Path(out_dir) / f"basecall_{pod5.stem}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture_bam, d / "chunk_0_0.bam")
        return d

    def align(prefix: str, ref: Path, out_dir: Path) -> Path:
        bam = Path(out_dir) / f"aligned_{prefix}.bam"
        shutil.copy(fixture_bam, bam)
        return bam

    def pipe() -> OfflinePipeline:
        cfg = pod5_ready(load_config(None, {
            "reference": str(curlcake_ref), "mode": "pod5", "min_reads": 1,
            "on_error": "skip"}))
        return OfflinePipeline(in_dir, tmp_path / "out", cfg,
                               basecaller=basecall, aligner=align)

    pipe().run()
    assert calls == ["a.pod5", "b.pod5"]

    os.chmod(in_dir / "a.pod5", 0)
    if os.access(in_dir / "a.pod5", os.R_OK):
        pytest.skip("this account reads a file whatever its mode")
    try:
        second = pipe()
        second.run()
    finally:
        os.chmod(in_dir / "a.pod5", 0o644)

    assert second._failed_inputs == ["a.pod5"]
    assert calls == ["a.pod5", "b.pod5"], "b.pod5's cached basecall was not reused"
    assert second._n_basecall_cached == 1
