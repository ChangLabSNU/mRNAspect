"""One type scale and one set of status colours, for both media.

There were three ladders: eight sizes in the print stylesheet (8, 8.5, 9, 10, 10.5, 12,
16 pt and an 11 px that had no business in a print context), four more on screen, and
three chosen separately for chart text. None knew about the others, so a caption and the
axis label under it were near-misses rather than relatives.

And the warning banner's rule was #b8860b — CATEGORICAL[1] exactly. A reader had seen
that hue minutes earlier meaning "deletion".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mrnaspect.reporting import figspec
from mrnaspect.reporting.reporter import _TEMPLATE_DIR, _TEMPLATE_NAME, CoAReporter

TEMPLATE = (Path(_TEMPLATE_DIR) / _TEMPLATE_NAME).read_text()


def test_the_stylesheet_names_no_size_of_its_own():
    """A literal is a step of no ladder, and nothing relates it to the chart text."""
    literals = re.findall(r"font-size:\s*([0-9.]+(?:pt|px|rem|em));", TEMPLATE)
    assert not literals, (
        f"{literals} are set directly. Use var(--type-<step>); the steps are "
        f"{sorted(figspec.TYPE_SCALE_PT)}.")


def test_every_step_the_stylesheet_asks_for_exists():
    """A typo in a custom property is silent: the declaration is simply dropped and the
    element inherits, which looks like a deliberate size."""
    used = set(re.findall(r"var\(--type-([a-z]+)\)", TEMPLATE))
    assert used, "the stylesheet uses no scale at all"
    assert used <= set(figspec.TYPE_SCALE_PT), (
        f"no such step: {sorted(used - set(figspec.TYPE_SCALE_PT))}")


def test_the_tokens_are_emitted_for_both_media():
    css = CoAReporter._design_tokens()
    for step, pt in figspec.TYPE_SCALE_PT.items():
        assert f"--type-{step}: {pt:g}pt;" in css, step
    # Screen gets the SAME steps, a size larger, so the relationships survive.
    assert "@media screen" in css
    screen = css[css.index("@media screen"):]
    for step in figspec.TYPE_SCALE_PT:
        assert f"--type-{step}:" in screen, f"{step} has no screen value"
    assert "--type-body: 15px;" in screen, (
        "the body step must land on the size the screen already read at")


def test_the_chart_text_is_on_the_same_ladder():
    """A panel is rendered at the size it is PLACED at, so a point inside a figure is
    the same size as a point beside it. The two can share one ladder, and the whole
    reason to have one is that they do."""
    layout = figspec._layout()
    assert layout["font"]["size"] == figspec.type_size("micro")
    assert layout["legend"]["font"]["size"] == figspec.type_size("micro")
    assert figspec._title("A", "d")["font"]["size"] == figspec.type_size("body")

    from mrnaspect.reporting import figrender
    assert figrender._FONT_PT_PER_SPEC_UNIT == pytest.approx(
        1 / figspec.SPEC_UNITS_PER_PT), (
        "figrender carries its own conversion again; the spec sizes and the factor that "
        "converts them must come from one place")


def test_a_spec_size_round_trips_to_its_step():
    for step, pt in figspec.TYPE_SCALE_PT.items():
        from mrnaspect.reporting import figrender
        back = figspec.type_size(step) * figrender._FONT_PT_PER_SPEC_UNIT
        assert back == pytest.approx(pt, abs=0.4), step


# --- status colours ----------------------------------------------------------

def test_no_status_colour_is_a_series_colour():
    """RESERVED means reserved. The warning rule WAS CATEGORICAL[1], so the same hue
    meant "deletion" in a figure and "look at this" in a banner on the same page."""
    series = {c.lower() for c in figspec.CATEGORICAL}
    # Every STATUS_*, not a list of six: the fills (J6a) and STATUS_GOOD (J6c) were added
    # after the list was written, and a hand list guards only the names it remembers.
    names = [n for n in dir(figspec) if n.startswith("STATUS_")]
    assert len(names) >= 9, names
    for name in names:
        assert getattr(figspec, name).lower() not in series, name


def test_the_banners_name_no_colour_of_their_own():
    banners = re.search(r"\.insufficient-banner \{.*?\}", TEMPLATE, re.S)
    assert banners, "the banner rule moved; this guard is checking nothing"
    assert not re.search(r"#[0-9a-fA-F]{6}", banners.group(0)), (
        "a banner is naming a hex directly; use the reserved status tokens")


def test_status_text_clears_the_body_text_contrast_floor():
    """A warning nobody can read is not a warning."""
    def contrast(a: str, b: str) -> float:
        def lum(h):
            def chan(v):
                v /= 255
                return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
            r, g, bl = (chan(int(h[i:i + 2], 16)) for i in (1, 3, 5))
            return 0.2126 * r + 0.7152 * g + 0.0722 * bl
        la, lb = sorted((lum(a), lum(b)))
        return (lb + 0.05) / (la + 0.05)

    assert contrast(figspec.STATUS_WARNING_INK, figspec.STATUS_WARNING_SURFACE) >= 4.5
    assert contrast(figspec.STATUS_SERIOUS_INK, figspec.STATUS_SERIOUS_SURFACE) >= 4.5
