"""Font families are tokens, like the sizes and the status colours already were.

The token system covered SIZES (test_design_tokens forbids a literal font-size: "a literal
is a step of no ladder") and never FAMILIES. A guard watching one attribute of the type
rather than the type -- so the families drifted exactly where the sizes could not. Measured
2026-10-28: four spellings of the sans (figspec's, the report's print rule, its screen
rule, the GUI's) and three of the mono ("Courier New" on the report's values, a system
stack on its code, the GUI's own copy of that stack).

What it looked like on the page, on a Linux host: the report's values in Liberation Mono
and its code in Noto Sans Mono; the footer -- sample identity and page count, on every
page -- in DejaVu Serif, because @page margin boxes inherit from the page and not from
<body>; and the printed figures in DejaVu Sans, because matplotlib matches family names
exactly and finds no "Helvetica" or "Arial" on Linux, while the text around them reached
TeX Gyre Heros through fontconfig's alias.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mrnaspect.reporting import figspec
from mrnaspect.reporting.figrender import GRAYSCALE, SCREEN, _font_family
from mrnaspect.reporting.reporter import CoAReporter

_TEMPLATE = (Path(__file__).resolve().parents[1] / "reporting" / "templates"
             / "sample_coa.html").read_text()
_GUI = Path(__file__).resolve().parents[2] / "gui" / "index.html"


def _families(css: str) -> list[str]:
    return [v.strip() for v in re.findall(r"font-family:\s*([^;}]+)[;}]", css)]


def test_the_template_names_no_family_of_its_own() -> None:
    literal = [v for v in _families(_TEMPLATE) if not v.startswith("var(--font-")]
    assert not literal, f"literal font stacks in the template: {literal}"


@pytest.mark.skipif(not _GUI.exists(), reason="GUI sources are not in this checkout")
def test_the_gui_names_no_family_outside_its_root() -> None:
    html = _GUI.read_text()
    root_end = html.index("}", html.index(":root {"))
    literal = [v for v in _families(html[root_end:]) if not v.startswith("var(--font-")]
    assert not literal, f"literal font stacks in the GUI stylesheet: {literal}"


def test_both_media_get_their_stack_from_figspec() -> None:
    css = str(CoAReporter._design_tokens())
    base, screen = css.split("@media screen")
    assert f"--font-sans: {figspec.FONT_FAMILY_PRINT};" in base
    assert f"--font-mono: {figspec.FONT_MONO};" in base
    assert f"--font-sans: {figspec.FONT_FAMILY};" in screen


def test_the_quotes_in_a_stack_survive_the_template(tmp_path) -> None:
    """autoescape turns '"' into &#34;, and a browser drops that rule without a word --
    the trap _json_for_script already documents for the charts."""
    rep = CoAReporter(tmp_path, min_reads=1)
    rep.update("run_summary", {"mapping": {"total": 10, "mapped": 9, "unmapped": 1,
                                           "mapping_rate": 0.9}})
    html = Path(rep.finalize("run_end")["html_path"]).read_text()
    assert '--font-sans: "Helvetica"' in html
    assert "&#34;" not in html[:html.index("</style>")]


def test_the_footer_names_its_family() -> None:
    """Margin boxes inherit from the PAGE: without this the footer was the one serif text."""
    for box in ("@bottom-left", "@bottom-right"):
        block = _TEMPLATE[_TEMPLATE.index(box):_TEMPLATE.index("}", _TEMPLATE.index(box))]
        assert "font-family: var(--font-sans)" in block, f"{box} sets no family"


def test_the_printed_figures_use_the_print_face() -> None:
    assert GRAYSCALE.font_family == figspec.FONT_FAMILY_PRINT
    assert SCREEN.font_family is None, "the screen keeps the spec's own stack"


def test_matplotlib_can_actually_resolve_the_print_stack() -> None:
    """The point of naming the clones: the first family matplotlib finds is a named face
    from the stack, not its silent fallback. Machine-dependent by nature, so it asks only
    that SOMETHING in the stack resolves wherever the suite runs."""
    wanted = [n.strip().strip('"') for n in figspec.FONT_FAMILY_PRINT.split(",")]
    first = _font_family(figspec.FONT_FAMILY_PRINT)[0]
    assert first in wanted, (
        f"matplotlib resolved none of {wanted[:-1]} and fell back to {first!r}; the printed "
        f"figures would be set in a different face from the page")


def test_the_screen_sans_names_no_system_ui(tmp_path) -> None:
    """2026-11-05: the HTML report looked broken on Korean Windows. system-ui is the
    OS's own UI face, and on Korean Windows that is Malgun Gothic, so the report's Latin
    text and every chart's came out in a Hangul face. Each OS's face is named instead,
    checked on the stack and on a rendered page."""
    assert "system-ui" not in figspec.FONT_FAMILY
    rep = CoAReporter(tmp_path, min_reads=1)
    rep.update("run_summary", {"mapping": {"total": 10, "mapped": 9, "unmapped": 1,
                                           "mapping_rate": 0.9}})
    html = Path(rep.finalize("run_end")["html_path"]).read_text()
    assert "system-ui" not in html
    assert '"Segoe UI"' in html


def test_each_os_reaches_its_own_face_first() -> None:
    """The Mac keeps San Francisco (-apple-system, what system-ui gave it there) and
    Windows gets Segoe UI, both ahead of the cross-platform names, so neither OS falls
    through to Arial."""
    stack = [f.strip().strip('"') for f in figspec.FONT_FAMILY.split(",")]
    assert stack[0] == "-apple-system"
    assert stack.index("Segoe UI") < min(stack.index("Helvetica"), stack.index("Arial"))
    assert stack[-1] == "sans-serif"
