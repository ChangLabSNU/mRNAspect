"""R5: about two seconds of animation as the Overview's numbers rise and its charts draw.

The review of 2026-11-12: a run should feel live. So when a new result arrives, each
number on the Overview counts from the value it showed to the new one, each bar grows from
its last height, and each histogram moves from its last shape, in about two seconds
(`gui/src/tween.ts`). Before the first result everything starts from zero.

What must not happen is held here too: a redraw with nothing new does not replay it (a tab
switch is not news), a reader who asked the system for less motion gets none, and only the
Overview moves, because a panel's chart is Plotly's and redrawing it resets its zoom.

The first half reads the sources as text. The second runs the engine under node on a
stand-in page, and is skipped where node is missing or too old to run TypeScript directly.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_TS = _REPO / "gui" / "src" / "main.ts"
_TW = _REPO / "gui" / "src" / "tween.ts"

pytestmark = pytest.mark.skipif(not _TS.exists(), reason="GUI sources are not in this checkout")


def _fn(source: str, head: str) -> str:
    """A top-level function's text, from its first line to the closing brace in column 0."""
    start = source.index(head)
    return source[start:source.index("\n}\n", start) + 3]


# ---------------------------------------------------------------------------
# The sources, as text
# ---------------------------------------------------------------------------

def test_the_animation_lasts_about_two_seconds() -> None:
    duration = int(re.search(r"export const DURATION_MS = (\d+);", _TW.read_text()).group(1))
    assert 1500 <= duration <= 2500


def test_a_reader_who_asked_for_less_motion_gets_none() -> None:
    assert "(prefers-reduced-motion: reduce)" in _TW.read_text()


def test_only_the_overview_is_animated() -> None:
    """One call, right after the Overview is written."""
    ts = _TS.read_text()
    render = _fn(ts, "function renderCurrentPanel()")
    assert "el.innerHTML = renderOverview(p, lastPoll); wireOverview(); animate(el); return;" in render
    assert len(re.findall(r"\banimate\(", ts)) == 1


def test_a_new_run_starts_from_zero() -> None:
    """What the last run showed is forgotten with it."""
    assert "resetTweens();" in _fn(_TS.read_text(), "function clearRun()")


@pytest.mark.parametrize("key", [
    "reads.total", "reads.mapped", "reads.unmapped", "reads.rate",
    "top.read-length.head", "top.read-length.fl.lo", "top.read-length.fl.hi",
    "top.polya.head", "top.polya.q1", "top.polya.q3", "top.polya.called",
    "top.quality.head", "top.quality.median", "top.quality.q1", "top.quality.q3",
    "bottom.identity.head", "bottom.fl.lo", "bottom.fl.hi",
    "tile.share", "tile.identity", "tile.fl.lo", "tile.fl.hi",
    "health.pore", "health.ejected", "health.voltage",
])
def test_each_number_on_the_overview_is_one_the_engine_can_count(key: str) -> None:
    ts = _TS.read_text()
    assert re.search(rf'tw(Unit)?\("{re.escape(key)}"', ts), key


def test_a_tagged_number_reads_exactly_as_before() -> None:
    """The span holds the text num() and pct() gave: the engine's formats are theirs."""
    ts, tw = _TS.read_text(), _TW.read_text()
    helper = ts[ts.index("const tw = ("):ts.index("const twUnit")]
    assert 'data-v="${x}" data-f="${f}">${format(x, f)}</span>' in helper
    assert ": NOT_MEASURED" in helper, "a value that is not a number is still said, not drawn"
    fmt = _fn(tw, "export function format(")
    assert "toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })" in fmt
    assert '(v * 100).toFixed(d) + "%"' in fmt
    # And the page's own two formatters are still the ones the engine copies.
    assert "x.toLocaleString(undefined,\n  { minimumFractionDigits: d, maximumFractionDigits: d })" in ts
    assert '(x * 100).toFixed(d) + "%"' in ts


def test_the_bars_say_which_of_their_measures_moves() -> None:
    ts = _fn(_TS.read_text(), "function renderOverview(")
    for mark in ('data-tw="reads.bar" data-tws="width"',
                 'data-tw="share.${esc(k)}" data-tws="height"',
                 'data-tw="identity.${esc(k)}.stem" data-tws="height"',
                 'data-tw="identity.${esc(k)}.head" data-tws="bottom"',
                 'data-tw="fl.${esc(k)}.stem" data-tws="height"',
                 'data-tw="fl.${esc(k)}.head" data-tws="bottom,height"'):
        assert mark in ts, mark


def test_a_histogram_carries_its_heights_and_one_function_draws_it() -> None:
    """The card's path and the engine's frames come from the same `stepPath`, so the last
    frame is the card as it would have been drawn without the animation."""
    ts = _TS.read_text()
    assert "return stepPath(histHeights(counts, sqrt));" in _fn(ts, "function histPath(")
    card = _fn(ts, "function topCardHtml(")
    assert 'data-tw="top.${c.panel}.hist" data-h="${' in card
    assert "export function stepPath(" in _TW.read_text()


