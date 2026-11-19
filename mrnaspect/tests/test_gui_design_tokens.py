"""The GUI's tokens come from figspec: its own type ladder, the report's spacing, and DARK.

Rewritten for the dark GUI (2026-11-06): spacing, fonts and the
series line rule are the report's; the colours are figspec.DARK, the set the GUI's charts
are swapped into, so the page and its charts are one palette. The type ladder was the
report's too until 2026-11-12, when larger text on screen was asked for: it is now
figspec.GUI_TYPE_PX, five steps, and the report keeps TYPE_SCALE_PT. Still one source,
still checked here.

`reporter.py` generates `--type-*` and `--status-*` from `figspec` and injects them into
the template, and `test_design_tokens.py` forbids a literal size there — *"a literal is
a step of no ladder, and nothing relates it to the chart text."* The GUI knew none of
that: its own sizes, its own greys, no `:root` at all.

It cannot import figspec — it is a static page served by vite, with no Python at render
time — so the shared block is a CHECKED COPY. This file regenerates it from figspec and
fails on any drift, which is the same arrangement `test_gui_core_contract.py` uses for
the camelCase/snake_case boundary no compiler sees.

What is deliberately NOT shared: the bar's track, declared locally and saying so. The
surfaces were local until J6c, when the report's screen view took the same paper and
panels; now figspec owns them. The channel states were a ramp, part local and part bound
to the status fills, until 2026-10-29; they are the report's band colours now, checked
against the report's own figure in test_gui_state_bar.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mrnaspect.reporting import figspec

_HTML = Path(__file__).resolve().parents[2] / "gui" / "index.html"

pytestmark = pytest.mark.skipif(
    not _HTML.exists(), reason="GUI sources are not in this checkout")


def _declared() -> dict[str, str]:
    """Every custom property in the GUI's :root, as written."""
    html = _HTML.read_text()
    root = html[html.index(":root {"):html.index("}", html.index(":root {"))]
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"--([a-z0-9-]+):\s*([^;]+);", root)}


def _expected_shared() -> dict[str, str]:
    """What figspec says the shared tokens are. The single source.

    The colours are the GUI's DARK set (figspec.DARK and CATEGORICAL_DARK, palette "A
    Graphite", 2026-11-06), not the report's paper colours: the report stays white.
    """
    out = {f"type-{n}": f"{px}px" for n, px in figspec.GUI_TYPE_PX.items()}
    out.update({
        # 2026-10-31: the one way to draw a distribution, the line and its fill's alpha.
        "series-line-width": f"{figspec.SERIES_LINE_PX:g}px",
        "series-fill-alpha": f"{figspec.FILL_ALPHA:g}",
        # The families, since J2: the GUI is a screen, so it takes the screen sans.
        "font-sans": figspec.FONT_FAMILY,
        "font-mono": figspec.FONT_MONO,
    })
    out.update(figspec.DARK)
    out.update({f"series-{i + 1}": c for i, c in enumerate(figspec.CATEGORICAL_DARK)})
    out.update({f"space-{k}": f"{v}px" for k, v in figspec.SPACE_PX.items()})
    return out


def test_every_shared_token_matches_figspec() -> None:
    declared = _declared()
    wrong = {k: (v, declared.get(k)) for k, v in _expected_shared().items()
             if declared.get(k) != v}
    assert not wrong, (
        "the GUI's copy of the shared tokens has drifted from figspec "
        "(expected, found):\n  " + "\n  ".join(f"--{k}: {e} / {g}" for k, (e, g) in wrong.items()))


def test_the_whole_type_ladder_is_present() -> None:
    """A missing step is how a surface starts inventing its own again."""
    declared = _declared()
    missing = [n for n in figspec.GUI_TYPE_PX if f"type-{n}" not in declared]
    assert not missing, f"steps absent from the GUI: {missing}"


