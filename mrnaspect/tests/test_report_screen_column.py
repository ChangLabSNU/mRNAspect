"""The HTML report's screen column (2026-11-13, from the Mac).

On screen the report is one centred column, 60rem wide. It was set as `body > *` with
auto side margins, and every block whose own rule names a `margin` shorthand
(`.section-title`, `.section-note`, `.construct-block`) set those side margins back to 0:
"Sample & Batch Information" and its note, the QC Test Results title, all of
Per-construct Results and Provenance sat flush left while the tables beside them were
centred. The column is now the body itself, so no block's margin can take it out.
"""

from __future__ import annotations

import re
from pathlib import Path

from mrnaspect.reporting import reporter

_TEMPLATE = Path(reporter.__file__).parent / "templates" / "sample_coa.html"


def _screen_css() -> str:
    css = _TEMPLATE.read_text()
    start = css.index("@media screen {")
    depth, i = 0, css.index("{", start)
    for j in range(i, len(css)):
        depth += {"{": 1, "}": -1}.get(css[j], 0)
        if depth == 0:
            return css[i + 1:j]
    raise AssertionError("unbalanced @media screen block")


def test_the_column_is_the_body() -> None:
    screen = _screen_css()
    body = re.search(r"(?m)^\s*body\s*\{([^}]*)\}", screen).group(1)
    assert re.search(r"max-width:\s*60rem", body), body
    assert re.search(r"margin:\s*0 auto", body), body


def test_no_rule_centres_the_body_s_children_one_by_one() -> None:
    """The old way, which a block's own margin shorthand undid."""
    assert not re.search(r"body\s*>\s*\*\s*\{", _screen_css())