def test_a_head_is_markup_the_page_built_and_nothing_a_user_typed() -> None:
    """The head now holds a span, so it is no longer escaped where it is placed. It must
    then be built only from numbers: a construct's name still goes through esc()."""
    ts = _TS.read_text()
    assert '<span class="h">${c.head}</span>' in _fn(ts, "function topCardHtml(")
    tile = _fn(ts, "function tileCard(")
    assert '<span class="tv">${value}</span><span class="ts">${esc(sub)}</span>' in tile
    over = _fn(ts, "function renderOverview(")
    for built in re.findall(r"head: ([^\n]+)", over):
        assert "esc(" in built or "tw" in built or "NOT_MEASURED" in built, built


# ---------------------------------------------------------------------------
# The engine, run
# ---------------------------------------------------------------------------

_NODE = shutil.which("node")


def _node_runs_typescript() -> bool:
    if _NODE is None:
        return False
    version = subprocess.run([_NODE, "--version"], capture_output=True, text=True).stdout
    major, minor = (int(x) for x in version.strip().lstrip("v").split(".")[:2])
    return (major, minor) >= (23, 6) or (major == 22 and minor >= 18)


needs_node = pytest.mark.skipif(
    not _node_runs_typescript(),
    reason="node 22.18+ (it runs TypeScript directly) is not on PATH")

