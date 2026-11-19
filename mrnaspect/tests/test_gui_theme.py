"""Two themes: dark by default, as MinKNOW is, and light, chosen in Settings.

Dark only was decided 2026-11-06 (2026-11-05), reversing the 2026-10-30 light-only
decision (Core 2ca2510). A light mode was then asked for, for manuscript figures and
poster screenshots (2026-11-12): a switch in Settings › Appearance, dark by default, the
title bar following. The page never follows the system's own setting.

What carries over from the J6 pass (2026-10-28), in BOTH themes: every colour a token,
every style in the stylesheet where a guard can see it, a clean finish never printed in
a status colour, a control's edge at 3:1 against the window (WCAG 1.4.11), and
placeholder text that is readable text.

The charts follow the theme (figspec's dark and light maps, test_gui_dark_theme.py and
test_gui_light_theme.py); the HTML report and the PDF stay white paper and are not
checked here.

No compiler or browser runs here, so the page is checked as text. How it LOOKS is judged
on the Mac build only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mrnaspect.reporting import figspec

_GUI = Path(__file__).resolve().parents[2] / "gui"
_HTML = _GUI / "index.html"
_TS = _GUI / "src" / "main.ts"
_CONF = _GUI / "src-tauri" / "tauri.conf.json"
_CAPS = _GUI / "src-tauri" / "capabilities" / "default.json"

pytestmark = pytest.mark.skipif(
    not _HTML.exists(), reason="GUI sources are not in this checkout")

#: Every ground text and controls sit on.
_GROUNDS = ("surface-menu", "surface-page", "surface-panel", "surface-raised")
_LIGHT_OPEN = ':root[data-theme="light"] {'
THEMES = {"dark": figspec.DARK, "light": figspec.LIGHT}


def _parts() -> tuple[str, str, str]:
    """(:root block, the rest of the stylesheet, the markup after it)."""
    html = _HTML.read_text()
    s0, s1 = html.index("<style>"), html.index("</style>")
    root_end = html.index("}", html.index(":root {", s0)) + 1
    return html[s0:root_end], html[root_end:s1], html[s1:]


def _light_block() -> str:
    html = _HTML.read_text()
    start = html.index(_LIGHT_OPEN)
    return html[start:html.index("}", start) + 1]


def _tokens(block: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"--([a-z0-9-]+):\s*([^;]+);", block)}


def _declared(theme: str = "dark") -> dict[str, str]:
    """The tokens one theme sees: :root, with the light block laid over it for light."""
    root, _, _ = _parts()
    out = _tokens(root)
    if theme == "light":
        out.update(_tokens(_light_block()))
    return out


def _resolve(value: str, theme: str = "dark") -> str:
    """Follow var() through the theme's tokens to the colour it lands on."""
    declared = _declared(theme)
    for _ in range(8):
        m = re.fullmatch(r"var\(--([a-z0-9-]+)\)", value.strip())
        if not m:
            return value.strip()
        value = declared[m.group(1)]
    raise AssertionError(f"var() chain too deep or circular at {value}")


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _rule(css: str, selector: str) -> str:
    m = re.search(r"(?:^|[}\s/])" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return m.group(1)


def _rules(css: str) -> list[tuple[str, str]]:
    """(selector, body) for every rule, comments removed."""
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    return [(m.group(1).strip(), m.group(2))
            for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def _prop(body: str, name: str) -> str:
    m = re.search(rf"(?:^|[;\s{{]){re.escape(name)}\s*:\s*([^;}}]+)", body)
    assert m, f"no {name} in {body!r}"
    return m.group(1).strip()


# --- dark by default, light on a switch ------------------------------------------------------

def test_dark_is_the_default_and_light_is_one_attribute_away() -> None:
    root, css, _ = _parts()
    assert re.search(r"color-scheme:\s*dark\s*;", root)
    assert re.search(r"color-scheme:\s*light\s*;", _light_block())
    assert "prefers-color-scheme" not in css + root, (
        "the page follows the system again; the theme is the operator's switch")


@pytest.mark.skipif(not _CONF.exists(), reason="no tauri.conf.json")
def test_the_title_bar_starts_dark_and_follows_the_switch() -> None:
    w = json.loads(_CONF.read_text())["app"]["windows"][0]
    assert w.get("theme") == "Dark", w.get("theme")
    assert "core:window:allow-set-theme" in json.loads(_CAPS.read_text())["permissions"], (
        "setTheme needs its own permission; core:default does not grant it")
    ts = _TS.read_text()
    fn = ts[ts.index("function applyTheme("):]
    fn = fn[:fn.index("\n}\n")]
    assert "getCurrentWindow().setTheme(t)" in fn


def test_the_first_paint_and_the_switch_read_one_key() -> None:
    """index.html's first script applies the saved theme before the page is drawn, so a
    light window does not open dark and flip; main.ts keeps the same key."""
    html, ts = _HTML.read_text(), _TS.read_text()
    key = re.search(r'const THEME_KEY = "([^"]+)";', ts).group(1)
    head = html[:html.index("<style>")]
    assert f'localStorage.getItem("{key}") === "light"' in head
    assert 'document.documentElement.dataset.theme = "light"' in head


def test_the_display_page_is_one_radio_group_wired_to_the_switch() -> None:
    """Settings › Display (was Appearance; 2026-11-13)."""
    html = _HTML.read_text()
    page = html[html.index('id="page-display"'):]
    page = page[:page.index("</section>")]
    assert 'role="radiogroup"' in page
    choices = re.findall(r'data-theme-choice="(\w+)" role="radio"', page)
    assert choices == ["dark", "light"], choices
    ts = _TS.read_text()
    assert 'b.addEventListener("click", () => applyTheme(b.dataset.themeChoice as ThemeName));' in ts
    assert re.search(r'const SETTINGS_PAGES: Page\[\] = \[[^\]]*"display"', ts)
    assert "appearance" not in ts and 'id="page-appearance"' not in html


def test_the_body_sits_on_the_page_surface_in_the_primary_ink() -> None:
    _, css, _ = _parts()
    body = _rule(css, "body")
    assert _prop(body, "background") == "var(--surface-page)"
    assert _prop(body, "color") == "var(--ink-primary)"


# --- every style where a guard can see it --------------------------------------------------

def test_every_style_lives_in_the_stylesheet() -> None:
    """Inline style attributes are where nine spacing values hid from every guard in J6.
    Geometry computed per value (a bar's width) is set from main.ts, not written here."""
    _, _, markup = _parts()
    assert not re.findall(r'\sstyle="', markup)


def test_every_token_it_asks_for_is_declared() -> None:
    _, css, _ = _parts()
    asked = set(re.findall(r"var\(--([a-z0-9-]+)\)", css + _TS.read_text()))
    missing = asked - set(_declared())
    assert not missing, f"used but never declared in :root: {sorted(missing)}"


def test_the_body_and_the_fields_sit_on_the_type_ladder() -> None:
    _, css, _ = _parts()
    assert _prop(_rule(css, "body"), "font-size") == "var(--type-body)"


# --- contrast on every ground, in both themes ----------------------------------------------

@pytest.mark.parametrize("theme", sorted(THEMES))
@pytest.mark.parametrize("ground", _GROUNDS)
@pytest.mark.parametrize("ink", ["ink-primary", "ink-secondary", "ink-muted", "accent",
                                 "status-serious", "status-warning"])
def test_text_clears_4_5_to_1_on_every_ground(ink, ground, theme) -> None:
    r = _contrast(_resolve(f"var(--{ink})", theme), _resolve(f"var(--{ground})", theme))
    assert r >= 4.5, (theme, ink, ground, round(r, 2))


@pytest.mark.parametrize("theme", sorted(THEMES))
@pytest.mark.parametrize("ground", ["surface-page", "surface-panel"])
def test_every_controls_edge_clears_3_to_1(ground, theme) -> None:
    """A field or button edge is a control's boundary (WCAG 1.4.11)."""
    _, css, _ = _parts()
    for sel in (".btn", "input:not([type=checkbox]), select"):
        border = _prop(_rule(css, sel), "border")
        token = re.search(r"var\(--[a-z0-9-]+\)", border).group(0)
        r = _contrast(_resolve(token, theme), _resolve(f"var(--{ground})", theme))
        assert r >= 3.0, (theme, sel, token, ground, round(r, 2))


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_the_primary_action_is_white_on_the_accent_fill(theme) -> None:
    _, css, _ = _parts()
    body = _rule(css, ".btn.primary")
    fg = _resolve(_prop(body, "color"), theme)
    bg = _resolve(_prop(body, "background"), theme)
    assert bg == THEMES[theme]["accent-fill"]
    assert _contrast(fg, bg) >= 4.5


def _over(top: str, alpha: float, ground: str) -> str:
    """`top` at `alpha` laid over `ground`, as the browser composites it (sRGB)."""
    a = [int(top[i:i + 2], 16) for i in (1, 3, 5)]
    g = [int(ground[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(alpha * x + (1 - alpha) * y):02x}" for x, y in zip(a, g))


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_the_waiting_box_is_white_on_translucent_black(theme) -> None:
    """The box over an empty Overview (2026-11-13): translucent black, white text,
    readable over whatever it covers, the cards' panel or the page between them."""
    root, css, _ = _parts()
    body = _rule(css, ".ovwait")
    assert _prop(body, "background") == "var(--scrim)"
    scrim = re.fullmatch(r"rgba\(0,\s*0,\s*0,\s*([\d.]+)\)", _tokens(root)["scrim"])
    assert scrim, "the scrim is translucent black"
    ink = _resolve(_prop(body, "color"), theme)
    for ground in ("surface-panel", "surface-page"):
        under = _over("#000000", float(scrim.group(1)), THEMES[theme][ground])
        assert _contrast(ink, under) >= 4.5, (theme, ground, under)


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_the_soft_action_reads_and_keeps_an_edge(theme) -> None:
    """View report wears the accent as a tint, not a fill (2026-11-13: the blue
    stood out too much); its text reads on the tint, and its edge stays a control's."""
    root, css, _ = _parts()
    body = _rule(css, ".btn.soft")
    tint = re.fullmatch(r"color-mix\(in srgb, var\(--accent-fill\) (\d+)%, transparent\)",
                        _tokens(root)["accent-soft"])
    assert tint and _prop(body, "background") == "var(--accent-soft)"
    fill = THEMES[theme]["accent-fill"]
    for ground in ("surface-panel", "surface-page"):
        under = _over(fill, int(tint.group(1)) / 100, THEMES[theme][ground])
        assert _contrast(_resolve(_prop(body, "color"), theme), under) >= 4.5, (theme, ground)
        assert _contrast(_resolve(_prop(body, "border-color"), theme),
                         THEMES[theme][ground]) >= 3.0, (theme, ground)


def test_no_rule_can_reach_into_a_plotly_chart() -> None:
    """Plotly's SVG carries classes of its own: each line of a title is `tspan.line`, each
    subplot a `g.plot`. A rule written as `svg .line` once drew every chart title as blue
    outlines (2026-11-06, on the Mac). The page's own chart classes are prefixed."""
    _, css, _ = _parts()
    for sel, _body in _rules(css):
        for part in sel.split(","):
            assert not re.search(r"(^|\s)(svg\s+)?\.(line|plot|area|bar|points|trace|legend)\b",
                                 part.strip()), part


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_stop_is_the_serious_red_outlined_and_its_confirmation_filled(theme) -> None:
    """The plan's Stop: outlined in the serious red while running (§4.3), and a red Stop
    in the confirmation (§4.4)."""
    _, css, markup = _parts()
    red = THEMES[theme]["status-serious"]
    assert _resolve(_prop(_rule(css, ".btn.danger"), "color"), theme) == red
    fill = _rule(css, ".btn.danger-fill")
    assert _resolve(_prop(fill, "background"), theme) == red
    assert _contrast(_resolve(_prop(fill, "color"), theme), red) >= 4.5
    assert 'id="stop"' in markup and 'class="btn danger" id="stop"' in markup


def test_the_placeholder_is_readable_text() -> None:
    """It carries the config's value since I2, so it is text a person reads."""
    _, css, _ = _parts()
    body = _rule(css, "input::placeholder")
    assert _prop(body, "color") == "var(--ink-secondary)"
    assert _prop(body, "opacity") == "1"


def test_a_clean_finish_is_not_printed_in_a_status_colour() -> None:
    """Every clean finish was printed in the warning amber until 2026-10-28."""
    _, css, _ = _parts()
    assert "status" not in _rule(css, ".pill.done i")
    assert "status-serious" in _rule(css, ".pill.bad")


# --- the marks are the report's -------------------------------------------------------------

def test_the_histogram_is_drawn_the_report_s_way_in_its_card_s_colour() -> None:
    """One step line over the same colour at the fill alpha (figspec.FILL_ALPHA), in the
    colour of the card's result type (--mark, set by [data-result]; 2026-11-12)."""
    _, css, _ = _parts()
    area, line = _rule(css, ".hx-area"), _rule(css, ".hx-line")
    assert _prop(area, "fill") == "var(--mark)"
    assert _prop(area, "fill-opacity") == "var(--series-fill-alpha)"
    assert _prop(line, "stroke") == "var(--mark)"
    assert _prop(line, "stroke-width") == "var(--series-line-width)"


# --- the window ----------------------------------------------------------------------

@pytest.mark.skipif(not _CONF.exists(), reason="no tauri.conf.json")
def test_the_window_fits_a_13_inch_mac_and_cannot_be_crushed() -> None:
    """1440x900 logical at default scaling leaves ~805 px under the menu bar and above
    the Dock, and Tauri's height excludes the ~28 px title bar -- so 760 is the tallest
    default that fits every current Mac laptop."""
    w = json.loads(_CONF.read_text())["app"]["windows"][0]
    assert w["width"] <= 1180 and w["height"] <= 760, (w["width"], w["height"])
    assert w.get("minWidth") and w.get("minHeight"), "no minimum size"
    assert w["minWidth"] <= w["width"] and w["minHeight"] <= w["height"]
