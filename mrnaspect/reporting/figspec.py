"""Figure specs, and the design tokens every surface shares.

Each builder returns a plotly figure as a plain dict (``data``, ``layout``, ``config``);
Core never imports plotly. The GUI and the report HTML draw a spec with the vendored
plotly.js (``PLOTLY_BUNDLE``), and ``figrender`` draws the same spec with matplotlib for
the PDF, which runs no JavaScript. The fonts, colours, and type and spacing scales here
are the ones the report and the GUI use too.

Nothing here flags hotspots: with no calibrated expected error to exceed, the
per-position profile's only reference line is the construct's own median.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

#: The screen font stack: the GUI, the report's screen view and every chart. Each OS's
#: face is named, not system-ui: on Korean Windows that is Malgun Gothic, which set the
#: report's Latin text in a Hangul face (2026-11-05). Mac: -apple-system for WebKit and
#: Firefox, BlinkMacSystemFont for Chrome. Windows: Segoe UI. Linux: Noto Sans.
FONT_FAMILY = ('-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", '
               'Helvetica, Arial, sans-serif')

#: The print font stack: the PDF page and figrender's print theme. Neither WeasyPrint
#: nor matplotlib has a system UI face, and matplotlib matches names exactly, so
#: Helvetica's metric clones are named to give both renderers the same face.
FONT_FAMILY_PRINT = '"Helvetica", "Arial", "TeX Gyre Heros", "Liberation Sans", sans-serif'

#: The monospace stack, for every surface and medium.
FONT_MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

#: Categorical series colours, assigned by position and never cycled, so a series keeps
#: its colour however many others are shown.
#:
#: Validated as a set, every pair rather than only neighbours (stacked charts put
#: non-adjacent slots side by side): ΔE >= 15 in normal vision, >= 8 under
#: colour-vision-deficiency simulation, and >= 3:1 contrast on the figure white and on
#: the GUI panel. No slot is a status colour or within ΔE 15 of one (status fills, status
#: text, EMPHASIS).
#:
#: 2026-11-13: the set is "05 Lagoon" from the QC-metric palette sheet
#: (length sky blue, composition lime, poly(A) violet, accuracy teal), fine-tuned rather
#: than copied, as asked: as drawn, its olive sat ΔE 12.3 from its teal. Each
#: colour moved the least that clears every gate above with a margin (light: at most
#: ΔE 4.4, the violet not at all; test_palette_lagoon.py holds them to the sheet). The
#: CVD gate is the validator's, which clamps a simulated colour to the screen's range
#: (test_palette_separation.delta_e); unclamped, a saturated pair read as apart.
CATEGORICAL: tuple[str, ...] = ("#158ae0", "#779302", "#7c3aed", "#04837b")

#: The colour of each result type, by Core's panel key (2026-11-12: "a colour code
#: per result type"; option A, 2026-11-12). Six types share four families, all
#: CATEGORICAL slots, because no sixth or even fifth colour clears the gates beside the
#: four on a dark ground (best fifth: ΔE 11.8 normal at CVD 7.6). So every pair keeps the
#: set's own validation. Families: length (read length, full length) slot 1; poly(A)
#: slot 3; accuracy (read quality, per-base identity) slot 4; composition (construct
#: share) slot 2. A figure's PRIMARY series takes its panel's colour; series that are
#: categories (error types, channel states) keep the categorical order, and the flow cell
#: has no entry. Print is unchanged: the reporter tags these traces `meta.result`, and
#: figrender's print theme draws a tagged trace in the grey and texture slot 1 always had.
RESULT_COLOURS: dict[str, str] = {
    "read-length": CATEGORICAL[0],
    "full-length": CATEGORICAL[0],
    "polya": CATEGORICAL[2],
    "quality": CATEGORICAL[3],
    "identity": CATEGORICAL[3],
    "constructs": CATEGORICAL[1],
}

#: The second poly(A) series, corrected beside uncorrected: the poly(A) family's other
#: lightness step, so the panel stays one family and slot 2's lime keeps one meaning
#: (composition). Lagoon's violet, a step darker on white (ΔE 15.7 normal / 14.9 CVD,
#: 9.96:1 on white) and a step lighter on the dark panel (#cfc8fd: 19.7 / 17.5), each
#: ΔE >= 15 from the other three series and from every status (2026-11-13).
POLYA_CORRECTED = "#5203a7"
POLYA_CORRECTED_DARK = "#cfc8fd"


def tag_result(spec: dict[str, Any], colour: str) -> dict[str, Any]:
    """Mark every trace of ``spec`` drawn in the result colour ``colour``.

    The tag (``meta.result``) is what lets print keep one grey for a figure's primary
    series whichever result colour it wears on screen. A trace is drawn in ``colour``
    when its line, marker, marker outline or fill names it (a fill by its rgb).
    """
    want = colour.lower()

    def names(trace: Mapping[str, Any]) -> set[str]:
        marker = trace.get("marker") or {}
        found = {(trace.get("line") or {}).get("color"), marker.get("color"),
                 (marker.get("line") or {}).get("color"), trace.get("fillcolor")}
        out = set()
        for c in found:
            if isinstance(c, str):
                m = re.fullmatch(r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*[\d.]+\s*\)", c.strip())
                out.add("#%02x%02x%02x" % tuple(int(v) for v in m.groups()) if m
                        else c.strip().lower())
        return out

    for trace in spec.get("data") or []:
        if want in names(trace):
            meta = trace.get("meta")
            trace["meta"] = {**(meta if isinstance(meta, Mapping) else {}), "result": want}
    return spec

#: The emphasis colour, for the marks a figure points at (positions and calls to act
#: on). Reserved, never a series; ΔE >= 15 from every series. figrender's print theme
#: maps it to #c62828, the only chromatic colour on the printed page. Never the only
#: cue: an emphasis mark also has its own symbol and legend entry.
EMPHASIS = "#7f1d1d"

#: How a distribution is filled on every surface: a line in the series colour over the
#: same colour at this alpha. Stated in the spec so plotly and figrender agree; the GUI
#: copies it as a token. Bars follow the same rule (`_bar_marker`), except the
#: per-position profile's, which are too thin to fill and stay solid.
FILL_ALPHA = 0.25
#: The series line width, in plotly px. The GUI's histogram outline and sparkline match.
SERIES_LINE_PX = 1.5


def fill_of(colour: str) -> str:
    """``colour`` (#rgb or #rrggbb) at FILL_ALPHA, as plotly's ``fillcolor`` takes it."""
    h = colour[1:] if colour.startswith("#") else ""
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"fill_of takes #rgb or #rrggbb, not {colour!r}")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {FILL_ALPHA:g})"


def _bar_marker(colour: str | Sequence[str]) -> dict[str, Any]:
    """A bar's marker by the fill rule: its colour at FILL_ALPHA, outlined in the colour."""
    if isinstance(colour, str):
        return {"color": fill_of(colour),
                "line": {"color": colour, "width": SERIES_LINE_PX}}
    return {"color": [fill_of(c) for c in colour],
            "line": {"color": list(colour), "width": SERIES_LINE_PX}}

#: The type scale in points, shared by the page and the charts, named by role. A panel
#: is drawn at about the size it is placed at, so a point inside a figure is a point
#: beside it.
TYPE_SCALE_PT: dict[str, float] = {
    "micro": 8.0,     # panel labels, the footer, chart tick labels
    "small": 9.0,     # captions, notes, tables, the signoff, chart axis titles
    "body": 10.0,
    "heading": 11.0,  # a construct's own title
    "section": 12.0,
    "title": 16.0,
}

#: plotly sizes text in CSS px; figrender divides by this to get points.
SPEC_UNITS_PER_PT = 1.5

#: The GUI's own type ladder, in CSS px. Until 2026-11-12 the GUI took the report's
#: screen ladder (TYPE_SCALE_PT); larger text on screen was then asked for, and the
#: run view had grown nine sizes, some half a pixel apart (11, 12, 13, 13.5, 15, 16,
#: 16.5, 18, 22 px), which read as one size drawn twice. So the GUI has five steps,
#: each at least 13% above the one below; the report keeps TYPE_SCALE_PT. The GUI's
#: :root carries a checked copy (test_gui_design_tokens.py), and its charts read their
#: sizes from that :root, so a chart's text sits on the same steps as the page's.
GUI_TYPE_PX: dict[str, int] = {
    "micro": 13,    # axis ticks, construct names, footnotes, the log
    "small": 15,    # secondary lines, tables, tabs, hints, chart legends
    "body": 17,     # body text, fields, a chart's construct line
    "heading": 20,  # card and chart titles, the run name, the page title in the top bar
    "title": 24,    # headline values and page headings (26 until 2026-11-14: a little large)
}


