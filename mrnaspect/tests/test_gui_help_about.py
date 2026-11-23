"""Help and About (2026-11-12).

- The Help button opened About. It opens a Help page of its own: which folder each run
  kind takes (the first thing a new user got wrong on a live run), what the Overview
  shows, where the results go.
- About carries the copyright (the university's, as LICENSE has it), the licence, that mRNAspect is
  not Oxford Nanopore Technologies' software, that its results carry no warranty, and
  the third-party software it ships.

The facts these pages state are checked against their sources here: the live run
folder is the one that holds Core's `pod5_subdir`, the licence line follows Core's
LICENSE (MIT since 2026-11-13), plotly's licence is the one its bundle declares.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
_HTML = _REPO / "gui" / "index.html"
_TS = _REPO / "gui" / "src" / "main.ts"
_PLOTLY = _REPO / "gui" / "public" / "plotly-cartesian-3.7.0.min.js"
_CFG = _REPO / "mrnaspect" / "config" / "run_config.yaml"

pytestmark = pytest.mark.skipif(
    not (_HTML.exists() and _TS.exists()), reason="GUI sources are not in this checkout")


def _section(page: str) -> str:
    html = _HTML.read_text()
    body = html[html.index(f'id="page-{page}"'):]
    return body[:body.index("</section>")]


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_help_opens_its_own_page_not_about() -> None:
    ts = _TS.read_text()
    assert '$("help").addEventListener("click", () => showPage("help"));' in ts
    # About is reached from Help's link and from Settings.
    assert '$("toabout").addEventListener("click", () => showPage("about"));' in ts
    assert 'id="toabout"' in _section("help")
    assert re.search(r'type Page = [^;]*"help"', ts)
    assert 'data-title="Help"' in _section("help")


def test_help_names_the_live_run_folder_as_core_reads_it() -> None:
    """Core's live driver watches <run folder>/<realtime.pod5_subdir>; Help must name the
    same level, or the page that answers "which folder" answers it wrong."""
    cfg = yaml.safe_load(_CFG.read_text())
    sub = (cfg.get("realtime") or {}).get("pod5_subdir", "pod5")
    text = _text(_section("help"))
    assert f"the one that holds {sub}/" in text
    assert "/Library/MinKNOW/data" in text


def test_about_names_the_copyright_holder() -> None:
    assert '<p id="copyright">Copyright © 2026 Seoul National University.</p>' in _section("about")


def test_core_is_mit_licensed_to_the_university() -> None:
    """MIT, copyright Seoul National University: a laboratory has no legal personality, so
    the university is named. The file is the licence's own text."""
    text = (_REPO / "LICENSE").read_text()
    assert text.startswith("MIT License\n\nCopyright (c) 2026 Seoul National University\n")
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND' in text


def test_the_licence_line_follows_core_s_licence_file() -> None:
    """About names the licence Core's LICENSE grants, and prints its text: MIT asks that
    the notice travel with every copy, and the app is a copy."""
    about = _section("about")
    licence = re.search(r'<p id="licence">([^<]*)</p>', about).group(1)
    first = (_REPO / "LICENSE").read_text().splitlines()[0]
    assert first == "MIT License"
    assert licence == "Licence: MIT. This licence covers mRNAspect; the software listed below keeps its own."
    shown = _text(about)
    body = " ".join((_REPO / "LICENSE").read_text().split()[2:])   # past "MIT License"
    assert " ".join(body.split()) in " ".join(shown.split()), "About does not print the licence text"


def test_about_says_it_is_not_ont_software_and_promises_no_accuracy() -> None:
    text = _text(_section("about"))
    assert "not affiliated with, endorsed by or supported by Oxford Nanopore Technologies" in text
    assert "without warranty of any kind" in text
    assert "not guaranteed to be accurate" in text
    assert "does not include a basecaller" in text


