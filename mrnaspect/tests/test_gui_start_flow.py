"""Start: three cards, then one choice per screen (2026-11-12).

Replaces the three-step flow of 2026-11-06 (Input · Sample · Run configuration), which
gave a first-time user too much at once. Each card fixes what the old form's Mode and
Live-run box asked, so neither is a field: Live run = pod5 + the real-time driver, POD5
files = pod5, BAM files = bam. Then one screen per choice: Input · Reference · Modification ·
Model (POD5 and Live only) · Sample · Save to · Review, with MinKNOW's footer (Back ·
"* Missing required values" · Continue).

The input screen takes any level of a MinKNOW run and asks Core (`describe_input`) which
folder the run reads and how many inputs it holds; Continue waits for that answer.

What carries over and is pinned again here: every run field sits inside one lock
(#setupfields) that follows the run; the form locks only once a run has started; the
review resolves a blank field exactly as the run and the hints do; an edit describes
the next run; and when Core cannot be asked, the window says where to fix it.

Read from the parsed markup, because what matters is which element CONTAINS a field. No
compiler or browser runs here, so the TypeScript is held as text.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

_GUI = Path(__file__).resolve().parents[2] / "gui"
_HTML = _GUI / "index.html"
_TS = _GUI / "src" / "main.ts"
_RS = _GUI / "src-tauri" / "src" / "lib.rs"

pytestmark = pytest.mark.skipif(
    not (_HTML.exists() and _TS.exists()), reason="GUI sources are not in this checkout")


class _Tree(HTMLParser):
    """Each field's ancestors (tag, id, data-step), in document order."""

    VOID = {"input", "br", "img", "meta", "link", "hr", "col", "source", "wbr", "path",
            "circle"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict] = []
        self.fields: dict[str, list[dict]] = {}

    def handle_starttag(self, tag, attrs) -> None:
        a = dict(attrs)
        if tag in ("input", "select") and a.get("id"):
            self.fields[a["id"]] = [dict(n) for n in self.stack]
        if tag not in self.VOID:
            self.stack.append({"tag": tag, "id": a.get("id"), "step": a.get("data-step")})

    def handle_startendtag(self, tag, attrs) -> None:
        a = dict(attrs)
        if tag in ("input", "select") and a.get("id"):
            self.fields[a["id"]] = [dict(n) for n in self.stack]

    def handle_endtag(self, tag) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break


def _tree() -> _Tree:
    t = _Tree()
    html = _HTML.read_text()
    t.feed(html[html.index("<body>"):])
    return t


def _step(tree: _Tree, field: str) -> str | None:
    for node in reversed(tree.fields[field]):
        if node["step"]:
            return node["step"]
    return None


def _inside(tree: _Tree, field: str, element_id: str) -> bool:
    return any(n["id"] == element_id for n in tree.fields[field])