#: The spacing scale for the screen surfaces (the GUI and the report's screen view), in
#: CSS px on a 4 px grid, named by step. Print CSS uses mm.
SPACE_PX: dict[str, int] = {
    "2xs": 2, "xs": 4, "s": 8, "m": 12, "l": 16, "xl": 24, "2xl": 32, "3xl": 48,
}


def type_size(step: str) -> int:
    """A step of the shared scale, in the units a plotly layout wants."""
    return round(TYPE_SCALE_PT[step] * SPEC_UNITS_PER_PT)


#: Status colours: ``STATUS_*`` for rules and status text, ``*_INK`` for text on a
#: banner's ``*_SURFACE`` tint. Every text pairing clears 4.5:1. Reserved: never a
#: series colour.
STATUS_WARNING = "#8a5a00"
STATUS_WARNING_INK = "#7a4e00"
STATUS_WARNING_SURFACE = "#fdf4e3"
STATUS_SERIOUS = "#a3231c"
STATUS_SERIOUS_INK = "#7a1c17"
STATUS_SERIOUS_SURFACE = "#fdecea"
#: The same statuses as fills, for a mark that is itself a status (never text), such as
#: the GUI sparkline's partial bins.
STATUS_WARNING_FILL = "#d8a639"
STATUS_SERIOUS_FILL = "#c0504d"
#: A pass. Text only: a met criterion needs no banner.
STATUS_GOOD = "#006300"

#: Chart chrome. Never a series colour: the mark beside a label carries identity.
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
AXIS_LINE = "#c3c2b7"

#: Screen surfaces, shared by the GUI and the report's screen view: white paper, and a
#: panel and a sunken well in the ink's warm family. The PDF uses only the panel, behind
#: a construct heading.
SURFACE = "#ffffff"
SURFACE_PANEL = "#f6f5f2"
SURFACE_SUNKEN = "#f9f9f7"

#: Muted grey for the second line of a title and for reference lines.
_MUTED = INK_MUTED

#: Sequential ramp for the heatmap: one hue, light to dark, so it reads as magnitude
#: rather than as categories.
SEQUENTIAL_BLUE = [
    [0.0, "#eef4fd"], [0.25, "#9ec5f4"], [0.5, "#5598e7"],
    [0.75, "#2a78d6"], [1.0, "#104281"],
]


# --- The GUI's dark theme -----------------------------------------------------------
#
# The GUI is dark only (2026-11-05; decided 2026-11-06); the HTML report and the PDF
# stay white paper and never read anything below. Palette "A Graphite"
# (2026-11-06): a cool neutral ground, P1's hues re-stepped for a dark panel. The GUI
# cannot import this module, so `dark_theme()` is written to gui/src/dark_theme.json
# (scripts/gen_gui_themes.py) and test_gui_dark_theme.py fails on any drift.

#: The dark series, slot for slot with CATEGORICAL, held to the same gates
#: (test_gui_dark_theme.py): every pair ΔE >= 15 in normal vision and >= 8 under all
#: three CVD simulations, >= 3:1 on every dark surface, ΔE >= 15 from the statuses.
#: 2026-11-13: the dark half of "05 Lagoon", fine-tuned. As drawn its teal sat
#: ΔE 12.1 from its sky blue and 6.4 from the check-mark green, and its violet 5.2 from
#: the sky blue for a deuteranope; the least move that clears every gate is at most
#: ΔE 6.2 (the lime; the sky blue not at all), with the check-mark green moved too
#: (status-good, below).
CATEGORICAL_DARK: tuple[str, ...] = ("#38bdf8", "#a5e639", "#9c83fe", "#01e8c7")

#: Dark tokens by the GUI's CSS custom-property name (without the leading ``--``).
#: Surfaces step menu < page < panel < raised, as MinKNOW's do, in our own ink.
DARK: dict[str, str] = {
    "surface-menu": "#0e1115",
    "surface-page": "#13171c",
    "surface-panel": "#1b2027",
    "surface-raised": "#242a32",
    "rule": "#2c333c",
    # The "other" channel-state band and a strong divider: recessive, not text. Dark
    # enough to clear the CVD floor against every series (protan ΔE 10.5 against the
    # rose; #5b6571 was 4.7). Like the light axis grey it is under 3:1 on the panel,
    # and the key names the band.
    "rule-strong": "#4a525c",
    # Chart gridlines (2026-11-12: too few, and hard to see). `rule` was 1.28:1 on
    # the panel; this is 1.83:1, still under `rule-strong` (2.07:1), so a gridline
    # stays behind the axis and far under the 3:1 a data mark needs.
    "grid": "#424a55",
    "ink-primary": "#e8ebee",
    "ink-secondary": "#aab3bd",
    "ink-muted": "#8a949f",
    # Accent as text and links; the series blue carries white at only 4.23:1, so a
    # filled button takes its own darker blue (white on it 4.77:1).
    "accent": "#80b3fd",
    "accent-fill": "#1a6fe1",
    # Serious was #fd7562 until a red that stands out was asked for (2026-11-06).
    "status-serious": "#ff5337",
    "status-warning": "#f3b94c",
    # The input screen's "✓ N POD5 files" is its one use. #43dd9a until 2026-11-13: it
    # sat between Lagoon's teal and lime; this green is >= 19.0 from every series and
    # >= 4.90:1 as text on every dark surface.
    "status-good": "#32ab5d",
    # Banner tints; the status colour is the text on them.
    "status-serious-surface": "#3a1c1a",
    "status-warning-surface": "#33290f",
    # Reserved for the marks a figure points at; ΔE >= 15 from every dark series.
    "emphasis": "#ff7a70",
}

#: The heatmap ramp for a dark ground: magnitude is brightness, so the low end sits
#: near the panel and the high end is the lightest. One hue, lightness monotonic.
SEQUENTIAL_BLUE_DARK = [
    [0.0, "#1a2b42"], [0.25, "#1c4b8c"], [0.5, "#2f74d6"],
    [0.75, "#6ea4f2"], [1.0, "#c4dcff"],
]


def dark_colour_map() -> dict[str, str]:
    """Every colour a spec can carry, light (lower-case hex) → its dark counterpart.

    An rgba() fill is mapped by its hex and keeps its alpha (the GUI does that), so
    only hexes are keys here. "white" is the paper and the ring around an emphasis
    mark; on a dark chart both become the panel.
    """
    out: dict[str, str] = {}
    out.update(zip(CATEGORICAL, CATEGORICAL_DARK))
    out.update({light: dark for (_, light), (_, dark)
                in zip(SEQUENTIAL_BLUE, SEQUENTIAL_BLUE_DARK)})
    out.update({
        POLYA_CORRECTED: POLYA_CORRECTED_DARK,
        EMPHASIS: DARK["emphasis"],
        INK_PRIMARY: DARK["ink-primary"],
        INK_SECONDARY: DARK["ink-secondary"],
        INK_MUTED: DARK["ink-muted"],
        GRIDLINE: DARK["grid"],
        AXIS_LINE: DARK["rule-strong"],
        STATUS_WARNING: DARK["status-warning"],
        STATUS_WARNING_INK: DARK["status-warning"],
        STATUS_WARNING_FILL: DARK["status-warning"],
        STATUS_WARNING_SURFACE: DARK["status-warning-surface"],
        STATUS_SERIOUS: DARK["status-serious"],
        STATUS_SERIOUS_INK: DARK["status-serious"],
        STATUS_SERIOUS_FILL: DARK["status-serious"],
        STATUS_SERIOUS_SURFACE: DARK["status-serious-surface"],
        STATUS_GOOD: DARK["status-good"],
        SURFACE: DARK["surface-panel"],
        SURFACE_PANEL: DARK["surface-raised"],
        SURFACE_SUNKEN: DARK["surface-page"],
        "white": DARK["surface-panel"],
    })
    return {k.lower(): v for k, v in out.items()}


