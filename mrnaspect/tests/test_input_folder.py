"""Any folder the operator picks, resolved to the one Core reads (GUI round 2, 2026-11-12).

The first live run in the new GUI did not know which of five MinKNOW levels to
pick (``/Library/MinKNOW/data/demo/no_sample_id/<run>/pod5``). Each case below is one
of those levels, for each kind of run, on a MinKNOW-shaped tree.
"""

from __future__ import annotations

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

def test_bam_a_run_folder_moves_down_into_bam_pass(data) -> None:
    d = F.describe(str(_run(data, RUN_A)), "bam")
    assert d["use"] == str(_run(data, RUN_A) / "bam_pass") and d["moved"] == "down"
    assert d["inputs"] == 1


def test_bam_a_folder_of_bams_is_read_as_picked(tmp_path) -> None:
    _touch(tmp_path / "mine" / "a.bam")
    d = F.describe(str(tmp_path / "mine"), "bam")
    assert d["use"] == str(tmp_path / "mine") and d["inputs"] == 1 and d["run"] is None


def test_bam_skips_runs_without_bams(data) -> None:
    """RUN_B has no bam_pass: of the two runs under demo/, one answers."""
    d = F.describe(str(data / "demo"), "bam")
    assert d["use"] == str(_run(data, RUN_A) / "bam_pass") and d["moved"] == "down"


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


# --- edges --------------------------------------------------------------------------------------

def test_a_live_run_in_bam_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="mode must be pod5"):
        F.describe("/x", "bam", live=True)


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="mode must be one of"):
        F.describe("/x", "fast5")


def test_a_missing_folder_is_said_so(tmp_path) -> None:
    d = F.describe(str(tmp_path / "nope"), "pod5")
    assert d["exists"] is False and d["note"].endswith("is not a folder.")
