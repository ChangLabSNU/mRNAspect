"""A run name continues the earlier run of its folder only with the same input (2026-11-23).

Reproduced before the rule, with two mimic MinKNOW runs: a live run continued on another
run restored the first run's files from its manifest and went on with the second's, so
one report held both (2 + 3 files). Now the first start records the run's input
(``run_input.json``) and a later start must match it, or is refused before anything is
processed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mrnaspect.runtime.run_input import (RUN_INPUT_NAME, RunInputMismatch,
                                         check_run_input, refusal, run_input)


def _ref(tmp_path: Path, name: str = "ref.fa", seq: str = "ACGT") -> Path:
    path = tmp_path / name
    path.write_text(f">c\n{seq}\n")
    return path


def _folder(tmp_path: Path, name: str = "in") -> Path:
    path = tmp_path / name
    path.mkdir(exist_ok=True)
    return path


def _base(tmp_path: Path) -> dict:
    return dict(driver="live", mode="bam", input_dir=_folder(tmp_path),
                reference=_ref(tmp_path))


def test_the_first_start_records_the_input(tmp_path: Path) -> None:
    run = tmp_path / "out" / "r"
    current = run_input(**_base(tmp_path))
    assert check_run_input(run, current) == "recorded"
    assert json.loads((run / RUN_INPUT_NAME).read_text()) == current
    assert current["input_dir"] == str((tmp_path / "in").resolve())


def test_the_same_input_continues(tmp_path: Path) -> None:
    run = tmp_path / "r"
    check_run_input(run, run_input(**_base(tmp_path)))
    assert check_run_input(run, run_input(**_base(tmp_path))) == "same"


def test_a_pasted_reference_written_to_a_new_file_is_the_same_reference(
        tmp_path: Path) -> None:
    """The app writes a pasted reference to a new file at every start: the content is
    what is compared, not the path."""
    run = tmp_path / "r"
    check_run_input(run, run_input(**_base(tmp_path)))
    again = {**_base(tmp_path), "reference": _ref(tmp_path, "pasted_2.fa")}
    assert check_run_input(run, run_input(**again)) == "same"


@pytest.mark.parametrize("change,named", [("input_dir", "input folder"),
                                          ("mode", "files read"),
                                          ("driver", "kind of run"),
                                          ("reference", "reference")])
def test_another_input_is_refused_and_named(tmp_path: Path, change: str, named: str) -> None:
    run = tmp_path / "out" / "r"
    base = _base(tmp_path)
    check_run_input(run, run_input(**base))
    other = dict(base)
    other[change] = {"input_dir": _folder(tmp_path, "in2"), "mode": "pod5",
                     "driver": "offline",
                     "reference": _ref(tmp_path, "ref2.fa", "TTTT")}[change]
    with pytest.raises(RunInputMismatch) as caught:
        check_run_input(run, run_input(**other))
    said = str(caught.value)
    assert f"{named}:" in said and "Give another run name for a new run" in said
    assert "The run name r is a run of another input" in said
    assert json.loads((run / RUN_INPUT_NAME).read_text()) == run_input(**base), \
        "a refused start must leave the record as it was"


def test_a_folder_moved_since_counts_as_another_input(tmp_path: Path) -> None:
    run = tmp_path / "r"
    check_run_input(run, run_input(**_base(tmp_path)))
    (tmp_path / "in").rename(tmp_path / "moved")
    moved = {**_base(tmp_path), "input_dir": tmp_path / "moved"}
    with pytest.raises(RunInputMismatch, match="input folder"):
        check_run_input(run, run_input(**moved))


@pytest.mark.parametrize("left", ["ingest_manifest.tsv", "basecall_cache.tsv",
                                  "latest_snapshot.json", "qc_result.json",
                                  "run_status.json"])
def test_a_run_made_before_the_record_cannot_be_checked_and_is_refused(
        tmp_path: Path, left: str) -> None:
    run = tmp_path / "r"
    run.mkdir()
    (run / left).write_text("{}")
    with pytest.raises(RunInputMismatch, match="recorded no input"):
        check_run_input(run, run_input(**_base(tmp_path)))
    assert not (run / RUN_INPUT_NAME).exists()


def test_an_empty_folder_or_a_log_alone_is_a_new_run(tmp_path: Path) -> None:
    """run.log is written before the check, also by a refused start."""
    run = tmp_path / "r"
    run.mkdir()
    (run / "run.log").write_text("refused before\n")
    assert check_run_input(run, run_input(**_base(tmp_path))) == "recorded"


def test_a_record_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    run = tmp_path / "r"
    run.mkdir()
    (run / RUN_INPUT_NAME).write_text("{")
    with pytest.raises(RunInputMismatch, match="cannot be read"):
        check_run_input(run, run_input(**_base(tmp_path)))


def test_a_run_must_give_its_reference(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs its reference"):
        check_run_input(tmp_path / "r", run_input(**{**_base(tmp_path),
                                                     "reference": None}))


def test_a_question_without_the_reference_compares_the_rest(tmp_path: Path) -> None:
    """Before Start, a pasted reference has no file yet: the input is still compared."""
    run = tmp_path / "r"
    check_run_input(run, run_input(**_base(tmp_path)))
    same = run_input(**{**_base(tmp_path), "reference": None})
    other = run_input(**{**_base(tmp_path), "reference": None,
                         "input_dir": _folder(tmp_path, "in2")})
    assert refusal(run, same) is None
    assert "input folder:" in refusal(run, other)
    assert refusal(tmp_path / "absent", other) is None, "no folder: a new run"


# --- the command lines --------------------------------------------------------------------

def test_the_offline_command_line_refuses_another_input_before_processing(
        tmp_path: Path, fixture_bam: Path, curlcake_ref: Path, capsys) -> None:
    from mrnaspect.main import main

    one, two, out = tmp_path / "one", tmp_path / "two", tmp_path / "out"
    for folder in (one, two):
        folder.mkdir()
        (folder / "a.bam").write_bytes(fixture_bam.read_bytes())

    def run(folder: Path) -> tuple[int, dict]:
        code = main(["--input-dir", str(folder), "--output-dir", str(out),
                     "--output-name", "r", "--mode", "bam", "--reference",
                     str(curlcake_ref), "--min-reads", "1"])
        return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert run(one)[0] == 0
    assert run(one)[0] == 0, "the same input continues"
    kept = ("qc_result.json", "run_status.json", "latest_snapshot.json", RUN_INPUT_NAME)
    before = {name: (out / "r" / name).read_bytes() for name in kept}
    code, emitted = run(two)
    assert code == 2 and "is a run of another input" in emitted["error"]
    assert "input folder:" in emitted["error"] and str(two.resolve()) in emitted["error"]
    # Nothing was processed: the earlier run's result, progress and record are untouched.
    assert {name: (out / "r" / name).read_bytes() for name in kept} == before


def test_both_command_lines_check_before_anything_runs() -> None:
    root = Path(__file__).resolve().parents[1]
    offline = (root / "main.py").read_text()
    live = (root / "realtime" / "main.py").read_text()
    check = offline.index("check_run_input(run_dir, run_input(driver=\"offline\"")
    assert offline.index("pipe = OfflinePipeline(") < check < offline.index("result = pipe.run()")
    check = live.index("check_run_input(out_dir, run_input(driver=\"live\"")
    assert (live.index("pipe = RealtimePipeline(") < check
            < live.index("pipe.mimic_params = _start_mimic("))
    assert check < live.index("result = pipe.run()")