def dark_layout() -> dict[str, Any]:
    """Layout keys the GUI lays over every spec's own: dark chrome, the spec's grid.

    Merged per axis, so a spec's title, type, range and grid survive. Backgrounds are
    transparent so the chart sits straight on its panel, as MinKNOW's do. The grid was
    switched off here, MinKNOW's way, until the charts proved too hard to read
    (2026-11-12): each axis now keeps the spec's own grid, in the dark grid colour.
    """
    axis = {"gridcolor": DARK["grid"], "zeroline": False, "linecolor": DARK["ink-muted"],
            "tickcolor": DARK["ink-muted"],
            "tickfont": {"color": DARK["ink-secondary"]},
            "title": {"font": {"color": DARK["ink-secondary"]}}}
    return {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"color": DARK["ink-secondary"]},
        "title": {"font": {"color": DARK["ink-primary"]}},
        "legend": {"font": {"color": DARK["ink-secondary"]}},
        "hoverlabel": {"bgcolor": DARK["surface-raised"], "bordercolor": DARK["rule"],
                       "font": {"color": DARK["ink-primary"]}},
        "axis": axis,
        "rangeslider": {"bgcolor": DARK["surface-page"], "bordercolor": DARK["rule"]},
    }


def dark_theme() -> dict[str, Any]:
    """What gui/src/dark_theme.json holds: tokens, series, colour map, layout."""
    return {
        "palette": "Lagoon (fine-tuned)",
        "tokens": dict(DARK),
        "series": list(CATEGORICAL_DARK),
        "sequential": [list(step) for step in SEQUENTIAL_BLUE_DARK],
        "colors": dark_colour_map(),
        "layout": dark_layout(),
    }


#: The GUI's light tokens (2026-11-12: a light mode for manuscript figures and
#: poster screenshots; a switch in Settings, dark by default). Same names and the same
#: gates as DARK, on the report's paper: panels are its white, the page its panel grey,
#: and the series are CATEGORICAL itself, so a light chart IS the report's chart. Where
#: the report's own value fails a GUI gate it is darkened here, and only here: the
#: muted ink (#898781, 2.98:1 on the lightest grey, would be text) and the accent
#: (the report's former blue #396cd8, 4.04:1) clear 4.5:1 on every light ground.
LIGHT: dict[str, str] = {
    # The page is the cool grey the palette sheet was drawn on (2026-11-13); the menu and the raised surface take its tone at their own lightness
    # (were the report's warm greys #f1f0ec, #f6f5f2, #ebeae4).
    "surface-menu": "#eef0f3",
    "surface-page": "#f3f5f8",
    "surface-panel": SURFACE,
    "surface-raised": "#e8eaed",
    "rule": GRIDLINE,
    "rule-strong": AXIS_LINE,
    # 1.58:1 on the white panel; the report's GRIDLINE is 1.32:1 there, the grid that
    # was too faint to see (2026-11-12).
    "grid": "#cfcec6",
    "ink-primary": INK_PRIMARY,
    "ink-secondary": INK_SECONDARY,
    "ink-muted": "#67655f",
    "accent": "#2f5fc4",
    "accent-fill": "#2f5fc4",
    "status-serious": STATUS_SERIOUS,
    "status-warning": STATUS_WARNING,
    "status-good": STATUS_GOOD,
    "status-serious-surface": STATUS_SERIOUS_SURFACE,
    "status-warning-surface": STATUS_WARNING_SURFACE,
    "emphasis": EMPHASIS,
}


def light_colour_map() -> dict[str, str]:
    """The light charts' swaps: the specs are light already, so only the grid moves, to
    the light grid colour."""
    return {GRIDLINE.lower(): LIGHT["grid"]}


def light_layout() -> dict[str, Any]:
    """Layout keys the GUI lays over every spec on the light theme: dark_layout's roles in
    LIGHT's colours, so the two themes differ in colour only."""
    axis = {"gridcolor": LIGHT["grid"], "zeroline": False, "linecolor": LIGHT["ink-muted"],
            "tickcolor": LIGHT["ink-muted"],
            "tickfont": {"color": LIGHT["ink-secondary"]},
            "title": {"font": {"color": LIGHT["ink-secondary"]}}}
    return {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"color": LIGHT["ink-secondary"]},
        "title": {"font": {"color": LIGHT["ink-primary"]}},
        "legend": {"font": {"color": LIGHT["ink-secondary"]}},
        "hoverlabel": {"bgcolor": LIGHT["surface-panel"], "bordercolor": LIGHT["rule-strong"],
                       "font": {"color": LIGHT["ink-primary"]}},
        "axis": axis,
        "rangeslider": {"bgcolor": LIGHT["surface-page"], "bordercolor": LIGHT["rule"]},
    }


def light_theme() -> dict[str, Any]:
    """What gui/src/light_theme.json holds, in dark_theme()'s shape."""
    return {
        "palette": "Lagoon (fine-tuned)",
        "tokens": dict(LIGHT),
        "series": list(CATEGORICAL),
        "sequential": [list(step) for step in SEQUENTIAL_BLUE],
        "colors": light_colour_map(),
        "layout": light_layout(),
    }


def _title(name: str, detail: str = "") -> dict[str, Any]:
    """A short name, with the measurement detail as a smaller second line.

    The detail sits inside the figure because it changes with the construct selector.
    An empty name is normal for a figure without a selector: its caption names it.
    """
    size = TYPE_SCALE_PT["micro"]
    small = f'<span style="font-size:{size:g}pt;color:{_MUTED}">{detail}</span>'
    if not name:
        return {"text": small if detail else "", "font": {"size": type_size("micro")}}
    if not detail:
        return {"text": name, "font": {"size": type_size("body")}}
    return {"text": f"{name}<br>{small}", "font": {"size": type_size("body")}}


def _layout(**over: Any) -> dict[str, Any]:
    """Shared layout: the font, and margins that leave room for a two-line title."""
    base: dict[str, Any] = {
        "font": {"family": FONT_FAMILY, "size": type_size("micro"),
                 "color": INK_SECONDARY},
        "paper_bgcolor": "white",
        "plot_bgcolor": "white",
        # Recessive chrome, so the data carries the weight.
        "xaxis": {"gridcolor": GRIDLINE, "linecolor": AXIS_LINE, "zerolinecolor": GRIDLINE,
                  "ticks": "outside", "tickcolor": AXIS_LINE, "ticklen": 4},
        "yaxis": {"gridcolor": GRIDLINE, "linecolor": AXIS_LINE, "zerolinecolor": GRIDLINE,
                  "ticks": "outside", "tickcolor": AXIS_LINE, "ticklen": 4},
        # Right of the plot, top-aligned, where the PDF draws it (figrender): under the
        # plot it sat on the range slider (2026-11-05). The GUI lays its own over it.
        "legend": {"orientation": "v", "x": 1.02, "xanchor": "left",
                   "y": 1, "yanchor": "top",
                   "font": {"size": type_size("micro"),
                            "color": INK_SECONDARY}},
        "margin": {"t": 70, "r": 20, "b": 50, "l": 60},
        "hoverlabel": {"font": {"family": FONT_FAMILY, "size": type_size("micro")}},
    }
    # Merge axis dicts, so a caller's title or type keeps the chrome above. A second y
    # axis (a panel under the first, "yaxis2") takes the same chrome as the first.
    for key in list(over):
        if key.startswith(("xaxis", "yaxis")):
            chrome = base["xaxis" if key.startswith("xaxis") else "yaxis"]
            over = {**over, key: {**chrome, **over[key]}}
    base.update(over)
    return base


#: A log axis with one tick per decade, written out in full (plotly's default labels
#: use SI prefixes such as "100µ").
_LOG_AXIS = {"type": "log", "dtick": 1, "exponentformat": "none", "showexponent": "none"}


#: The plotly bundle the report and the GUI both load, pinned by filename. The cartesian
#: build, a third the size of the full one, because every report carries its own copy.
#: It has no WebGL, so a ``scattergl`` trace would render nothing, silently: keep traces
#: to ``SUPPORTED_TRACE_TYPES``.
PLOTLY_BUNDLE = "plotly-cartesian-3.7.0.min.js"

