"""A MinKNOW run's failed reads are accounted for before a BAM run starts.

MinKNOW writes the reads it basecalls into ``bam_pass/`` and ``bam_fail/``, split on the
read's qscore. Picking the run folder for a BAM run used to switch the input to
``bam_pass/`` and show that folder's file count; nothing said that ``bam_fail/`` had been
left out, which is a qscore filter on what a reader takes for the whole run.

Now the run folder reads both folders, and says so; ``bam_pass/`` picked by itself reads
the passed reads only, and says how many failed-read files are left out and how to
include them. Barcodes are never pooled. The pre-run summary offers the choice.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pysam
import pytest

from mrnaspect.main import OfflinePipeline, load_config, main
from mrnaspect.runtime import input_folder as F
from mrnaspect.wrappers._shell import resolve_tool

RUN = "20261112_1434_MN12345_FBA12345_1a2b3c4d"
_GUI = Path(__file__).resolve().parents[2] / "gui"


def _touch(path: Path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def _run(tmp_path: Path, *, n_pass: int = 2, n_fail: int = 1) -> Path:
    """<data>/exp/sample/<run>/ with stand-in BAMs named as MinKNOW names them."""
    run = tmp_path / "data" / "exp" / "sample" / RUN
    for i in range(n_pass):
        _touch(run / "bam_pass" / f"FBA12345_pass_1a2b3c4d_0361a8e5_{i}.bam", 50)
    for i in range(n_fail):
        _touch(run / "bam_fail" / f"FBA12345_fail_1a2b3c4d_0361a8e5_{i}.bam", 30)
    return run


# ---------------------------------------------------------------------------
# 1. The pre-run summary (Core's describe; the GUI shows what it returns)
# ---------------------------------------------------------------------------

def test_a_run_folder_reads_passed_and_failed_reads_and_says_so(tmp_path: Path) -> None:
    run = _run(tmp_path)
    d = F.describe(str(run), "bam")
    assert d["use"] == str(run) and d["moved"] is None
    assert d["inputs"] == 3 and d["bytes"] == 130
    bam = d["bam"]
    assert bam["pass"] == {"dir": str(run / "bam_pass"), "inputs": 2, "bytes": 100}
    assert bam["fail"] == {"dir": str(run / "bam_fail"), "inputs": 1, "bytes": 30}
    assert bam["fail_included"] is True
    # The other choice, one pick away.
    assert bam["switch"] == str(run / "bam_pass")
    assert "2 BAM files in bam_pass/" in d["note"] and "1 in bam_fail/" in d["note"]
    assert d["run"]["name"] == RUN


def test_a_run_without_failed_read_files_says_there_are_none(tmp_path: Path) -> None:
    run = _run(tmp_path, n_fail=0)
    for with_empty_folder in (False, True):
        if with_empty_folder:
            (run / "bam_fail").mkdir()
        d = F.describe(str(run), "bam")
        assert d["use"] == str(run) and d["inputs"] == 2
        assert d["bam"]["fail"]["inputs"] == 0 and d["bam"]["fail_included"] is True
        assert d["bam"]["switch"] is None, "there is no second choice to offer"
        assert "no BAM files in bam_fail/" in d["note"]


def test_bam_pass_picked_by_itself_reads_the_passed_reads_only(tmp_path: Path) -> None:
    run = _run(tmp_path)
    d = F.describe(str(run / "bam_pass"), "bam")
    assert d["use"] == str(run / "bam_pass") and d["moved"] is None
    assert d["inputs"] == 2
    bam = d["bam"]
    assert bam["fail_included"] is False
    assert bam["fail"] == {"dir": str(run / "bam_fail"), "inputs": 1, "bytes": 30}
    assert bam["switch"] == str(run), "how to include them: the run folder"
    # How many are left out, and how to include them, before the run starts.
    assert "1 BAM file" in d["note"] and "not included" in d["note"]
    assert str(run) in d["note"]
    assert d["run"]["name"] == RUN


def test_bam_pass_with_no_failed_read_files_beside_it_has_nothing_to_say(
    tmp_path: Path
) -> None:
    run = _run(tmp_path, n_fail=0)
    d = F.describe(str(run / "bam_pass"), "bam")
    assert d["inputs"] == 2 and d["note"] is None
    assert d["bam"]["fail"]["inputs"] == 0 and d["bam"]["switch"] is None


def test_bam_fail_picked_by_itself_says_the_passed_reads_are_left_out(
    tmp_path: Path
) -> None:
    """The mirror of bam_pass/ picked by itself. It said nothing: no `bam`, no note."""
    run = _run(tmp_path)
    d = F.describe(str(run / "bam_fail"), "bam")
    assert d["use"] == str(run / "bam_fail") and d["inputs"] == 1
    bam = d["bam"]
    assert bam["pass_included"] is False and bam["fail_included"] is True
    assert bam["pass"] == {"dir": str(run / "bam_pass"), "inputs": 2, "bytes": 100}
    assert bam["fail"] == {"dir": str(run / "bam_fail"), "inputs": 1, "bytes": 30}
    # `switch` is the choice about the failed reads; here they are what is read.
    assert bam["switch"] is None
    assert d["note"].startswith("Failed reads only")
    assert "2 BAM files of passed reads" in d["note"] and "are not included" in d["note"]
    assert d["note"].endswith(f"pick the run folder: {run}")
    assert d["run"]["name"] == RUN


def test_bam_fail_with_no_passed_read_files_beside_it_has_nothing_to_say(
    tmp_path: Path
) -> None:
    run = _run(tmp_path, n_pass=0)
    d = F.describe(str(run / "bam_fail"), "bam")
    assert d["inputs"] == 1 and d["note"] is None
    assert d["bam"]["pass_included"] is False and d["bam"]["pass"]["inputs"] == 0


def test_every_answer_says_whether_the_passed_reads_are_in(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert F.describe(str(run), "bam")["bam"]["pass_included"] is True
    assert F.describe(str(run / "bam_pass"), "bam")["bam"]["pass_included"] is True
    assert F.describe(str(run), "bam", live=True)["bam"]["pass_included"] is True


@pytest.mark.parametrize("n_pass, n_fail, said", [
    (1, 0, "its 1 BAM file is in bam_pass/."),
    (3, 0, "its 3 BAM files are all in bam_pass/."),
    (0, 1, "its 1 BAM file is in bam_fail/, the reads MinKNOW failed."),
    (0, 2, "its 2 BAM files are all in bam_fail/, the reads MinKNOW failed."),
])
def test_one_file_is_said_in_the_singular(
    tmp_path: Path, n_pass: int, n_fail: int, said: str
) -> None:
    """It was "its 1 BAM file are all in bam_pass/"."""
    run = _run(tmp_path, n_pass=n_pass, n_fail=n_fail)
    assert F.describe(str(run), "bam")["note"].endswith(said)


def test_a_folder_of_bams_that_is_no_minknow_folder_is_only_itself(tmp_path: Path) -> None:
    _touch(tmp_path / "mine" / "a.bam")
    d = F.describe(str(tmp_path / "mine"), "bam")
    assert d["use"] == str(tmp_path / "mine") and d["inputs"] == 1
    assert d["bam"] is None and d["note"] is None


def test_one_barcode_s_folder_is_read_alone_and_its_failed_reads_are_counted(
    tmp_path: Path
) -> None:
    """Barcodes are kept apart: a barcode's own folder is what is read, and the folder
    that mirrors it under bam_fail/ is counted, not pooled in."""
    run = tmp_path / "data" / "exp" / "sample" / RUN
    for bc, n in (("barcode01", 2), ("barcode02", 3)):
        for i in range(n):
            _touch(run / "bam_pass" / bc / f"x_pass_{bc}_{i}.bam")
    _touch(run / "bam_fail" / "barcode01" / "x_fail_barcode01_0.bam")
    d = F.describe(str(run / "bam_pass" / "barcode01"), "bam")
    assert d["use"] == str(run / "bam_pass" / "barcode01") and d["inputs"] == 2
    assert d["bam"]["fail"] == {
        "dir": str(run / "bam_fail" / "barcode01"), "inputs": 1, "bytes": 10}
    assert d["bam"]["fail_included"] is False and d["bam"]["switch"] is None
    assert "not included" in d["note"] and "one folder" in d["note"]


def test_a_barcoded_run_folder_is_not_pooled(tmp_path: Path) -> None:
    run = tmp_path / "data" / "exp" / "sample" / RUN
    for bc in ("barcode01", "barcode02"):
        _touch(run / "bam_pass" / bc / f"x_pass_{bc}_0.bam")
        _touch(run / "bam_fail" / bc / f"x_fail_{bc}_0.bam")
    d = F.describe(str(run), "bam")
    assert d["use"] is None and d["inputs"] == 0
    assert "barcode01" in d["note"] and "barcode02" in d["note"]
    assert F.discover_inputs(run, "bam") == []


def _barcoded_run(tmp_path: Path, name: str = RUN) -> Path:
    run = tmp_path / "data" / "exp" / "sample" / name
    for bc, n in (("barcode01", 2), ("barcode02", 3)):
        for i in range(n):
            _touch(run / "bam_pass" / bc / f"x_pass_{bc}_{i}.bam")
    _touch(run / "bam_fail" / "barcode01" / "x_fail_barcode01_0.bam")
    return run


@pytest.mark.parametrize("where", ["bam_pass", "bam_fail", "sample", "experiment"])
def test_a_barcoded_run_offers_its_barcodes_from_one_level_off(
    tmp_path: Path, where: str
) -> None:
    """Only the run folder itself listed the barcodes. Its bam_pass/, and the folders
    above it, answered "No BAM files here, or in a MinKNOW run below this folder"."""
    run = _barcoded_run(tmp_path)
    picked = {"bam_pass": run / "bam_pass", "bam_fail": run / "bam_fail",
              "sample": run.parent, "experiment": run.parent.parent}[where]
    d = F.describe(str(picked), "bam")
    assert d == {**F.describe(str(run), "bam"), "picked": d["picked"]}
    assert d["use"] is None, "barcodes are offered, never read together"
    assert [(r["name"], r["inputs"], r["use"]) for r in d["runs"]] == [
        ("barcode01", 2, str(run / "bam_pass" / "barcode01")),
        ("barcode02", 3, str(run / "bam_pass" / "barcode02"))]
    assert d["note"].startswith("This run is barcoded")


def test_a_barcoded_run_is_listed_among_the_runs_above_it(tmp_path: Path) -> None:
    """It was left out of the list, having no BAM a run of its folder would read."""
    barcoded = _barcoded_run(tmp_path)
    plain = tmp_path / "data" / "exp" / "other" / "20261115_0900_MN12345_FBA99999_0a0b0c0d"
    _touch(plain / "bam_pass" / "y_pass_0.bam")
    d = F.describe(str(tmp_path / "data"), "bam")
    assert d["use"] is None and len(d["runs"]) == 2
    rows = {r["name"]: r for r in d["runs"]}
    # Picking the row gives the run folder, which then offers the barcodes.
    assert rows[RUN]["use"] == str(barcoded) and rows[RUN]["barcodes"] == 2
    assert rows[RUN]["inputs"] == 5, "its barcodes' files, so the row is not an empty run"
    assert "barcodes" not in rows[plain.name] and rows[plain.name]["inputs"] == 1
    assert "2 MinKNOW runs here" in d["note"] and "barcoded run" in d["note"]
    assert F.describe(rows[RUN]["use"], "bam")["note"].startswith("This run is barcoded")


def test_a_list_with_no_barcoded_run_says_nothing_of_barcodes(tmp_path: Path) -> None:
    _run(tmp_path)
    other = tmp_path / "data" / "exp" / "other" / "20261115_0900_MN12345_FBA99999_0a0b0c0d"
    _touch(other / "bam_pass" / "y_pass_0.bam")
    assert "barcode" not in F.describe(str(tmp_path / "data"), "bam")["note"]


def test_a_folder_above_one_run_moves_down_to_the_run_folder(tmp_path: Path) -> None:
    run = _run(tmp_path)
    d = F.describe(str(tmp_path / "data" / "exp"), "bam")
    assert d["use"] == str(run) and d["moved"] == "down" and d["inputs"] == 3
    assert d["bam"]["fail_included"] is True


def test_the_count_shown_is_the_files_core_reads(tmp_path: Path) -> None:
    run = _run(tmp_path, n_pass=11, n_fail=2)
    _touch(run / "bam_pass" / "notes.txt")
    _touch(run / "bam_pass" / "deeper" / "x.bam")            # Core does not look below
    for pick in (run, run / "bam_pass"):
        files = F.discover_inputs(pick, "bam")
        assert F.describe(str(pick), "bam")["inputs"] == len(files)
    names = [p.name for p in F.discover_inputs(run, "bam")]
    # Passed reads first, each folder in MinKNOW's own order (_2 before _10).
    assert names[:11] == [f"FBA12345_pass_1a2b3c4d_0361a8e5_{i}.bam" for i in range(11)]
    assert names[11:] == [f"FBA12345_fail_1a2b3c4d_0361a8e5_{i}.bam" for i in range(2)]


def test_two_files_of_one_name_in_the_two_folders_are_refused(tmp_path: Path) -> None:
    """Each input's files in the run folder are named after it: two inputs of one name
    would overwrite each other."""
    run = _run(tmp_path)
    _touch(run / "bam_fail" / "FBA12345_pass_1a2b3c4d_0361a8e5_0.bam")
    with pytest.raises(ValueError, match="FBA12345_pass_1a2b3c4d_0361a8e5_0.bam.*both"):
        F.discover_inputs(run, "bam")


def test_the_command_line_prints_the_same_summary(tmp_path: Path, capsys) -> None:
    """The issue's own check: mrnaspect --describe-input --mode bam --input-dir <run>."""
    run = _run(tmp_path, n_pass=1, n_fail=1)
    assert main(["--describe-input", "--mode", "bam", "--input-dir", str(run)]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["use"] == str(run) and d["inputs"] == 2
    assert d["bam"]["pass"]["inputs"] == 1 and d["bam"]["fail"]["inputs"] == 1
    assert d["note"]


# ---------------------------------------------------------------------------
# 2. What the run then reads (real BAMs, the command line, the real alignment)
# ---------------------------------------------------------------------------

def _tools_missing() -> bool:
    try:
        resolve_tool("minimap2")
        resolve_tool("samtools")
    except FileNotFoundError:
        return True
    return False


needs_tools = pytest.mark.skipif(
    _tools_missing(), reason="the alignment needs minimap2 and samtools")

N_PASS = 100


@pytest.fixture()
def real_run(tmp_path: Path, fixture_bam: Path) -> tuple[Path, int]:
    """A run folder whose bam_pass/ holds the fixture's first 100 reads and whose
    bam_fail/ holds the rest. Returns the folder and the number of reads in all."""
    run = tmp_path / "data" / "exp" / "sample" / RUN
    (run / "bam_pass").mkdir(parents=True)
    (run / "bam_fail").mkdir()
    with pysam.AlignmentFile(fixture_bam, "rb") as src:
        reads = list(src)
        with pysam.AlignmentFile(run / "bam_pass" / "FBA_pass_1a2b3c4d_0.bam", "wb",
                                 header=src.header) as out:
            for read in reads[:N_PASS]:
                out.write(read)
        with pysam.AlignmentFile(run / "bam_fail" / "FBA_fail_1a2b3c4d_0.bam", "wb",
                                 header=src.header) as out:
            for read in reads[N_PASS:]:
                out.write(read)
    assert len(reads) > N_PASS
    return run, len(reads)


def _cli(tmp_path: Path, input_dir: Path, reference: Path, name: str) -> dict:
    out = tmp_path / "out"
    code = main(["--input-dir", str(input_dir), "--output-dir", str(out),
                 "--output-name", name, "--reference", str(reference), "--mode", "bam",
                 "--min-reads", "1", "--min-reads-per-construct", "0"])
    assert code == 0
    return json.loads((out / name / "qc_result.json").read_text())


def _total(result: dict) -> int:
    return result["snapshots"]["run_summary"]["mapping"]["total"]


@needs_tools
def test_the_run_folder_gives_every_read_and_bam_pass_the_passed_ones(
    tmp_path: Path, real_run, curlcake_ref: Path
) -> None:
    run, n_all = real_run
    whole = _cli(tmp_path, run, curlcake_ref, "whole")
    passed = _cli(tmp_path, run / "bam_pass", curlcake_ref, "passed")
    assert _total(whole) == n_all
    assert _total(passed) == N_PASS
    assert whole["n_inputs"] == 2 and passed["n_inputs"] == 1


@needs_tools
def test_the_report_says_which_reads_it_holds(
    tmp_path: Path, real_run, curlcake_ref: Path
) -> None:
    run, _ = real_run
    _cli(tmp_path, run, curlcake_ref, "whole")
    _cli(tmp_path, run / "bam_pass", curlcake_ref, "passed")
    _cli(tmp_path, run / "bam_fail", curlcake_ref, "failed")
    whole = (tmp_path / "out" / "whole" / "report_latest.html").read_text()
    passed = (tmp_path / "out" / "passed" / "report_latest.html").read_text()
    failed = (tmp_path / "out" / "failed" / "report_latest.html").read_text()
    assert "1 in bam_pass/, 1 in bam_fail/" in whole
    assert "bam_fail/ not read: 1 file" in passed
    # bam_fail/ by itself: one folder was read, so not "1 in bam_pass/, 1 in bam_fail/".
    assert "bam_pass/ not read: 1 file" in failed and "1 in bam_pass/" not in failed


def test_a_folder_with_no_bams_and_no_minknow_folders_is_still_an_error(
    tmp_path: Path, curlcake_ref: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam", "min_reads": 1})
    with pytest.raises(FileNotFoundError, match=r"No \*\.bam files"):
        OfflinePipeline(empty, tmp_path / "out", cfg).run()


def test_a_barcoded_run_folder_is_refused_with_the_way_on(
    tmp_path: Path, curlcake_ref: Path
) -> None:
    run = tmp_path / "data" / "exp" / "sample" / RUN
    _touch(run / "bam_pass" / "barcode01" / "x_pass_0.bam")
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam", "min_reads": 1})
    with pytest.raises(FileNotFoundError, match="barcode01"):
        OfflinePipeline(run, tmp_path / "out", cfg).run()


# ---------------------------------------------------------------------------
# 3. The desktop app's input card: the choice is on it
# ---------------------------------------------------------------------------

def _fn(source: str, head: str) -> str:
    """One top-level function of main.ts, from its head to the next one."""
    start = source.index(head)
    nxt = re.search(r"\n(?:async )?function |\n\$\(", source[start + len(head):])
    return source[start:start + len(head) + (nxt.start() if nxt else len(source))]


def test_the_input_card_offers_the_failed_reads_as_a_choice() -> None:
    ts = (_GUI / "src" / "main.ts").read_text(encoding="utf-8")
    card = _fn(ts, "function renderInputInfo(")
    # A checkbox, ticked when the failed reads are in, shown only where there is a
    # second choice to switch to.
    assert 'id="withfail"' in card and "d.bam.fail_included" in card
    assert "d.bam.switch" in card
    # Changing it picks the other folder and asks Core again; nothing is decided in the page.
    change = card[card.index('"#withfail"'):]
    assert "describeInput(" in change and ".switch" in change
    # Core's sentence about the two folders is shown with the count, not dropped.
    assert "d.note" in card[:card.index("const rows")]
    # On every card that reads BAM files, the live BAM card too: it was the BAM card only,
    # so a live run could not leave the failed reads out.
    box = card[card.index("const withFail"):card.index('id="withfail"')]
    assert "readsBam(kind)" in box and 'kind === "bam"' not in box


def test_the_review_card_says_whether_the_failed_reads_are_in() -> None:
    ts = (_GUI / "src" / "main.ts").read_text(encoding="utf-8")
    review = _fn(ts, "function reviewRows(")
    assert "failed reads included" in review and "failed reads not included" in review
    # bam_fail/ picked by itself: Core's pass_included is false.
    assert "pass_included === false" in review and "failed reads only" in review


def test_help_shows_both_folders() -> None:
    html = (_GUI / "index.html").read_text(encoding="utf-8")
    tree = html[html.index('<pre class="tree">'):html.index("</pre>", html.index('<pre class="tree">'))]
    assert "bam_pass/" in tree and "bam_fail/" in tree
