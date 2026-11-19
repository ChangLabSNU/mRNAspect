"""Where a run reads and writes can come from the config, not only from a flag.

On a given instrument these paths do not change between runs, and retyping a path is how
the wrong one gets typed. So `input_dir`, `output_dir` and `output_name` join `reference`
and `basecall.model` as things a config can answer — with the flag still winning, because
a one-off run must not need a config edit.

Two properties are asserted that are easy to lose:

* **Blank is absent, not an answer.** Both flags used to be `type=Path`, and `Path("")` is
  `Path(".")` — truthy. An unfilled field would have resolved to the current directory
  instead of being refused. That is the defect `--reference` carried, fixed there on
  2026-10-21; this is the same class, caught across the whole set rather than at the one
  place it was found.
* **Both drivers.** They carry the same lines, so a test that exercised only the offline
  one would leave the realtime one broken.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mrnaspect.main import resolve_io

from .conftest import LAB_PRIVATE


# -- the resolver itself ------------------------------------------------------------

def test_the_config_answers_when_no_flag_did():
    inp, out, name = resolve_io(
        {"input_dir": "/in", "output_dir": "/out", "output_name": "run7"},
        need_input=True)
    assert (inp, out, name) == (Path("/in"), Path("/out"), "run7")


def test_a_missing_output_dir_names_BOTH_channels():
    """Neither is "required" any more, so neither may be named alone."""
    with pytest.raises(ValueError) as exc:
        resolve_io({"input_dir": "/in"}, need_input=True)
    msg = str(exc.value)
    assert "--output-dir" in msg and "output_dir:" in msg, msg


def test_a_missing_input_dir_names_BOTH_channels():
    with pytest.raises(ValueError) as exc:
        resolve_io({"output_dir": "/out"}, need_input=True)
    msg = str(exc.value)
    assert "--input-dir" in msg and "input_dir:" in msg, msg


def test_input_is_optional_when_the_caller_says_so():
    """`--resume-from-snapshot` renders from a snapshot and reads no inputs."""
    inp, out, _ = resolve_io({"output_dir": "/out"}, need_input=False)
    assert inp is None and out == Path("/out")


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
@pytest.mark.parametrize("key", ["input_dir", "output_dir", "output_name"])
def test_blank_is_absent_not_the_current_directory(key: str, blank: str):
    cfg = {"input_dir": "/in", "output_dir": "/out", "output_name": "n"}
    cfg[key] = blank
    if key == "output_name":
        assert resolve_io(cfg, need_input=True)[2] is None
    else:
        with pytest.raises(ValueError):
            resolve_io(cfg, need_input=True)


# -- end to end, through a real driver ----------------------------------------------

def _run(args: list[str]) -> dict:
    """Invoke the offline driver and return its one JSON object."""
    proc = subprocess.run([sys.executable, "-m", "mrnaspect.main", *args],
                          capture_output=True, text=True)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_a_config_alone_drives_a_whole_run(tmp_path: Path, fixture_bam: Path,
                                           curlcake_ref: Path):
    """No path flags at all: the config says where to read, where to write, what to call it."""
    inputs = tmp_path / "in"; inputs.mkdir()
    (inputs / fixture_bam.name).symlink_to(fixture_bam)
    cfg = tmp_path / "my.yaml"
    cfg.write_text(
        f"input_dir: {inputs}\noutput_dir: {tmp_path / 'out'}\n"
        f"output_name: named_by_config\nreference: {curlcake_ref}\nmin_reads: 1\n")

    res = _run(["--config", str(cfg), "--mode", "bam"])
    assert res["exit_code"] == 0, res
    assert Path(res["run_dir"]) == tmp_path / "out" / "named_by_config"


def test_the_flag_still_wins(tmp_path: Path, fixture_bam: Path, curlcake_ref: Path):
    inputs = tmp_path / "in"; inputs.mkdir()
    (inputs / fixture_bam.name).symlink_to(fixture_bam)
    cfg = tmp_path / "my.yaml"
    cfg.write_text(
        f"input_dir: {inputs}\noutput_dir: {tmp_path / 'out'}\n"
        f"output_name: from_config\nreference: {curlcake_ref}\nmin_reads: 1\n")

    res = _run(["--config", str(cfg), "--mode", "bam", "--output-name", "from_flag"])
    assert res["exit_code"] == 0, res
    assert Path(res["run_dir"]).name == "from_flag"


def test_a_config_error_is_reported_not_raised(tmp_path: Path):
    """It reaches the terminal as the one line that says what to fix.

    `load_config` is called before the run directory exists now, and its ValueError was
    caught by nobody in EITHER driver — the most ordinary operator mistake there is
    printed a traceback.
    """
    res = _run(["--mode", "bam", "--output-dir", str(tmp_path)])
    assert res["exit_code"] == 2
    assert "no reference was supplied" in res["error"]


# -- the two drivers have to agree --------------------------------------------------

@pytest.mark.parametrize("module", ["mrnaspect.main", "mrnaspect.realtime.main"])
@pytest.mark.parametrize("flag", ["--input-dir", "--output-dir", "--output-name"])
def test_neither_driver_declares_these_as_Path_or_required(module: str, flag: str):
    import importlib

    parser = _build(importlib.import_module(module))
    action = next(a for a in parser._actions if flag in a.option_strings)
    assert action.type is str, f"{module}{flag}: type is {action.type!r}, not str"
    assert not action.required, f"{module}{flag}: still required"


def _build(mod) -> "argparse.ArgumentParser":
    """The parser, by parsing nothing — these modules build it inside `_parse_args`."""
    import argparse
    captured = {}
    real = argparse.ArgumentParser.parse_args

    def spy(self, *a, **kw):
        captured["p"] = self
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = spy
    try:
        try:
            mod._parse_args([])
        except SystemExit:
            pass
    finally:
        argparse.ArgumentParser.parse_args = real
    return captured["p"]


# -- a run name is a directory name, so it has to be one ----------------------------

@pytest.mark.parametrize("bad", ["../escaped", "/etc/passwd", "a/b", ".", "..",
                                 "sub/dir/deep"])
def test_a_run_name_that_is_a_PATH_is_refused(bad: str):
    """Unchecked, `output_dir / output_name` leaves the directory the operator chose.

    Three different ways, and the quiet one is the worst:

        "/etc/passwd"  -> /etc/passwd      an absolute name REPLACES output_dir
        "../escaped"   -> /out/../escaped  climbs out
        "."            -> /out             writes into the PARENT

    That last defeats `resolve_run_dir`'s stated purpose. Its docstring says every run
    gets its own subdirectory because artifacts are named by KIND, not by run, so a
    second run lands on top of the first's report and qc_result.json. `--output-name .`
    was all it took.
    """
    from mrnaspect.main import resolve_run_dir

    with pytest.raises(ValueError, match="single directory name"):
        resolve_run_dir(Path("/out"), output_name=bad)


@pytest.mark.parametrize("good", ["run7", "batch-04_12", "QK.2026", "  spaced  "])
def test_an_ordinary_run_name_is_kept_and_trimmed(good: str):
    from mrnaspect.main import resolve_run_dir

    assert resolve_run_dir(Path("/out"), output_name=good) == Path("/out") / good.strip()


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_run_name_falls_back_to_the_timestamp_not_a_blank_directory(blank: str):
    """Not a directory whose name is three spaces, and not the parent."""
    from mrnaspect.main import RUN_DIR_PREFIX, resolve_run_dir

    got = resolve_run_dir(Path("/out"), output_name=blank)
    assert got.parent == Path("/out")
    assert got.name.startswith(RUN_DIR_PREFIX), got


# ---------------------------------------------------------------------------
# No config in this package may name a path that exists on one machine.
#
# `test_gui_ships_no_dev_machine.py` has enforced exactly this since 2026-10-21 --
# for the GUI, because the GUI is where the pre-filled dev paths were found. Its
# subject list is three files in `gui/`. Meanwhile `mrnaspect/config/` held three paths on
# one lab filesystem (a reference, a model inside a server bundle and a fine-tuned model's
# folder) the entire time, and nothing looked.
#
# That is this project's own recurring shape -- a guard scoped to where the bug was
# found rather than to the class it belongs to -- so this one is scoped to the class:
# it GLOBS the config directory instead of listing files, and a config added tomorrow
# is covered without anyone remembering to add it.
# ---------------------------------------------------------------------------

#: Roots that only exist on one machine: a developer's home, and the developing lab's
#: own mounts, which are named in an untracked file (conftest LAB_PRIVATE) so that no
#: tracked file names them. Same list as the GUI guard; kept duplicated rather than
#: imported, because importing it would couple a config test to the GUI test's skipif
#: (GUI sources absent -> whole module skipped).
_PRIVATE_ROOTS = ("/Users/", "/home/", *LAB_PRIVATE.get("roots", ()))

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def _config_files() -> list[Path]:
    return sorted(p for p in _CONFIG_DIR.rglob("*")
                  if p.is_file() and p.suffix in {".yaml", ".yml", ".py", ".json"})


def test_there_are_configs_to_check() -> None:
    """Guards the glob: an empty subject set would make every assertion below vacuous."""
    files = _config_files()
    assert len(files) >= 3, f"expected the shipped configs plus examples, found {files}"
    assert any(p.name == "run_config.yaml" for p in files)


@pytest.mark.parametrize("path", _config_files(), ids=lambda p: p.name)
def test_no_config_names_a_one_machine_path(path: Path) -> None:
    offenders = [
        f"{path.name}:{lineno}: {line.strip()}"
        for lineno, line in enumerate(path.read_text().splitlines(), 1)
        for root in _PRIVATE_ROOTS
        if root in line
    ]
    assert not offenders, (
        f"{path.relative_to(_CONFIG_DIR.parent)} names a path that exists on one "
        f"machine:\n  " + "\n  ".join(offenders) + "\n"
        "Use a /path/to/... placeholder with an `# <-- EDIT:` marker. A real path is "
        "worse than a placeholder here: it RUNS on the machine that wrote it, so the "
        "config looks correct until someone else opens it.")