#: sha256 of that file as vendored, so a swapped or truncated copy is visible.
PLOTLY_BUNDLE_SHA256 = (
    "7c593b9eda0e74a1d07335cf89cbf7a55ffc114909980c3729af835453bdb02a")

#: The licence texts of plotly.js and of every package inside that bundle, beside it.
#: Written by scripts/vendor_plotly_notices.py; whatever carries the bundle carries this.
PLOTLY_NOTICES = "plotly-cartesian-3.7.0.LICENSES.txt"

#: Trace types the pinned bundle can actually draw. Anything else is a blank panel.
SUPPORTED_TRACE_TYPES = frozenset({"scatter", "bar", "heatmap"})

# =============================================================================
# Per-position: where the reads leave the reference, and how
#
# One figure, a construct per dropdown option. The upper panel stacks the substitution
# and deletion rates; their sum is the mismatch rate, the share of covering reads that do
# not carry the reference base, which is what the consensus module flags on. So the
# dashed line at its threshold separates the flagged bars from the rest, and a marker
# stands on each flagged bar. Insertions have no reference position and take no share of
# the reads: stacked on the parts they would push a bar past a threshold the reads did
# not cross, so they get a lower panel on their own scale.
# =============================================================================

#: The parts of the mismatch rate, ``(error_hotspot key, name, colour)``, bottom first.
MISMATCH_PARTS: tuple[tuple[str, str, str], ...] = (
    ("substitution_rate", "substitution", CATEGORICAL[0]),
    ("deletion_rate", "deletion", CATEGORICAL[1]),
)

#: Insertions, ``(error_hotspot key, name, colour)``, drawn in the lower panel.
INSERTION: tuple[str, str, str] = ("insertion_rate", "insertion", CATEGORICAL[2])

#: The outcomes a position's reads can show, in the order a flagged position's hover
#: lists them.
_OUTCOMES = ("A", "C", "G", "T", "del")


def _axis_length(snapshot: Mapping[str, Any]) -> int:
    """The length every per-position array shares, read from the arrays themselves.

    Not ``covered_positions``: that counts measured positions only.
    """
    keys = [key for key, _, _ in (*MISMATCH_PARTS, INSERTION)]
    lengths = {len(snapshot[key]) for key in keys if key in snapshot}
    if not lengths:
        raise KeyError(
            f"error_hotspot snapshot carries none of {keys}; cannot build a per-position "
            f"figure")
    if len(lengths) > 1:
        # Zipping to the shortest would misalign the components.
        raise ValueError(
            f"per-position arrays disagree in length: {sorted(lengths)}. They index the "
            f"same reference, so one of them is not what it claims to be.")
    return lengths.pop()


def _rate_bars(xs: list[int], ys: Optional[Sequence[Optional[float]]], name: str,
               colour: str) -> dict[str, Any]:
    """One bar per position. None (not measured) stays None: a gap, not a zero."""
    return {
        "type": "bar", "name": name, "x": xs,
        "y": list(ys) if ys is not None else [None] * len(xs),
        # Solid, no outline: the exception to FILL_ALPHA, as a bar a pixel wide is all
        # edge.
        "marker": {"color": colour, "line": {"width": 0}},
        "hovertemplate": f"{name}: %{{y:.4f}}<extra></extra>",
        "visible": False,
    }


def _site_customdata(site: Mapping[str, Any]) -> list[Any]:
    """``[reference base, reads carrying it, depth, what the reads carry]`` for one
    flagged position. The last lists every outcome seen, leaving out zero counts."""
    counts = site.get("base_counts") or {}
    carried = " · ".join(f"{k} {counts[k]}" for k in _OUTCOMES if counts.get(k))
    return [site.get("ref_base"), counts.get(site.get("ref_base"), 0), site.get("depth"),
            carried]


def _per_position_detail(cons: Mapping[str, Any]) -> str:
    """The subtitle: the construct's identity and what the rest of its reads are, which
    add to 100, then how many positions are flagged and at what."""
    parts = []
    ident = cons.get("consensus_identity_pct")
    if ident is not None:
        parts.append(f"per-base identity {ident:.1f}%")
    sub, dele = cons.get("substitution_pct"), cons.get("deletion_pct")
    if sub is not None and dele is not None:
        parts += [f"substitution {sub:.1f}%", f"deletion {dele:.1f}%"]
    ins = cons.get("insertions_per_100_bases")
    if ins is not None:
        parts.append(f"insertions {ins:.1f} per 100 bases")
    if "mismatch_sites" in cons:
        n = len(cons.get("mismatch_sites") or [])
        flagged = f"{n} {'position' if n == 1 else 'positions'} flagged"
        thr = cons.get("mismatch_threshold")
        if thr is not None:
            flagged += f" at mismatch \u2265 {thr:g}"
        parts.append(flagged)
    return " · ".join(parts)


def per_position_spec(
    tracks: Sequence[tuple[str, Mapping[str, Any], Optional[Mapping[str, Any]]]],
) -> dict[str, Any]:
    """Per-position disagreement with the reference, by kind, with the flags marked.

    The bars are error_hotspot's per-position rates; the flags, their threshold and the
    subtitle are the consensus module's. Both gate positions at depth 50 by default, so
    every flag stands on a bar; a construct with no consensus snapshot draws its bars
    alone. The axes are linear because stacking needs it: on a log axis the segments
    would not add up to the bar.

    Args:
        tracks: ``(label, error_hotspot_snapshot, consensus_snapshot or None)``, in the
            order they should be offered.

    Raises:
        ValueError: no tracks, or a snapshot whose per-position arrays disagree.
        KeyError: an error_hotspot snapshot with no per-position arrays.
    """
    if not tracks:
        raise ValueError("per_position_spec needs at least one construct track")

    data: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    thresholds: list[Optional[float]] = []
    details: list[str] = []
    for _label, err, cons in tracks:
        cons = cons or {}
        start = len(data)
        xs = list(range(1, _axis_length(err) + 1))  # 1-based for display; arrays 0-based
        for key, name, colour in MISMATCH_PARTS:
            data.append(_rate_bars(xs, err.get(key), name, colour))
        sites = cons.get("mismatch_sites") or []
        data.append({
            "type": "scatter", "mode": "markers",
            "name": "flagged",
            # Ignored by plotly; figrender's print theme draws these marks red.
            "meta": {"emphasis": True},
            # pos is 0-based and the bars 1-based, so +1 stands each marker on its own
            # bar rather than the one before it.
            "x": [s.get("pos") + 1 for s in sites],
            "y": [s.get("mismatch_rate") for s in sites],
            "marker": {"size": 7, "color": EMPHASIS, "symbol": "circle",
                       # A white ring keeps adjacent flags countable.
                       "line": {"width": 1, "color": "white"}},
            "customdata": [_site_customdata(s) for s in sites],
            "hovertemplate": (
                "position %{x}<br>mismatch %{y:.3f}: reference %{customdata[0]} in "
                "%{customdata[1]} of %{customdata[2]} reads<br>%{customdata[3]}"
                "<extra>flagged</extra>"),
            "visible": False,
        })
        key, name, colour = INSERTION
        data.append({**_rate_bars(xs, err.get(key), name, colour), "yaxis": "y2"})
        spans.append((start, len(data)))
        thresholds.append(cons.get("mismatch_threshold"))
        details.append(_per_position_detail(cons))

    for i in range(*spans[0]):
        data[i]["visible"] = True

    def threshold_shape(value: Optional[float]) -> list[dict[str, Any]]:
        if value is None:
            return []
        # The consensus module's own flag threshold, from the snapshot.
        return [{"type": "line", "xref": "paper", "yref": "y", "x0": 0, "x1": 1,
                 "y0": value, "y1": value,
                 "line": {"color": INK_MUTED, "width": 1, "dash": "dash"}}]

    buttons = []
    for idx, (label, _err, _cons) in enumerate(tracks):
        lo_i, hi_i = spans[idx]
        visible = [False] * len(data)
        for i in range(lo_i, hi_i):
            visible[i] = True
        buttons.append({
            "label": label, "method": "update",
            "args": [{"visible": visible},
                     {"title": _title(label, details[idx]),
                      "shapes": threshold_shape(thresholds[idx])}],
        })

    return {
        "data": data,
        "layout": _layout(
            title=_title(tracks[0][0], details[0]),
            barmode="stack",
            bargap=0,
            # One x, under the lower panel. The slider redraws every bar in miniature.
            xaxis={"title": {"text": "reference position (1-based)"}, "anchor": "y2",
                   "rangeslider": {"visible": True, "thickness": 0.06}},
            # Short titles: the lower panel is too low for a long one, which would run
            # into the upper panel's. The caption gives the units in full.
            yaxis={"title": {"text": "fraction of reads"}, "range": [0, 1],
                   "domain": [0.3, 1.0]},
            yaxis2={"title": {"text": "insertions"}, "domain": [0.0, 0.22],
                    "anchor": "x", "rangemode": "tozero"},
            hovermode="x unified",
            shapes=threshold_shape(thresholds[0]),
            updatemenus=[{
                "buttons": buttons, "direction": "down", "showactive": True,
                "x": 0, "xanchor": "left", "y": 1.18, "yanchor": "top",
            }],
            margin={"t": 95, "r": 20, "b": 60, "l": 60},
        ),
        "config": {"displaylogo": False, "responsive": True},
    }