def test_about_states_plotly_s_licence_as_its_bundle_declares_it() -> None:
    head = _PLOTLY.read_text()[:400]
    declared = re.search(r"Licensed under the (\w+) license", head).group(1)
    version = re.search(r"plotly\.js \([^)]*\) v([\d.]+)", head).group(1)
    notices = _text(_section("about"))
    assert f"plotly.js {version}" in notices
    assert re.search(rf"plotly\.js {re.escape(version)} {declared}:", notices), notices


# --- Help's colour key (2026-11-16) ---------------------------------------------
# The app has had a colour per result type since 2026-11-12 (figspec.RESULT_COLOURS) and
# said so nowhere. Help now does, and every statement there is read back from its source:
# the palette changed three times in six weeks, and a key that names last month's colours
# is worse than none.

def _colour_rows() -> list[tuple[str, str, str, str]]:
    """Help's colour key as (swatch classes, result key or "", colour word, explanation)."""
    card = _section("help")
    card = card[card.index('id="colourkey"'):]
    rows = re.findall(
        r'<dt><i class="sw([^"]*)"(?: data-result="([a-z-]+)")?></i>([^<]+)</dt><dd>([^<]+)</dd>',
        card[:card.index("</dl>")])
    assert len(rows) == card[:card.index("</dl>")].count("<dt>"), "a row the test cannot read"
    return [(cls.strip(), key, word.strip(), text.strip()) for cls, key, word, text in rows]


def _card_titles() -> dict[str, str]:
    """panel key -> the title its Overview card carries, from the page's own code."""
    ts = _TS.read_text()
    pairs = re.findall(r'title: "([^"]+)", panel: "([a-z-]+)"', ts)
    pairs += re.findall(r'(?:tileCard|bottomCard)\("([^"]+)", "([a-z-]+)"', ts)
    titles: dict[str, str] = {}
    for title, panel in pairs:
        assert titles.setdefault(panel, title) == title, f"{panel} has two titles"
    return titles


def _hue(colour: str) -> float:
    import colorsys
    r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360


#: What each colour word may be called, as a hue range in degrees (red wraps at 0).
#: "Green", not the palette's "lime": the slot is lime on the dark panel and an olive
#: green on white, and the word has to be true of the swatch in both.
_HUES = {"Blue": (190, 240), "Violet": (240, 290), "Teal": (160, 190), "Green": (60, 160),
         "Red": (345, 375), "Amber": (30, 50)}


def _is(word: str, colour: str) -> bool:
    lo, hi = _HUES[word]
    h = _hue(colour)
    return lo <= h < hi or lo <= h + 360 < hi


def test_help_s_colour_key_names_every_result_type_under_its_own_colour() -> None:
    from mrnaspect.reporting import figspec

    titles = _card_titles()
    assert set(figspec.RESULT_COLOURS) <= set(titles), "a coloured result type with no card"
    by_colour = {figspec.RESULT_COLOURS[key]: text
                 for _cls, key, _word, text in _colour_rows() if key}
    # One row per colour of the code, no colour twice and none missing.
    assert len(by_colour) == len([r for r in _colour_rows() if r[1]])
    assert set(by_colour) == set(figspec.RESULT_COLOURS.values())
    for panel, colour in figspec.RESULT_COLOURS.items():
        assert titles[panel] in by_colour[colour], (
            f"Help's colour key does not list {titles[panel]!r} under its colour")


def test_the_colour_words_are_the_palette_s_colours_in_both_themes() -> None:
    """ "Blue" beside a swatch that a new palette has turned green would be a wrong key."""
    from mrnaspect.reporting import figspec

    dark = figspec.dark_colour_map()
    seen = set()
    for cls, key, word, _text_ in _colour_rows():
        if key:
            light = figspec.RESULT_COLOURS[key]
            pair = (light, dark[light.lower()])
        elif cls == "serious":
            pair = (figspec.STATUS_SERIOUS, figspec.DARK["status-serious"])
        elif cls == "caution":
            pair = (figspec.STATUS_WARNING, figspec.DARK["status-warning"])
        else:
            raise AssertionError(f"a swatch with no colour of its own: {word}")
        for colour in pair:
            assert _is(word, colour), f"{word} is not what {colour} looks like"
        seen.add(word)
    assert seen == {"Blue", "Violet", "Teal", "Green", "Red", "Amber"}


