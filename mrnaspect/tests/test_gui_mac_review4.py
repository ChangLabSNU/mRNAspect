"""The fourth Mac review (2026-11-14), on the build of `0ff84e0`.

One test per note, as test_gui_mac_review3.py does for the third:

- *Run details* wrote its rows into the run card: the button that opens the dialog and
  the list inside the dialog had the same id, and the first one in the page won.
- The headline numbers of the Overview were a little large, and "+N outside" sat on the
  histogram it describes.
- Under the minimum of mapped reads the panels still drew charts: three panels were
  gated, and only until a chart file existed.
- A new copy of the app opens dark (it already did; held here so it stays so).

`scripts/check_bundle.sh` failing on a relative path is in test_check_bundle_script.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mrnaspect.reporting import figspec

_REPO = Path(__file__).resolve().parents[2]
_HTML = _REPO / "gui" / "index.html"
_TS = _REPO / "gui" / "src" / "main.ts"

pytestmark = pytest.mark.skipif(
    not (_HTML.exists() and _TS.exists()), reason="GUI sources are not in this checkout")


def _fn(source: str, head: str) -> str:
    start = source.index(head)
    return source[start:source.index("\n}\n", start) + 3]


def _css_rule(selector: str) -> str:
    html = _HTML.read_text()
    css = html[html.index("<style>"):html.index("</style>")]
    m = re.search(r"(?:^|[}\s/])" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return m.group(1).replace(" ", "")


# --- Run details ---------------------------------------------------------------------------

def test_no_id_names_two_elements() -> None:
    """`$("x")` is getElementById: of two elements with one id it returns the first in the
    page. An id written by the script must not be one the page already has."""
    in_page = re.findall(r'\bid="([^"$]+)"', _HTML.read_text())
    twice = sorted({i for i in in_page if in_page.count(i) > 1})
    assert not twice, twice
    written = set(re.findall(r'\bid="([^"$]+)"', _TS.read_text()))
    assert not written & set(in_page), sorted(written & set(in_page))


def test_run_details_fills_the_list_in_its_dialog() -> None:
    html, ts = _HTML.read_text(), _TS.read_text()
    dialog = html[html.index('<dialog id="detailsdlg"'):]
    dialog = dialog[:dialog.index("</dialog>")]
    assert '<dl class="review" id="details"></dl>' in dialog
    assert '$("details").innerHTML = rows.map(' in _fn(ts, "async function openDetails()")
    # The button that opens it has a name of its own.
    assert '<button class="linkbtn" id="opendetails">Details</button>' in ts
    assert '$("opendetails").addEventListener("click", openDetails);' in ts


def test_the_rows_of_run_details_read_as_the_report_does() -> None:
    """Seen in the same screenshot: "0.1.0 ·" with nothing after the dot (the packaged app
    has no commit), and the finish time as a raw UTC stamp with microseconds."""
    ts = _TS.read_text()
    details = _fn(ts, "async function openDetails()")
    assert '.filter(Boolean).join(" · ")' in details
    assert 'add("Finished", prov?.generated_at ? clockText(String(prov.generated_at)) : null);' in details
    clock = _fn(ts, "function clockText(")
    assert "getTimezoneOffset()" in clock and "(UTC${" in clock
    assert "reporter._clock_text" in ts, "the form is the report's; say whose"


# --- the Overview's numbers and notes --------------------------------------------------------

def test_the_headline_step_is_a_little_smaller_and_still_a_step() -> None:
    """24 px for 26. Still a step above a card's title (the ladder's rule is 12%, held in
    test_gui_design_tokens.py), and not under the report's own title on screen, which is
    24 px: 23 would have been."""
    px = figspec.GUI_TYPE_PX
    assert px["title"] == 24
    assert px["title"] >= 1.12 * px["heading"]
    assert "--type-title: 24px;" in _HTML.read_text()


def test_the_notes_on_a_histogram_have_a_band_of_their_own() -> None:
    """"+N outside" and "√ count" sat inside the plot's corners, over the curve when its
    peak was under them. The drawing now starts below a band one line of that text high."""
    svg = _css_rule(".plotbox svg")
    assert "bottom:0" in svg and "top:" not in svg and "inset" not in svg
    assert "height:calc(100%-var(--plot-band))" in svg
    band = re.search(r"--plot-band:\s*calc\(var\(--type-micro\) \* ([\d.]+)\)", _HTML.read_text())
    assert band and float(band.group(1)) >= 1.3, "a line of the note's text, with its leading"
    for label in (".plotbox .corner", ".plotbox .beyond"):
        rule = _css_rule(label)
        assert "top:0" in rule and "line-height:var(--plot-band)" in rule, label


def test_a_headline_value_and_its_label_wrap_together() -> None:
    """"112 nt" stayed and "median" fell to the next line, under the title. The label
    comes first since 2026-11-16 (test_gui_words.py); the pair still wraps as one."""
    card = _fn(_TS.read_text(), "function topCardHtml(")
    assert ('<span class="hpair"><span class="hs">${esc(c.headSub)}</span>'
            '<span class="h">${c.head}</span></span>') in card
    pair = _css_rule(".ovhead .hpair")
    assert "white-space:nowrap" in pair and "margin-left:auto" in pair


# --- the panels wait for the minimum of reads ------------------------------------------------

def test_every_panel_of_reads_waits_for_the_minimum_whatever_has_been_drawn() -> None:
    """Three panels were gated, and the gate stood after `if (figs.length) return ""`, so
    a chart file from any render opened every panel. Now every panel whose content is the
    run's reads waits, as the Overview's cards do, before anything else is asked."""
    ts = _TS.read_text()
    gated = re.search(r"const MIN_GATED = new Set\(\[([^\]]*)\]\);", ts).group(1)
    assert set(re.findall(r'"([^"]+)"', gated)) == {
        "read-length", "polya", "quality", "constructs", "identity", "full-length"}
    why = _fn(ts, "function panelReason(")
    assert why.index("readsWait(p)") < why.index("if (figs.length) return")
    # The flow cell is MinKNOW's record of the run, not a count of reads.
    assert "flow-cell" not in gated
    assert why.index('key === "flow-cell"') < why.index("readsWait(p)")


# --- the theme a new copy opens in -----------------------------------------------------------

def test_a_new_copy_opens_dark() -> None:
    """Light is a choice the app remembers (Settings › Display); nothing stored is dark."""
    ts = _TS.read_text()
    assert 'return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";' in ts
    html = _HTML.read_text()
    assert not re.search(r"<html[^>]*data-theme=", html), "the page itself names no theme"


# --- Settings › Advanced is gone (2026-11-14) ------------------------------------------
#
# Its one field, Core repo, told the shell where a Core checkout was. The packaged app
# never read it: its own interpreter is found first. Only a run from source did, and a
# development build can find the checkout it was compiled in by itself.

_RS = _REPO / "gui" / "src-tauri" / "src" / "lib.rs"


def test_there_is_no_advanced_page_and_no_core_repo_field() -> None:
    html, ts, rust = _HTML.read_text(), _TS.read_text(), _RS.read_text()
    for gone in ('id="page-advanced"', 'data-page="advanced"', 'id="core"', "Core repo",
                 'id="toadvanced"', 'id="corehint"'):
        assert gone not in html, gone
    for gone in ('"advanced"', "coreDir", 'val("core")', '$("core")', "corehint", "toadvanced"):
        assert gone not in ts, gone
    assert 'const SETTINGS_PAGES: Page[] = ["device", "display", "about"];' in ts
    assert "core_dir" not in rust, "no command takes a checkout's path any more"


def test_a_development_build_finds_the_checkout_it_was_compiled_in() -> None:
    """`tauri dev` runs on the environment `npm run dev` stages first. The path is the
    build machine's, so it is compiled into development builds only."""
    rust = _RS.read_text()
    start = rust.index("fn resolve_python()")
    body = rust[start:rust.index("\n}\n", start)]
    override = body.index("QCRNA_PYTHON")
    bundled = body.index("../Resources/env/bin/python3")
    checkout = body.index('env!("CARGO_MANIFEST_DIR")')
    assert override < bundled < checkout, "the override, the bundle, then the checkout"
    assert "../../dist/bundle-env/bin/python3" in body[checkout:]
    assert '".pixi/envs/default' not in rust, "the editable environment is not what ships"
    # Every use of that path sits inside a block compiled for development builds alone.
    uses = [m.start() for m in re.finditer(r'env!\("CARGO_MANIFEST_DIR"\)', rust)]
    assert len(uses) == 2, "the interpreter and the notices"
    for at in uses:
        before = rust[:at]
        block = before[before.rindex("#[cfg(debug_assertions)]"):]
        assert block.count("{") - block.count("}") == 1, "outside its cfg(debug_assertions) block"
    # And `npm run dev` does stage it.
    package = (_REPO / "gui" / "package.json").read_text()
    assert '"predev": "bash ../scripts/stage_bundle_env.sh"' in package