def _code(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def _fn(ts: str, head: str) -> str:
    body = ts[ts.index(head):]
    return body[:body.index("\n}\n")]


# --- the cards ------------------------------------------------------------------------

def test_there_is_one_card_per_run_kind() -> None:
    kinds = re.findall(r'class="kind" data-kind="([a-z0-9]+)"', _HTML.read_text())
    assert kinds == ["live", "pod5", "bam"]


def test_a_card_fixes_core_s_mode_and_the_live_flag() -> None:
    ts = _code(_TS.read_text())
    # BAM mode for the BAM card, and for a live run that follows MinKNOW's BAM files.
    assert re.search(r'const readsBam = \(k: Kind\) => k === "bam" \|\| '
                     r'\(k === "live" && liveSource === "bam"\);', ts)
    assert re.search(r'const modeOf = \(k: Kind\) => \(readsBam\(k\) \? "bam" : "pod5"\);', ts)
    assert re.search(r'const isRealtime = \(\) => kind === "live";', ts)
    go = ts[ts.index('$("go").addEventListener("click"'):]
    call = go[go.index('invoke<string>("start_qc"'):go.index("});")]
    assert "mode: modeOf(kind)" in call and "realtime: isRealtime()" in call


def test_a_live_run_can_follow_pod5_files_or_minknow_s_bam_files() -> None:
    """The choice is on the live card's input screen, POD5 unless changed. Changing it
    rebuilds the screens (a BAM run asks no model) and asks Core about the folder again;
    the shell accepts either and writes it as the live run's one-key config."""
    html = _HTML.read_text()
    control = html[html.index('id="livesource"'):html.index("</span>", html.index('id="livesource"'))]
    assert re.findall(r'data-livesource="([a-z0-9]+)"', control) == ["pod5", "bam"]
    assert 'data-livesource="pod5" role="radio" aria-checked="true"' in control
    assert "hidden" in html[html.index('id="livesource"') - 40:html.index('id="livesource"') + 120]
    ts = _code(_TS.read_text())
    assert 'let liveSource: "pod5" | "bam" = "pod5";' in ts
    assert '($("livesource") as HTMLElement).hidden = k !== "live";' in _fn(ts, "function openFlow(")
    change = _fn(ts, "function setLiveSource(")
    assert "liveSource = m;" in change and "setStep(step);" in change
    assert "describeInput()" in change
    rs = _RS.read_text()
    assert 'if realtime && mode != "pod5" && mode != "bam" {' in rs
    assert 'let layer = if mode == "bam" { LIVE_LAYER_BAM } else { LIVE_LAYER_POD5 };' in rs


def test_mode_and_live_are_no_longer_fields() -> None:
    fields = _tree().fields
    assert "mode" not in fields and "realtime" not in fields


# --- one choice per screen -------------------------------------------------------------

STEPS = {"input": "input", "ref": "reference", "m1psi": "arm", "armu": "arm",
         "model": "model", "manufacturer": "sample", "product": "sample",
         "batchid": "sample", "sampleid": "sample", "mfgdate": "sample",
         "operator": "sample", "output": "output", "runname": "output",
         "config": "review"}


@pytest.mark.parametrize("field,step", sorted(STEPS.items()))
def test_each_field_sits_in_its_step(field: str, step: str) -> None:
    assert _step(_tree(), field) == step


def _steps_of(ts: str) -> tuple[list[str], list[str]]:
    m = re.search(r'const stepsOf = \(k: Kind\): StepId\[\] => readsBam\(k\)\s*'
                  r'\? \[([^\]]*)\]\s*: \[([^\]]*)\];', ts)
    assert m, "stepsOf is gone"
    return (re.findall(r'"([a-z]+)"', m.group(1)), re.findall(r'"([a-z]+)"', m.group(2)))


def test_the_screens_are_named_in_order_and_bam_asks_no_model() -> None:
    ts = _code(_TS.read_text())
    bam, pod5 = _steps_of(ts)
    assert pod5 == ["input", "reference", "arm", "model", "sample", "output", "review"]
    assert bam == [s for s in pod5 if s != "model"]
    names = dict(re.findall(r'([a-z]+): "([^"]+)"',
                            ts[ts.index("const STEP_NAMES"):ts.index("};", ts.index("const STEP_NAMES"))]))
    assert set(names) == set(pod5)
    # Every screen the TypeScript names has its block in the page.
    html = _HTML.read_text()
    for step in pod5:
        assert f'data-step="{step}"' in html, step


def test_one_screen_at_a_time() -> None:
    fn = _fn(_code(_TS.read_text()), "function setStep(")
    assert "el.hidden = el.dataset.step !== s;" in fn
    assert "const order = stepsOf(kind);" in fn, "the step bar is not built for the card"


def test_the_model_is_asked_on_pod5_cards_only() -> None:
    tree = _tree()
    assert _inside(tree, "model", "pod5only")
    ts = _code(_TS.read_text())
    assert '($("pod5only") as HTMLElement).hidden = readsBam(k)' in _fn(ts, "function openFlow(")
    assert "model" not in _steps_of(ts)[0]


def test_continue_waits_for_the_required_values() -> None:
    """Reference has no default; the input waits for Core's answer. A missing one
    shows MinKNOW's line and keeps Continue off; Start waits for every screen."""
    assert "* Missing required values" in _HTML.read_text()
    ts = _code(_TS.read_text())
    miss = _fn(ts, "function missingOnStep(")
    assert "inputReady()" in miss and 'eff("ref", "reference")' in miss
    sync = _fn(ts, "function syncFlow(")
    assert '($("next") as HTMLButtonElement).disabled = miss.length > 0' in sync
    assert "stepsOf(kind).some(s => missingOnStep(s).length > 0)" in sync


def test_save_to_waits_for_an_output_folder() -> None:
    """The shipped config names none, and Start refuses a run without one; the review
    said "not set" and Start failed at the last screen (Chromium check, 2026-11-12)."""
    import json
    import subprocess
    import sys

    miss = _fn(_code(_TS.read_text()), "function missingOnStep(")
    # The output step is blocked first by a missing folder, then by a run name whose run
    # is of another input (2026-11-23; test_gui_continued_run.py).
    assert 'if (!eff("output", "output_dir")) return ["output"];' in miss
    out = subprocess.run([sys.executable, "-m", "mrnaspect.main", "--print-config"],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out)["resolved_config"].get("output_dir") in (None, ""), (
        "the shipped config names an output folder now; the screen may not need to wait")


def test_a_field_s_own_input_keeps_its_height() -> None:
    """Run name sits straight in its .field (a column): the generic flex:1 took it to a
    sliver until this rule, which must out-rank it."""
    html = _HTML.read_text()
    assert ".field > input:not([type=checkbox]) { flex:none }" in html


# --- the input: any level of a MinKNOW run -----------------------------------------------

def test_the_input_asks_core_which_folder_the_run_reads() -> None:
    """Which of five MinKNOW levels to pick was not clear (2026-11-12). Core
    answers, with its own glob, and the folder it reads replaces the one picked."""
    ts = _code(_TS.read_text())
    fn = _fn(ts, "async function describeInput(")
    call = fn[fn.index('invoke<any>("describe_input"'):]
    for needed in ('config: val("config")', "mode: modeOf(kind)", "live: isRealtime()"):
        assert needed in call, needed
    assert '($("input") as HTMLInputElement).value = d.use' in fn
    assert '$("input").addEventListener("change", () => void describeInput());' in ts
    assert '$("findruns").addEventListener("click", () => void describeInput("", true));' in ts


def test_the_rust_command_takes_what_typescript_sends_and_the_run_s_config() -> None:
    rs = _RS.read_text()
    sig = rs[rs.index("fn describe_input("):]
    sig = sig[:sig.index("{")]
    params = set(re.findall(r"(\w+): ", sig)) - {"app"}
    # No checkout's path since 2026-11-14: the shell finds its own interpreter.
    assert params == {"config", "path", "mode", "live"}, params
    body = rs[rs.index("fn describe_input("):]
    body = body[:body.index("\n}\n")]
    # The same config layers as print_config, so pod5_subdir is the run's.
    assert "machine_layer(&app)?" in body and '"--describe-input"' in body
    assert "describe_input," in rs[rs.index("generate_handler!["):]


def test_continue_needs_core_s_folder_and_for_files_its_inputs() -> None:
    """A live run may start before MinKNOW's first POD5; a POD5 or BAM run needs inputs.
    When Core cannot be asked, a named folder is enough, as before."""
    ts = _code(_TS.read_text())
    fn = _fn(ts, "function inputReady(")
    assert 'if (!eff("input", "input_dir")) return false;' in fn
    assert "if (inputInfo === null || inputInfo.failed) return true;" in fn
    assert '!!inputInfo.use && (kind === "live" || inputInfo.inputs > 0)' in fn
    # Never on the previous folder's answer: asking, or edited since, waits.
    assert fn.index("if (inputInfo?.pending) return false;") < fn.index("inputInfo === null")
    ask = _fn(ts, "async function describeInput(")
    assert ask.index("inputInfo = { pending: true };") < ask.index('invoke<any>("describe_input"')
    typing = ts[ts.index('$("input").addEventListener("input"'):]
    assert "inputInfo = { pending: true };" in typing[:typing.index("});")]


# --- the arm, as two cards ----------------------------------------------------------------------

def test_the_arm_is_two_cards_m1psi_first() -> None:
    html = _HTML.read_text()
    group = html[html.index('<div class="choices" role="radiogroup"'):]
    group = group[:group.index("</div>")]
    radios = re.findall(r'<input type="radio" name="arm" id="(\w+)"( checked)?', group)
    assert radios == [("m1psi", " checked"), ("armu", "")], radios
    ts = _code(_TS.read_text())
    assert 'return ($("m1psi") as HTMLInputElement).checked ? "m1psi" : "U";' in ts
    cfg = _fn(ts, "async function refreshConfig(")
    assert '($(cfgArm === "m1psi" ? "m1psi" : "armu") as HTMLInputElement).checked = true;' in cfg


def test_the_two_hints_that_stop_a_silent_wrong_result_stay_visible() -> None:
    html = _HTML.read_text()
    assert re.search(r'<p class="dim" id="refhint">Required\. A wrong construct maps ~0%\.</p>', html)
    assert re.search(r'<p class="dim" id="runnamehint">Same name continues that run\.</p>', html)


def test_the_live_card_says_when_it_ends() -> None:
    assert "Ends when MinKNOW ends the run." in _HTML.read_text()


# --- the lock -------------------------------------------------------------------------------

def test_every_run_field_is_inside_the_lock() -> None:
    tree = _tree()
    out = [f for f in STEPS if not _inside(tree, f, "setupfields")]
    assert not out, f"outside the lock: {out}"


def test_device_and_core_stay_out_of_the_run() -> None:
    """Facts about the computer are Settings, not a run's fields. (Where Core is was one
    of them until 2026-11-14; the shell now finds it, and no field asks.)"""
    tree = _tree()
    for f in ("server", "device", "models"):
        assert not _inside(tree, f, "setupfields"), f
    assert "core" not in tree.fields


def _go_handler() -> str:
    ts = _code(_TS.read_text())
    start = ts.index('$("go").addEventListener("click"')
    return ts[start:ts.index("\n});\n", start)]


def test_the_form_locks_only_once_the_run_has_started() -> None:
    """A run that could not start leaves the flow open, where the thing to fix is."""
    go = _go_handler()
    started = go.index('started = await invoke<string>("start_qc"')
    gave_up = go.index("return;", started)
    lock = go.index('($("setupfields") as HTMLFieldSetElement).disabled = true')
    assert started < gave_up < lock


def test_the_lock_follows_the_run() -> None:
    """Released when the run ends, however it ends: the poll decides, not the button."""
    tick = _fn(_code(_TS.read_text()), "async function tick(")
    assert '($("setupfields") as HTMLFieldSetElement).disabled = active' in tick


# --- the review ---------------------------------------------------------------------------

def test_the_review_names_what_the_run_gets() -> None:
    fn = _fn(_code(_TS.read_text()), "function reviewRows(")
    for needed in ('"input"', '"ref"', '$("model")', '"output"', '"runname"', "startedAs",
                   "armValue()"):
        assert needed in fn, f"the review leaves out {needed}"


def test_the_review_resolves_a_blank_field_as_the_run_does() -> None:
    """Typed, else the config's, read with the hints' own keys, so the review, the hints
    and the run cannot each resolve a blank field differently."""
    ts = _code(_TS.read_text())
    assert re.search(r'const eff = \(id: string, key: string\) =>\s*val\(id\)\.trim\(\) \|\| '
                     r'String\(dig\(resolvedCfg, key\) \?\? ""\);', ts)
    fn = _fn(ts, "function reviewRows(")
    table = ts[ts.index("const PREFILL"):ts.index("const AUTHORED")]
    hint_keys = set(re.findall(r'\["[a-z]+",\s*"([a-z0-9_.]+)"\]', table))
    line_keys = set(re.findall(r'"(input_dir|reference|output_dir|output_name)"', fn))
    assert line_keys == {"input_dir", "reference", "output_dir", "output_name"}
    assert line_keys <= hint_keys, line_keys - hint_keys


def test_an_edit_describes_the_next_run() -> None:
    ts = _code(_TS.read_text())
    assert re.search(r'\$\("setupfields"\)\.addEventListener\("change",\s*\(\)\s*=>\s*\{\s*'
                     r"startedAs = null;", ts)


def test_core_that_cannot_be_asked_is_said_in_the_flow() -> None:
    """When Core cannot be asked the flow says so, in the shell's own words: there is no
    setting to send the reader to (the Core repo field went on 2026-11-14;
    test_gui_mac_review4.py). A packaged app finds Core."""
    fn = _fn(_code(_TS.read_text()), "async function refreshConfig(")
    caught = fn.index("} catch (e) {")
    failed = fn[caught:fn.index("\n  }\n", caught)]
    assert '$("corebanner").hidden = named' in failed
    assert '$("corebanner").textContent = String(e)' in failed
    assert 'id="corebanner"' in _HTML.read_text()
