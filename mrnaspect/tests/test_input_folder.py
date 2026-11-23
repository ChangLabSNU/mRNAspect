"""Any folder the operator picks, resolved to the one Core reads (GUI round 2, 2026-11-12).

The first live run in the new GUI did not know which of five MinKNOW levels to
pick (``/Library/MinKNOW/data/demo/no_sample_id/<run>/pod5``). Each case below is one
of those levels, for each kind of run, on a MinKNOW-shaped tree.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mrnaspect.runtime import input_folder as F

RUN_A = "20261112_1434_MS00000_SIM06556_b4ed6268"   # a demo run, finished
RUN_B = "20261113_0910_MN12345_FBA12345_1a2b3c4d"   # newer, still running


def _touch(path: Path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


@pytest.fixture()
def data(tmp_path) -> Path:
    """<data>/demo/no_sample_id/RUN_A (2 POD5, finished, BAMs) and
    <data>/demo/other_sample/RUN_B (3 POD5 so far, no marker)."""
    root = tmp_path / "data"
    a = root / "demo" / "no_sample_id" / RUN_A
    for i in range(2):
        _touch(a / "pod5" / f"{RUN_A}_{i}.pod5", 100)
    _touch(a / f"final_summary_{RUN_A}.txt")
    _touch(a / "bam_pass" / "x_pass_0.bam", 50)
    b = root / "demo" / "other_sample" / RUN_B
    for i in range(3):
        _touch(b / "pod5" / f"{RUN_B}_{i}.pod5", 100)
    return root


def _run(data: Path, name: str) -> Path:
    return next(data.rglob(name))


# --- a live run reads the RUN folder ---------------------------------------------------------

def test_live_the_run_folder_is_read_as_picked(data) -> None:
    d = F.describe(str(_run(data, RUN_A)), "pod5", live=True)
    assert d["use"] == str(_run(data, RUN_A)) and d["moved"] is None
    assert d["inputs"] == 2 and d["bytes"] == 200
    assert d["run"]["started"] == "2026-11-12 14:34"
    assert d["run"]["flow_cell"] == "SIM06556" and d["run"]["position"] == "MS00000"
    assert d["run"]["finished"] is True


def test_live_its_pod5_folder_moves_up_one(data) -> None:
    d = F.describe(str(_run(data, RUN_A) / "pod5"), "pod5", live=True)
    assert d["use"] == str(_run(data, RUN_A)) and d["moved"] == "up"


def test_live_a_sample_folder_with_one_run_moves_down_to_it(data) -> None:
    d = F.describe(str(data / "demo" / "other_sample"), "pod5", live=True)
    assert d["use"] == str(_run(data, RUN_B)) and d["moved"] == "down"
    assert d["inputs"] == 3 and d["run"]["finished"] is False


def test_an_experiment_with_two_runs_lists_them_newest_first_and_moves_nothing(data) -> None:
    d = F.describe(str(data / "demo"), "pod5", live=True)
    assert d["use"] is None
    assert [r["name"] for r in d["runs"]] == [RUN_B, RUN_A]
    assert [r["inputs"] for r in d["runs"]] == [3, 2]
    assert "2 MinKNOW runs here" in d["note"]


def test_two_runs_of_one_experiment_and_sample_are_said_to_be_two_runs(tmp_path) -> None:
    """Seen on the first live BAM run (2026-11-22): a run started twice in MinKNOW two
    minutes apart left two run folders under one experiment and sample. The list titles a
    run by those two names, so it read as one run shown twice."""
    sample = tmp_path / "data" / "test02" / "no_sample_id"
    older = sample / "20261122_1649_MS00000_SIM06556_c92cd3d1"
    newer = sample / "20261122_1651_MS00000_SIM06556_75e3fe73"
    older.mkdir(parents=True)
    for i in range(3):
        _touch(newer / "bam_pass" / f"x_pass_{i}.bam")
    _touch(newer / "bam_fail" / "x_fail_0.bam")
    d = F.describe(str(tmp_path / "data"), "bam", live=True)
    assert [r["started"] for r in d["runs"]] == ["2026-11-22 16:51", "2026-11-22 16:49"]
    assert [r["inputs"] for r in d["runs"]] == [4, 0]
    assert d["note"].startswith("2 MinKNOW runs here: pick one.")
    assert "Both were started as test02 / no_sample_id" in d["note"]
    assert "separate runs" in d["note"] and "start time" in d["note"]


def test_the_sentence_counts_the_runs_that_share_their_names(data) -> None:
    """Three runs, two of them under one experiment and sample."""
    again = data / "demo" / "other_sample" / "20261113_1100_MN12345_FBA12345_5e6f7a8b"
    _touch(again / "pod5" / "y_0.pod5")
    d = F.describe(str(data / "demo"), "pod5", live=True)
    assert len(d["runs"]) == 3
    assert "2 of them were started as demo / other_sample" in d["note"]


def test_runs_named_apart_get_no_such_sentence(data) -> None:
    d = F.describe(str(data / "demo"), "pod5", live=True)
    assert d["note"] == "2 MinKNOW runs here: pick one."


def test_live_an_unrelated_folder_says_what_it_lacks(tmp_path) -> None:
    (tmp_path / "x").mkdir()
    d = F.describe(str(tmp_path / "x"), "pod5", live=True)
    assert d["use"] is None and d["runs"] == []
    assert "No MinKNOW run folder (one that holds pod5/)" in d["note"]


def test_live_reads_the_configured_pod5_subdir(data) -> None:
    run = _run(data, RUN_A)
    (run / "pod5").rename(run / "pod5_pass")
    assert F.describe(str(run), "pod5", live=True, pod5_subdir="pod5_pass")["inputs"] == 2
    assert F.describe(str(run / "pod5_pass"), "pod5", live=True,
                      pod5_subdir="pod5_pass")["moved"] == "up"


def test_live_a_fresh_run_without_pod5_yet_is_known_by_its_name(tmp_path) -> None:
    """MinKNOW names the run folder before it writes the first POD5."""
    run = tmp_path / "exp" / "s" / RUN_B
    run.mkdir(parents=True)
    d = F.describe(str(run), "pod5", live=True)
    assert d["use"] == str(run) and d["inputs"] == 0 and d["run"]["finished"] is False


def test_a_final_summary_alone_does_not_make_a_run(tmp_path) -> None:
    """One run's copy keeps MinKNOW's files in data/ and the POD5s two levels down
    (data/IVT_260407/pod5): data/ must not read as an empty live run."""
    top = tmp_path / "data"
    _touch(top / "final_summary_FBA31122_a3bfad2b_0361a8e5.txt")
    for i in range(2):
        _touch(top / "IVT_260407" / "pod5" / f"x_{i}.pod5")
    d = F.describe(str(top), "pod5", live=True)
    assert d["use"] == str(top / "IVT_260407") and d["moved"] == "down" and d["inputs"] == 2


def test_a_minknow_run_is_named_by_the_experiment_and_sample_typed_into_minknow(data) -> None:
    """MinKNOW writes <data>/<experiment>/<sample>/<run>, and the operator typed the first
    two; the run folder's own name is a timestamp two levels below them
    (2026-11-13). Each run says both, so the list can lead with them."""
    d = F.describe(str(data / "demo"), "pod5", live=True)
    assert [(r["experiment"], r["sample"]) for r in d["runs"]] == [
        ("demo", "other_sample"), ("demo", "no_sample_id")]
    one = F.describe(str(_run(data, RUN_A)), "pod5", live=True)
    assert (one["run"]["experiment"], one["run"]["sample"]) == ("demo", "no_sample_id")


def test_a_folder_not_named_as_minknow_names_a_run_claims_no_experiment(tmp_path) -> None:
    """One run's copy (data/IVT_260407): its parents are not an experiment and a
    sample, so neither is claimed."""
    top = tmp_path / "data"
    for i in range(2):
        _touch(top / "IVT_260407" / "pod5" / f"x_{i}.pod5")
    d = F.describe(str(top), "pod5", live=True)
    assert d["run"]["name"] == "IVT_260407"
    assert d["run"]["experiment"] is None and d["run"]["sample"] is None


# --- a POD5 run reads the folder of POD5 files -----------------------------------------------

def test_pod5_the_pod5_folder_is_read_as_picked(data) -> None:
    d = F.describe(str(_run(data, RUN_A) / "pod5"), "pod5")
    assert d["use"] == str(_run(data, RUN_A) / "pod5") and d["moved"] is None
    assert d["inputs"] == 2 and d["run"]["name"] == RUN_A


def test_pod5_a_run_folder_moves_down_into_its_pod5(data) -> None:
    d = F.describe(str(_run(data, RUN_A)), "pod5")
    assert d["use"] == str(_run(data, RUN_A) / "pod5") and d["moved"] == "down"


def test_pod5_the_count_is_core_s_own_glob(data) -> None:
    """The number shown is the number Core's offline driver will process."""
    from mrnaspect.main import _MODE_GLOB
    from mrnaspect.runtime.ordering import natural_sorted

    assert F.MODE_GLOB == _MODE_GLOB
    folder = _run(data, RUN_B) / "pod5"
    _touch(folder / "notes.txt")
    (folder / "sub").mkdir()
    _touch(folder / "sub" / "deeper.pod5")          # Core does not look below
    assert F.describe(str(folder), "pod5")["inputs"] == len(
        natural_sorted(folder.glob(_MODE_GLOB["pod5"]))) == 3


