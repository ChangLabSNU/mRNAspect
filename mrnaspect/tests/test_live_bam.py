"""A live run can follow the BAM files MinKNOW basecalls itself.

The offline BAM driver lists its files once at startup, and the live driver followed
POD5 files only: an operator working from MinKNOW's BAM output had to start a new
offline run to see later batches. With ``mode: bam`` the live driver follows
``bam_pass/`` and ``bam_fail/`` of the run folder, processes each BAM once its writer has
closed it, keeps the cumulative QC, and on a restart counts each finished batch once.

The driver, the alignment (minimap2, samtools) and the report are the real ones. The
"sequencer" is a thread that writes real BAM batches into the run folder, in the order
and at the moments each test needs.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pysam
import pytest

from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.realtime import main as rt
from mrnaspect.realtime.main import RealtimePipeline
from mrnaspect.realtime.manifest import IngestManifest, MANIFEST_NAME
from mrnaspect.realtime.sealing import read_sequencing_summary
from mrnaspect.realtime.watcher import BGZF_EOF, DirectoryWatcher, bam_is_complete
from mrnaspect.tests.golden_compare import canonicalize, diff_docs
from mrnaspect.wrappers._shell import resolve_tool
from mrnaspect.wrappers.aligner import shell_align

RUN = "20261112_1434_MN12345_FBA12345_1a2b3c4d"
#: (folder, file name, first read, one past the last read) of the fixture's 166 reads.
BATCHES = [("bam_pass", "FBA12345_pass_1a2b3c4d_0361a8e5_0.bam", 0, 60),
           ("bam_fail", "FBA12345_fail_1a2b3c4d_0361a8e5_0.bam", 60, 90),
           ("bam_pass", "FBA12345_pass_1a2b3c4d_0361a8e5_1.bam", 90, 140),
           ("bam_fail", "FBA12345_fail_1a2b3c4d_0361a8e5_1.bam", 140, 166)]
MARKER = "final_summary_FBA12345_1a2b3c4d_0361a8e5.txt"


def _tools_missing() -> bool:
    try:
        resolve_tool("minimap2")
        resolve_tool("samtools")
    except FileNotFoundError:
        return True
    return False


pytestmark = pytest.mark.skipif(
    _tools_missing(), reason="the alignment needs minimap2 and samtools")


@pytest.fixture(scope="module")
def batches(tmp_path_factory, fixture_bam: Path) -> dict[str, Path]:
    """The four BAM batches, written once, to be copied into a run folder when due."""
    store = tmp_path_factory.mktemp("batches")
    with pysam.AlignmentFile(fixture_bam, "rb") as src:
        reads = list(src)
        assert len(reads) == BATCHES[-1][3]
        for _, name, start, stop in BATCHES:
            with pysam.AlignmentFile(store / name, "wb", header=src.header) as out:
                for read in reads[start:stop]:
                    out.write(read)
    return {name: store / name for _, name, _, _ in BATCHES}


def _deliver(run: Path, batches: dict[str, Path], index: int) -> Path:
    """Put one finished batch into its folder, whole, as a rename does."""
    folder, name, _, _ = BATCHES[index]
    (run / folder).mkdir(parents=True, exist_ok=True)
    staged = run / f".{name}.staged"
    shutil.copy(batches[name], staged)
    os.replace(staged, run / folder / name)
    return run / folder / name


def _cfg(reference: Path, **realtime) -> dict:
    cfg = load_config(None, {"reference": str(reference), "mode": "bam", "min_reads": 1})
    cfg["realtime"] = {
        **cfg.get("realtime", {}),
        "poll_interval_s": 0.05, "stable_polls": 3,
        "seal_poll_interval_s": 0.05, "seal_stable_polls": 3,
        "seal_budget_s": 20.0, "interim_every_n": 1, "progress_interval_s": 0,
        **realtime,
    }
    return cfg


def _pipe(run: Path, out: Path, reference: Path, aligned: list[str] | None = None,
          **realtime) -> RealtimePipeline:
    """The real driver. ``aligned`` collects each alignment the real aligner is asked for."""
    cfg = _cfg(reference, **realtime)

    def align(prefix: str, ref: Path, out_dir: Path) -> Path:
        if aligned is not None:
            aligned.append(prefix)
        return shell_align(prefix, ref, out_dir,
                           align_flags=cfg.get("align", {}).get("minimap2_flags"))

    return RealtimePipeline(run, out, cfg, aligner=align)


def _when(condition, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("the live run did not get there in time")
        time.sleep(0.02)


def _result(out: Path) -> dict:
    return json.loads((out / "qc_result.json").read_text())


def _mapping(out: Path) -> dict:
    return _result(out)["snapshots"]["run_summary"]["mapping"]


def _offline(run: Path, out: Path, reference: Path) -> dict:
    """The offline BAM analysis of the same run folder: both of its BAM folders."""
    OfflinePipeline(run, out, load_config(
        None, {"reference": str(reference), "mode": "bam", "min_reads": 1})).run()
    return _result(out)


def _numbers(doc: dict) -> dict:
    """A result with what legitimately differs between the two drivers left out.

    The bookkeeping of each driver, and the order the modules were first seen in: a
    restart restores the reads before it looks for the instrument's files, so the same
    modules are listed in another order. Every module's own numbers are compared.
    """
    doc = canonicalize(doc)
    for key in ("run_accounting", "n_inputs"):
        doc.pop(key, None)
    if isinstance(doc.get("modules"), list):
        doc["modules"] = sorted(doc["modules"])
    return doc


# ---------------------------------------------------------------------------
# Batches that arrive one after another, from both folders
# ---------------------------------------------------------------------------

def test_staggered_batches_from_both_folders_give_the_offline_result(
        tmp_path: Path, batches, curlcake_ref: Path) -> None:
    """Each batch is delivered only once the one before it has been taken in, the two
    folders alternating; the marker comes last. Every batch is aligned once, the totals
    grow batch by batch, and the final numbers are the offline analysis's of the same
    files."""
    run, out = tmp_path / "data" / "exp" / "sample" / RUN, tmp_path / "live"
    run.mkdir(parents=True)
    _deliver(run, batches, 0)                      # there before QC starts
    aligned: list[str] = []
    pipe = _pipe(run, out, curlcake_ref, aligned)
    totals: list[int] = []
    feed_error: list[BaseException] = []

    def sequencer() -> None:
        try:
            for index in range(len(BATCHES)):
                name = BATCHES[index][1]
                if index:
                    _deliver(run, batches, index)
                _when(lambda: name in pipe.progress.ingested)
                totals.append(pipe.core.engine.primary_total)
            (run / MARKER).write_text("acquisition_stopped=2026-11-12T16:00:00\n")
        except BaseException as exc:    # shown by the test, not lost in the thread
            feed_error.append(exc)
            (run / MARKER).write_text("stopped by the test\n")

    thread = threading.Thread(target=sequencer, daemon=True)
    thread.start()
    result = pipe.run()
    thread.join(timeout=30)
    assert not feed_error, feed_error

    assert result["status"] == "report"
    assert pipe.progress.ingested == {name for _, name, _, _ in BATCHES}
    assert totals == [60, 90, 140, 166], "the count grows by each batch, cumulatively"
    assert sorted(aligned) == sorted(Path(name).stem for _, name, _, _ in BATCHES), (
        "each batch is aligned once, and none again for a later update")
    assert _mapping(out)["total"] == 166

    offline = _offline(run, tmp_path / "offline", curlcake_ref)
    assert diff_docs(_numbers(offline), _numbers(_result(out))) == [], (
        "the live BAM run and the offline BAM analysis disagree on QC numbers")
    # The report says what it read: both folders, and that it followed them live.
    page = (out / "report_latest.html").read_text(encoding="utf-8")
    assert "2 in bam_pass/, 2 in bam_fail/" in page and "bam (real time)" in page


def test_a_run_that_ended_before_qc_started_is_read_whole(
        tmp_path: Path, batches, curlcake_ref: Path) -> None:
    """Every batch and the marker are there at the start: the final batches of both
    folders are taken in at run end, and nothing waits."""
    run, out = tmp_path / "run", tmp_path / "out"
    run.mkdir()
    for index in range(len(BATCHES)):
        _deliver(run, batches, index)
    (run / MARKER).write_text("x")
    pipe = _pipe(run, out, curlcake_ref)
    assert pipe.run()["status"] == "report"
    assert len(pipe.progress.ingested) == 4 and _mapping(out)["total"] == 166


# ---------------------------------------------------------------------------
# A BAM that is still being written
# ---------------------------------------------------------------------------

def test_a_finished_bam_ends_with_the_end_of_file_block(tmp_path: Path, batches) -> None:
    whole = next(iter(batches.values()))
    assert whole.read_bytes().endswith(BGZF_EOF) and bam_is_complete(whole)
    cut = tmp_path / "cut.bam"
    cut.write_bytes(whole.read_bytes()[:-len(BGZF_EOF)])     # every read, no ending
    assert not bam_is_complete(cut)
    assert not bam_is_complete(tmp_path / "absent.bam")
    (tmp_path / "tiny.bam").write_bytes(b"\x1f\x8b")
    assert not bam_is_complete(tmp_path / "tiny.bam")


def test_the_watcher_holds_a_quiet_unfinished_bam_until_it_is_closed(
        tmp_path: Path, batches) -> None:
    """Size alone would release it: a writer that pauses leaves a quiet, half-written
    file, and such a file reads as a shorter BAM."""
    whole = next(iter(batches.values())).read_bytes()
    folder = tmp_path / "run" / "bam_pass"
    folder.mkdir(parents=True)
    growing = folder / "x_pass_0.bam"
    growing.write_bytes(whole[:len(whole) // 2])
    ready: list[str] = []
    watcher = DirectoryWatcher(tmp_path / "run", pod5_subdir="bam_pass", pattern="*.bam",
                               on_ready=lambda e: ready.append(e.path.name),
                               poll_interval_s=0.01, stable_polls=2,
                               is_complete=bam_is_complete)
    for _ in range(6):
        watcher.scan_once()
    assert ready == [], "a BAM with no end-of-file block was released"
    growing.write_bytes(whole)
    for _ in range(3):
        watcher.scan_once()
    assert ready == ["x_pass_0.bam"]


def test_a_bam_still_being_written_is_taken_in_once_it_is_finished(
        tmp_path: Path, batches, curlcake_ref: Path) -> None:
    """The second batch appears half written and stays so while the first is processed
    and well past the stability window. It is not read until its writer finishes it, and
    then its reads count once."""
    run, out = tmp_path / "run", tmp_path / "out"
    run.mkdir()
    _deliver(run, batches, 0)
    folder, name, _, _ = BATCHES[1]
    (run / folder).mkdir()
    whole = batches[name].read_bytes()
    growing = run / folder / name
    growing.write_bytes(whole[:len(whole) // 2])
    pipe = _pipe(run, out, curlcake_ref)
    seen_while_growing: list[set[str]] = []
    feed_error: list[BaseException] = []

    def sequencer() -> None:
        try:
            _when(lambda: BATCHES[0][1] in pipe.progress.ingested)
            time.sleep(1.0)                 # twenty polls: far beyond "stable"
            seen_while_growing.append(set(pipe.progress.ingested) | set(pipe.progress.failed))
            growing.write_bytes(whole)
            _when(lambda: name in pipe.progress.ingested)
            (run / MARKER).write_text("x")
        except BaseException as exc:
            feed_error.append(exc)
            (run / MARKER).write_text("stopped by the test\n")

    thread = threading.Thread(target=sequencer, daemon=True)
    thread.start()
    result = pipe.run()
    thread.join(timeout=30)
    assert not feed_error, feed_error
    assert seen_while_growing == [{BATCHES[0][1]}], "the unfinished BAM was read early"
    assert result["status"] == "report" and _mapping(out)["total"] == 90


def test_a_bam_never_finished_is_named_and_not_counted(
        tmp_path: Path, batches, curlcake_ref: Path) -> None:
    """At run end a BAM whose writer never closed it is left out, and the result says
    which: a report may be partial, never silently."""
    run, out = tmp_path / "run", tmp_path / "out"
    run.mkdir()
    _deliver(run, batches, 0)
    folder, name, _, _ = BATCHES[1]
    (run / folder).mkdir()
    whole = batches[name].read_bytes()
    (run / folder / name).write_bytes(whole[:len(whole) // 2])
    (run / MARKER).write_text("x")
    pipe = _pipe(run, out, curlcake_ref, seal_budget_s=1.0)
    assert pipe.run()["status"] == "report"
    assert pipe.progress.ingested == {BATCHES[0][1]} and _mapping(out)["total"] == 60
    assert name in pipe.progress.unstable
    assert name in json.dumps(_result(out)["run_accounting"])


# ---------------------------------------------------------------------------
# A restart
# ---------------------------------------------------------------------------

def test_a_restart_counts_each_finished_batch_once(
        tmp_path: Path, batches, curlcake_ref: Path) -> None:
    """Two batches are taken in and the driver is gone (killed: no seal, no report). A
    new one in the same output folder restores them from their aligned BAMs without
    aligning them again, takes in the two that came meanwhile, and ends on the numbers
    of the offline analysis."""
    run, out = tmp_path / "run", tmp_path / "out"
    run.mkdir()
    first = _pipe(run, out, curlcake_ref)
    first.boot()
    first.replay()
    for index in (0, 1):
        assert first._handle(_deliver(run, batches, index)) is not None
    assert first.core.engine.primary_total == 90

    for index in (2, 3):
        _deliver(run, batches, index)
    (run / MARKER).write_text("x")
    aligned: list[str] = []
    second = _pipe(run, out, curlcake_ref, aligned)
    result = second.run()

    assert result["status"] == "report"
    assert sorted(aligned) == sorted(Path(BATCHES[i][1]).stem for i in (2, 3)), (
        "a batch finished before the restart was aligned again")
    assert second.progress.ingested == {name for _, name, _, _ in BATCHES}
    assert _mapping(out)["total"] == 166
    entries, torn = IngestManifest(out / MANIFEST_NAME, root=out).read()
    assert sorted(e.pod5_name for e in entries) == sorted(n for _, n, _, _ in BATCHES)
    assert not torn
    offline = _offline(run, tmp_path / "offline", curlcake_ref)
    assert diff_docs(_numbers(offline), _numbers(_result(out))) == []


# ---------------------------------------------------------------------------
# Barcodes and samples stay apart
# ---------------------------------------------------------------------------

def test_a_barcoded_run_is_refused_unless_one_barcode_is_named(
        tmp_path: Path, batches, curlcake_ref: Path) -> None:
    run = tmp_path / "run"
    for barcode, index in (("barcode01", 0), ("barcode02", 2)):
        folder, name, _, _ = BATCHES[index]
        (run / folder / barcode).mkdir(parents=True)
        shutil.copy(batches[name], run / folder / barcode / name)
    folder, name, _, _ = BATCHES[1]
    (run / folder / "barcode01").mkdir(parents=True)
    shutil.copy(batches[name], run / folder / "barcode01" / name)
    (run / MARKER).write_text("x")

    with pytest.raises(ValueError, match="barcoded run.*barcode01.*--barcode"):
        _pipe(run, tmp_path / "pooled", curlcake_ref).run()

    one = _pipe(run, tmp_path / "one", curlcake_ref, barcode="barcode01")
    assert one.run()["status"] == "report"
    # Its passed and its failed reads, and nothing of barcode02.
    assert one.progress.ingested == {BATCHES[0][1], BATCHES[1][1]}
    assert _mapping(tmp_path / "one")["total"] == 90
    # The report's input line names the folders the files are in. It named
    # <run>/barcode01, which does not exist.
    assert one._inputs_text() == (
        f"2 BAM from {run}, barcode barcode01: 1 in bam_pass/barcode01/, "
        f"1 in bam_fail/barcode01/")


def test_two_files_of_one_name_in_the_two_folders_stop_the_run(
        tmp_path: Path, batches, curlcake_ref: Path) -> None:
    run = tmp_path / "run"
    _deliver(run, batches, 0)
    (run / "bam_fail").mkdir()
    shutil.copy(batches[BATCHES[1][1]], run / "bam_fail" / BATCHES[0][1])
    (run / MARKER).write_text("x")
    with pytest.raises(ValueError, match="is in both"):
        _pipe(run, tmp_path / "out", curlcake_ref).run()


def test_a_barcode_is_a_folder_name_of_a_bam_run(tmp_path: Path, curlcake_ref: Path) -> None:
    (tmp_path / "run").mkdir()
    with pytest.raises(ValueError, match="realtime.barcode"):
        _pipe(tmp_path / "run", tmp_path / "out", curlcake_ref, barcode="../elsewhere")


# ---------------------------------------------------------------------------
# The command line, and MinKNOW's own count of the reads
# ---------------------------------------------------------------------------

def test_the_command_line_follows_bam_files_and_starts_no_basecall_server(
        tmp_path: Path, batches, curlcake_ref: Path, monkeypatch, capsys) -> None:
    run = tmp_path / "run"
    for index in range(len(BATCHES)):
        _deliver(run, batches, index)
    (run / MARKER).write_text("x")
    fast = tmp_path / "fast.yaml"
    fast.write_text("realtime: {poll_interval_s: 0.05, stable_polls: 3, "
                    "seal_poll_interval_s: 0.05, seal_stable_polls: 3}\n")

    def no_server(*args, **kwargs):
        raise AssertionError("a BAM run started the basecall server")

    monkeypatch.setattr(rt, "dorado_server", no_server)
    code = rt.main(["--input-dir", str(run), "--output-dir", str(tmp_path / "out"),
                    "--output-name", "r", "--mode", "bam", "--config", str(fast),
                    "--reference", str(curlcake_ref), "--min-reads", "1"])
    emitted = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and emitted["exit_code"] == 0 and emitted["inputs_processed"] == 4
    assert _mapping(tmp_path / "out" / "r")["total"] == 166


def test_minknow_s_count_of_each_bam_s_reads_is_read_from_its_own_column(
        tmp_path: Path) -> None:
    """``filename_bam`` names the BAM each read went into; a read MinKNOW wrote to no
    BAM has ``-`` there (seen on a real run, for the reads it failed)."""
    (tmp_path / "sequencing_summary_FBA_x.txt").write_text(
        "filename_pod5\tfilename_bam\tread_id\n"
        "a_0.pod5\tFBA_pass_x_0.bam\tr1\n"
        "a_0.pod5\tFBA_pass_x_0.bam\tr2\n"
        "a_0.pod5\tFBA_fail_x_0.bam\tr3\n"
        "a_0.pod5\t-\tr4\n")
    by_bam = read_sequencing_summary(tmp_path, column="filename_bam")
    assert by_bam.reads_per_pod5 == {"FBA_pass_x_0.bam": 2, "FBA_fail_x_0.bam": 1}
    assert read_sequencing_summary(tmp_path).reads_per_pod5 == {"a_0.pod5": 4}


# ---------------------------------------------------------------------------
# One of the two folders, by itself
# ---------------------------------------------------------------------------

def _summary(run: Path) -> None:
    """MinKNOW's sequencing summary for the four batches: each read's BAM, and whether
    MinKNOW passed it (TRUE for bam_pass/, as on a real run)."""
    rows = ["filename_pod5\tfilename_bam\tread_id\tpasses_filtering"]
    for folder, name, start, stop in BATCHES:
        verdict = "TRUE" if folder == "bam_pass" else "FALSE"
        rows += [f"x_0.pod5\t{name}\tr{i}\t{verdict}" for i in range(start, stop)]
    (run / "sequencing_summary_FBA12345_1a2b3c4d_0361a8e5.txt").write_text(
        "\n".join(rows) + "\n")


@pytest.mark.parametrize("folder, reads", [("bam_pass", 110), ("bam_fail", 56)])
def test_one_folder_given_is_followed_by_itself(
        tmp_path: Path, batches, curlcake_ref: Path, folder: str, reads: int) -> None:
    """bam_pass/ or bam_fail/ given instead of the run folder: that folder only, as the
    offline analysis of the same folder. The run-end marker and the summary are still
    read from the run folder above it. A live run had no way to leave either out."""
    run, out = tmp_path / "data" / "exp" / "sample" / RUN, tmp_path / "live"
    for index in range(len(BATCHES)):
        _deliver(run, batches, index)
    _summary(run)
    (run / MARKER).write_text("x")
    pipe = _pipe(run / folder, out, curlcake_ref)
    assert pipe.run_dir == run and pipe.watch_dirs == [run / folder]
    assert pipe.run()["status"] == "report"

    wanted = {name for f, name, _, _ in BATCHES if f == folder}
    assert pipe.progress.ingested == wanted and _mapping(out)["total"] == reads
    offline = _offline(run / folder, tmp_path / "offline", curlcake_ref)
    assert diff_docs(_numbers(offline), _numbers(_result(out))) == [], (
        "following one folder live and analysing it offline disagree on QC numbers")

    # Compared with the reads MinKNOW passed (or failed) alone: the other half was left
    # out on purpose, so the run is not short of it.
    acc = _result(out)["run_accounting"]
    assert acc["coverage"]["fraction_of_run_reads"] == 1.0
    assert acc["coverage"]["instrument_reads"] == reads
    assert acc["lost_before_processing"] == []
    assert acc["read_coverage"]["status"] == "measured"
    assert acc["read_coverage"]["n_shortfall_pod5s"] == 0
    which = "passed" if folder == "bam_pass" else "failed"
    page = (out / "report_latest.html").read_text(encoding="utf-8")
    assert f"2 BAM from {run}: {folder}/, the {which} reads only" in page


def test_the_command_line_follows_one_folder_given_as_the_input(
        tmp_path: Path, batches, curlcake_ref: Path, capsys) -> None:
    run = tmp_path / "run"
    for index in range(len(BATCHES)):
        _deliver(run, batches, index)
    (run / MARKER).write_text("x")
    fast = tmp_path / "fast.yaml"
    fast.write_text("realtime: {poll_interval_s: 0.05, stable_polls: 3, "
                    "seal_poll_interval_s: 0.05, seal_stable_polls: 3}\n")
    code = rt.main(["--input-dir", str(run / "bam_fail"), "--output-dir",
                    str(tmp_path / "out"), "--output-name", "r", "--mode", "bam",
                    "--config", str(fast), "--reference", str(curlcake_ref),
                    "--min-reads", "1"])
    emitted = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and emitted["inputs_processed"] == 2
    assert _mapping(tmp_path / "out" / "r")["total"] == 56


def test_a_run_name_continues_a_live_run_only_with_the_same_folders(
        tmp_path: Path, batches, curlcake_ref: Path, capsys) -> None:
    """A live run restores the earlier run's files from its manifest, so continuing one
    with another input reported both together (2026-11-23). bam_pass/ alone is another
    input than the run folder's two folders: refused, before anything is processed."""
    run = tmp_path / "run"
    for index in range(len(BATCHES)):
        _deliver(run, batches, index)
    (run / MARKER).write_text("x")
    fast = tmp_path / "fast.yaml"
    fast.write_text("realtime: {poll_interval_s: 0.05, stable_polls: 3, "
                    "seal_poll_interval_s: 0.05, seal_stable_polls: 3}\n")

    def live(folder: Path) -> tuple[int, dict]:
        code = rt.main(["--input-dir", str(folder), "--output-dir", str(tmp_path / "out"),
                        "--output-name", "r", "--mode", "bam", "--config", str(fast),
                        "--reference", str(curlcake_ref), "--min-reads", "1"])
        return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert live(run)[0] == 0
    result = (tmp_path / "out" / "r" / "qc_result.json").read_bytes()
    code, emitted = live(run / "bam_pass")
    assert code == 2 and "is a run of another input" in emitted["error"]
    assert "input folder:" in emitted["error"]
    assert (tmp_path / "out" / "r" / "qc_result.json").read_bytes() == result
    code, emitted = live(run)
    assert code == 0, "the same folder continues the run"


