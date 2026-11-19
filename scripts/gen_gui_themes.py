#!/usr/bin/env python
"""Write gui/src/dark_theme.json and light_theme.json from figspec, the single source of
the GUI's colours.

The GUI is a static page with no Python at render time, so it imports these JSON files
instead of figspec. test_gui_dark_theme.py and test_gui_light_theme.py regenerate them
in memory and fail on any drift, so run this after changing a GUI token, then commit
the files with it. (Named gen_gui_dark_theme.py until the light theme, 2026-11-12.)

usage: python scripts/gen_gui_themes.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mrnaspect.reporting.figspec import dark_theme, light_theme

REPO = Path(__file__).resolve().parent.parent
OUT = {"dark": REPO / "gui" / "src" / "dark_theme.json",
       "light": REPO / "gui" / "src" / "light_theme.json"}


def render(theme: str = "dark") -> str:
    """One file's exact text: sorted keys, two-space indent, trailing newline."""
    data = dark_theme() if theme == "dark" else light_theme()
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for theme, path in OUT.items():
        path.write_text(render(theme))
        logging.info("wrote %s", path)


if __name__ == "__main__":
    main()
