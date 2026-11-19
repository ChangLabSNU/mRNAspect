"""The run's arm from the command line and the window (I4).

Core withholds the poly(A) correction without an arm, deliberately: the terminus term
applies on m1psi and is zero on U, and guessing is wrong by up to ~7 nt. The window had no
way to declare one, so a GUI-only run never showed a corrected tail. Decided
2026-10-28: an m1Ψ box by the reference, checked by default; constructs that differ stay
in the config's sample.arms. The box goes to Core as --arm, which sets sample.arm.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from mrnaspect.main import main as offline_main

from .test_gui_start_flow import _step, _tree

PY = [sys.executable, "-m", "mrnaspect.main"]
_GUI = Path(__file__).resolve().parents[2] / "gui"
_TS = _GUI / "src" / "main.ts"
_RS = _GUI / "src-tauri" / "src" / "lib.rs"


def _print_config(*args: str) -> dict:
    r = subprocess.run([*PY, "--print-config", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)["resolved_config"]


# --- Core ---------------------------------------------------------------------------------

def test_the_flag_sets_every_construct_s_arm(tmp_path) -> None:
    assert _print_config("--arm", "U", "--reference", "x.fa")["sample"]["arm"] == "U"


def test_it_beats_the_config_s_arm_and_keeps_its_exceptions(tmp_path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("sample:\n  arm: U\n  arms:\n    HA-WT: U\n")
    sample = _print_config("--config", str(cfg), "--arm", "m1psi", "--reference", "x.fa")[
        "sample"]
    assert sample["arm"] == "m1psi" and sample["arms"] == {"HA-WT": "U"}


def test_a_blank_flag_leaves_the_config_s_arm(tmp_path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("sample:\n  arm: U\n")
    assert _print_config("--config", str(cfg), "--arm", "",
                         "--reference", "x.fa")["sample"]["arm"] == "U"


def test_the_live_driver_takes_it_too(monkeypatch, capsys) -> None:
    """What the live driver hands load_config, caught before it builds anything. Reading
    --help for the word "--arm" also passed for a flag renamed --arm-unused."""
    from mrnaspect.realtime import main as rt

    seen: dict = {}

    def capture(config, overrides, *args, **kwargs):
        seen.update(overrides)
        raise ValueError("stop: the overrides are what this test reads")

    monkeypatch.setattr(rt, "load_config", capture)
    assert rt.main(["--arm", "U", "--input-dir", "in", "--output-dir", "out"]) == 2
    assert seen.get("sample.arm") == "U", seen


def _run(tmp: Path, bam: Path, ref: Path, *extra: str, config: str | None = None) -> Path:
    src = tmp / "in"
    src.mkdir(parents=True)
    (src / bam.name).symlink_to(bam)
    bai = bam.with_suffix(bam.suffix + ".bai")
    if bai.exists():
        (src / bai.name).symlink_to(bai)
    argv = ["--input-dir", str(src), "--reference", str(ref), "--output-dir",
            str(tmp / "out"), "--output-name", "r", "--mode", "bam", "--min-reads", "1", *extra]
    if config is not None:
        (tmp / "c.yaml").write_text(config)
        argv += ["--config", str(tmp / "c.yaml")]
    assert offline_main(argv) == 0
    return tmp / "out" / "r"


def _arms(run: Path) -> dict[str, str]:
    snaps = json.loads((run / "latest_snapshot.json").read_text())["snapshots"]
    return {k.split(":", 1)[1]: v["polya"]["correction"]["arm"]
            for k, v in snaps.items() if k.startswith("construct:")}


def test_through_a_real_run_the_flag_reaches_every_construct(
        tmp_path, variant_bam, qk_ha_ref) -> None:
    run = _run(tmp_path, variant_bam, qk_ha_ref, "--arm", "m1psi")
    assert _arms(run) == {"QK-09": "m1psi", "HA-WT": "m1psi"}


def test_and_the_config_s_exceptions_still_apply(tmp_path, variant_bam, qk_ha_ref) -> None:
    run = _run(tmp_path, variant_bam, qk_ha_ref, "--arm", "m1psi",
               config="sample:\n  arms:\n    HA-WT: U\n")
    assert _arms(run) == {"QK-09": "m1psi", "HA-WT": "U"}


def test_an_arm_core_does_not_know_stops_the_run(tmp_path, variant_bam, qk_ha_ref) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / variant_bam.name).symlink_to(variant_bam)
    r = subprocess.run([*PY, "--input-dir", str(src), "--reference", str(qk_ha_ref),
                        "--output-dir", str(tmp_path / "out"), "--mode", "bam",
                        "--arm", "partial"], capture_output=True, text=True)
    assert r.returncode != 0
    assert "arm must be one of" in r.stdout + r.stderr


# --- the window (text checks: no Rust or TypeScript toolchain here) ------------------------

def test_the_choice_follows_the_reference_m1psi_checked_by_default() -> None:
    """Two cards on a screen of their own, right after the reference they describe (one
    choice per screen, 2026-11-12; a checkbox beside the reference before)."""
    html = (_GUI / "index.html").read_text()
    assert re.search(r'<input type="radio" name="arm" id="m1psi" checked\s*/>', html)
    assert re.search(r'<input type="radio" name="arm" id="armu"\s*/>', html)
    assert _step(_tree(), "m1psi") == _step(_tree(), "armu") == "arm"
    assert html.index('id="ref"') < html.index('id="m1psi"') < html.index('id="output"')


def test_the_window_sends_the_box_as_the_arm() -> None:
    ts = _TS.read_text()
    assert re.search(r"arm:\s*armValue\(\)", ts)
    assert re.search(r'\.checked \? "m1psi" : "U"', ts)


def test_rust_passes_it_as_arm_and_refuses_anything_else() -> None:
    rs = _RS.read_text()
    sig = rs[rs.index("fn start_qc("):rs.index("{", rs.index("fn start_qc("))]
    assert "arm: String" in sig
    assert 'if arm != "m1psi" && arm != "U"' in rs
    assert re.search(r'args\.push\("--arm"\);\s*args\.push\(&arm\);', rs)


def test_a_config_naming_the_arm_sets_the_box() -> None:
    """Otherwise the box's default, sent as a flag, would overrule a config saying U."""
    ts = _TS.read_text()
    body = ts[ts.index("async function refreshConfig"):]
    body = body[:body.index("\n}\n")]
    assert 'dig(resolved, "sample.arm")' in body
    assert re.search(r'\(\$\(cfgArm === "m1psi" \? "m1psi" : "armu"\) as HTMLInputElement\)'
                     r'\.checked = true', body)


def test_the_review_says_the_arm() -> None:
    ts = _TS.read_text()
    body = ts[ts.index("function reviewRows"):]
    body = body[:body.index("\n}\n")]
    # Named as its step is (test_gui_words.py); the value is said by one function.
    assert 'rows.push(["Modification", modificationText(armValue())]);' in body