# =============================================================================
# Distributions: read length, poly(A), qscore
#
# All arrive as `HistogramAccumulator.to_dict` (bin geometry, per-bin counts, escaped
# mass, quartiles) and go through one builder, so a bin edge means the same everywhere.
# =============================================================================

#: Drawn as one filled step line rather than a bar per bin, which is far cheaper.
_STEP_SHAPE = "hv"

#: Not used: no default window is padded.
_RANGE_PAD = 0.04

#: Where the read-length (whole run) and poly(A) (whole run and per construct) views
#: begin and end: the values below which 1% and 99% of the reads fall (2026-11-09).
#: The Tukey fences assume one peak. A run of constructs of different lengths has
#: several, and the fences fell wherever the quartiles did. The upper one left every HA
#: read off screen on run 1 (1,376 nt, HA median ~1,900) and spCas9 or BNT162b2 on
#: OCT_261013 (2,722-3,134 nt, medians 3,937-4,361); the lower one left 21% of run 1's
#: reads, the short fragments, off screen (953 nt). Each end hides at most 1% of the
#: reads, so neither can hide a construct. Per-construct read length and quality keep
#: the fences.
WINDOW_QUANTILES = (0.01, 0.99)


def _quantile_bin(dist: Mapping[str, Any], q: float) -> Optional[int]:
    """The bin where the running share of reads first reaches ``q``; None past the last.

    Counted over every read the histogram saw, under- and overflow included, so a tail
    past either end of the axis still counts against the quantile.

    Raises:
        ValueError: ``q`` outside (0, 1).
    """
    if not 0 < q < 1:
        raise ValueError(f"a quantile is in (0, 1), got {q}")
    counts = dist.get("bin_counts") or []
    under, over = dist.get("underflow") or 0, dist.get("overflow") or 0
    target = q * (sum(counts) + under + over)
    cum = under
    for i, c in enumerate(counts):
        cum += c
        if cum >= target:
            return i
    return None


def _quantile_window(
    dist: Mapping[str, Any], quantiles: tuple[float, float],
) -> tuple[float, float]:
    """From the left edge of the lower quantile's bin to the right edge of the upper's.

    Whole bins, not interpolated values: every read in an end bin is inside the window,
    so the reads left out are fewer than ``lower`` below and at most ``1 - upper`` above.

    Raises:
        ValueError: a quantile outside (0, 1), or lower >= upper.
    """
    q_lo, q_hi = quantiles
    if not q_lo < q_hi:
        raise ValueError(f"the lower quantile must be below the upper, got {quantiles}")
    lo_edge, width = dist["bin_min"], dist["bin_width"]
    i_lo, i_hi = _quantile_bin(dist, q_lo), _quantile_bin(dist, q_hi)
    lo = dist["bin_max"] if i_lo is None else lo_edge + i_lo * width
    hi = dist["bin_max"] if i_hi is None else lo_edge + (i_hi + 1) * width
    return (lo, hi)


def _default_range(
    dist: Mapping[str, Any], quantiles: Optional[tuple[float, float]] = None,
) -> Optional[tuple[float, float]]:
    """The x-range to open on: the Tukey fences, Q1 - 1.5 IQR to Q3 + 1.5 IQR.

    The histogram's axis runs far past the data, and a few long reads would hold a
    first-to-last-occupied window open. The fences are a convention, not a cut-off
    chosen here. Clamped to the histogram's bounds and to the occupied bins. Falls back
    to the occupied span when the quartiles are missing or the fences collapse; None
    with no geometry or no counts.

    ``quantiles`` (lower, upper) replaces both fences with those quantiles of the reads
    (:func:`_quantile_window`), for a histogram with several peaks.
    """
    lo_edge, hi_edge = dist.get("bin_min"), dist.get("bin_max")
    q1, q3 = dist.get("Q1"), dist.get("Q3")
    counts = dist.get("bin_counts") or []
    width = dist.get("bin_width")
    if lo_edge is None or hi_edge is None or not width:
        return None
    filled = [i for i, c in enumerate(counts) if c]
    if not filled:
        return None
    occupied_hi = lo_edge + (filled[-1] + 1) * width
    occupied_lo = lo_edge + filled[0] * width
    lo, hi = occupied_lo, occupied_hi
    if quantiles is not None:
        q_lo, q_hi = _quantile_window(dist, quantiles)
        lo, hi = max(occupied_lo, q_lo), min(occupied_hi, q_hi)
    elif q1 is not None and q3 is not None:
        iqr = q3 - q1
        lo = max(lo_edge, occupied_lo, q1 - 1.5 * iqr)
        hi = min(hi_edge, occupied_hi, q3 + 1.5 * iqr)
    if not hi > lo:
        return (occupied_lo, occupied_hi)
    return (lo, hi)


def _outside_default(
    dist: Mapping[str, Any], quantiles: Optional[tuple[float, float]] = None,
) -> int:
    """How many reads the default window leaves off screen, by bins wholly outside it.

    They stay in the figure (the range slider spans the whole histogram); this count
    keeps the trimming visible.
    """
    rng = _default_range(dist, quantiles)
    counts = dist.get("bin_counts") or []
    lo_edge, width = dist.get("bin_min"), dist.get("bin_width")
    if rng is None or lo_edge is None or not width:
        return 0
    lo, hi = rng
    out = 0
    for i, c in enumerate(counts):
        if not c:
            continue
        if lo_edge + (i + 1) * width <= lo or lo_edge + i * width >= hi:
            out += c
    return out


def _stat(value: float) -> str:
    """A quartile as the tables beside the figure write it: from 100 up a whole number
    with its thousands grouped (a read length, "1,143", was printed "1142.97"); below
    that, as measured."""
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:g}"


def _escaped_note(
    dist: Mapping[str, Any], quantiles: Optional[tuple[float, float]] = None,
) -> str:
    """A note on the reads the default view does not show, of both kinds.

    Under/overflow fell outside the histogram and is gone; the default window is only a
    display choice. States the fact, never how to act on it: the line is also printed in
    the PDF, which has no slider.
    """
    parts = []
    under, over = dist.get("underflow") or 0, dist.get("overflow") or 0
    if under or over:
        lo, hi = dist.get("bin_min"), dist.get("bin_max")
        # Only the side that has reads: "0 below 0 and 1 above 5700" said nothing twice.
        sides = [f"{n:,} {word} {edge:,g}" for n, word, edge in
                 ((under, "below", lo), (over, "above", hi)) if n]
        parts.append(" and ".join(sides) + " outside the histogram")
    outside = _outside_default(dist, quantiles)
    if outside:
        parts.append(f"{outside:,} beyond the default window")
    return (" · " + " · ".join(parts)) if parts else ""


