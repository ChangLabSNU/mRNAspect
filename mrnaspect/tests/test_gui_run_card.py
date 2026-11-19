"""The run card's buttons say what they do (2026-11-12).

- **Stop** read as stopping the sequencing. It is *Stop analysis*, and on a live run its
  confirmation says MinKNOW keeps sequencing.
- **Report** and **Show in Finder** read as one thing. The report button names what it
  opens (the PDF at the end, the interim HTML page during the run), each button carries
  its own icon, and the folder button names where it opens the folder.
- **Show in Finder** worked only after the run: Rust read the folder from the result
  object and answered "the run has not finished yet" from an enabled button. It reads
  the run folder Core named at the start.

Read as text, like the other GUI tests: no compiler or browser runs on the suite host.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_GUI = Path(__file__).resolve().parents[2] / "gui"
_HTML = _GUI / "index.html"
_TS = _GUI / "src" / "main.ts"
_RS = _GUI / "src-tauri" / "src" / "lib.rs"

pytestmark = pytest.mark.skipif(
    not (_HTML.exists() and _TS.exists()), reason="GUI sources are not in this checkout")


def _code(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def _between(src: str, start: str, end: str = "\n}\n") -> str:
    body = src[src.index(start):]
    return body[:body.index(end)]


def test_stop_names_the_analysis_not_the_sequencing() -> None:
    html = _HTML.read_text()
    assert '<button class="btn danger" id="stop" disabled>Stop analysis</button>' in html
    dlg = _between(html, '<dialog id="stopdlg"', "</dialog>")
    assert "Stop the analysis?" in dlg
    assert 'id="stopconfirm">Stop analysis</button>' in dlg
    assert re.search(r'id="stoplive" hidden>MinKNOW keeps sequencing', dlg)


def test_the_live_line_shows_on_a_live_run_only() -> None:
    ts = _code(_TS.read_text())
    stop = _between(ts, '$("stop").addEventListener("click"', "\n});\n")
    assert '$("stoplive").hidden = runKind !== "live";' in stop
    go = _between(ts, '$("go").addEventListener("click"', "\n});\n")
    assert "runKind = kind;" in go, "the dialog must follow the RUN's card, not the form's"


def test_the_report_button_names_what_it_opens_and_keeps_its_icon() -> None:
    html = _HTML.read_text()
    # A soft accent, not the solid fill (2026-11-13: the blue stood out too much).
    rep = _between(html, '<button class="btn soft" id="openrep"', "</button>")
    assert "<svg" in rep and 'id="openreplabel">View PDF report</span>' in rep
    ts = _code(_TS.read_text())
    card = _between(ts, "function renderRunCard(")
    assert '"View interim report"' in card and '"View PDF report"' in card
    assert '$("openreplabel").textContent' in card
    assert not re.search(r'\$\("openrep"\)[^;]*\.textContent', card), "it would wipe the icon"


def test_the_folder_button_has_its_own_icon_and_names_finder_on_a_mac() -> None:
    html = _HTML.read_text()
    rev = _between(html, '<button class="btn" id="reveal"', "</button>")
    assert "<svg" in rev and 'id="reveallabel">Show in Finder</span>' in rev
    ts = _code(_TS.read_text())
    assert re.search(r'\$\("reveallabel"\)\.textContent = /Mac/\.test\(navigator\.userAgent\) '
                     r'\? "Show in Finder"', ts)


def test_the_folder_opens_during_the_run() -> None:
    rs = _RS.read_text()
    fn = _between(rs, "fn reveal_run_dir()")
    assert 'result_path("run_dir")' not in fn, "it waits for the run to finish again"
    assert ".run_dir.clone()" in fn