def test_the_ladder_is_the_only_set_of_sizes_in_root() -> None:
    """The Overview's own sizes (--ov-*) sat beside the ladder until 2026-11-12, and with
    them the run view had nine sizes, some half a pixel apart."""
    declared = _declared()
    extra = sorted(k for k in declared if k.startswith("type-")
                   and k[len("type-"):] not in figspec.GUI_TYPE_PX)
    assert not extra, f"steps that are not figspec.GUI_TYPE_PX's: {extra}"
    sizes = sorted(k for k, v in declared.items()
                   if re.fullmatch(r"[0-9.]+px", v) and not k.startswith(("type-", "space-"))
                   and k != "series-line-width")
    assert not sizes, f"px tokens beside the ladder: {sizes}"


def test_no_two_steps_read_as_one_size() -> None:
    """Each step at least 12% above the one below (2026-11-12: 13 and 13.5 px, or 16
    and 16.5 px, side by side read as one size drawn twice)."""
    steps = sorted(figspec.GUI_TYPE_PX.values())
    ratios = [b / a for a, b in zip(steps, steps[1:])]
    assert min(ratios) >= 1.12, list(zip(steps, steps[1:]))
    assert all(isinstance(v, int) for v in steps), "half pixels render unevenly"


def test_the_gui_text_is_larger_than_the_report_s_screen_text() -> None:
    """The request, kept: every GUI step a reader reads at is above the report's
    screen step of the same name (report px = pt x 4/3 x the screen scale).

    The headline step is held to "not under": the Overview's headline
    numbers were found a little large on 2026-11-14, and they now stand at the report's own size."""
    from mrnaspect.reporting.reporter import _SCREEN_TYPE_SCALE

    for name, px in figspec.GUI_TYPE_PX.items():
        if name in figspec.TYPE_SCALE_PT:
            report_px = figspec.TYPE_SCALE_PT[name] * 4 / 3 * _SCREEN_TYPE_SCALE
            if name == "title":
                assert px >= report_px, (name, px, report_px)
            else:
                assert px > report_px, (name, px, report_px)


def test_the_series_colours_are_the_dark_map_of_the_report_s() -> None:
    """Slot for slot: the GUI's series N is what the report's series N becomes in the
    dark map, so a chart and the GUI's own marks agree."""
    cmap = figspec.dark_colour_map()
    assert [_declared()[f"series-{i + 1}"] for i in range(4)] == [
        cmap[c.lower()] for c in figspec.CATEGORICAL]


def test_the_strand_state_is_bound_to_the_first_series_not_copied() -> None:
    assert _declared()["state-strand"] == "var(--series-1)"


def _resolved(name: str) -> str:
    """The colour a token lands on, following var() through :root. A state token may
    bind rather than copy, so its declared text can be a NAME, and a check on the name
    passes whatever colour it points at."""
    declared, value = _declared(), _declared()[name]
    for _ in range(8):
        m = re.fullmatch(r"var\(--([a-z0-9-]+)\)", value)
        if not m:
            return value
        value = declared[m.group(1)]
    raise AssertionError(f"--{name}: var() chain too deep or circular")


def test_no_channel_state_wears_a_status_colour() -> None:
    """A status colour reused as a series stops meaning anything. The state bar wore the
    status amber and red, handed out by rank, until 2026-10-29."""
    statuses = {figspec.DARK[k] for k in ("status-serious", "status-warning",
                                          "status-good", "emphasis")}
    states = {k: _resolved(k) for k in _declared() if k.startswith("state-")}
    assert states and all(v.startswith("#") for v in states.values()), states
    assert not {k: v for k, v in states.items() if v in statuses}


_LIGHT_OPEN = ':root[data-theme="light"] {'


def _stylesheet() -> str:
    """Everything after :root, less the light theme's token block (a second :root,
    checked against figspec.LIGHT below)."""
    html = _HTML.read_text()
    block = html[html.index("<style>"):html.index("</style>")]
    rest = block[block.index("}", block.index(":root {")):]   # everything after :root
    start = rest.index(_LIGHT_OPEN)
    return rest[:start] + rest[rest.index("}", start) + 1:]


