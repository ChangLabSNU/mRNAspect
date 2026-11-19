"""The GUI's light theme (2026-11-12: for manuscript figures and poster screenshots).

The light charts are the report's charts: Core's specs carry the report's own colours,
so the light map moves only the grid (to a colour that can be seen), and the chrome lays
LIGHT's colours over the same roles the dark chrome does. The tokens pass the gates the
dark set passes; the text and control gates on every ground are in test_gui_theme.py.

gui/src/light_theme.json is a CHECKED COPY of `figspec.light_theme()`, written with the
dark one by `python scripts/gen_gui_themes.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mrnaspect.reporting import figspec
from mrnaspect.tests.test_palette_separation import delta_e

_JSON = Path(__file__).resolve().parents[2] / "gui" / "src" / "light_theme.json"


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


L = figspec.LIGHT


@pytest.mark.skipif(not _JSON.exists(), reason="GUI sources are not in this checkout")
def test_the_gui_copy_matches_figspec() -> None:
    assert json.loads(_JSON.read_text()) == figspec.light_theme(), (
        "gui/src/light_theme.json has drifted from figspec; run "
        "`python scripts/gen_gui_themes.py` and commit the result")


def test_the_light_map_moves_only_the_grid() -> None:
    assert figspec.light_colour_map() == {figspec.GRIDLINE.lower(): L["grid"]}


def test_the_light_tokens_name_every_dark_token() -> None:
    """One switch flips every token: a name only dark has would keep its dark colour."""
    assert set(L) == set(figspec.DARK)


def test_the_two_chromes_set_the_same_keys() -> None:
    """The themes differ in colour only: no layout key one sets and the other leaves."""
    dark, light = figspec.dark_layout(), figspec.light_layout()
    assert set(dark) == set(light)
    assert set(dark["axis"]) == set(light["axis"])
    assert light["paper_bgcolor"] == light["plot_bgcolor"] == "rgba(0,0,0,0)"


def test_the_light_grid_is_seen_and_stays_behind_the_axis() -> None:
    """The report's GRIDLINE is 1.32:1 on white, the grid that was too faint to see."""
    grid = _contrast(L["grid"], L["surface-panel"])
    axis = _contrast(figspec.light_layout()["axis"]["linecolor"], L["surface-panel"])
    assert 1.5 <= grid < 3.0 <= axis, (round(grid, 2), round(axis, 2))
    assert grid > _contrast(figspec.GRIDLINE, L["surface-panel"])


@pytest.mark.parametrize("surface", ["surface-page", "surface-panel"])
def test_light_series_clear_3_to_1_where_marks_sit(surface) -> None:
    """Charts and the Overview's marks sit on the panel, the page under it."""
    for c in (*figspec.CATEGORICAL, figspec.POLYA_CORRECTED):
        assert _contrast(c, L[surface]) >= 3.0, (c, surface)


@pytest.mark.parametrize("status", ["serious", "warning"])
def test_status_text_reads_on_its_own_light_banner(status) -> None:
    assert _contrast(L[f"status-{status}"], L[f"status-{status}-surface"]) >= 4.5


def test_no_light_series_can_be_mistaken_for_a_status() -> None:
    marks = (*figspec.CATEGORICAL, figspec.POLYA_CORRECTED)
    for k in ("status-serious", "status-warning", "status-good", "emphasis"):
        assert min(delta_e(c, L[k]) for c in marks) >= 15, k


def test_the_light_page_is_the_user_s_cool_grey() -> None:
    """The background the palette sheet was drawn on (2026-11-13); the menu and raised surfaces take the same cool tone, so the page and
    the menu beside it are one grey family."""
    assert L["surface-page"] == "#f3f5f8"
    for k in ("surface-menu", "surface-page", "surface-raised"):
        r, g, b = (int(L[k][i:i + 2], 16) for i in (1, 3, 5))
        assert b >= r, (k, L[k], "a warm grey beside a cool one")
