"""Spacing on screen is a scale, the way the type and the colours already are.

Measured 2026-10-28: the report's screen view used 14 distinct spacing values (in 19
declarations) and the GUI 19, across rem, px and em, and nothing related any of them --
"a little more room here" was a new number every time, and two surfaces built that way
cannot be made to agree by eye. They clustered on a 4 px grid, which figspec.SPACE_PX
now names.

Print geometry stays in mm and is outside this file on purpose: a 4 mm margin on A4 is a
measurement of the page, not a step on a screen scale.
"""

from __future__ import annotations

import re
from pathlib import Path

from mrnaspect.reporting import figspec
from mrnaspect.reporting.reporter import CoAReporter

_TEMPLATE = (Path(__file__).resolve().parents[1] / "reporting" / "templates"
             / "sample_coa.html").read_text()

_SPACING = re.compile(r"\b(?:margin|padding|gap|row-gap|column-gap)(?:-\w+)?\s*:\s*([^;}]+)")


def _screen_css() -> str:
    """The screen-only CSS: the @media screen block and the rules for elements only the
    screen render has (the interactive figures and their note)."""
    start = _TEMPLATE.index("@media screen {")
    depth, i = 0, _TEMPLATE.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(_TEMPLATE[i], 0)
        if depth == 0:
            break
        i += 1
    block = _TEMPLATE[start:i + 1]
    extra = re.findall(r"\.interactive-figure\s*\{[^}]*\}|\.interactive-note\s*\{[^}]*\}",
                       _TEMPLATE)
    return block + "\n".join(extra)


def test_the_scale_is_one_grid() -> None:
    steps = list(figspec.SPACE_PX.values())
    assert steps == sorted(steps) and len(set(steps)) == len(steps)
    assert all(v % 4 == 0 for v in steps[1:]), "every step past the hairline is on 4 px"


def test_every_step_is_emitted_as_a_token() -> None:
    css = str(CoAReporter._design_tokens())
    for k, v in figspec.SPACE_PX.items():
        assert f"--space-{k}: {v}px;" in css


def test_the_screen_view_names_no_spacing_of_its_own() -> None:
    literal = []
    for value in _SPACING.findall(_screen_css()):
        for part in value.split():
            if part.startswith("var(--space-") or part in ("0", "auto") or part.endswith(")"):
                continue
            if re.fullmatch(r"-?[\d.]+(?:rem|px|em)", part):
                literal.append(part)
    assert not literal, f"literal spacing on screen: {literal}; use var(--space-<step>)"


def test_every_step_the_screen_asks_for_exists() -> None:
    asked = set(re.findall(r"var\(--space-([\w-]+)\)", _TEMPLATE))
    assert asked and asked <= set(figspec.SPACE_PX), asked - set(figspec.SPACE_PX)