def histogram_spec(
    series: Sequence[tuple[str, Mapping[str, Any], str]],
    *,
    x_title: str,
    quantiles_from: Optional[int] = 0,
    name: str = "",
    window_quantiles: Optional[tuple[float, float]] = None,
) -> dict[str, Any]:
    """A distribution figure with range selection, from one or more histograms.

    A distribution that carries ``smoothed_counts`` (one per bin, as poly(A)'s do) is
    drawn as faint bars with no outline under that curve, two traces sharing the series
    name and legend group; one without is drawn as an outlined step histogram.

    Args:
        series: ``(name, distribution, colour)``, overlaid on one axis (e.g. poly(A)
            uncorrected and corrected).
        x_title: what the x axis measures, with units.
        quantiles_from: the one series the Q1/median/Q3 lines describe. ``None`` draws
            no lines and puts every series' quartiles in the subtitle, for a figure
            whose series can be hidden.
        name: title line, for a figure whose subject changes with a selector; usually
            empty, as the caption names the figure.
        window_quantiles: open the default view between these two quantiles of the
            reads instead of the Tukey fences (:data:`WINDOW_QUANTILES`);
            the count of reads beyond the view follows it.

    Raises:
        ValueError: no series, or a histogram whose counts or curve do not match its
            geometry.
    """
    if not series:
        raise ValueError("histogram_spec needs at least one series")

    data: list[dict[str, Any]] = []
    # Not `name`: that parameter is the title line.
    for series_name, dist, colour in series:
        counts = dist.get("bin_counts") or []
        lo, width = dist.get("bin_min"), dist.get("bin_width")
        n_bins = dist.get("n_bins")
        if n_bins is not None and len(counts) != n_bins:
            # A mismatched count array would shift every bin along the axis.
            raise ValueError(
                f"{series_name}: {len(counts)} bin_counts against n_bins={n_bins}")
        if lo is None or not width:
            raise ValueError(f"{series_name}: histogram carries no bin geometry")
        # One extra point closes the last step at the last bin's right edge. Rounded: a
        # corrected series' edges are F-stretched floats, and their float noise
        # (111.72860000000001) was most of a 1 nt figure's bytes.
        xs = [round(lo + i * width, 4) for i in range(len(counts) + 1)]
        ys = list(counts) + [0]
        # Fractional counts (a series re-binned by overlap, such as the run-level
        # corrected poly(A)) hover to one decimal.
        y_fmt = "%{y}" if all(float(c).is_integer() for c in counts) else "%{y:.1f}"
        smoothed = dist.get("smoothed_counts")
        if smoothed is not None and len(smoothed) != len(counts):
            raise ValueError(
                f"{series_name}: {len(smoothed)} smoothed_counts against "
                f"{len(counts)} bin_counts")
        bars = {
            "type": "scatter",
            "mode": "lines",
            "name": series_name,
            "x": xs,
            "y": ys,
            "line": {"shape": _STEP_SHAPE, "width": SERIES_LINE_PX, "color": colour},
            "fill": "tozeroy", "fillcolor": fill_of(colour),
            "hovertemplate": f"{series_name}: {y_fmt} reads<extra></extra>",
        }
        if smoothed is None:
            data.append(bars)
            continue
        # With a curve, the bars stay as a fill with no outline, under it: a peak the
        # kernel would soften is still there in the counts. One legend entry, the curve's,
        # and the hover reads the bars, which are the data.
        bars["line"]["width"] = 0
        bars.update(showlegend=False, legendgroup=series_name)
        data.append(bars)
        data.append({
            "type": "scatter",
            "mode": "lines",
            "name": series_name,
            # Bin centres: each value is the expected count in that bin.
            "x": [round(lo + (i + 0.5) * width, 4) for i in range(len(counts))],
            "y": list(smoothed),
            "line": {"width": SERIES_LINE_PX, "color": colour},
            "legendgroup": series_name,
            "hoverinfo": "skip",
        })

    ref = series[quantiles_from or 0][1]
    # Lines only; the values go in the subtitle, where zooming cannot make them collide.
    shapes = [
        {"type": "line", "x0": v, "x1": v, "yref": "paper", "y0": 0, "y1": 1,
         "line": {"color": _MUTED, "width": 1, "dash": dash}}
        for v, dash in ((ref.get("Q1"), "dot"), (ref.get("median"), "solid"),
                        (ref.get("Q3"), "dot"))
        if v is not None
    ] if quantiles_from is not None else []

    def _quartiles(dist: Mapping[str, Any]) -> str:
        return " · ".join(
            f"{k} {_stat(dist[k])}" for k in ("Q1", "median", "Q3")
            if dist.get(k) is not None)

    quantiles = (_quartiles(ref) if quantiles_from is not None else "; ".join(
        f"{label}: {_quartiles(dist)}" for label, dist, _c in series if _quartiles(dist)))

    layout = _layout(
        title=_title(name, (quantiles + _escaped_note(ref, window_quantiles)).lstrip(" ·")),
        xaxis={
            "title": {"text": x_title},
            # Zooming shows the same bins larger; Core ships counts, so nothing re-bins.
            "rangeslider": {"visible": True, "thickness": 0.08},
        },
        yaxis={"title": {"text": "reads"}},
        hovermode="x unified",
        shapes=shapes,
    )
    # The union of every series' window, so a shifted corrected series is not cut off.
    windows = [w for w in (_default_range(d, window_quantiles) for _n, d, _c in series)
               if w]
    occupied = (min(w[0] for w in windows), max(w[1] for w in windows)) if windows else None
    if occupied:
        # The default view only: the slider still spans the full histogram.
        layout["xaxis"]["range"] = list(occupied)
    return {"data": data, "layout": layout,
            "config": {"displaylogo": False, "responsive": True}}


def per_construct_histogram_spec(
    tracks: Sequence[tuple[str, Mapping[str, Any]]], *, x_title: str,
    corrected: Optional[Mapping[str, Mapping[str, Any]]] = None,
    window_quantiles: Optional[tuple[float, float]] = None,
    colour: str = CATEGORICAL[0], corrected_colour: str = CATEGORICAL[1],
) -> dict[str, Any]:
    """One distribution per construct, behind the construct selector.

    Each construct is a :func:`histogram_spec`, so validation, quartiles, the default
    window and the escaped-mass note match the run-level figure. ``colour`` draws the
    distribution (uncorrected, beside a correction) and ``corrected_colour`` the corrected
    one; the reporter passes its panel's result colours (RESULT_COLOURS).

    ``corrected``, by construct, adds a second series (poly(A) corrected beside
    uncorrected): the quartiles of both go in the subtitle, and a second control shows either
    or both. That control is screen only (print expands the first menu alone), and it
    sets opacity rather than visibility so it does not undo the construct selector.

    ``window_quantiles`` is passed to each construct's :func:`histogram_spec`.

    Raises:
        ValueError: no tracks.
    """
    if not tracks:
        raise ValueError("per_construct_histogram_spec needs at least one track")
    corrected = corrected or {}

    data: list[dict[str, Any]] = []
    roles: list[str] = []
    spans: list[tuple[int, int]] = []
    layouts: list[dict[str, Any]] = []
    for label, dist in tracks:
        # "Uncorrected", the word the page uses for it (2026-11-13; was "as called").
        series = [("uncorrected" if corrected else label, dist, colour)]
        if label in corrected:
            series.append(("corrected", corrected[label], corrected_colour))
        one = histogram_spec(series, x_title=x_title, name=label,
                             quantiles_from=None if corrected else 0,
                             window_quantiles=window_quantiles)
        start = len(data)
        # A series is one trace, or two with a curve (bars, then line); both carry the
        # series name, which is what the role is read from.
        for trace in one["data"]:
            trace["visible"] = False
            data.append(trace)
            roles.append("corrected" if trace["name"] == "corrected" else "called")
        spans.append((start, len(data)))
        layouts.append(one["layout"])

    for i in range(*spans[0]):
        data[i]["visible"] = True

    buttons = []
    for idx, ((label, _dist), (lo_i, hi_i)) in enumerate(zip(tracks, spans)):
        visible = [False] * len(data)
        for i in range(lo_i, hi_i):
            visible[i] = True
        lay = layouts[idx]
        buttons.append({
            "label": label, "method": "update",
            "args": [{"visible": visible},
                     # Each construct opens on its own default window.
                     {"title": lay["title"], "shapes": lay.get("shapes") or [],
                      "xaxis.range": lay["xaxis"].get("range")}],
        })

    layout = dict(layouts[0])
    layout["updatemenus"] = [{
        "buttons": buttons, "direction": "down", "showactive": True,
        "x": 0, "xanchor": "left", "y": 1.18, "yanchor": "top",
    }]
    if corrected:
        # A shown trace gets back its own hover and legend setting: a curve neither
        # hovers nor repeats its bars' legend entry.
        own = [(t.get("hoverinfo", "all"), t.get("showlegend", True)) for t in data]

        def _show(*wanted: str) -> dict[str, list[Any]]:
            on = [r in wanted for r in roles]
            return {"opacity": [1 if o else 0 for o in on],
                    "hoverinfo": [h if o else "skip" for o, (h, _l) in zip(on, own)],
                    "showlegend": [o and leg for o, (_h, leg) in zip(on, own)]}
        layout["updatemenus"].append({
            "type": "buttons", "direction": "right", "showactive": True, "active": 0,
            "x": 1, "xanchor": "right", "y": 1.18, "yanchor": "top",
            "buttons": [
                {"label": "Both", "method": "restyle", "args": [_show("called", "corrected")]},
                {"label": "Corrected", "method": "restyle", "args": [_show("corrected")]},
                {"label": "Uncorrected", "method": "restyle", "args": [_show("called")]},
            ],
        })
    # Room above the plot for the selector, as in the other selector figures.
    layout["margin"] = {**(layout.get("margin") or {}), "t": 95}
    return {"data": data, "layout": layout,
            "config": {"displaylogo": False, "responsive": True}}


