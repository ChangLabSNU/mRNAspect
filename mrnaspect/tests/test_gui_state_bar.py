"""The window's channel-state bar colours each state as the report's occupancy figure does.

Until 2026-10-29 it coloured by RANK: `stateBar` sorted the states by share and gave the
i-th the i-th class of a five-step ramp whose third and fourth steps were the status amber
and red. With the activity fixture "unavailable" came out amber and "adapter" red, and in
another run the same colours would have landed on other states. Colour follows the
entity, never its rank.

Now the bands and their order are figspec's `_PORE_BANDS`, each band resolves to the
colour the report's figure fills it with, the other states fold into the same "other (N
states)" in the same grey, and no status colour is on the bar. The page has no Python at
render time, so the band list in main.ts is a copy, checked here as the shared tokens are.
The window is dark only since 2026-11-06, so a band's colour is the report's fill passed
through figspec's light→dark map: the same entity, the dark chart's colour.
No browser runs here: how it looks is judged on the Mac.
"""

from __future__ import annotations

import itertools
import re

import pytest

from mrnaspect.reporting import figspec

from .test_gui_theme import _GUI, _HTML, _declared, _parts, _resolve, _rules
from .test_palette_separation import _CVD, CVD_FLOOR, NORMAL_FLOOR, delta_e

_MAIN = _GUI / "src" / "main.ts"

pytestmark = pytest.mark.skipif(
    not (_HTML.exists() and _MAIN.exists()), reason="GUI sources are not in this checkout")

_EXTRA = ("zero", "unclassified")   # two states the report folds into "other"
_TOKENS = (*(f"state-{b}" for b in figspec._PORE_BANDS), "state-other")


def _state_bar() -> str:
    """main.ts from the band list to the end of stateBar()."""
    ts = _MAIN.read_text()
    start = ts.index("const PORE_BANDS")
    return ts[start:ts.index("\n}\n", ts.index("function stateBar(", start))]


def _ts_bands() -> list[str]:
    m = re.search(r"const PORE_BANDS = \[([^\]]*)\];", _MAIN.read_text())
    assert m, "no PORE_BANDS list in main.ts"
    return re.findall(r'"([^"]+)"', m.group(1))


def _report_fills() -> dict[str, str]:
    """{trace name: fill} from the report's own figure: every band, plus two others."""
    states = (*figspec._PORE_BANDS, *_EXTRA)
    spec = figspec.pore_activity_spec(
        {"t_minutes": [0, 1], "fractions": {s: [0.1, 0.1] for s in states}})
    return {t["name"]: t["fillcolor"] for t in spec["data"]}


# --- the report's bands, in its order --------------------------------------------------

def test_the_bands_are_the_report_s_in_its_order() -> None:
    assert _ts_bands() == list(figspec._PORE_BANDS)


def test_other_is_named_as_the_report_names_it() -> None:
    """The key reads as the report's legend: "other (N states)", N the states folded."""
    m = re.search(r"`(other \([^`]*)`", _state_bar())
    assert m, "no 'other' label in stateBar"
    named = re.sub(r"\$\{[^}]*\}", str(len(_EXTRA)), m.group(1))
    assert named in _report_fills(), (named, list(_report_fills()))


# --- each state in the colour the report gives it ---------------------------------------

def test_the_state_tokens_are_the_bands_and_the_fold() -> None:
    assert {k for k in _declared() if k.startswith("state-")} == set(_TOKENS)


def test_each_band_has_a_class_bound_to_its_own_token() -> None:
    _, css, _ = _parts()
    bound = {}
    for sel, body in _rules(css):
        if re.fullmatch(r"\.st-[a-z]+", sel):
            m = re.fullmatch(r"\s*background:\s*var\(--([a-z0-9-]+)\)\s*", body)
            assert m, (sel, body)
            bound[sel[1:]] = m.group(1)
    assert bound == {"st-" + t[len("state-"):]: t for t in _TOKENS}


def test_each_band_is_drawn_in_the_colour_the_report_gives_it() -> None:
    """Read from the report's own figure, not from CATEGORICAL by slot: what is pinned is
    the colour a STATE gets, so a reorder on either side fails here."""
    window = {b: _resolve(f"var(--state-{b})") for b in figspec._PORE_BANDS}
    window[f"other ({len(_EXTRA)} states)"] = _resolve("var(--state-other)")
    cmap = figspec.dark_colour_map()
    assert window == {k: cmap[v.lower()] for k, v in _report_fills().items()}


# --- never by rank, never a status -----------------------------------------------------

def test_the_bar_does_not_rank_the_states() -> None:
    """Sorting by share is what made a colour follow a position. The bands are drawn in
    their fixed order and a share is only a segment's width; a class comes from a band's
    name, never from an index."""
    body = _state_bar()
    assert "function stateBar(" in body, "the slice missed stateBar"
    assert ".sort(" not in body, body
    assert "`st-${k}`" in body and '"st-other"' in body, body
    assert not re.search(r'class="s\$\{', body), body


def test_the_key_names_every_segment_the_bar_draws() -> None:
    """A band is never named by colour alone: the key walks the same list as the bar and
    names each entry. It was also the relief the pore amber's 2.98:1 on the panel relied
    on, until P1 (2026-10-31); the orchid now clears 3:1 by the least, at 3.01."""
    body = _state_bar()
    bar = body[body.index('class="sbar"'):body.index('class="key"')]
    key = body[body.index('class="key"'):]
    assert "shown.map(" in bar and "shown.map(" in key, body
    assert "${esc(k)} ${pct(f)}" in key, key


# --- told apart ------------------------------------------------------------------------

def test_every_pair_on_the_bar_is_told_apart() -> None:
    """The palette's own floors, every pair, the grey fold included: on the bar "other"
    touches whichever band is drawn last, and the key sets every swatch side by side.

    The tritan gap palette A shipped with (pore vs adapter, ΔE 5.9) was closed on
    2026-11-06 by moving pore to #59a936; nothing on the bar may fall under again."""
    colours = {t: _resolve(f"var(--{t})") for t in _TOKENS}
    under = []
    for (a, ca), (b, cb) in itertools.combinations(colours.items(), 2):
        normal = delta_e(ca, cb)
        assert normal >= NORMAL_FLOOR, f"{a} {ca} / {b} {cb}: ΔE {normal:.1f}"
        for kind in _CVD:
            if delta_e(ca, cb, kind) < CVD_FLOOR:
                under.append((a, b, kind))
    assert not under, under
