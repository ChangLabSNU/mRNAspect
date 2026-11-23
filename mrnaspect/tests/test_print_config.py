"""`--print-config`: what a run WOULD use, answered before the run.

A surface that cannot see the config shows the operator empty fields and lets them
conclude nothing is configured. This is the one source it reads — Core resolves the
config and says what it resolved to, so no second copy of the defaults exists anywhere.

The first thing it showed was a bug, which is the argument for having it:
`test_a_partial_nested_section_keeps_its_siblings`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mrnaspect.main import load_config

PY = [sys.executable, "-m", "mrnaspect.main"]


def _print_config(*args, cwd=None) -> dict:
    r = subprocess.run([*PY, "--print-config", *args], capture_output=True, text=True,
                       cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _write(tmp_path: Path, body: str, name: str = "mine.yaml") -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


# --- the defect it found ----------------------------------------------------

def test_a_partial_nested_section_keeps_its_siblings(tmp_path) -> None:
    """Setting one server key must not delete the rest of the server block.

    The merge was one level deep and `basecall.server` is two, so a config naming only
    `binary` replaced the whole block: `port`, `host`, the timeouts and `auto_start` all
    vanished. A missing `auto_start` is falsy, so the server silently stopped being
    started — a config that looks like it changed one path changing whether basecalling
    happens at all.
    """
    cfg = load_config(_write(tmp_path, """
basecall:
  server:
    binary: /opt/ont/bin/dorado_basecall_server
"""), {}, require_reference=False)
    server = cfg["basecall"]["server"]
    assert server["binary"] == "/opt/ont/bin/dorado_basecall_server"
    assert server["port"] == 5555, "the shipped port was dropped"
    assert server["auto_start"] is True, "auto_start was dropped, which disables the server"
    assert server["startup_timeout_s"] == 120


def test_a_list_is_replaced_whole_not_appended(tmp_path) -> None:
    """Naming three modules means those three, not those three plus the shipped five."""
    cfg = load_config(_write(tmp_path, "modules: [polya, consensus]"), {},
                      require_reference=False)
    assert cfg["modules"] == ["polya", "consensus"]


def test_a_scalar_still_wins_over_the_shipped_one(tmp_path) -> None:
    cfg = load_config(_write(tmp_path, "min_reads: 250"), {}, require_reference=False)
    assert cfg["min_reads"] == 250


# --- the flag itself --------------------------------------------------------

def test_it_answers_without_a_reference() -> None:
    """A surface asks BEFORE one is chosen, which is the whole point of asking."""
    out = _print_config()
    assert out["resolved_config"]["reference"] is None


def test_a_real_run_still_refuses_without_a_reference() -> None:
    """The exemption is for --print-config only."""
    r = subprocess.run([*PY, "--input-dir", ".", "--output-dir", "."],
                       capture_output=True, text=True)
    assert r.returncode == 2
    assert "no reference was supplied" in r.stdout


def test_it_creates_nothing(tmp_path) -> None:
    """Answering a question must not leave a run directory behind."""
    before = set(tmp_path.iterdir())
    _print_config(cwd=tmp_path)
    assert set(tmp_path.iterdir()) == before


def test_it_needs_no_input_directory() -> None:
    """resolve_io demands one; this returns before it."""
    assert _print_config()["resolved_config"]["input_dir"] is None


def test_it_says_where_the_values_came_from(tmp_path) -> None:
    """A field that pre-fills with something surprising is otherwise unanswerable."""
    cfg_file = _write(tmp_path, "min_reads: 7")
    out = _print_config("--config", str(cfg_file))
    assert out["config_paths"] == [str(cfg_file.resolve())]
    assert out["shipped_config"].endswith("run_config.yaml")
    assert out["resolved_config"]["min_reads"] == 7

    bare = _print_config()
    assert bare["config_paths"] == []


# --- --config is a stack, not a slot ----------------------------------------

def test_a_later_config_merges_over_an_earlier_one(tmp_path) -> None:
    """The GUI must add `mode: pod5` on top of a config it did not write.

    A second --config that REPLACED the first would throw the operator's file away; one
    that was ignored would be a knob wired to nothing. Both halves are asserted: the
    later file wins where they collide, and the earlier one survives where they do not.
    """
    a = _write(tmp_path, "min_reads: 7\nmode: bam\n", name="a.yaml")
    b = _write(tmp_path, "mode: pod5\n", name="b.yaml")
    out = _print_config("--config", str(a), "--config", str(b))
    assert out["resolved_config"]["mode"] == "pod5"
    assert out["resolved_config"]["min_reads"] == 7
    assert out["config_paths"] == [str(a.resolve()), str(b.resolve())]


def test_layering_is_recursive_too(tmp_path) -> None:
    """A layer naming one nested key must not drop the rest of that section."""
    a = _write(tmp_path, "basecall:\n  server:\n    port: 5999\n", name="a.yaml")
    b = _write(tmp_path, "basecall:\n  server:\n    binary: /opt/x\n", name="b.yaml")
    server = _print_config("--config", str(a), "--config", str(b),
                           )["resolved_config"]["basecall"]["server"]
    assert server["port"] == 5999 and server["binary"] == "/opt/x"
    assert "auto_start" in server, "the shipped block was replaced, not merged"


def test_it_is_not_the_run_result_contract() -> None:
    """A run's verdict and "what would this use" are different questions.

    Forcing this into RESULT_KEYS would also have scored it as a failure, because
    `exit_code_for` returns 1 for any status outside OK_STATUSES.
    """
    out = _print_config()
    assert "status" not in out and "exit_code" not in out
    assert set(out) == {"resolved_config", "config_paths", "shipped_config",
                        "run_dir", "run_dir_error", "run_dir_error_code",
                        "run_dir_state", "config_changes"}


def test_what_it_prints_is_what_a_run_would_use(tmp_path) -> None:
    """Same function, same arguments — not a reimplementation that can drift."""
    cfg_file = _write(tmp_path, "min_reads: 42\nmode: pod5\n")
    printed = _print_config("--config", str(cfg_file))["resolved_config"]
    assert printed == load_config(cfg_file, {}, require_reference=False)


# --- a config mistake is a sentence, not a traceback ------------------------

@pytest.mark.parametrize("driver", ["mrnaspect.main", "mrnaspect.realtime.main"])
@pytest.mark.parametrize("extra", [["--print-config"], []])
def test_a_missing_config_file_is_refused_in_one_line(driver, extra, tmp_path) -> None:
    """`--config nope.yaml` is the most ordinary config mistake there is.

    It raises FileNotFoundError, which is an OSError and NOT a ValueError, so it walked
    past the handler written to stop precisely this -- a handler scoped to the exception
    it was found with rather than to the class. The GUI shows Core's stderr under the
    Config field, so the difference is a sentence the operator can act on versus fifteen
    lines of Python internals in the window.
    """
    if driver == "mrnaspect.realtime.main" and extra:
        pytest.skip("--print-config is the offline driver's flag")
    r = subprocess.run([sys.executable, "-m", driver, *extra,
                        "--config", str(tmp_path / "nope.yaml")],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 2, f"expected the refusal exit code, got {r.returncode}"
    assert "Traceback" not in r.stderr, r.stderr
    assert "nope.yaml" in r.stderr and r.stderr.lstrip().startswith("ERROR:")


def test_an_unreadable_config_is_refused_the_same_way(tmp_path) -> None:
    """A directory where a file was named: the same class, a different errno."""
    (tmp_path / "adir.yaml").mkdir()
    r = subprocess.run([*PY, "--print-config", "--config", str(tmp_path / "adir.yaml")],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr
