"""A blank field means "what the config says" -- in the run, not only in the hint.

Measured 2026-10-28 (I8). With a config setting input_dir, output_dir, output_name and
reference, and those four window fields blank, the grey hints showed the config's values
and the run got something else:

    field        hint (read_config)        run (start_qc's flags)
    Input dir    /data/from_config/bam     ""            -> "no input directory was supplied"
    Output dir   /data/from_config/out     ""            -> "no output directory was supplied"
    Run name     name_from_config          gui_<unix seconds>, silently
    Reference    /data/from_config/ref.fa  the config's  (main.py maps "" to None)

The shell passed every path flag, blank or not, and always passed --output-name with a name
of its own. Core's load_config skipped only None, so "" overwrote the config one step
before resolve_io -- which says "blank is absent" -- could treat it as absent. The I2
tests asked read_config's question, never the run's.

Three fixes, one per layer: Core treats a blank CLI value as absent where precedence is
applied; --print-config names the run directory the run would use; the shell passes a path
flag only when typed and asks Core where the run writes before creating anything.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from mrnaspect.main import load_config

PY = [sys.executable, "-m", "mrnaspect.main"]
_GUI = Path(__file__).resolve().parents[2] / "gui"
_RS = _GUI / "src-tauri" / "src" / "lib.rs"

_CFG = ("input_dir: /data/from_config/bam\noutput_dir: /data/from_config/out\n"
        "output_name: name_from_config\nreference: /data/from_config/ref.fa\n")


def _print_config(*args: str) -> dict:
    r = subprocess.run([*PY, "--print-config", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


# --- Core: blank is absent where precedence is applied --------------------------------

@pytest.mark.parametrize("blank", ["", "   "])
@pytest.mark.parametrize("key", ["input_dir", "output_dir", "output_name", "reference"])
def test_a_blank_flag_leaves_the_configs_value(tmp_path, key, blank) -> None:
    cfg = tmp_path / "run.yaml"
    cfg.write_text(_CFG)
    resolved = load_config(cfg, {key: blank}, require_reference=False)
    assert resolved[key] == load_config(cfg, {}, require_reference=False)[key]


def test_a_typed_flag_still_wins(tmp_path) -> None:
    cfg = tmp_path / "run.yaml"
    cfg.write_text(_CFG)
    assert load_config(cfg, {"input_dir": "/typed"}, require_reference=False)[
        "input_dir"] == "/typed"


def test_the_flags_the_window_sends_for_blank_fields_keep_the_config(tmp_path) -> None:
    """The measurement itself, as a test: exactly what start_qc used to send."""
    cfg = tmp_path / "run.yaml"
    cfg.write_text(_CFG)
    out = _print_config("--input-dir", "", "--reference", "", "--output-dir", "",
                        "--mode", "bam", "--min-reads", "1", "--config", str(cfg))
    got = {k: out["resolved_config"][k]
           for k in ("input_dir", "output_dir", "output_name", "reference")}
    assert got == {"input_dir": "/data/from_config/bam",
                   "output_dir": "/data/from_config/out",
                   "output_name": "name_from_config",
                   "reference": "/data/from_config/ref.fa"}


# --- Core: --print-config names the run directory ---------------------------------------

def test_print_config_names_the_run_directory(tmp_path) -> None:
    out = _print_config("--output-dir", str(tmp_path), "--output-name", "mine")
    assert out["run_dir"] == str(tmp_path / "mine") and out["run_dir_error"] is None


def test_without_a_name_it_is_core_s_own_default(tmp_path) -> None:
    """run_YYYYMMDD_HHMMSS in local time, Core's format -- which the shell can now use
    instead of inventing gui_<unix seconds>."""
    out = _print_config("--output-dir", str(tmp_path))
    assert re.fullmatch(re.escape(str(tmp_path)) + r"/run_\d{8}_\d{6}(_\d+)?", out["run_dir"])


def test_a_run_the_window_started_unnamed_is_one_continue_latest_finds(tmp_path) -> None:
    """P2 (release_work.yaml): --continue-latest looks for run_* directories, and the shell
    used to call an unnamed run gui_<unix seconds>, so a run started from the window could
    never be continued from the command line. Since I8 the shell takes Core's name, so the
    two now meet: the directory the window would get is the one the flag finds."""
    from mrnaspect.main import RUN_DIR_PREFIX, resolve_run_dir

    run_dir = Path(_print_config("--output-dir", str(tmp_path))["run_dir"])
    assert run_dir.name.startswith(RUN_DIR_PREFIX), run_dir
    run_dir.mkdir()                         # the run creates it; --print-config does not
    assert resolve_run_dir(tmp_path, continue_latest=True) == run_dir


def test_no_output_directory_is_reported_not_raised() -> None:
    """--print-config also fills the window's hints, which must not fail for want of an
    output directory; the run's start reports the error instead."""
    out = _print_config()
    assert out["run_dir"] is None
    assert "no output directory was supplied" in out["run_dir_error"]


def test_a_bad_name_comes_back_in_core_s_words(tmp_path) -> None:
    out = _print_config("--output-dir", str(tmp_path), "--output-name", "../escape")
    assert out["run_dir"] is None
    assert "single directory name" in out["run_dir_error"]


def test_naming_the_run_directory_creates_nothing(tmp_path) -> None:
    _print_config("--output-dir", str(tmp_path / "out"))
    assert not (tmp_path / "out").exists()


# --- the shell -----------------------------------------------------------------------------

def _fn(name: str) -> str:
    rs = _RS.read_text()
    start = rs.index(f"fn {name}(")
    return rs[start: rs.index("\n}\n", start)]


def _code(src: str) -> str:
    return re.sub(r"//[^\n]*", " ", src)


_shell = pytest.mark.skipif(not _RS.exists(), reason="GUI sources are not in this checkout")


@_shell
def test_the_shell_asks_core_where_the_run_writes_before_creating_anything() -> None:
    body = _code(_fn("start_qc"))
    asked = body.index("print_config(")
    assert asked < body.index("create_dir_all(") and asked < body.index(".spawn()")
    assert '"--output-dir", output_dir.as_str()' in body
    assert '"--output-name", run_name.as_str()' in body


@_shell
def test_the_shell_names_no_run_of_its_own() -> None:
    """gui_<unix seconds> overrode a config's output_name and contradicted the field's
    own placeholder, "blank = run_YYYYMMDD_HHMMSS"."""
    assert '"gui_' not in _code(_fn("start_qc"))


@_shell
def test_input_and_reference_are_passed_only_when_typed() -> None:
    body = _code(_fn("start_qc"))
    vectors = body[body.index("let mut args: Vec<&str> = if realtime {"):]
    vectors = vectors[:vectors.index("};")]
    for flag in ("--input-dir", "--reference"):
        assert f'"{flag}"' not in vectors, f"{flag} is passed unconditionally again"
    guarded = body[body.index('for (flag, value) in [("--input-dir"'):]
    assert "if !value.trim().is_empty()" in guarded[:guarded.index("}\n    }")]


@_shell
def test_a_relative_output_directory_is_refused() -> None:
    """Relative to wherever the app was started -- "/" for a packaged app. The condition
    itself, leading straight to the refusal: a check that the words merely appear passed
    with the refusal disabled (`if false && !run_dir.is_absolute()`)."""
    assert re.search(r"\n\s*if !run_dir\.is_absolute\(\) \{\s*return Err\(",
                     _code(_fn("start_qc")))


@_shell
def test_core_s_refusal_of_a_name_is_passed_on_in_its_words() -> None:
    body = _code(_fn("start_qc"))
    assert 'place["run_dir_error"].as_str()' in body