def test_when_core_is_not_found_the_start_flow_says_what_was_looked_for() -> None:
    """There is no page to send the reader to, so the banner carries the shell's own
    message: where it looked, and the variable that names an interpreter."""
    html, ts, rust = _HTML.read_text(), _TS.read_text(), _RS.read_text()
    assert '<p class="banner" id="corebanner" hidden></p>' in html
    refresh = _fn(ts, "async function refreshConfig()")
    failed = refresh[refresh.index("} catch (e) {"):]
    assert '$("corebanner").textContent = String(e);' in failed
    assert '$("corebanner").hidden = named;' in failed
    start = rust.index("fn resolve_python()")
    body = rust[start:rust.index("\n}\n", start)]
    assert "Looked at: {}" in body and "$QCRNA_PYTHON" in body[body.index("Looked at"):]


def test_the_commands_take_what_the_page_sends() -> None:
    """Rust names its arguments in snake_case and Tauri expects camelCase from the page;
    a parameter the page no longer sends is an error at the first call, on the Mac."""
    ts, rust = _TS.read_text(), _RS.read_text()
    for command, sent in (("read_config", {"config", "output_dir", "run_name",
                                           "input_dir", "reference", "mode", "live"}),
                          ("describe_input", {"config", "path", "mode", "live"}),
                          ("open_notices", set())):
        head = re.search(rf"fn {command}\(([^)]*)\)", rust).group(1)
        params = {p.split(":")[0].strip() for p in head.split(",") if p.strip()} - {"app"}
        assert params == sent, (command, params)
    assert 'invoke("open_notices");' in ts
    assert ('invoke<any>("read_config",\n      { config: val("config"), outputDir: "", '
            'runName: "", inputDir: "", reference: "",\n        mode: "", live: false });') in ts


# --- Cmd + and Cmd - (2026-11-14: for the manuscript's and the poster's screenshots) ---

def test_the_window_zooms_with_the_keyboard_and_is_allowed_to() -> None:
    """Tauri's own option, nothing of ours: on macOS it adds Cmd + and Cmd -, 20% a step,
    by calling the webview's zoom, and that call needs a permission the default set does
    not hold (both from Tauri's description of `zoomHotkeysEnabled`). One without the
    other does nothing, silently."""
    import json

    tauri = _REPO / "gui" / "src-tauri"
    windows = json.loads((tauri / "tauri.conf.json").read_text())["app"]["windows"]
    assert [w.get("zoomHotkeysEnabled") for w in windows] == [True]
    permissions = json.loads((tauri / "capabilities" / "default.json").read_text())["permissions"]
    assert "core:webview:allow-set-webview-zoom" in permissions