# --- a BAM run reads the folder of BAM files ---------------------------------------------------

def test_bam_a_run_folder_is_read_as_the_run(data) -> None:
    """The run folder is given to Core as picked: it reads bam_pass/ and bam_fail/
    (test_bam_fail_input.py). It was switched to bam_pass/ before, and the failed reads
    went missing without a word."""
    d = F.describe(str(_run(data, RUN_A)), "bam")
    assert d["use"] == str(_run(data, RUN_A)) and d["moved"] is None
    assert d["inputs"] == 1


def test_bam_a_folder_of_bams_is_read_as_picked(tmp_path) -> None:
    _touch(tmp_path / "mine" / "a.bam")
    d = F.describe(str(tmp_path / "mine"), "bam")
    assert d["use"] == str(tmp_path / "mine") and d["inputs"] == 1 and d["run"] is None


def test_bam_skips_runs_without_bams(data) -> None:
    """RUN_B has no bam_pass: of the two runs under demo/, one answers."""
    d = F.describe(str(data / "demo"), "bam")
    assert d["use"] == str(_run(data, RUN_A)) and d["moved"] == "down"


# --- find for me: MinKNOW's data folder -------------------------------------------------------

def test_no_path_lists_the_runs_in_minknow_s_data_folder(data, monkeypatch) -> None:
    monkeypatch.setattr(F, "default_data_folder", lambda: str(data))
    d = F.describe("", "pod5", live=True)
    assert d["picked"] == str(data) and d["use"] is None
    assert [r["name"] for r in d["runs"]] == [RUN_B, RUN_A]