# =============================================================================
# Terminal reach: where reads start and stop on the reference
#
# A complete read ends at the reference end, so a peak in the terminal bin is the normal
# case, and its share is the one number reported. Interior truncation peaks are left to
# the eye: a "largest local excess" score would rank a healthy terminal peak worst.
# =============================================================================

def terminus_share(counts: Sequence[int], *, terminus: str) -> Optional[float]:
    """Fraction of reads in the expected terminal bin: the last, or the first.

    None when there are no reads.
    """
    if terminus not in ("first", "last"):
        raise ValueError(f"terminus must be 'first' or 'last', not {terminus!r}")
    total = sum(counts)
    if not total:
        return None
    idx = (len(counts) - 1) if terminus == "last" else 0
    return counts[idx] / total


def _terminal_detail(dist: Mapping[str, Any], terminus: str) -> str:
    counts = dist.get("bin_counts") or []
    share = terminus_share(counts, terminus=terminus)
    where = "reference end" if terminus == "last" else "reference start"
    share_txt = "n/a" if share is None else f"{share * 100:.1f}%"
    return f"{sum(counts)} reads · {share_txt} in the bin at the {where}"


def terminal_reach_spec(
    tracks: Sequence[tuple[str, Mapping[str, Any]]], *, terminus: str,
    what: str = "read end position", colour: str = CATEGORICAL[0],
) -> dict[str, Any]:
    """Read start/end positions along the reference, one construct at a time.

    No spike markers: on reads that stop a few bases short they would mark the whole
    shoulder. The subtitle carries the terminal share.

    Args:
        tracks: ``(label, position_distribution)``.
        terminus: "last" for end positions, "first" for start positions.
        what: axis wording.

    Raises:
        ValueError: no tracks, or a distribution with no bin geometry.
    """
    if not tracks:
        raise ValueError("terminal_reach_spec needs at least one construct track")
    if terminus not in ("first", "last"):
        raise ValueError(f"terminus must be 'first' or 'last', not {terminus!r}")

    data: list[dict[str, Any]] = []
    for label, dist in tracks:
        counts = list(dist.get("bin_counts") or [])
        lo, width = dist.get("bin_min"), dist.get("bin_width")
        if lo is None or not width:
            raise ValueError(f"{label}: position distribution carries no bin geometry")
        data.append({
            "type": "scatter", "mode": "lines", "name": "reads",
            "x": [lo + i * width for i in range(len(counts) + 1)],
            "y": counts + [0],
            "line": {"shape": _STEP_SHAPE, "width": SERIES_LINE_PX, "color": colour},
            "fill": "tozeroy", "fillcolor": fill_of(colour),
            "hovertemplate": "%{y} reads<extra></extra>", "visible": False,
        })
    data[0]["visible"] = True

    buttons = [
        {"label": label, "method": "update",
         "args": [{"visible": [k == idx for k in range(len(data))]},
                  {"title": _title(label, _terminal_detail(dist, terminus))}]}
        for idx, (label, dist) in enumerate(tracks)
    ]

    first_label, first_dist = tracks[0]
    return {
        "data": data,
        "layout": _layout(
            title=_title(first_label, _terminal_detail(first_dist, terminus)),
            xaxis={"title": {"text": f"{what} on the reference (nt)"}},
            yaxis={"title": {"text": "reads"}},
            hovermode="x unified",
            showlegend=False,
            updatemenus=[{
                "buttons": buttons, "direction": "down", "showactive": True,
                "x": 0, "xanchor": "left", "y": 1.18, "yanchor": "top",
            }],
            margin={"t": 95, "r": 20, "b": 50, "l": 60},
        ),
        "config": {"displaylogo": False, "responsive": True},
    }


# =============================================================================
# Joint shortfall: how far each read fell short at both ends at once
#
# The marginals cannot tell a read missing 200 nt at one end from one missing 100 at
# each. The joint can, and since aligned span = ref_length - (d5 + d3), every
# anti-diagonal is an iso-length contour.
# =============================================================================

#: Iso-length contours, as the fraction of the reference lost in total. Round numbers,
#: so they read as a ruler, not as fitted boundaries.
_ISO_LENGTH_FRACTIONS = (0.05, 0.10, 0.25, 0.50)


def joint_shortfall_spec(
    tracks: Sequence[tuple[str, Mapping[str, Any], Optional[int]]],
) -> dict[str, Any]:
    """5' shortfall against 3' shortfall, one construct at a time.

    Args:
        tracks: ``(label, joint_distribution, ref_length)``. ``ref_length`` may be None,
            in which case the iso-length contours are omitted rather than guessed.

    Raises:
        ValueError: no tracks, or a distribution without its grid geometry.
    """
    if not tracks:
        raise ValueError("joint_shortfall_spec needs at least one construct track")

    data: list[dict[str, Any]] = []
    shapes_per: list[list[dict[str, Any]]] = []
    titles: list[str] = []

    for label, joint, ref_length in tracks:
        n_bins, width = joint.get("n_bins"), joint.get("bin_width")
        if not n_bins or not width:
            raise ValueError(f"{label}: joint distribution carries no grid geometry")
        # A dense grid from the sparse cells. Empty cells stay None, not 0, so plotly
        # leaves them blank.
        z: list[list[Optional[int]]] = [[None] * n_bins for _ in range(n_bins)]
        for cell in joint.get("cells") or []:
            i, j, count = cell
            # Cells are (5' bin, 3' bin): rows are the 3' axis (y), columns the 5' (x).
            z[j][i] = count
        # Bin centres: plotly (and figrender) read heatmap x/y as cell centres, so cell k
        # then spans [k*width, (k+1)*width), the reads it counts.
        axis = [(k + 0.5) * width for k in range(n_bins)]
        half = f"{round(width / 2, 1):g}"
        data.append({
            "type": "heatmap",
            "x": axis, "y": axis, "z": z,
            "colorscale": SEQUENTIAL_BLUE, "reversescale": False,
            "colorbar": {"title": {"text": "reads"}},
            "hovertemplate": (f"5' short %{{x:.0f}} ± {half} nt · 3' short %{{y:.0f}} ± "
                              f"{half} nt<br>%{{z}} reads<extra></extra>"),
            "visible": False,
        })
        extent = n_bins * width
        shapes = []
        if ref_length:
            for frac in _ISO_LENGTH_FRACTIONS:
                total = ref_length * frac
                if total > extent:
                    continue
                shapes.append({
                    "type": "line", "x0": 0, "y0": total, "x1": total, "y1": 0,
                    "line": {"color": INK_MUTED, "width": 1, "dash": "dot"},
                })
        shapes_per.append(shapes)
        out = joint.get("out_of_range") or 0
        extra = f" · {out} outside the grid" if out else ""
        titles.append(_title(
            label,
            f"{joint.get('n', 0)} reads · dotted lines are iso-length contours{extra}"))

    data[0]["visible"] = True
    buttons = [
        {"label": label, "method": "update",
         "args": [{"visible": [k == idx for k in range(len(data))]},
                  {"title": titles[idx], "shapes": shapes_per[idx]}]}
        for idx, (label, _, _) in enumerate(tracks)
    ]

    return {
        "data": data,
        "layout": _layout(
            title=titles[0],
            xaxis={"title": {"text": "5' shortfall (nt)"}, "constrain": "domain"},
            # Square cells: the anti-diagonal is a 45-degree ruler only on equal scales.
            yaxis={"title": {"text": "3' shortfall (nt)"},
                   "scaleanchor": "x", "scaleratio": 1},
            shapes=shapes_per[0],
            updatemenus=[{
                "buttons": buttons, "direction": "down", "showactive": True,
                "x": 0, "xanchor": "left", "y": 1.18, "yanchor": "top",
            }],
            margin={"t": 95, "r": 20, "b": 50, "l": 60},
        ),
        "config": {"displaylogo": False, "responsive": True},
    }


