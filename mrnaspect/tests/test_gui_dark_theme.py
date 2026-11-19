"""The GUI's dark charts: every colour a spec can carry has a dark counterpart (C2).

The GUI swaps each colour in a figure spec through figspec's light→dark map before
plotly draws it. A colour missing from the map is drawn as it is: a light colour on a
dark chart, with no error anywhere. So the map is checked against every builder's
output, by the same pattern the GUI uses to find colours (`themeSpec` in main.ts):
any six-digit hex inside any string (a title's ``<span style="color:#…">`` too), any
rgba() by its hex, and the named colour "white".

The dark series are held to the gates P1 passes on white (test_palette_separation.py):
every pair ΔE >= 15 normal / >= 8 CVD, >= 3:1 on every dark surface, ΔE >= 15 between
any series and a status colour or the emphasis.

gui/src/dark_theme.json is a CHECKED COPY of `figspec.dark_theme()`, like the GUI's
:root tokens in test_gui_design_tokens.py; regenerate with
`python scripts/gen_gui_themes.py`.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest

from mrnaspect.reporting import figspec
from mrnaspect.tests.test_palette_separation import (
    CVD_FLOOR, NORMAL_FLOOR, _CVD, _lin, _oklab, delta_e,
)
from mrnaspect.tests.test_plotly_bundle_contract import _every_spec

_REPO = Path(__file__).resolve().parents[2]
_JSON = _REPO / "gui" / "src" / "dark_theme.json"
_MAIN = _REPO / "gui" / "src" / "main.ts"

#: The GUI's pattern, character for character (main.ts `COLOUR_RE`).
_HEX = re.compile(r"#[0-9a-fA-F]{6}(?![0-9a-fA-F])")
_RGBA = re.compile(r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)")


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        r, g, b_ = _lin(h)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _found(node) -> set[str]:
    """Every colour the GUI's swap would look up, as lower-case hex or "white"."""
    out: set[str] = set()
    if isinstance(node, str):
        out |= {m.group(0).lower() for m in _HEX.finditer(node)}
        out |= {"#%02x%02x%02x" % tuple(int(m.group(i)) for i in (1, 2, 3))
                for m in _RGBA.finditer(node)}
        if node.strip().lower() == "white":
            out.add("white")
    elif isinstance(node, dict):
        for v in node.values():
            out |= _found(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            out |= _found(v)
    return out


_SURFACES = ("surface-menu", "surface-page", "surface-panel", "surface-raised")


@pytest.mark.parametrize("name", sorted(_every_spec()))
def test_every_colour_in_every_spec_has_a_dark_counterpart(name):
    missing = _found(_every_spec()[name]) - set(figspec.dark_colour_map())
    assert not missing, (
        f"{name} carries {sorted(missing)}, which the dark map does not cover; the GUI "
        f"would draw it unchanged on a dark chart")


def test_the_map_is_lower_case_hex_to_hex():
    for k, v in figspec.dark_colour_map().items():
        assert k == "white" or re.fullmatch(r"#[0-9a-f]{6}", k), k
        assert re.fullmatch(r"#[0-9a-f]{6}", v), (k, v)


def test_every_series_slot_maps_to_its_own_dark_slot():
    cmap = figspec.dark_colour_map()
    assert [cmap[c.lower()] for c in figspec.CATEGORICAL] == list(
        figspec.CATEGORICAL_DARK)


def test_dark_series_clear_the_normal_vision_floor():
    for a, b in itertools.combinations(figspec.CATEGORICAL_DARK, 2):
        assert delta_e(a, b) >= NORMAL_FLOOR, (a, b, delta_e(a, b))


@pytest.mark.parametrize("kind", ["protan", "deutan"])
def test_dark_series_clear_the_red_green_cvd_floor(kind):
    for a, b in itertools.combinations(figspec.CATEGORICAL_DARK, 2):
        assert delta_e(a, b, kind) >= CVD_FLOOR, (kind, a, b, delta_e(a, b, kind))


def test_dark_series_clear_the_tritan_floor():
    """Closed 2026-11-06: palette A was first searched against protan and deutan only,
    and pore #7ca12b sat ΔE 5.9 from the lavender under tritan. Pore #59a936 fixed it."""
    for a, b in itertools.combinations(figspec.CATEGORICAL_DARK, 2):
        assert delta_e(a, b, "tritan") >= CVD_FLOOR, (a, b, delta_e(a, b, "tritan"))


@pytest.mark.parametrize("surface", _SURFACES)
def test_dark_series_clear_3_to_1_on_every_surface(surface):
    ground = figspec.DARK[surface]
    for c in figspec.CATEGORICAL_DARK:
        assert _contrast(c, ground) >= 3.0, (c, surface, _contrast(c, ground))


@pytest.mark.parametrize("token", ["status-serious", "status-warning", "status-good",
                                   "emphasis"])
def test_no_dark_series_can_be_mistaken_for_a_status(token):
    colour = figspec.DARK[token]
    near = min(delta_e(colour, s) for s in figspec.CATEGORICAL_DARK)
    assert near >= NORMAL_FLOOR, (token, colour, near)


@pytest.mark.parametrize("token", ["ink-primary", "ink-secondary", "ink-muted",
                                   "accent", "status-serious", "status-warning",
                                   "status-good"])
@pytest.mark.parametrize("surface", _SURFACES)
def test_dark_text_clears_4_5_to_1_on_every_surface(token, surface):
    r = _contrast(figspec.DARK[token], figspec.DARK[surface])
    assert r >= 4.5, (token, surface, round(r, 2))


@pytest.mark.parametrize("status", ["serious", "warning"])
def test_status_text_reads_on_its_own_banner(status):
    r = _contrast(figspec.DARK[f"status-{status}"],
                  figspec.DARK[f"status-{status}-surface"])
    assert r >= 4.5, (status, round(r, 2))


def test_the_check_mark_green_reads_as_text_on_every_surface():
    """status-good is text: the input screen's "✓ 40 POD5 files" (2026-11-13: moved to
    #32ab5d so the Lagoon teal and lime could stay near the palette sheet)."""
    for s in _SURFACES:
        assert _contrast(figspec.DARK["status-good"], figspec.DARK[s]) >= 4.5, s


def test_white_text_reads_on_the_button_fill():
    assert _contrast("#ffffff", figspec.DARK["accent-fill"]) >= 4.5


def test_emphasis_marks_clear_3_to_1_on_every_surface():
    for s in _SURFACES:
        assert _contrast(figspec.DARK["emphasis"], figspec.DARK[s]) >= 3.0, s


def test_the_dark_ramp_is_one_hue_rising_in_lightness():
    """Magnitude is brightness on a dark ground: the low end near the panel."""
    lightness = [_oklab(_lin(c))[0] for _, c in figspec.SEQUENTIAL_BLUE_DARK]
    assert lightness == sorted(lightness) and len(set(lightness)) == len(lightness)
    assert [p for p, _ in figspec.SEQUENTIAL_BLUE_DARK] == [
        p for p, _ in figspec.SEQUENTIAL_BLUE]


def test_the_dark_layout_keeps_a_visible_grid_and_no_paper():
    """The grid was switched off here, MinKNOW's way, until the charts proved too
    hard to read (2026-11-12). Every axis now keeps the spec's own grid, in a colour that
    is seen on the panel (`rule`, the old grid colour, was 1.28:1) but stays under the
    axis line and far under the 3:1 a data mark needs."""
    lay = figspec.dark_layout()
    assert lay["axis"].get("showgrid", True) is not False
    assert lay["axis"]["gridcolor"] == figspec.DARK["grid"]
    panel = figspec.DARK["surface-panel"]
    grid = _contrast(figspec.DARK["grid"], panel)
    axis = _contrast(figspec.DARK["rule-strong"], panel)
    assert 1.5 <= grid < axis < 3.0, (round(grid, 2), round(axis, 2))
    assert lay["paper_bgcolor"] == lay["plot_bgcolor"] == "rgba(0,0,0,0)"


def test_a_spec_s_gridline_becomes_the_dark_grid():
    """Specs draw their grid in the report's GRIDLINE; on a dark chart that is the grid
    colour, not the faint rule it was mapped to before 2026-11-12."""
    assert figspec.dark_colour_map()[figspec.GRIDLINE.lower()] == figspec.DARK["grid"]


@pytest.mark.skipif(not _JSON.exists(), reason="GUI sources are not in this checkout")
def test_the_gui_copy_matches_figspec():
    on_disk = json.loads(_JSON.read_text())
    assert on_disk == figspec.dark_theme(), (
        "gui/src/dark_theme.json has drifted from figspec; run "
        "`python scripts/gen_gui_themes.py` and commit the result")


@pytest.mark.skipif(not _MAIN.exists(), reason="GUI sources are not in this checkout")
def test_the_gui_finds_colours_with_the_same_pattern():
    """The patterns above are only a check of the GUI if the GUI uses them."""
    src = _MAIN.read_text()
    assert "#[0-9a-fA-F]{6}(?![0-9a-fA-F])" in src
    assert r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)" in src