#: A page of stand-in elements, a clock the test moves, and frames it steps by hand.
_HARNESS = r"""
import * as tw from %(module)s;

let now = 0; const frames = [];
tw.host.now = () => now;
tw.host.frame = step => { frames.push(step); };
let still = false;
tw.host.still = () => still;
const advance = ms => { now += ms; const due = frames.splice(0); due.forEach(f => f()); };

const number = (key, v, f = "n0") => ({ dataset: { tw: key, v: String(v), f },
                                        textContent: tw.format(v, f) });
const bar = (key, props) => {
  const style = { ...props };
  return { dataset: { tw: key, tws: Object.keys(props).join(",") },
           style: { getPropertyValue: p => style[p], setProperty: (p, v) => { style[p] = v; } },
           shown: style };
};
const histogram = (key, heights) => {
  const paths = { ".hx-area": { d: tw.stepPath(heights).area }, ".hx-line": { d: tw.stepPath(heights).line } };
  const path = sel => ({ getAttribute: () => paths[sel].d, setAttribute: (_, v) => { paths[sel].d = v; } });
  return { dataset: { tw: key, h: heights.join(",") }, querySelector: path, paths };
};
const page = (...els) => ({ querySelectorAll: () => els });
const out = {};

// 1. A first result counts from zero and lands on the page's own text.
let reads = number("reads", 12345);
tw.animate(page(reads));
out.first_at_start = reads.textContent;
advance(1000); out.first_midway = reads.textContent;
advance(1100); out.first_done = reads.textContent;
out.frames_after_done = frames.length;

// 2. The same value drawn again does not replay.
reads = number("reads", 12345);
tw.animate(page(reads));
out.redraw_same = reads.textContent; out.redraw_frames = frames.length;

// 3. A new value counts from the old one, in either direction.
reads = number("reads", 20000);
tw.animate(page(reads));
out.second_at_start = reads.textContent;
advance(2100); out.second_done = reads.textContent;
let rate = number("rate", 0.9, "p1");
tw.animate(page(rate)); advance(2100);
rate = number("rate", 0.5, "p1");
tw.animate(page(rate)); advance(1000);
out.falling_midway = parseFloat(rate.textContent);
advance(1100); out.falling_done = rate.textContent;

// 4. A redraw while it is moving carries on from where it was, on the new element.
let a = number("carry", 1000);
tw.animate(page(a)); advance(1000);
const before = Number(a.textContent.replace(/\D/g, ""));
a = number("carry", 1000);
tw.animate(page(a));
out.carry_before = before; out.carry_after = Number(a.textContent.replace(/\D/g, ""));
advance(1100); out.carry_done = a.textContent;

// 5. A target that changes mid-flight starts from what is on screen.
let b = number("turn", 1000);
tw.animate(page(b)); advance(1000);
const shown = Number(b.textContent.replace(/\D/g, ""));
b = number("turn", 3000);
tw.animate(page(b));
out.turn_from = shown; out.turn_start = Number(b.textContent.replace(/\D/g, ""));
advance(2100); out.turn_done = b.textContent;

// 6. A bar grows from its last height; its final style is the page's own string.
let col = bar("share", { height: "40.00%" });
tw.animate(page(col));
out.bar_start = col.shown.height; advance(1000); out.bar_midway = parseFloat(col.shown.height);
advance(1100); out.bar_done = col.shown.height;
col = bar("share", { height: "60.5%" });
tw.animate(page(col)); out.bar_second_start = col.shown.height;
advance(2100); out.bar_second_done = col.shown.height;

// 7. A histogram rises from flat, and from its last shape even when the bins changed.
let h = histogram("hist", [0.5, 1, 0.25]);
const final = { ...{ area: h.paths[".hx-area"].d, line: h.paths[".hx-line"].d } };
tw.animate(page(h));
out.hist_start = h.paths[".hx-line"].d; advance(1000); out.hist_midway = h.paths[".hx-line"].d;
advance(1100);
out.hist_done_is_final = h.paths[".hx-line"].d === final.line && h.paths[".hx-area"].d === final.area;
h = histogram("hist", [1, 1, 1, 1, 1, 1]);
tw.animate(page(h)); out.hist_rebinned_start = h.paths[".hx-line"].d;

// 8. Less motion asked for: the page is left as it was written.
still = true;
let quiet = number("quiet", 777);
tw.animate(page(quiet));
out.quiet = quiet.textContent; still = false;

// 9. A new run forgets the last one.
advance(5000);
tw.resetTweens();
reads = number("reads", 20000);
tw.animate(page(reads));
out.after_reset = reads.textContent;

out.fit = tw.fit([0, 1], 4); out.fit_same = tw.fit([0.2, 0.4], 2);
out.ease = [tw.ease(0), tw.ease(0.5), tw.ease(1), tw.ease(2)];
out.flat = tw.stepPath([0, 0, 0]).line;
out.format = [tw.format(1234.5, "n0"), tw.format(0.9712, "p1"), tw.format(97.123, "f1"), tw.format(12.34, "n1")];
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def ran(tmp_path_factory) -> dict:
    harness = tmp_path_factory.mktemp("tween") / "harness.mjs"
    # Not %-formatting: the script is full of percent signs.
    harness.write_text(_HARNESS.replace("%(module)s", json.dumps(_TW.as_uri())))
    proc = subprocess.run([_NODE, "--disable-warning=ExperimentalWarning", str(harness)],
                          capture_output=True, text=True, timeout=60, env={"LANG": "en_US.UTF-8"})
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@needs_node
def test_a_first_result_counts_up_from_zero(ran: dict) -> None:
    assert ran["first_at_start"] == "0"
    midway = int(ran["first_midway"].replace(",", ""))
    assert 6173 < midway < 12345, "decelerating: past half the value at half the time"
    assert ran["first_done"] == "12,345"
    assert ran["frames_after_done"] == 0, "the loop stops when nothing is moving"


@needs_node
def test_drawing_the_same_value_again_replays_nothing(ran: dict) -> None:
    assert ran["redraw_same"] == "12,345" and ran["redraw_frames"] == 0


@needs_node
def test_a_new_value_counts_from_the_old_one_up_or_down(ran: dict) -> None:
    assert ran["second_at_start"] == "12,345" and ran["second_done"] == "20,000"
    assert 50.0 < ran["falling_midway"] < 90.0
    assert ran["falling_done"] == "50.0%"


@needs_node
def test_a_redraw_while_moving_carries_on(ran: dict) -> None:
    """The poll redraws the Overview when a status line changes; the count must not jump
    back to zero, or forward to its end."""
    assert 0 < ran["carry_before"] < 1000
    assert ran["carry_after"] == ran["carry_before"]
    assert ran["carry_done"] == "1,000"


@needs_node
def test_a_target_that_changes_mid_flight_starts_from_what_is_shown(ran: dict) -> None:
    assert ran["turn_start"] == ran["turn_from"]
    assert ran["turn_done"] == "3,000"


@needs_node
def test_a_bar_grows_from_its_last_height(ran: dict) -> None:
    assert ran["bar_start"] == "0.00%"
    assert 20.0 < ran["bar_midway"] < 40.0
    assert ran["bar_done"] == "40.00%"
    assert ran["bar_second_start"] == "40.00%"
    assert ran["bar_second_done"] == "60.5%", "the page's own string, not a reformatted one"


@needs_node
def test_a_histogram_rises_from_flat_and_ends_as_the_card_drew_it(ran: dict) -> None:
    assert ran["hist_start"] == ran["flat"]
    assert ran["hist_midway"] not in (ran["flat"], ran["hist_start"])
    assert ran["hist_done_is_final"]
    # Six bins from three: each old bin stands for two new ones, not a flat restart.
    assert ran["hist_rebinned_start"].count("V") > 1
    assert ran["hist_rebinned_start"].startswith("M0 55.00")


@needs_node
def test_less_motion_and_a_new_run(ran: dict) -> None:
    assert ran["quiet"] == "777"
    assert ran["after_reset"] == "0"


@needs_node
def test_the_small_parts(ran: dict) -> None:
    assert ran["fit"] == [0, 0, 1, 1] and ran["fit_same"] == [0.2, 0.4]
    assert ran["ease"][0] == 0 and ran["ease"][2] == 1 and ran["ease"][3] == 1
    assert ran["ease"][1] == pytest.approx(0.875)
    assert ran["format"] == ["1,235", "97.1%", "97.1", "12.3"]
