"""The report's screen figures: the legend clears the range slider, and the poly(A) view
control hides a series.

2026-11-05 (#6, #9), decided 2026-11-09 and 2026-11-10. Both problems were on the
HTML report, which draws Core's plotly spec as it stands; the GUI redesign had already
fixed both for the GUI alone (gui/src/main.ts themeSpec, then darkenSpec; drawFig, applyVisibility).

- #9: figspec's legend sat under the plot (y = -0.18), where the poly(A) and
  per-position figures draw their range slider. It now sits right of the plot, where the
  PDF draws it (figrender). The GUI still lays its own placement over Core's.
- #6: the Both / Corrected / As called buttons restyled `opacity`; in the user's browser
  the legend changed and the trace stayed. The report now draws them as page buttons, as
  the GUI does, and hides a series with `visible`, combined with the construct selector
  so neither control undoes the other. Core's spec keeps the buttons: the GUI builds its
  control from their args.

The script runs under node against a stub Plotly and DOM, when node is on PATH. That
checks what the page sends to Plotly, not how Plotly draws it; that `visible: false`
hides a trace is Plotly's own contract.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from mrnaspect.reporting import figspec

from .test_gui_panels import _TS, _code
from .test_polya_reaches_the_report import ARMS, _run

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")
_TEMPLATE = Path(figspec.__file__).parent / "templates" / "sample_coa.html"
_VIEWS = "plot-polya-by-construct"


@pytest.fixture(scope="module")
def declared(tmp_path_factory, variant_bam, qk_ha_ref) -> Path:
    """A real run with the poly(A) correction applied, so the view control exists."""
    return _run(tmp_path_factory.mktemp("declared"), variant_bam, qk_ha_ref, ARMS)


def _figures(run_dir: Path) -> dict[str, dict]:
    return {f["id"]: f for f in json.loads((run_dir / "figures.json").read_text())}


# --- #9 the legend ---------------------------------------------------------------------

def test_the_screen_legend_sits_right_of_the_plot_as_the_pdf_draws_it() -> None:
    legend = figspec._layout()["legend"]
    assert (legend["orientation"], legend["xanchor"], legend["yanchor"]) == (
        "v", "left", "top")
    assert legend["x"] > 1 and legend["y"] == 1
    assert legend["font"]["size"] == figspec.type_size("micro")


def test_no_figure_puts_its_legend_under_the_plot(declared) -> None:
    """Under the plot (y < 0) is where the range slider is; right of it nothing is."""
    figures = _figures(declared)
    sliders = {fid for fid, f in figures.items()
               if any((ax or {}).get("rangeslider", {}).get("visible")
                      for k, ax in f["spec"]["layout"].items() if k.startswith("xaxis"))}
    assert {"polya-by-construct", "per-position"} <= sliders
    for fid, f in figures.items():
        legend = f["spec"]["layout"]["legend"]
        assert legend["y"] >= 0 and legend["x"] > 1, fid


def test_the_gui_still_lays_its_own_legend_over_core_s() -> None:
    """All five placement keys, so Core's vertical legend never reaches a GUI card."""
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function themeSpec("):]
    fn = fn[:fn.index("\n}\n")]
    assert ('lay.legend = { ...lay.legend, orientation: "h", x: 1, xanchor: "right", '
            'y: 1.02, yanchor: "bottom" }') in fn


# --- #6 the poly(A) view control -------------------------------------------------------

def test_core_s_spec_keeps_the_view_buttons_the_gui_builds_from(declared) -> None:
    menus = _figures(declared)["polya-by-construct"]["spec"]["layout"]["updatemenus"]
    views = [m for m in menus if m.get("type") == "buttons"]
    assert [[b["label"] for b in m["buttons"]] for m in views] == [
        ["Both", "Corrected", "Uncorrected"]]


def test_the_page_buttons_are_styled_for_the_screen() -> None:
    src = _TEMPLATE.read_text()
    screen = src[src.index("@media screen {"):src.index("</style>")]
    assert ".plot-views" in screen and '.seg[aria-pressed="true"]' in screen


#: The report's figure script under a stub Plotly and DOM, then each action in turn. A
#: menu action does what plotly does on a click: run the button's method, then emit
#: `plotly_buttonclicked` with that button (plotly 3.7.0, updatemenus).
_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const script = fs.readFileSync(process.argv[2], "utf8");
const actions = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

function node(tag) {
  return {
    tagName: tag, children: [], attrs: {}, listeners: {}, className: "", textContent: "",
    setAttribute(k, v) { this.attrs[k] = String(v); },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    insertBefore(c, ref) {
      const i = this.children.indexOf(ref);
      if (i < 0) throw new Error("insertBefore: ref is not a child");
      c.parentNode = this;
      this.children.splice(i, 0, c);
      return c;
    },
  };
}
const plots = {};
const document = {
  createElement: node,
  getElementById(id) {
    if (!plots[id]) {
      const el = node("div");
      el.id = id;
      el.handlers = {};
      el.restyles = [];
      el.on = (t, fn) => { (el.handlers[t] = el.handlers[t] || []).push(fn); };
      node("div").appendChild(el);
      plots[id] = el;
    }
    return plots[id];
  },
};
const Plotly = {
  newPlot(el, data, layout) { el.drawn = { data, layout }; },
  restyle(el, upd) { el.restyles.push(upd); },
};
vm.runInNewContext(script, { document, Plotly });

const bars = (el) => el.parentNode.children.filter((c) => c.className === "plot-views");
const pressed = (el) => bars(el).map((bar) => bar.children.map((c) => c.attrs["aria-pressed"]));
const out = { drawn: {}, bars: {}, pressed: {}, steps: [] };
for (const [id, el] of Object.entries(plots)) {
  if (!el.drawn) continue;
  out.drawn[id] = (el.drawn.layout.updatemenus || []).map((m) => ({
    type: m.type || "dropdown", visible: m.visible !== false }));
  out.bars[id] = bars(el).map((bar) => bar.children.map((c) => c.textContent));
  out.pressed[id] = pressed(el);
}
for (const [kind, id, a, b] of actions) {
  const el = plots[id];
  const before = el.restyles.length;
  if (kind === "click") {
    const btn = bars(el).flatMap((bar) => bar.children).find((c) => c.textContent === a);
    if (!btn) throw new Error(`${id} has no page button "${a}"`);
    btn.listeners.click.forEach((fn) => fn());
  } else {
    const button = el.drawn.layout.updatemenus[a].buttons[b];
    el.restyles.push({ visible: button.args[0].visible, byPlotly: true });
    (el.handlers.plotly_buttonclicked || []).forEach((fn) => fn({ button, active: b }));
  }
  const ours = el.restyles.slice(before).filter((u) => !u.byPlotly);
  out.steps.push({
    keys: ours.map((u) => Object.keys(u)),
    visible: ours.length ? ours[ours.length - 1].visible : null,
    pressed: pressed(el),
  });
}
process.stdout.write(JSON.stringify(out));
"""


def _figure_script(html: str) -> str:
    """The report's own script: the one that draws the figures, not the plotly bundle."""
    blocks = [b for b in re.findall(r"<script>(.*?)</script>", html, re.S)
              if "var figures =" in b]
    assert len(blocks) == 1
    return blocks[0]


def _run_script(run_dir: Path, tmp_path: Path, actions: list[list]) -> dict:
    html = (run_dir / "report_run_end.html").read_text()
    (tmp_path / "figures.js").write_text(_figure_script(html))
    (tmp_path / "actions.json").write_text(json.dumps(actions))
    (tmp_path / "harness.js").write_text(_HARNESS)
    done = subprocess.run(
        [NODE, str(tmp_path / "harness.js"), str(tmp_path / "figures.js"),
         str(tmp_path / "actions.json")],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_the_view_control_is_drawn_on_the_page_and_core_s_copy_hidden(
        declared, tmp_path) -> None:
    html = (declared / "report_run_end.html").read_text()
    out = _run_script(declared, tmp_path, [])
    # Every figure is still drawn: the script did not stop at the one it changed.
    assert set(out["drawn"]) == {f"plot-{i}" for i in re.findall(r'id="plot-([^"]+)"', html)}
    assert out["drawn"][_VIEWS] == [{"type": "dropdown", "visible": True},
                                    {"type": "buttons", "visible": False}]
    assert out["bars"][_VIEWS] == [["Both", "Corrected", "Uncorrected"]]
    assert out["pressed"][_VIEWS] == [["true", "false", "false"]]
    # A figure without a view control is drawn as Core wrote it, with no page buttons.
    for pid, drawn in out["drawn"].items():
        if pid != _VIEWS:
            assert {"type": "buttons", "visible": False} not in drawn, pid
            assert out["bars"][pid] == [], pid


@needs_node
def test_a_view_hides_by_visible_and_survives_a_construct_change(declared, tmp_path) -> None:
    fig = _figures(declared)["polya-by-construct"]
    corrected = [t["name"] == "corrected" for t in fig["spec"]["data"]]
    menus = fig["spec"]["layout"]["updatemenus"]
    ci = next(i for i, m in enumerate(menus) if m.get("type") != "buttons")
    first, second = (b["args"][0]["visible"] for b in menus[ci]["buttons"][:2])
    out = _run_script(declared, tmp_path, [
        ["click", _VIEWS, "Corrected"],
        ["menu", _VIEWS, ci, 1],
        ["click", _VIEWS, "Uncorrected"],
        ["click", _VIEWS, "Both"],
    ])
    assert [s["visible"] for s in out["steps"]] == [
        [s and c for s, c in zip(first, corrected)],
        [s and c for s, c in zip(second, corrected)],
        [s and not c for s, c in zip(second, corrected)],
        second,
    ]
    # Hidden by `visible`; never the opacity restyle that left the trace drawn.
    for step in out["steps"]:
        assert step["keys"] and all(set(k) <= {"visible", "hoverinfo", "showlegend"}
                                    for k in step["keys"])
    assert [s["pressed"] for s in out["steps"]] == [
        [["false", "true", "false"]],
        [["false", "true", "false"]],
        [["false", "false", "true"]],
        [["true", "false", "false"]],
    ]


@needs_node
def test_a_figure_without_a_view_control_is_left_to_plotly(declared, tmp_path) -> None:
    menus = _figures(declared)["per-position"]["spec"]["layout"]["updatemenus"]
    assert len(menus) == 1 and menus[0].get("type") != "buttons"
    out = _run_script(declared, tmp_path, [["menu", "plot-per-position", 0, 1]])
    assert out["steps"][0]["keys"] == []
