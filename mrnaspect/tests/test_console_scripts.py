"""The installed entry points exist in THIS environment and honour the stdout contract.

Why this file exists
--------------------
`pyproject.toml` declares `mrnaspect` and `mrnaspect-realtime` as console scripts, and the GUI
invokes Core by name — it does not import it. Nothing checked that those scripts were
ever created.

They vanish easily. A pip-installed package is not in `pixi.lock`, so any `pixi install`
that rebuilds the environment (a changed lock, an added platform, `pixi clean`) removes
it. Measured 2026-09-21: adding a platform rebuilt the env, the editable install went
with it, and `pixi run test` stayed **green** — because the tests import `mrnaspect` through
the repo-root `conftest.py`'s sys.path entry, not through the install. The suite was
passing over a product whose entry points did not exist, and the first thing to notice
was the Tauri shell failing with `mrnaspect: command not found`.

Resolved through ``sys.prefix``, never through PATH
---------------------------------------------------
While diagnosing that, `pixi run which mrnaspect` answered
with a stale binary of the old name, in an unrelated personal conda that
happened to be on PATH. A PATH-based check would have passed against the wrong program
entirely. This is the same reason `wrappers/_shell.resolve_tool` puts `sys.prefix/bin`
ahead of PATH, and the rule is worth applying to ourselves.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = ("mrnaspect", "mrnaspect-realtime")


def _script(name: str) -> Path:
    """The console script belonging to the interpreter running this test."""
    return Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin") / name


@pytest.mark.parametrize("name", SCRIPTS)
def test_the_console_script_exists_in_this_environment(name: str) -> None:
    path = _script(name)
    assert path.is_file(), (
        f"{name} is not installed in {sys.prefix}. The editable install was probably "
        f"wiped by an environment rebuild — run `pixi run dev-install`. Deliberately NOT "
        f"a skip: the GUI launches Core by name, so a missing script is a broken product, "
        f"not an absent convenience.")


@pytest.mark.parametrize("name", SCRIPTS)
def test_the_console_script_actually_runs(name: str) -> None:
    """--help, so this stays fast and needs no data: it proves the entry point resolves."""
    out = subprocess.run([str(_script(name)), "--help"], capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr
    assert "--output-dir" in out.stdout, out.stdout[:400]


def test_the_offline_script_emits_the_stdout_contract(tmp_path: Path) -> None:
    """One JSON object on stdout even for a refusal — checked through the SCRIPT.

    The contract is tested in-process by `test_cli_result_contract.py`; this is the same
    claim through the artifact a GUI actually launches, which is the only form the Tauri
    shell can observe. A usage error is used because it needs no inputs and still has to
    produce the full object.
    """
    out = subprocess.run(
        [str(_script("mrnaspect")), "--output-dir", str(tmp_path / "out"),
         "--reference", "/placeholder/ref.fasta", "--mode", "bam"],
        capture_output=True, text=True, timeout=120)
    assert out.returncode == 2, (out.returncode, out.stdout, out.stderr)
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout must carry exactly one object, got {lines!r}"
    obj = json.loads(lines[0])
    assert obj["status"] == "error" and obj["exit_code"] == 2
    assert obj["error"], obj
    # The human sentence belongs on the other channel; a GUI reading stdout must not
    # have to filter prose out of it.
    assert out.stderr.strip()
