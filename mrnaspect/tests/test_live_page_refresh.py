"""The live page's refresh stops once a reader presses on a figure (2026-11-10).

report_latest.html draws the figures during a run and reloads itself every
DEFAULT_AUTO_REFRESH_SECONDS. A reload redraws every figure from the start, undoing the
reader's zoom, legend clicks and view buttons, so the first press on a figure stops the
timer and a note says the page no longer updates itself. Hovering does not stop it.
The page's own script runs here under node with a stub DOM and timer.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from mrnaspect.reporting.reporter import DEFAULT_AUTO_REFRESH_SECONDS, LATEST_HTML_NAME

from .test_live_html import _with_constructs
from .test_report_figure_controls import NODE, needs_node

_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const script = fs.readFileSync(process.argv[2], "utf8");
const actions = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const timers = [];
const cleared = [];
let reloads = 0;
function node() {
  return { listeners: {},
           addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); } };
}
const figures = [node(), node()];
const note = { hidden: true };
vm.runInNewContext(script, {
  setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; },
  clearTimeout(id) { cleared.push(id); },
  location: { reload() { reloads += 1; } },
  document: {
    querySelectorAll(sel) { return sel === ".interactive-figure" ? figures : []; },
    getElementById(id) { return id === "refresh-paused" ? note : null; },
  },
});
for (const [kind, i] of actions) {
  if (kind === "fire") { timers[timers.length - 1].fn(); continue; }
  (figures[i].listeners[kind] || []).forEach((fn) => fn());
}
process.stdout.write(JSON.stringify(
  { ms: timers.map((t) => t.ms), cleared, reloads, noteHidden: note.hidden }));
"""


@pytest.fixture(scope="module")
def script(tmp_path_factory) -> str:
    out = tmp_path_factory.mktemp("live")
    _with_constructs(out).report("interim_1")
    html = (out / LATEST_HTML_NAME).read_text(encoding="utf-8")
    (block,) = [b for b in re.findall(r"<script>(.*?)</script>", html, re.S)
                if "location.reload" in b]
    return block


def _run(script: str, tmp_path: Path, actions: list[list]) -> dict:
    (tmp_path / "page.js").write_text(script)
    (tmp_path / "actions.json").write_text(json.dumps(actions))
    (tmp_path / "harness.js").write_text(_HARNESS)
    done = subprocess.run([NODE, str(tmp_path / "harness.js"), str(tmp_path / "page.js"),
                           str(tmp_path / "actions.json")],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_left_alone_it_reloads_at_the_pod5_cadence(script, tmp_path) -> None:
    out = _run(script, tmp_path, [["fire", 0]])
    assert out["ms"] == [DEFAULT_AUTO_REFRESH_SECONDS * 1000]
    assert out["reloads"] == 1 and out["noteHidden"] is True


@needs_node
def test_hovering_does_not_stop_it(script, tmp_path) -> None:
    out = _run(script, tmp_path, [["mousemove", 0], ["fire", 0]])
    assert out["cleared"] == [] and out["reloads"] == 1


@needs_node
@pytest.mark.parametrize("press", ["mousedown", "touchstart"])
def test_a_press_on_a_figure_stops_it_and_says_so(script, tmp_path, press) -> None:
    out = _run(script, tmp_path, [[press, 1], [press, 0]])
    assert out["cleared"] == [1], "stopped once, by the first press"
    assert out["noteHidden"] is False
