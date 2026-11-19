"""What a config DOES, said where it is chosen.

A check of I2 with and without a config did not show what changed (I3,
2026-10-28). Only seven keys drive a grey hint in the window -- input_dir, reference,
output_dir, output_name, basecall.model and the server's binary and device -- so a config
setting sample.arm, sample.arms, min_reads or a module switch changed the run and showed
nothing at all.

Core now reports, per --config layer, the leaves whose value differs from the stack below
it, and the window prints the operator's under the Config field. The defaults stay in one
place: nothing outside load_config compares against them.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from mrnaspect.main import config_changes, load_config

_GUI = Path(__file__).resolve().parents[2] / "gui"
_TS = _GUI / "src" / "main.ts"


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


def test_a_config_s_changes_are_listed(tmp_path) -> None:
    cfg = _write(tmp_path, "run.yaml", "min_reads: 7\nsample:\n  arm: m1psi\n")
    (layer,) = config_changes([cfg])
    assert layer["path"] == str(cfg.resolve())
    assert layer["changes"] == {"min_reads": 7, "sample.arm": "m1psi"}


def test_restating_a_default_is_not_a_change(tmp_path) -> None:
    default = load_config(None, {}, require_reference=False)["min_reads"]
    cfg = _write(tmp_path, "run.yaml", f"min_reads: {default}\nmode: pod5\n")
    (layer,) = config_changes([cfg])
    assert layer["changes"] == {"mode": "pod5"}


def test_each_layer_is_compared_with_the_one_below(tmp_path) -> None:
    """The window passes This Mac's layer first; each file's changes are its own. The
    second file restates the first's value, which against the layer below changes
    nothing -- against the shipped defaults alone it would look like a change."""
    a = _write(tmp_path, "machine.yaml", "min_reads: 7\n")
    b = _write(tmp_path, "run.yaml", "min_reads: 7\nmode: pod5\n")
    first, second = config_changes([a, b])
    assert first["changes"] == {"min_reads": 7}
    assert second["changes"] == {"mode": "pod5"}


def test_a_per_construct_map_is_flattened_to_its_leaves(tmp_path) -> None:
    cfg = _write(tmp_path, "run.yaml", "sample:\n  arms:\n    QK-01: U\n    HA-L4: m1psi\n")
    (layer,) = config_changes([cfg])
    assert layer["changes"] == {"sample.arms.QK-01": "U", "sample.arms.HA-L4": "m1psi"}


def test_no_config_changes_nothing() -> None:
    assert config_changes(None) == []


def test_print_config_carries_them(tmp_path) -> None:
    cfg = _write(tmp_path, "run.yaml", "min_reads: 7\n")
    r = subprocess.run([sys.executable, "-m", "mrnaspect.main", "--print-config",
                        "--config", str(cfg)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["config_changes"] == [
        {"path": str(cfg.resolve()), "changes": {"min_reads": 7}}]


# --- the window -----------------------------------------------------------------------------

_shell = pytest.mark.skipif(not _TS.exists(), reason="GUI sources are not in this checkout")


def _refresh() -> str:
    ts = re.sub(r"/\*.*?\*/", " ", _TS.read_text(), flags=re.S)
    start = ts.index("async function refreshConfig(")
    return ts[start: ts.index("\n}\n", start)]


@_shell
def test_the_hint_names_the_operator_s_layer_and_only_when_one_is_named() -> None:
    """read_config passes This Mac's layer first, so the operator's file is the last
    entry; with no file named the last entry is This Mac's, which is not the config."""
    body = _refresh()
    assert 'answer["config_changes"]' in body
    assert "if (named && layers.length) changes = layers[layers.length - 1]" in body
    assert "named ? describeChanges(changes) : CONFIG_HINT" in body


@_shell
def test_the_window_compares_against_nothing_of_its_own() -> None:
    """describeChanges formats what Core found; it holds no default to compare with."""
    ts = _TS.read_text()
    fn = ts[ts.index("function describeChanges("):]
    fn = fn[:fn.index("\n}\n")]
    assert "dig(" not in fn and "resolved" not in fn and "1000" not in fn