def test_the_colour_key_s_swatches_are_drawn_from_the_theme_s_tokens() -> None:
    """A swatch with a colour written into it would stay behind when the palette moves,
    and would be wrong in one of the two themes from the start."""
    html = _HTML.read_text()
    card = _section("help")
    card = card[card.index('id="colourkey"'):card.index("Where the results go")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|style=", card)
    assert re.search(r"\.sw \{[^}]*background:var\(--mark\)", html)
    assert ".sw.serious { background:var(--status-serious) }" in html
    assert ".sw.caution { background:var(--status-warning) }" in html
    for _cls, key, _word, _text_ in _colour_rows():
        if key:
            assert f'[data-result="{key}"] {{ --mark: var(--result-{key}) }}' in html


def test_what_the_colour_key_says_beside_the_rows_is_what_the_code_does() -> None:
    from mrnaspect.reporting import figrender, figspec

    html = _HTML.read_text()
    text = _text(_section("help"))
    rows = {word: body for _cls, _key, word, body in _colour_rows()}

    # "the same four colours name the parts": the channel states and the error types are
    # drawn in the series slots, which are the result colours' own.
    assert "the channel states in Sequencing information" in text
    for state, slot in (("strand", 1), ("pore", 2), ("adapter", 3), ("unavailable", 4)):
        assert f"--state-{state}: var(--series-{slot});" in html
    assert "the error types in Per-base identity" in text
    parts = [c for _k, _n, c in figspec.MISMATCH_PARTS] + [figspec.INSERTION[2]]
    assert set(parts) <= set(figspec.CATEGORICAL)
    assert set(figspec.RESULT_COLOURS.values()) <= set(figspec.CATEGORICAL)

    # "the corrected length is a second violet": its own colour, violet in both themes.
    assert "the corrected length is a second violet" in rows["Violet"]
    assert figspec.POLYA_CORRECTED != figspec.RESULT_COLOURS["polya"]
    for colour in (figspec.POLYA_CORRECTED, figspec.POLYA_CORRECTED_DARK):
        assert _is("Violet", colour)

    # "and the mapped reads": the Reads card's key for mapped is the composition colour.
    assert "the mapped reads" in rows["Green"]
    assert ".key-m { background:var(--result-constructs) }" in html

    # "a flagged position on a figure" is red on screen in both themes, and it is the one
    # colour the PDF keeps: every series slot prints as a grey.
    assert "a flagged position on a figure" in rows["Red"]
    assert _is("Red", figspec.EMPHASIS) and _is("Red", figspec.DARK["emphasis"])
    assert "The PDF report prints in greys and patterns; only a flagged position keeps its red." in text
    printed = figrender.GRAYSCALE.colors

    def grey(colour: str) -> bool:
        return len({colour[1:3], colour[3:5], colour[5:7]}) == 1

    for slot in list(figspec.CATEGORICAL) + [figspec.POLYA_CORRECTED]:
        assert grey(printed[slot.lower()]), f"{slot} prints in colour"
    assert _is("Red", printed[figspec.EMPHASIS.lower()])
    assert set(figrender.GRAYSCALE.hatches) >= {c.lower() for c in figspec.CATEGORICAL}

    # "⚠ uncorrected beside a poly(A) tail" is amber, and the page writes it so.
    assert "⚠ uncorrected" in rows["Amber"]
    assert ".uncorr {" in html and "color:var(--status-warning) }" in html
    assert "⚠ uncorrected</span>" in _TS.read_text()

    # A stopped or failed run is red, with its word beside it.
    assert "a run that stopped or failed" in rows["Red"]
    assert ".pill.bad { color:var(--status-serious) }" in html
    ts = _TS.read_text()
    assert '["Stopped", "bad"]' in ts and '["Failed", "bad"]' in ts