def test_the_summary_counts_the_passed_or_the_failed_reads_alone(tmp_path: Path) -> None:
    (tmp_path / "sequencing_summary_FBA_x.txt").write_text(
        "filename_bam\tread_id\tpasses_filtering\n"
        "FBA_pass_x_0.bam\tr1\tTRUE\n"
        "FBA_pass_x_0.bam\tr2\tTRUE\n"
        "FBA_fail_x_0.bam\tr3\tFALSE\n"
        "-\tr4\tFALSE\n")
    count = functools.partial(read_sequencing_summary, tmp_path, column="filename_bam")
    assert count(passes_filtering=True).reads_per_pod5 == {"FBA_pass_x_0.bam": 2}
    assert count(passes_filtering=False).reads_per_pod5 == {"FBA_fail_x_0.bam": 1}
    assert count().total_reads == 3


def test_a_summary_without_minknow_s_verdict_is_not_measured_for_one_folder(
        tmp_path: Path) -> None:
    """Without passes_filtering the two halves cannot be told apart: not measured, rather
    than the whole run compared with half of it."""
    (tmp_path / "sequencing_summary_FBA_x.txt").write_text(
        "filename_bam\tread_id\nFBA_pass_x_0.bam\tr1\n")
    assert read_sequencing_summary(tmp_path, column="filename_bam",
                                   passes_filtering=True) is None
    assert read_sequencing_summary(tmp_path, column="filename_bam").total_reads == 1
