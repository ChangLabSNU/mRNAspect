"""The window says when a Start continues an earlier run (2026-11-23).

Core answers (test_continued_run.py); these hold the page and the shell to asking and
saying it. Source checks: this host builds no app, and the page's text was first checked
by `tsc` and on the Mac.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_TS = _REPO / "gui" / "src" / "main.ts"
_RS = _REPO / "gui" / "src-tauri" / "src" / "lib.rs"
_HTML = _REPO / "gui" / "index.html"


def _fn(text: str, head: str) -> str:
    start = text.index(head)
    return text[start:text.index("\n}\n", start)]


def test_the_shell_asks_core_about_the_folder_a_start_would_write() -> None:
    body = _fn(_RS.read_text(), "fn read_config(")
    assert ('("--output-dir", output_dir.as_str()), ("--output-name", run_name.as_str())'
            in body)


def test_the_hint_asks_with_the_two_fields_and_the_placeholders_without_them() -> None:
    ts = _TS.read_text()
    check = _fn(ts, "async function checkRunDir(")
    assert 'outputDir: val("output"), runName: val("runname")' in check
    assert 'answer["run_dir_state"]' in check and 'answer["run_dir_error"]' in check
    # refreshConfig fills placeholders: a typed value would come back as "(from config)".
    assert 'outputDir: "", runName: ""' in _fn(ts, "async function refreshConfig(")


def test_the_hint_is_asked_again_whenever_its_answer_can_change() -> None:
    ts = _TS.read_text()
    # While typing, a moment after the last key: on `change` alone, the line read "A new
    # run" for a name typed over an earlier run's until the field lost focus.
    loop = ts[ts.index('for (const id of ["output", "runname", "input", "ref"]) {'):]
    loop = loop[:loop.index("\n}\n")]
    assert '$(id).addEventListener("input", checkRunDirSoon);' in loop
    assert '$(id).addEventListener("change", () => void checkRunDir());' in loop
    assert "setTimeout(() => void checkRunDir(), 300)" in _fn(ts, "function checkRunDirSoon(")
    assert '$("config").addEventListener("change", () => void checkRunDir());' in ts
    assert "void checkRunDir();" in _fn(ts, "function openFlow(")
    assert "void checkRunDir();" in _fn(ts, "async function describeInput(")
    assert "void checkRunDir();" in _fn(ts, "function setRefMode(")
    # At the end of a run its folder exists, so the same name now continues it.
    tick_end = ts[ts.index("if (!active && timer !== undefined) {"):]
    assert "void checkRunDir();" in tick_end[:tick_end.index("\n  }\n")]


def test_the_hint_says_continues_in_the_warning_colour() -> None:
    body = _fn(_TS.read_text(), "function renderRunNameHint(")
    assert "Continues the earlier run of this name" in body
    assert "Its report is replaced. For a separate run, give another name." in body
    assert 'el.className = "dim warn"' in body
    assert "A new run." in body
    # Before any answer, the sentence in index.html stays.
    assert re.search(r'id="runnamehint">Same name continues that run\.</p>',
                     _HTML.read_text())


def test_the_review_and_the_run_card_say_it_too() -> None:
    ts = _TS.read_text()
    assert '" (continues the earlier run of this name)"' in _fn(ts, "function reviewRows(")
    card = _fn(ts, "function renderRunCard(")
    assert "continuedText(st.continued)" in card
    assert '<span class="kv warn"><span>Continues</span>' in card
    text = _fn(ts, "function continuedText(")
    assert "c.inputs_restored" in text and "c.basecalls_cached" in text


def test_the_hint_asks_with_what_start_would_send() -> None:
    check = _fn(_TS.read_text(), "async function checkRunDir(")
    assert ('inputDir: val("input"), reference: refMode === "file" ? val("ref") : "",\n'
            '        mode: modeOf(kind), live: isRealtime() });') in check
    body = _fn(_RS.read_text(), "fn read_config(")
    assert 'let flags: &[&str] = if live { &["--live"] } else { &[] };' in body
    for flag in ("--input-dir", "--reference", "--mode"):
        assert f'("{flag}", ' in body


def test_a_refusal_is_said_in_red_and_keeps_start_off() -> None:
    """Another input or reference: Core's own words under Run name, and Start off
    (2026-11-23)."""
    ts = _TS.read_text()
    hint = _fn(ts, "function renderRunNameHint(")
    assert hint.index("if (s?.refusal) {") < hint.index("if (!s?.exists) {")
    assert 'el.className = "dim err";' in hint[hint.index("if (s?.refusal) {"):]
    missing = _fn(ts, "function missingOnStep(")
    assert ('return runDirState?.refusal || (runDirState?.error && runDirState.code !== '
            '"no_output_dir")\n      ? ["runname"] : [];') in missing
    sync = _fn(ts, "function syncFlow(")
    assert '"* Give another run name"' in sync
    assert "stepsOf(kind).some(s => missingOnStep(s).length > 0)" in sync
    assert '" (refused: the run of this name is of another input)"' in _fn(
        ts, "function reviewRows(")


def test_the_state_is_declared_beside_the_state_the_review_already_reads() -> None:
    """`let` is not hoisted: read before its line has run, it throws. reviewRows already
    reads `startedAs`, so a declaration in the same block is safe wherever that one is."""
    ts = _TS.read_text()
    block = ts[ts.index("let startedAs: string | null = null;"):][:300]
    assert "let runDirState: any = null;" in block and "let runDirGen = 0;" in block


def test_core_s_command_line_wording_is_not_shown_for_what_the_window_can_reach() -> None:
    """Before an output folder was chosen, the line under Run name read "no output
    directory was supplied. Pass --output-dir <dir>, or set `output_dir:` in your
    --config." in red (2026-11-23). The window words Core's refusals itself, by the code
    --print-config gives (`run_dir_error_code`)."""
    ts = _TS.read_text()
    words = ts[ts.index("const RUN_DIR_WORDS"):]
    words = words[:words.index("};")]
    assert 'no_output_dir: ["Choose an output folder.", false]' in words
    assert "run_name_not_one_folder" in words and "--" not in words
    check = _fn(ts, "async function checkRunDir(")
    assert 'code: answer["run_dir_error_code"] ?? null' in check
    hint = _fn(ts, "function renderRunNameHint(")
    assert "const own = RUN_DIR_WORDS[s.code];" in hint
    assert 'el.className = own && !own[1] ? "dim" : "dim err";' in hint
    # A name that is not one folder keeps Start off, as Start would fail on it.
    assert 'runDirState.code !== "no_output_dir"' in _fn(ts, "function missingOnStep(")


def test_every_code_the_window_words_is_one_core_gives() -> None:
    """A code renamed in Core would fall back to the command line's sentence in silence."""
    ts = _TS.read_text()
    words = ts[ts.index("const RUN_DIR_WORDS"):]
    words = words[:words.index("};")]
    codes = re.findall(r"^\s*([a-z_]+): \[", words, flags=re.M)
    core = (_REPO / "mrnaspect" / "main.py").read_text()
    assert codes, "no codes found"
    for code in codes:
        assert f'"{code}")' in core, f"{code} is not a RunDirError code in main.py"