def test_one_run_in_the_data_folder_is_still_listed_not_chosen(data, monkeypatch) -> None:
    """Searched, not picked: the operator confirms a run they did not point at."""
    import shutil
    shutil.rmtree(data / "demo" / "other_sample")
    monkeypatch.setattr(F, "default_data_folder", lambda: str(data))
    d = F.describe(None, "pod5", live=True)
    assert d["use"] is None and [r["name"] for r in d["runs"]] == [RUN_A]


def test_no_data_folder_says_so(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(F, "default_data_folder", lambda: str(tmp_path / "absent"))
    d = F.describe("", "pod5", live=True)
    assert d["exists"] is False and "is not on this computer" in d["note"]


def test_the_mac_data_folder_is_the_one_the_user_s_run_was_in() -> None:
    assert F.MINKNOW_DATA["darwin"] == "/Library/MinKNOW/data"


@pytest.mark.parametrize("mode,live,holds", [("pod5", True, "No MinKNOW run folder"),
                                             ("bam", True, "No MinKNOW run folder"),
                                             ("pod5", False, "No POD5 files"),
                                             ("bam", False, "No BAM files")])
def test_find_with_no_run_names_the_folder_it_searched(tmp_path, monkeypatch, mode, live,
                                                       holds) -> None:
    """Find leaves the input field empty, so "at or below this folder" named nothing the
    operator could see (2026-11-23, on every card)."""
    monkeypatch.setattr(F, "default_data_folder", lambda: str(tmp_path))
    d = F.describe("", mode, live=live)
    assert d["note"].startswith(holds)
    assert f"MinKNOW's data folder ({tmp_path})" in d["note"]
    assert "pick that folder with Browse" in d["note"]
    assert d["unreadable"] == []


def test_a_picked_folder_with_no_run_is_named_too(tmp_path) -> None:
    d = F.describe(str(tmp_path), "pod5", live=True)
    assert f"in {tmp_path} or up to {F.SEARCH_DEPTH} levels below it" in d["note"]
    assert "Browse" not in d["note"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root reads a folder whatever its permissions")
def test_a_folder_that_cannot_be_read_is_named_not_passed_over(tmp_path, monkeypatch) -> None:
    """One unreadable folder made the whole data folder read as holding no run, and said
    nothing of why (reproduced 2026-11-23 with chmod 000)."""
    locked = tmp_path / "exp"
    (locked / "sample" / RUN_A / "pod5").mkdir(parents=True)
    locked.chmod(0)
    try:
        monkeypatch.setattr(F, "default_data_folder", lambda: str(tmp_path))
        d = F.describe("", "pod5", live=True)
    finally:
        locked.chmod(0o755)
    assert d["runs"] == [] and d["unreadable"] == [str(locked)]
    assert "1 folder there could not be read by this account" in d["note"]
    assert str(locked) in d["note"]


# --- edges --------------------------------------------------------------------------------------

def test_a_live_bam_run_is_given_the_run_folder_and_counts_both_folders(data) -> None:
    """A live run can follow the BAM files MinKNOW basecalls itself. It is given the run
    folder, like a live POD5 run, and follows bam_pass/ and bam_fail/ in it."""
    run = _run(data, RUN_A)
    _touch(run / "bam_fail" / "x_fail_0.bam", 30)
    d = F.describe(str(run), "bam", live=True)
    assert d["use"] == str(run) and d["moved"] is None and d["inputs"] == 2
    assert d["bam"]["pass"]["inputs"] == 1 and d["bam"]["fail"]["inputs"] == 1
    assert d["bam"]["pass_included"] is True and d["bam"]["fail_included"] is True
    # The other choice, as offline: bam_pass/, the passed reads only.
    assert d["bam"]["switch"] == str(run / "bam_pass")
    # Said in words too: the card shows this sentence under the count. Without it a
    # live BAM run showed "2 BAM files so far" and nothing about the failed reads.
    assert d["note"] == ("Follows bam_pass/ and bam_fail/ together, the passed and the "
                         "failed reads: so far 1 BAM file in bam_pass/ and 1 in bam_fail/.")


def test_a_live_bam_run_of_bam_pass_follows_the_passed_reads_only(data) -> None:
    """bam_pass/ picked for a live run is followed by itself, as offline. It was moved up
    to the run folder, so a live run had no way to leave the failed reads out."""
    run = _run(data, RUN_A)
    _touch(run / "bam_fail" / "x_fail_0.bam", 30)
    d = F.describe(str(run / "bam_pass"), "bam", live=True)
    assert d["use"] == str(run / "bam_pass") and d["moved"] is None and d["inputs"] == 1
    assert d["run"]["path"] == str(run)
    assert d["bam"]["pass_included"] is True and d["bam"]["fail_included"] is False
    assert d["bam"]["switch"] == str(run), "unticking the box goes back to the run folder"
    assert d["note"] == ("Follows bam_pass/ only, the passed reads: so far 1 BAM file. "
                         "bam_fail/ (1 so far) is not followed. MinKNOW fails a read on "
                         f"its qscore, so this is a qscore filter. To include them, pick "
                         f"the run folder: {run}")


def test_a_live_bam_run_of_bam_fail_follows_the_failed_reads_only(data) -> None:
    run = _run(data, RUN_A)
    _touch(run / "bam_fail" / "x_fail_0.bam", 30)
    _touch(run / "bam_fail" / "x_fail_1.bam", 30)
    d = F.describe(str(run / "bam_fail"), "bam", live=True)
    assert d["use"] == str(run / "bam_fail") and d["moved"] is None and d["inputs"] == 2
    assert d["bam"]["pass_included"] is False and d["bam"]["fail_included"] is True
    # No box: the choice is about the failed reads, and here they are what is followed.
    assert d["bam"]["switch"] is None
    assert d["note"] == ("Follows bam_fail/ only, the reads MinKNOW failed on their "
                         "qscore: so far 2 BAM files. bam_pass/ (1 so far) is not "
                         f"followed. To include them, pick the run folder: {run}")


def test_a_live_bam_run_offers_the_choice_before_any_failed_read(tmp_path) -> None:
    """A live run is set up before MinKNOW writes its files, so the box is offered with
    bam_fail/ still empty or absent. Offline it is offered only when both hold files."""
    run = tmp_path / "data" / "exp" / "sample" / RUN_A
    _touch(run / "bam_pass" / "x_pass_0.bam")
    d = F.describe(str(run), "bam", live=True)
    assert d["bam"]["switch"] == str(run / "bam_pass")
    only = F.describe(str(run / "bam_pass"), "bam", live=True)
    assert only["use"] == str(run / "bam_pass") and only["bam"]["switch"] == str(run)
    assert "bam_fail/ (none so far) is not followed" in only["note"]


def test_a_live_bam_run_says_what_it_follows_before_there_is_anything(tmp_path) -> None:
    """A run MinKNOW has just started: neither folder yet, then passed reads only."""
    run = tmp_path / "data" / "exp" / "sample" / RUN_A
    run.mkdir(parents=True)
    d = F.describe(str(run), "bam", live=True)
    assert d["use"] == str(run) and d["inputs"] == 0
    assert d["bam"]["pass"]["inputs"] == 0 and d["bam"]["fail"]["inputs"] == 0
    assert "bam_pass/ and bam_fail/" in d["note"] and "no BAM file there yet" in d["note"]
    for i in range(3):
        _touch(run / "bam_pass" / f"x_pass_{i}.bam")
    d = F.describe(str(run), "bam", live=True)
    assert d["inputs"] == 3
    assert d["note"].endswith("so far 3 BAM files in bam_pass/ and none in bam_fail/.")


def test_a_live_bam_run_of_a_barcoded_folder_is_not_offered(tmp_path) -> None:
    run = tmp_path / "data" / "exp" / "sample" / RUN_A
    _touch(run / "bam_pass" / "barcode01" / "x_pass_0.bam")
    _touch(run / "bam_pass" / "barcode02" / "x_pass_0.bam")
    d = F.describe(str(run), "bam", live=True)
    assert d["use"] is None
    assert "barcode01" in d["note"] and "--barcode" in d["note"]


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="mode must be one of"):
        F.describe("/x", "fast5")


def test_a_missing_folder_is_said_so(tmp_path) -> None:
    d = F.describe(str(tmp_path / "nope"), "pod5")
    assert d["exists"] is False and d["note"].endswith("is not a folder.")
