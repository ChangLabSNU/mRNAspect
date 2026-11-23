"""A run name that names an existing folder continues that run, and now says so.

Nothing told the operator whether a Start had continued an earlier run or begun a new
one (2026-11-23: "it's hard to know if the run is continued or not, because there is no
notification about it"). Two answers, both from Core:

- before Start, ``--print-config`` describes the folder a Start would write
  (``run_dir_state``: whether it exists, how far the earlier run got, what is on record);
- while the run goes, ``run_status.json`` says what it carried over (``continued``):
  the live driver's inputs restored from its manifest, the offline driver's basecalls on
  record.

No GPU: the basecall and align stages are the fakes test_realtime_pipeline.py uses.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mrnaspect.main import OfflinePipeline
from mrnaspect.realtime.main import RealtimePipeline
from mrnaspect.runtime.run_dir_state import describe_run_dir
from mrnaspect.runtime.run_status import RUN_STATUS_NAME, write_run_status
from mrnaspect.tests.test_realtime_pipeline import _cfg, _fakes, _rt_cfg, _run_mimic

_NEW = {"exists": False, "inputs_processed": None, "inputs_found": None,
        "inputs_total": None, "report": None, "inputs_on_record": 0,
        "basecalls_cached": 0, "unreadable": [], "recorded_input": None,
        "refusal": None, "not_compared": []}


def _status(out: Path) -> dict:
    return json.loads((out / RUN_STATUS_NAME).read_text())


# --- what is in the folder, before Start -------------------------------------------------

def test_a_folder_that_is_not_there_is_a_new_run(tmp_path: Path) -> None:
    assert describe_run_dir(tmp_path / "run_a") == _NEW
    assert not (tmp_path / "run_a").exists(), "describing a folder must not create it"


def test_an_earlier_run_s_progress_and_report_are_read(tmp_path: Path) -> None:
    run = tmp_path / "r"
    run.mkdir()
    write_run_status(run, found=40, processed=12, total=40, current=None)
    (run / "qc_result.json").write_text(json.dumps({"report": {"reason": "user_abort"}}))
    (run / "basecall_cache.tsv").write_text("a.pod5\t/x/a\nb.pod5\t/x/b\n")
    got = describe_run_dir(run)
    assert got == {**_NEW, "exists": True, "inputs_processed": 12, "inputs_found": 40,
                   "inputs_total": 40, "report": "user_abort", "basecalls_cached": 2}


def test_an_empty_folder_exists_with_nothing_on_record(tmp_path: Path) -> None:
    """Core continues any folder that is there, so the answer is "continues", with nothing
    to carry over, not "new"."""
    (tmp_path / "r").mkdir()
    assert describe_run_dir(tmp_path / "r") == {**_NEW, "exists": True}


def test_a_file_that_cannot_be_read_is_named_not_raised(tmp_path: Path) -> None:
    run = tmp_path / "r"
    run.mkdir()
    (run / "qc_result.json").write_text("{")
    (run / RUN_STATUS_NAME).write_text("[1, 2]")
    got = describe_run_dir(run)
    assert got["exists"] is True and got["report"] is None
    assert got["inputs_processed"] is None
    assert [u.split(":")[0] for u in got["unreadable"]] == [RUN_STATUS_NAME, "qc_result.json"]


def test_print_config_describes_the_folder_a_start_would_write(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (out / "r").mkdir(parents=True)
    write_run_status(out / "r", found=3, processed=3, total=3, current=None)
    before = sorted(tmp_path.rglob("*"))

    def ask(name: str) -> dict:
        r = subprocess.run([sys.executable, "-m", "mrnaspect.main", "--print-config",
                            "--output-dir", str(out), "--output-name", name],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return json.loads(r.stdout)

    old, new = ask("r"), ask("s")
    assert old["run_dir"] == str(out / "r")
    assert old["run_dir_state"]["exists"] is True
    assert old["run_dir_state"]["inputs_processed"] == 3
    # Asked with no input, nothing is compared, and it says so.
    assert new["run_dir"] == str(out / "s")
    assert new["run_dir_state"] == {**_NEW, "not_compared": ["input folder"]}
    assert sorted(tmp_path.rglob("*")) == before, "--print-config must create nothing"


def test_print_config_says_before_start_what_the_run_would_refuse(tmp_path: Path) -> None:
    """The app's check before Start: Core's own `refusal`, given the input the Start
    would give (2026-11-23)."""
    from mrnaspect.runtime.run_input import check_run_input, run_input

    one, two, ref, out = (tmp_path / "one", tmp_path / "two", tmp_path / "ref.fa",
                          tmp_path / "out")
    one.mkdir(), two.mkdir()
    ref.write_text(">c\nACGT\n")
    check_run_input(out / "r", run_input(driver="live", mode="bam", input_dir=one,
                                         reference=ref))

    def ask(*extra: str) -> dict:
        r = subprocess.run([sys.executable, "-m", "mrnaspect.main", "--print-config",
                            "--output-dir", str(out), "--output-name", "r", *extra],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return json.loads(r.stdout)["run_dir_state"]

    same = ask("--live", "--mode", "bam", "--input-dir", str(one), "--reference", str(ref))
    assert same["refusal"] is None and same["not_compared"] == []
    assert same["recorded_input"]["input_dir"] == str(one.resolve())
    other = ask("--live", "--mode", "bam", "--input-dir", str(two), "--reference", str(ref))
    assert "input folder:" in other["refusal"]
    offline = ask("--mode", "bam", "--input-dir", str(one), "--reference", str(ref))
    assert "kind of run: live before, offline now" in offline["refusal"]
    pasted = ask("--live", "--mode", "bam", "--input-dir", str(one))
    assert pasted["refusal"] is None and pasted["not_compared"] == ["reference"]


def test_print_config_without_an_output_folder_describes_nothing() -> None:
    r = subprocess.run([sys.executable, "-m", "mrnaspect.main", "--print-config"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout)
    # The shipped config names no output folder, so there is no folder to describe.
    assert got["run_dir"] is None and "no output directory" in got["run_dir_error"]
    assert got["run_dir_state"] is None


# --- what the run carried over, while it goes ---------------------------------------------

def test_a_live_run_that_continues_says_how_many_inputs_it_restored(
        tmp_path: Path, fixture_bam: Path, curlcake_ref: Path) -> None:
    run_dir, out = tmp_path / "run", tmp_path / "out"
    _run_mimic(run_dir, n=4)
    bc, al = _fakes(fixture_bam)

    def live(out_dir: Path, continues: bool) -> RealtimePipeline:
        return RealtimePipeline(run_dir, out_dir, _rt_cfg(curlcake_ref),
                                continues=continues, basecaller=bc, aligner=al)

    # A first attempt stops after 2 inputs, as a crash would.
    first = live(out, continues=False)
    first.boot()
    first.replay()
    for pod5 in sorted((run_dir / "pod5").glob("*.pod5"))[:2]:
        first._handle(pod5)
    assert describe_run_dir(out)["inputs_on_record"] == 2

    live(out, continues=True).run()
    end = _status(out)
    assert end["continued"] == {"inputs_restored": 2}
    assert end["inputs_processed"] == 4, "restored inputs count as done, as before"

    clean = tmp_path / "clean"
    live(clean, continues=False).run()
    assert _status(clean)["continued"] is None


def test_an_offline_run_that_continues_says_what_it_reuses(
        tmp_path: Path, fixture_bam: Path, curlcake_ref: Path) -> None:
    in_dir, out = tmp_path / "pod5", tmp_path / "out"
    in_dir.mkdir()
    for i in range(3):
        (in_dir / f"r_{i}.pod5").write_bytes(b"x%d" % i)
    bc, al = _fakes(fixture_bam)
    seen: list[dict] = []

    def basecall_and_look(pod5: Path, out_dir: Path) -> Path:
        seen.append(_status(out))
        return bc(pod5, out_dir)

    OfflinePipeline(in_dir, out, _cfg(curlcake_ref), basecaller=basecall_and_look,
                    aligner=al).run()
    assert seen[0]["continued"] is None and _status(out)["continued"] is None
    cached = describe_run_dir(out)["basecalls_cached"]
    assert cached == 3

    OfflinePipeline(in_dir, out, _cfg(curlcake_ref), basecaller=bc, aligner=al,
                    continues=True).run()
    assert _status(out)["continued"] == {"basecalls_cached": 3}


def test_a_continued_count_must_be_one_of_the_two_kinds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="continued"):
        write_run_status(tmp_path, found=1, processed=1, total=1, current=None,
                         continued={"inputs": 1})
    with pytest.raises(ValueError, match="continued"):
        write_run_status(tmp_path, found=1, processed=1, total=1, current=None,
                         continued={"inputs_restored": -1})


def test_both_command_lines_tell_the_driver_whether_the_folder_was_there() -> None:
    """The folder exists by the time a driver is built (run.log is in it), so only the
    command line can say whether it was there before: `existed`, sampled before logging
    creates it."""
    root = Path(__file__).resolve().parents[1]
    offline = (root / "main.py").read_text()
    live = (root / "realtime" / "main.py").read_text()
    assert "pipe = OfflinePipeline(input_dir, run_dir, cfg, continues=existed)" in offline
    assert "pipe = RealtimePipeline(input_dir, out_dir, cfg, continues=existed)" in live
    for text, folder in ((offline, "run_dir"), (live, "out_dir")):
        assert text.index(f"existed = {folder}.exists()") < text.index(
            f"_setup_logging({folder}, args.verbose)")


def test_the_offline_command_line_continues_a_named_run_and_says_so(
        tmp_path: Path, fixture_bam: Path, curlcake_ref: Path) -> None:
    from mrnaspect.main import main

    in_dir, out = tmp_path / "bams", tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "a.bam").write_bytes(fixture_bam.read_bytes())
    args = ["--input-dir", str(in_dir), "--output-dir", str(out), "--output-name", "r",
            "--mode", "bam", "--reference", str(curlcake_ref), "--min-reads", "1"]
    assert main(args) == 0
    assert _status(out / "r")["continued"] is None
    assert main(args) == 0
    assert _status(out / "r")["continued"] == {"basecalls_cached": 0}
