"""The GUI's one line on short poly(A) tails reads keys Core writes.

The Rust shell and the TypeScript cannot be built here, so this holds the contract at
the source: the panel passes the integrator's poly(A) blocks through, and every
short-tail key the line reads is one the integrator emits.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = (ROOT / "gui" / "src-tauri" / "src" / "lib.rs").read_text()
MAIN = (ROOT / "gui" / "src" / "main.ts").read_text()
INTEGRATOR = (ROOT / "mrnaspect" / "analysis" / "modules" / "qc_integrator.py").read_text()


def test_the_panel_passes_the_short_tail_blocks_through() -> None:
    assert '"polya_tail":  v["qc_metrics"]["metrics"]["polya_tail"]' in LIB
    assert '"constructs":  v["qc_metrics"]["constructs"]' in LIB


def test_the_line_is_drawn_on_the_polya_panel() -> None:
    """Since the redesign (2026-11-06) it sits under the Poly(A) panel's chart, live and
    after the run."""
    assert "function shortTails(d: any)" in MAIN
    assert "shortTails(p.distributions)" in MAIN


def test_every_key_the_line_reads_is_one_the_integrator_writes() -> None:
    read = set(re.findall(r"\b(short_tail_[a-z_]+)\b", MAIN))
    assert read, "the line reads no short-tail key"
    missing = {k for k in read if f"{k}=" not in INTEGRATOR}
    assert not missing, missing