def _light() -> dict[str, str]:
    """Every custom property in the light theme's block, as written."""
    html = _HTML.read_text()
    start = html.index(_LIGHT_OPEN)
    block = html[start:html.index("}", start)]
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"--([a-z0-9-]+):\s*([^;]+);", block)}


def test_the_light_block_is_figspec_light_and_the_report_s_series() -> None:
    """The light theme (2026-11-12) is the report's paper and its series, so a light
    chart is the report's chart."""
    want = dict(figspec.LIGHT)
    want.update({f"series-{i + 1}": c for i, c in enumerate(figspec.CATEGORICAL)})
    assert _light() == want


def test_the_light_block_redeclares_every_dark_colour() -> None:
    """A colour token the light block forgets keeps its dark value on a light page."""
    dark = {k for k, v in _declared().items() if re.fullmatch(r"#[0-9a-fA-F]{6}", v)}
    missing = dark - set(_light()) - {"on-accent"}   # white on the fill, in both themes
    assert not missing, sorted(missing)


def test_every_result_type_is_bound_to_its_figspec_slot() -> None:
    """The colour code (figspec.RESULT_COLOURS, 2026-11-12) is bound to the series slots,
    not copied, so it moves with the theme; each card and panel takes it as --mark."""
    declared = _declared()
    for panel, colour in figspec.RESULT_COLOURS.items():
        slot = figspec.CATEGORICAL.index(colour) + 1
        assert declared[f"result-{panel}"] == f"var(--series-{slot})", panel
    html = _HTML.read_text()
    for panel in figspec.RESULT_COLOURS:
        assert re.search(rf'\[data-result="{panel}"\] \{{ --mark: var\(--result-{panel}\) \}}',
                         html), panel


def test_no_rule_outside_root_names_a_size_of_its_own() -> None:
    """The same rule the report's stylesheet lives under."""
    literals = re.findall(r"font-size:\s*([0-9.]+(?:pt|px|rem|em))", _stylesheet())
    assert not literals, (
        f"{literals} are set directly. Use var(--type-<step>); the steps are "
        f"{sorted(figspec.GUI_TYPE_PX)}.")


def test_no_rule_outside_root_names_a_colour_of_its_own() -> None:
    literals = re.findall(r"(?<!-)#[0-9a-fA-F]{3,6}\b", _stylesheet())
    assert not literals, (
        f"{literals} are hard-coded. Every colour is a token; add one to :root with a "
        f"reason if the report has nothing to share.")


# --- the scaffold is gone, and a stylesheet nobody imports must not reappear ----------

_GUI = _HTML.parent


def test_no_orphan_stylesheet() -> None:
    """`src/styles.css` sat unreferenced for three weeks styling `.logo.vite:hover`.

    Dead CSS is worse than none: it reads as the app's stylesheet, so the next person
    edits it and nothing changes. Any .css under gui/ must be referenced by something.
    """
    sources = "\n".join(
        p.read_text() for p in _GUI.rglob("*")
        if p.is_file() and p.suffix in {".html", ".ts", ".js", ".json"}
        and "node_modules" not in p.parts and "src-tauri" not in p.parts)
    orphans = [p.name for p in _GUI.rglob("*.css")
               if "node_modules" not in p.parts and p.name not in sources]
    assert not orphans, f"stylesheets nothing imports: {orphans}"


def test_the_tauri_scaffold_art_is_not_back() -> None:
    leftovers = [p.name for p in _GUI.rglob("*.svg")
                 if "node_modules" not in p.parts
                 and p.name in {"tauri.svg", "vite.svg", "typescript.svg"}]
    assert not leftovers, f"`tauri init` artwork is back in the tree: {leftovers}"