# =============================================================================
# Comparisons across constructs, and the instrument's own record
# =============================================================================

def ranked_bar_spec(
    items: Sequence[tuple[str, Optional[float]]], *,
    x_title: str,
    colours: Optional[Mapping[str, str]] = None,
    reference: Optional[tuple[float, str]] = None,
    value_suffix: str = "",
    colour: str = CATEGORICAL[0],
) -> dict[str, Any]:
    """One value per category as horizontal bars, largest first, in ``colour`` unless
    ``colours`` names a bar's own.

    Sorted bars rank near-equal shares, which a pie cannot. The figure carries no title
    of its own: its caption names it, and the same words inside it were said twice.

    Args:
        items: ``(label, value)``. A None value is kept as a gap: not measured is not
            zero.
        reference: ``(value, caption)`` for a line the bars are judged against, e.g. an
            even split. Omit it when no such expectation exists.
    """
    if not items:
        raise ValueError("ranked_bar_spec needs at least one item")
    ranked = sorted(items, key=lambda kv: (kv[1] is None, -(kv[1] or 0.0)))
    labels = [k for k, _ in ranked]
    values = [v for _, v in ranked]
    bar = {
        "type": "bar", "orientation": "h",
        # Reversed: plotly draws the first category at the bottom of horizontal bars.
        "x": values[::-1], "y": labels[::-1],
        "hovertemplate": f"%{{y}}: %{{x}}{value_suffix}<extra></extra>",
    }
    if colours:
        bar["marker"] = _bar_marker([colours.get(k, colour) for k in labels[::-1]])
    else:
        bar["marker"] = _bar_marker(colour)

    layout = _layout(
        title=_title(""),
        xaxis={"title": {"text": x_title}},
        yaxis={"automargin": True},
        showlegend=False,
        margin={"t": 30, "r": 20, "b": 50, "l": 110},
    )
    if reference is not None:
        value, caption = reference
        layout["shapes"] = [{"type": "line", "x0": value, "x1": value, "yref": "paper",
                             "y0": 0, "y1": 1,
                             "line": {"color": INK_MUTED, "width": 1, "dash": "dash"}}]
        layout["annotations"] = [{"x": value, "yref": "paper", "y": 1.02, "text": caption,
                                  "showarrow": False,
                                  "font": {"size": 10, "color": INK_MUTED}}]
    return {"data": [bar], "layout": layout,
            "config": {"displaylogo": False, "responsive": True}}


def category_bar_spec(counts: Mapping[Any, int], *, x_title: str,
                      log_y: bool = False, colour: str = CATEGORICAL[0]) -> dict[str, Any]:
    """Counts over an ordered discrete axis, such as MAPQ.

    Not `histogram_spec`: these are exact values, not bins, and a filled step would
    invent coverage between them.
    """
    if not counts:
        raise ValueError("category_bar_spec needs at least one category")
    keys = sorted(counts, key=lambda k: float(k))
    # ``log_y`` for counts dominated by one value (MAPQ 60), where a linear axis hides
    # every other bar.
    yaxis = {"title": {"text": "reads"}}
    yaxis.update(_LOG_AXIS if log_y else {})
    return {
        "data": [{"type": "bar", "x": [float(k) for k in keys],
                  "y": [counts[k] for k in keys],
                  "marker": _bar_marker(colour),
                  "hovertemplate": "%{x}: %{y} reads<extra></extra>"}],
        "layout": _layout(
            title=_title(""),
            xaxis={"title": {"text": x_title}},
            yaxis=yaxis,
            margin={"t": 30, "r": 20, "b": 50, "l": 60},
        ),
        "config": {"displaylogo": False, "responsive": True},
    }


def voltage_spec(series: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Running and offset voltage (mV) over the run, as the instrument recorded them.

    A rising magnitude is the instrument compensating as the flow cell ages: a signal to
    act on while the run is still going.
    """
    if not series:
        raise ValueError("voltage_spec needs a non-empty series")
    t = [p.get("t_hours") for p in series]
    return {
        "data": [{"type": "scatter", "mode": "lines+markers", "name": "running",
                  "x": t, "y": [p.get("running_mv") for p in series],
                  "line": {"color": CATEGORICAL[0]},
                  "hovertemplate": "%{x:.2f} h: %{y} mV<extra>running</extra>"},
                 {"type": "scatter", "mode": "lines", "name": "offset",
                  "x": t, "y": [p.get("offset_mv") for p in series],
                  "line": {"color": CATEGORICAL[1], "dash": "dot"},
                  "hovertemplate": "%{x:.2f} h: %{y} mV<extra>offset</extra>"}],
        "layout": _layout(
            title=_title(""),
            xaxis={"title": {"text": "run time (hours)"}},
            yaxis={"title": {"text": "mV"}},
            hovermode="x unified",
            margin={"t": 30, "r": 20, "b": 50, "l": 60},
        ),
        "config": {"displaylogo": False, "responsive": True},
    }


#: Channel states drawn as their own band, in stacking order; every other state is
#: summed into "other". The GUI's state bar draws the same bands in the same colours
#: (`PORE_BANDS` in gui/src/main.ts, held equal by test_gui_state_bar.py).
_PORE_BANDS = ("strand", "pore", "adapter", "unavailable")


def pore_activity_spec(series: Mapping[str, Any]) -> dict[str, Any]:
    """Pore occupancy over the run, as stacked fractions of channel time.

    A falling strand fraction with a rising unavailable one is pores dying, which
    matters while the run can still be stopped. ``coverage`` (the share of each minute
    recorded; MinKNOW's last minute is partial) goes to the hover rather than rescaling
    the fractions.
    """
    t = series.get("t_minutes")
    fractions = series.get("fractions") or {}
    if not t or not fractions:
        raise ValueError("pore_activity_spec needs t_minutes and fractions")
    hours = [m / 60.0 for m in t]
    coverage = series.get("coverage") or [None] * len(t)

    named = [s for s in _PORE_BANDS if s in fractions]
    rest = [s for s in fractions if s not in _PORE_BANDS]
    data = []
    # Colours by position in the fixed order, like every categorical set.
    palette = dict(zip(_PORE_BANDS, CATEGORICAL))
    for state in named:
        data.append({
            "type": "scatter", "mode": "lines", "name": state,
            "x": hours, "y": fractions[state],
            "stackgroup": "one", "line": {"width": 0},
            "fillcolor": palette.get(state, INK_MUTED),
            "customdata": coverage,
            "hovertemplate": (f"{state}: %{{y:.3f}}"
                              "<br>minute coverage %{customdata:.2f}<extra></extra>"),
        })
    if rest:
        other = [sum((fractions[s][i] or 0.0) for s in rest) for i in range(len(hours))]
        data.append({
            "type": "scatter", "mode": "lines", "name": f"other ({len(rest)} states)",
            "x": hours, "y": other, "stackgroup": "one",
            "line": {"width": 0}, "fillcolor": AXIS_LINE,
            "hovertemplate": "other: %{y:.3f}<extra></extra>",
        })
    return {
        "data": data,
        "layout": _layout(
            title=_title(""),
            xaxis={"title": {"text": "run time (hours)"}},
            yaxis={"title": {"text": "fraction of channel time"}, "range": [0, 1]},
            hovermode="x unified",
            margin={"t": 30, "r": 20, "b": 50, "l": 60},
        ),
        "config": {"displaylogo": False, "responsive": True},
    }
