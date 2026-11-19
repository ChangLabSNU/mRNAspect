"""Draw a figspec with matplotlib, for the PDF.

The screen draws ``figspec`` specs with plotly.js; WeasyPrint runs no JavaScript, so the
PDF's figures are drawn here from the same specs. This module knows traces, not charts,
so a new figure in ``figspec`` needs no change here. The vocabulary is closed (scatter,
bar and heatmap traces; line shapes), held there by ``figspec.SUPPORTED_TRACE_TYPES``.

A selector becomes one panel per option. Hover text, ``customdata`` and range sliders
are dropped: they carry nothing the axes, legend or caption do not. Titles are not drawn
into the image; each :class:`Panel` returns its title as text (``name``, ``detail``) for
the caller to set beside it.

Each panel is SVG, which WeasyPrint draws as vector paths (2026-11-12; it was a
200 dpi PNG). Nothing in it is raster: text is glyph outlines and a heatmap is one
rectangle per filled cell.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from . import figspec

__all__ = ["Panel", "Theme", "SCREEN", "GRAYSCALE", "render_spec", "draw_panel",
           "panel_count"]

# The figure's dpi and the default panel size (inches). The PDF takes panels as SVG, so
# the dpi is only the pixel grid matplotlib lays text out on, not a print resolution.
PANEL_DPI = 200
PANEL_SIZE = (7.0, 3.2)

#: How a panel is written. Text as glyph outlines, so the page shows the font matplotlib
#: measured the layout in on any machine (live text would be set by WeasyPrint in the
#: font that machine resolves, and a different width runs labels into each other). A
#: fixed id salt and no date (passed to savefig), so a spec always gives the same bytes.
_SVG_RC = {"svg.fonttype": "path", "svg.hashsalt": "mrnaspect"}

#: plotly marker symbols the specs use, mapped to matplotlib. In print every emphasised
#: mark is the same red, so the symbol alone tells one kind of mark from another.
_SYMBOLS = {"circle": "o", "square": "s", "triangle-up": "^", "triangle-down": "v",
            "diamond": "D", "cross": "X", "x": "x"}

#: The white edge between adjacent fills (keeps stacked segments countable), and the
#: colour a hatch is stroked in.
_HATCH_EDGE_W = 0.5
_HATCH_COLOR = "#ffffff"
#: Bars drawn narrower than this (pt) get no hatch: there it reads as gaps in the data.
_MIN_HATCH_PT = 2.0

#: Spec font sizes (CSS px) to matplotlib points; figspec owns the conversion.
_FONT_PT_PER_SPEC_UNIT = 1.0 / figspec.SPEC_UNITS_PER_PT


def _pt(width: float) -> float:
    """A spec line width (CSS px, like its font sizes) in points: a series outline,
    SERIES_LINE_PX, prints at 1 pt and a width-1 reference line under it."""
    return width / figspec.SPEC_UNITS_PER_PT

_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>", re.I)


# --------------------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Theme:
    """A colour substitution applied to every colour the spec names.

    The spec decides what is drawn and a theme only its colours, so the PDF can be
    monochrome from the same spec. Keyed on figspec's colour tokens, not on trace index
    or series name, so a filter that changes the series count repaints nothing.
    """

    name: str
    colors: Mapping[str, str] = field(default_factory=dict)
    #: Applied to any colour the map does not name. ``None`` passes it through.
    fallback: Optional[str] = None
    #: Colour for marks a spec tagged ``meta.emphasis``, or ``None`` to leave them as
    #: the spec drew them.
    emphasis: Optional[str] = None
    #: Diameter factor for a marker the spec tagged ``meta.emphasis``. Its edge scales
    #: with it, so what shows of the marker scales by this.
    emphasis_marker_scale: float = 1.0
    #: Texture per spec colour. In print, greys alone cannot carry four categories (see
    #: ``GRAYSCALE``), so the hatch carries identity and the grey is a second cue.
    hatches: Mapping[str, str] = field(default_factory=dict)
    #: This medium's font stack, or ``None`` for the spec's own screen stack, which
    #: matplotlib cannot resolve.
    font_family: Optional[str] = None
    #: Colour for a trace the reporter tagged ``meta.result`` (its panel's result colour,
    #: figspec.RESULT_COLOURS), where the trace draws in that colour; ``None`` maps it
    #: like any other colour. Print uses it so every primary series keeps one grey.
    result_ink: Optional[str] = None

    def __call__(self, color: Optional[str]) -> Optional[str]:
        if color is None:
            return None
        mapped = self.colors.get(color.lower() if isinstance(color, str) else color)
        if mapped is not None:
            return mapped
        return color if self.fallback is None else self.fallback

    def hatch(self, color: Optional[str],
              trace: Optional[Mapping[str, Any]] = None) -> Optional[str]:
        if color is None or (trace is not None and self._is_result(trace, color)):
            return None
        return self.hatches.get(color.lower()) or None

    def _is_result(self, trace: Mapping[str, Any], color: Optional[str]) -> bool:
        """True when ``color`` is the result colour ``trace`` was tagged with."""
        meta = trace.get("meta")
        return (self.result_ink is not None and isinstance(color, str)
                and isinstance(meta, Mapping)
                and str(meta.get("result", "")).lower() == color.lower())

    def for_trace(self, trace: Mapping[str, Any], color: Optional[str]) -> Optional[str]:
        """The colour a trace's marks come out, honouring its emphasis and result tags."""
        meta = trace.get("meta")
        if self.emphasis and isinstance(meta, Mapping) and meta.get("emphasis"):
            return self.emphasis
        if self._is_result(trace, color):
            return self.result_ink
        return self(color)

    def marker_scale(self, trace: Mapping[str, Any]) -> float:
        """The factor a trace's markers are drawn at: 1 unless the spec tagged it."""
        meta = trace.get("meta")
        if isinstance(meta, Mapping) and meta.get("emphasis"):
            return self.emphasis_marker_scale
        return 1.0


SCREEN = Theme("screen")
"""Identity: the PDF and the screen share one palette."""

# Monochrome, for a printed record that may be photocopied: grey steps and hatches for
# the series, red for emphasis marks alone.
GRAYSCALE = Theme(
    "grayscale",
    # The printed page's face.
    font_family=figspec.FONT_FAMILY_PRINT,
    colors={
        # By figspec's slot, never by rank. Every step clears 3:1 on white. Slot 1 is a
        # mid-grey, not near-black: most charts use it alone, where the darkest step
        # would only make the page heavy. The four greys are too close to tell apart
        # alone; the hatch carries identity (see `hatches`).
        # Keyed by the slot's constant, so a new palette (Lagoon, 2026-11-13) prints
        # exactly as the old one did.
        figspec.CATEGORICAL[0].lower(): "#3f3f3f",   # categorical 1
        figspec.CATEGORICAL[1].lower(): "#6b6b6b",   # categorical 2
        figspec.CATEGORICAL[2].lower(): "#949494",   # categorical 3
        figspec.CATEGORICAL[3].lower(): "#545454",   # categorical 4
        "#7f1d1d": "#c62828",   # EMPHASIS, and the only chroma on the page
        "#0b0b0b": "#0b0b0b",   # ink primary
        "#52514e": "#454545",   # ink secondary
        "#898781": "#767676",   # ink muted
        # The corrected poly(A) series: the grey and texture it had as slot 2.
        figspec.POLYA_CORRECTED.lower(): "#6b6b6b",
        "#e1e0d9": "#e3e3e3",   # gridline
        "#c3c2b7": "#b0b0b0",   # axis line, and the "other" catch-all band
        # The heatmap ramp's stops, so it prints grey too (an unmapped colour passes
        # through). Lightness stays monotonic, the only channel a grey ramp has.
        "#eef4fd": "#f0f0f0",
        "#9ec5f4": "#cccccc",
        "#5598e7": "#9e9e9e",
        "#2a78d6": "#6b6b6b",
        "#104281": "#2b2b2b",
    },
    emphasis="#c62828",
    # The flags at half the diameter they were drawn at (2026-11-12: too big on
    # paper).
    emphasis_marker_scale=0.5,
    # A figure's primary series prints in slot 1's grey, unhatched, whichever result
    # colour it wears on screen (figspec.RESULT_COLOURS, 2026-11-12), so the printed
    # page is what it was before the colour code.
    result_ink="#3f3f3f",
    hatches={
        # Four greys that all clear 3:1 are too close to tell apart, so identity moves
        # to texture and the grey is a second cue. The emphasis red is unhatched, to
        # read at once.
        figspec.CATEGORICAL[0].lower(): "",
        figspec.CATEGORICAL[1].lower(): "///",
        figspec.CATEGORICAL[2].lower(): "...",
        figspec.CATEGORICAL[3].lower(): "\\\\",
        figspec.POLYA_CORRECTED.lower(): "///",
        "#c3c2b7": "xx",
    },
    fallback=None,
)


# --------------------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Panel:
    """One rendered image, plus the title text that belongs BESIDE it rather than in it."""

    label: Optional[str]
    """The dropdown option this panel was, or ``None`` for a figure with no selector."""
    name: str
    """Title line, markup stripped. Empty when the caption already names the figure."""
    detail: str
    """The measurement detail that tracks the selector. Empty when there is none."""
    svg: bytes
    """The panel as an SVG document, vector throughout."""


def _text(markup: Any) -> tuple[str, str]:
    """Split a plotly title into (name, detail), stripping the markup figspec wrote."""
    if isinstance(markup, Mapping):
        markup = markup.get("text", "")
    if not isinstance(markup, str) or not markup:
        return "", ""
    parts = _BR.split(markup, maxsplit=1)
    head, tail = parts[0], parts[1] if len(parts) > 1 else ""
    name = _TAG.sub("", head).strip()
    detail = _TAG.sub("", tail).strip()
    # A title with no name is only the detail, in a <span>, so the "name" is the detail.
    if not detail and head.strip().startswith("<"):
        return "", name
    return name, detail


def _set_path(target: dict, path: str, value: Any) -> None:
    """Apply a dotted plotly key (``marker.color``) to a trace."""
    keys = path.split(".")
    for k in keys[:-1]:
        nxt = target.get(k)
        target[k] = nxt = dict(nxt) if isinstance(nxt, Mapping) else {}
        target = nxt
    target[keys[-1]] = value


def _panels(spec: Mapping[str, Any]) -> list[tuple[Optional[str], list, dict]]:
    """One (label, data, layout) per option of the first menu; one panel without a menu.

    Paper cannot switch options, so each becomes its own figure: its button's restyle
    and relayout patches applied to a copy of the spec.
    """
    data = list(spec.get("data", []))
    layout = dict(spec.get("layout", {}))
    menus = layout.pop("updatemenus", None) or []
    buttons = menus[0].get("buttons", []) if menus else []
    if not buttons:
        return [(None, [dict(t) for t in data], layout)]

    out = []
    for btn in buttons:
        args = list(btn.get("args") or [])
        traces = [dict(t) for t in data]
        if args and isinstance(args[0], Mapping):
            for path, values in args[0].items():
                # A restyle patch is {key: [per-trace values]}; a scalar applies to all.
                seq = values if isinstance(values, list) else [values] * len(traces)
                for i, v in enumerate(seq[:len(traces)]):
                    _set_path(traces[i], path, v)
        # A relayout patch keys nested fields with dots ("xaxis.range"); merged flat,
        # they would be ignored and every panel would keep the first option's range.
        panel_layout = {k: (dict(v) if isinstance(v, Mapping) else v)
                        for k, v in layout.items()}
        if len(args) > 1 and isinstance(args[1], Mapping):
            for path, value in args[1].items():
                _set_path(panel_layout, path, value)
        out.append((btn.get("label"), traces, panel_layout))
    return out


def panel_count(spec: Mapping[str, Any]) -> int:
    """How many images this spec becomes, without drawing any of them."""
    return len(_panels(spec))


# --------------------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------------------

def _apply_hatch(artist: Any, hatch: Optional[str]) -> None:
    """Hatch an artist, stroked in ``_HATCH_COLOR``.

    Only ``set_hatchcolor`` colours the hatch of both patches and collections:
    ``edgecolor`` and the ``hatch.color`` rcParam leave a collection's hatch a flat
    grey (matplotlib 3.11).

    Raises:
        RuntimeError: on a matplotlib without ``set_hatchcolor``. In print the texture
            carries series identity, so a silent fallback would ship series that cannot
            be told apart.
    """
    if not hatch:
        return
    if not hasattr(artist, "set_hatchcolor"):
        raise RuntimeError(
            "This matplotlib cannot colour a hatch (no set_hatchcolor), so the print "
            "theme's textures would not render and its grey steps alone cannot "
            "distinguish four series. Upgrade matplotlib, or render with theme=SCREEN.")
    artist.set_hatch(hatch)
    artist.set_hatchcolor(_HATCH_COLOR)


def _nums(values: Any) -> "Any":
    """Spec numbers as a float array, with ``None`` becoming ``nan``.

    ``None`` is a gap, such as an uncovered position. plotly breaks the line there, and
    matplotlib does the same at ``nan``.
    """
    import numpy as np
    if values is None:
        return np.asarray([], dtype=float)
    if not isinstance(values, (list, tuple)):
        values = [values]
    return np.asarray([math.nan if v is None else float(v) for v in values], dtype=float)


def _is_categorical(values: Any) -> bool:
    return isinstance(values, (list, tuple)) and bool(values) and isinstance(values[0], str)


def _colorscale(scale: Sequence[Sequence[Any]], theme: Theme, reverse: bool):
    from matplotlib.colors import LinearSegmentedColormap
    stops = [(float(pos), theme(color)) for pos, color in scale]
    if reverse:
        stops = [(1.0 - pos, color) for pos, color in reversed(stops)]
    return LinearSegmentedColormap.from_list("spec", stops)


#: plotly's fill for a trace that states none: its line colour at 0.5. figspec states
#: every fill; this draws one that does not as plotly would.
_PLOTLY_UNSTATED_FILL_ALPHA = 0.5
_RGBA = re.compile(
    r"rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([\d.]+)\s*\)")


def _fill_parts(fillcolor: Optional[str]) -> tuple[Optional[str], float]:
    """(hex, alpha) of a spec's ``fillcolor``: an rgba() splits, a hex is opaque.

    Split because a theme maps hex colours; ``None`` gives plotly's unstated alpha.
    """
    if fillcolor is None:
        return None, _PLOTLY_UNSTATED_FILL_ALPHA
    m = _RGBA.fullmatch(fillcolor.strip())
    if m is None:
        return fillcolor, 1.0
    r, g, b, a = m.groups()
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}", float(a)


def _draw_scatter(ax, tr: Mapping[str, Any], theme: Theme, base: dict,
                  swatches: Optional[dict] = None) -> None:
    """Lines, markers or a filled area, including a stacked band.

    ``base`` carries each ``stackgroup``'s running top between calls: a stacked trace
    fills from the previous top to its own, not to zero, which would cover the bands
    beneath. ``swatches`` maps a labelled line to the fill under it, for the legend: its
    own fill, or that of an earlier unlined trace in the same ``legendgroup``.
    """
    import numpy as np
    x, y = _nums(tr.get("x")), _nums(tr.get("y"))
    mode = tr.get("mode") or "lines"
    line = tr.get("line") or {}
    marker = tr.get("marker") or {}
    width = line.get("width", 1.5)
    width = 1.5 if width is None else float(width)
    color = theme.for_trace(tr, line.get("color") or marker.get("color"))
    fill_hex, fill_alpha = _fill_parts(tr.get("fillcolor"))
    fill_color = theme.for_trace(tr, fill_hex)
    hatch = theme.hatch(fill_hex or line.get("color"), tr)
    label = tr.get("name")
    # "hv" holds each value to the next x (bin edges), so it is drawn as steps.
    step = line.get("shape") == "hv"
    style = {"drawstyle": "steps-post"} if step else {}
    dash = {"dot": ":", "dash": "--", "dashdot": "-."}.get(line.get("dash"))
    if dash:
        style["linestyle"] = dash

    group = tr.get("stackgroup")
    lower = None
    area = None
    if group is not None and y.size:
        prev = base.get(group)
        lower = np.zeros_like(y) if prev is None or prev.shape != y.shape else prev
        y = lower + np.nan_to_num(y)
        base[group] = y

    if lower is not None:
        # A hatch needs a non-zero edge width or matplotlib draws none; the white edge
        # doubles as the gap between stacked fills.
        band = ax.fill_between(x, lower, y, color=fill_color or color, zorder=1,
                               linewidth=_HATCH_EDGE_W if hatch else 0,
                               label=label, step="post" if step else None,
                               edgecolor=_HATCH_COLOR if hatch else None)
        _apply_hatch(band, hatch)
    elif tr.get("fill") == "tozeroy":
        area = ax.fill_between(x, 0, y, color=fill_color or color, zorder=1,
                               linewidth=_HATCH_EDGE_W if hatch else 0,
                               alpha=fill_alpha,
                               step="post" if step else None,
                               edgecolor=_HATCH_COLOR if hatch else None)
        _apply_hatch(area, hatch)

    # A fill drawn with no line (bars under a curve) is kept by legend group, so the
    # group's line can show it in the legend: in grayscale print the fills are what tell
    # two series apart. Keyed by a tuple, never a legend handle.
    legend_group = tr.get("legendgroup")
    lined = "lines" in mode and width > 0
    if area is not None and not lined and legend_group and swatches is not None:
        swatches[("legendgroup", legend_group)] = area
    # A zero-width line means the band is the mark: no line.
    if lined:
        (drawn,) = ax.plot(x, y, color=color, linewidth=_pt(width), zorder=2,
                           label=None if lower is not None else label, **style)
        fill = area
        if fill is None and legend_group and swatches is not None:
            fill = swatches.get(("legendgroup", legend_group))
        if fill is not None and label and swatches is not None:
            swatches[drawn] = fill
    if "markers" in mode:
        from matplotlib import rcParams
        scale = theme.marker_scale(tr)
        ax.plot(x, y, linestyle="none",
                color=theme.for_trace(tr, marker.get("color")) or color,
                marker=_SYMBOLS.get(marker.get("symbol"), "o"),
                markersize=max(3.0, float(marker.get("size") or 6) * 0.6) * scale,
                markeredgewidth=rcParams["lines.markeredgewidth"] * scale,
                zorder=3, label=label if "lines" not in mode else None)


def _draw_bar(ax, tr: Mapping[str, Any], theme: Theme, layout: Mapping[str, Any],
              stack) -> None:
    import numpy as np
    from matplotlib.colors import to_rgba
    horizontal = tr.get("orientation") == "h"
    cats = tr.get("y") if horizontal else tr.get("x")
    vals = _nums(tr.get("x") if horizontal else tr.get("y"))
    marker = tr.get("marker") or {}
    # The fill rule (figspec.FILL_ALPHA): the alpha goes into the face only, so the
    # outline stays opaque.
    spec_color, alpha = (_fill_parts(marker["color"]) if marker.get("color") is not None
                         else (None, 1.0))
    color = theme.for_trace(tr, spec_color)
    face = None if color is None else to_rgba(color, alpha)
    hatch = theme.hatch(spec_color, tr)
    outline = marker.get("line") or {}
    edge_w = float(outline.get("width") or 0.0)
    edge = theme.for_trace(tr, outline.get("color")) if edge_w > 0 else None
    label = tr.get("name")

    if _is_categorical(cats):
        pos = np.arange(len(cats), dtype=float)
        width = 0.8
        ticks = list(cats)
    else:
        pos = _nums(cats)
        # Width from the data spacing and bargap, as plotly does; a fixed width would
        # merge dense bars into a block.
        gaps = np.diff(np.sort(pos[~np.isnan(pos)])) if pos.size > 1 else np.asarray([1.0])
        step = float(np.min(gaps[gaps > 0])) if np.any(gaps > 0) else 1.0
        width = step * (1.0 - float(layout.get("bargap", 0.0) or 0.0))
        ticks = None

    bottom = stack.setdefault("_", np.zeros(len(vals)))
    if layout.get("barmode") == "stack" and len(bottom) == len(vals):
        base = bottom.copy()
        stack["_"] = base + np.nan_to_num(vals)
    else:
        base = np.zeros(len(vals))

    if ticks is not None:
        # Named categories: ax.bar, with tick labels. An outline takes the edge; without
        # one a hatched bar keeps the white edge.
        edges = {"linewidth": _pt(edge_w) if edge else (_HATCH_EDGE_W if hatch else 0),
                 "edgecolor": edge or (_HATCH_COLOR if hatch else None)}
        if horizontal:
            bars = ax.barh(pos, vals, height=width, left=base, color=face,
                           label=label, **edges)
            for patch in bars:
                _apply_hatch(patch, hatch)
            ax.set_yticks(pos, ticks)
        else:
            bars = ax.bar(pos, vals, width=width, bottom=base, color=face,
                          label=label, **edges)
            for patch in bars:
                _apply_hatch(patch, hatch)
            ax.set_xticks(pos, ticks)
        return

    # Numeric x can mean thousands of bars: one PolyCollection with ax.bar's geometry,
    # instead of an artist per bar, which is far slower.
    import numpy as np
    from matplotlib.collections import PolyCollection
    ok = ~(np.isnan(pos) | np.isnan(vals))
    x, v, b = pos[ok], vals[ok], np.asarray(base)[ok] if np.size(base) == len(vals) else 0.0
    b = np.broadcast_to(b, x.shape)
    lo, hi = x - width / 2, x + width / 2
    verts = np.stack([np.column_stack([lo, b]), np.column_stack([lo, b + v]),
                      np.column_stack([hi, b + v]), np.column_stack([hi, b])], axis=1)
    if horizontal:
        verts = verts[:, :, ::-1]
    # The bar's drawn width in points, scaled by the data span: nothing is drawn yet, so
    # ax.get_xlim() would still be matplotlib's default (0, 1).
    finite = pos[~np.isnan(pos)]
    data_span = float(finite.max() - finite.min()) if finite.size > 1 else abs(width)
    axes_pt = (ax.get_window_extent().width or 1.0) * 72.0 / ax.figure.dpi
    pt = abs(width) / max(1e-9, data_span) * axes_pt
    narrow = pt < _MIN_HATCH_PT
    textured = None if narrow else (hatch or None)
    coll = PolyCollection(
        verts, facecolors=face, label=label,
        linewidths=_pt(edge_w) if edge else (0 if not textured else _HATCH_EDGE_W),
        edgecolors=edge or ("none" if not textured else _HATCH_COLOR))
    # The whole trace as ONE path, each bar a closed sub-path: the panel's SVG holds
    # one node instead of one per bar. WeasyPrint draws SVG node by node in Python, and
    # the per-position figure's ~3,700 nodes cost it over a second each (2026-11-12).
    if len(verts) > 1:
        from matplotlib.path import Path
        closed = np.concatenate([verts, verts[:, :1]], axis=1)
        codes = np.tile([Path.MOVETO, Path.LINETO, Path.LINETO, Path.LINETO,
                         Path.CLOSEPOLY], len(verts))
        coll.set_verts_and_codes([closed.reshape(-1, 2)], [codes])
    _apply_hatch(coll, textured)
    ax.add_collection(coll)
    # A collection does not autoscale the axes; without this the panel comes out empty.
    if verts.size:
        pts = verts.reshape(-1, 2)
        ax.update_datalim(np.vstack([pts, [[pts[:, 0].min(), 0.0]]]))
        ax.autoscale_view()


def _draw_heatmap(fig, ax, tr: Mapping[str, Any], theme: Theme) -> None:
    import numpy as np
    z = np.asarray([[math.nan if v is None else v for v in row] for row in tr.get("z", [])],
                   dtype=float)
    if z.size == 0:
        return
    x, y = _nums(tr.get("x")), _nums(tr.get("y"))
    if x.size and y.size:
        # plotly reads heatmap x/y as cell centres; the cells' edges are half a cell
        # further out, on an even grid.
        dx = (x[-1] - x[0]) / max(1, x.size - 1)
        dy = (y[-1] - y[0]) / max(1, y.size - 1)
        x_edges = np.linspace(x[0] - dx / 2, x[-1] + dx / 2, z.shape[1] + 1)
        y_edges = np.linspace(y[0] - dy / 2, y[-1] + dy / 2, z.shape[0] + 1)
    else:
        x_edges = np.arange(z.shape[1] + 1) - 0.5
        y_edges = np.arange(z.shape[0] + 1) - 0.5
    cmap = _colorscale(tr["colorscale"], theme, bool(tr.get("reversescale"))) \
        if tr.get("colorscale") else None
    # One vector rectangle per filled cell; an empty cell is not drawn at all. As an
    # image inside the SVG, a viewer smoothed the cells into the transparent empty ones
    # and drew dark fringes. Each cell is edged in its own colour, matplotlib's fix for
    # the hairline seams viewers draw between adjacent fills.
    mesh = ax.pcolor(x_edges, y_edges, np.ma.masked_invalid(z), cmap=cmap,
                     edgecolors="face", linewidth=0.25)
    bar_title = ((tr.get("colorbar") or {}).get("title") or {})
    bar = fig.colorbar(mesh, ax=ax, label=bar_title.get("text")
                       if isinstance(bar_title, Mapping) else None)
    # matplotlib rasterises a colour bar of 50 or more steps; kept vector, seams edged.
    bar.solids.set_rasterized(False)
    bar.solids.set_edgecolor("face")


def _font_family(spec_family: Optional[str]) -> list[str]:
    """The spec's CSS font stack, reduced to families matplotlib has installed.

    matplotlib resolves none of a browser stack's generic names, so the stack is
    filtered and put in front of matplotlib's own sans-serif list. That list is filtered
    too (each missing name logs a ``findfont`` warning); if none of it is installed, its
    last entry is kept so the result is never empty.
    """
    from matplotlib import font_manager, rcParams

    have = {f.name.lower(): f.name for f in font_manager.fontManager.ttflist}

    def installed(names):
        return [have[n.lower()] for n in names if n.lower() in have]

    wanted = ([n.strip().strip('"').strip("'") for n in spec_family.split(",")]
              if spec_family else [])
    fallback = list(rcParams["font.sans-serif"])
    return installed(wanted) + (installed(fallback) or fallback[-1:])


def _apply_font(fig, panels: Sequence[Any], layout: Mapping[str, Any], theme: Theme):
    """Set the panel's text from the spec's font, through the theme.

    ``panels`` are the plot axes, top first; any other axes in the figure is a colour
    bar. Returns ``(size in pt, colour, font families)``.
    """
    font = layout.get("font") or {}
    size = float(font.get("size") or 12) * _FONT_PT_PER_SPEC_UNIT
    colour = theme(font.get("color")) or "#000000"
    families = _font_family(theme.font_family or font.get("family"))

    # Axis titles at the scale's "small" step, one above the tick labels.
    title_size = figspec.TYPE_SCALE_PT["small"]
    for ax in panels:
        for item in (ax.xaxis.label, ax.yaxis.label):
            item.set_fontsize(title_size)
            item.set_color(colour)
            item.set_fontfamily(families)
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            label.set_fontsize(size)
            label.set_color(colour)
            label.set_fontfamily(families)
    for cax in [a for a in fig.axes if a not in panels]:   # a colour bar's own text
        cax.yaxis.label.set_fontsize(size)
        cax.yaxis.label.set_color(colour)
        cax.yaxis.label.set_fontfamily(families)
        for label in cax.get_yticklabels():
            label.set_fontsize(size)
            label.set_color(colour)
            label.set_fontfamily(families)
    return size, colour, families


def _apply_axis(ax, which: str, spec_axis: Mapping[str, Any], theme: Theme) -> None:
    """Map one plotly axis onto matplotlib: scale, range, title, grid, spine, ticks."""
    set_scale = ax.set_xscale if which == "x" else ax.set_yscale
    set_lim = ax.set_xlim if which == "x" else ax.set_ylim
    set_label = ax.set_xlabel if which == "x" else ax.set_ylabel
    mpl_axis = ax.xaxis if which == "x" else ax.yaxis

    is_log = spec_axis.get("type") == "log"
    if is_log:
        set_scale("log")
        if spec_axis.get("dtick") == 1:
            from matplotlib.ticker import LogLocator
            mpl_axis.set_major_locator(LogLocator(base=10.0, numticks=15))

    rng = spec_axis.get("range")
    if rng and len(rng) == 2 and all(v is not None for v in rng):
        lo, hi = float(rng[0]), float(rng[1])
        # plotly gives a log axis range in log10 units; matplotlib wants data units.
        set_lim(10 ** lo if is_log else lo, 10 ** hi if is_log else hi)

    title = spec_axis.get("title")
    if isinstance(title, Mapping) and title.get("text"):
        set_label(_TAG.sub("", title["text"]))

    grid = theme(spec_axis.get("gridcolor"))
    if grid:
        ax.grid(True, axis=which, color=grid, linewidth=0.6, zorder=0)
        ax.set_axisbelow(True)
    edge = theme(spec_axis.get("linecolor"))
    if edge:
        ax.spines["bottom" if which == "x" else "left"].set_color(edge)
    if spec_axis.get("ticks") == "outside":
        ax.tick_params(axis=which, direction="out",
                       length=float(spec_axis.get("ticklen") or 4),
                       color=theme(spec_axis.get("tickcolor")) or edge or "#000000")


def _apply_shapes(axes: Mapping[str, Any], shapes: Sequence[Mapping[str, Any]],
                  theme: Theme) -> None:
    """Draw the spec's line shapes; other shape types are skipped.

    ``xref: "paper"`` spans the plot horizontally (a median, a threshold),
    ``yref: "paper"`` vertically (a quartile); with neither, the line is a segment in
    data coordinates (the iso-length contours). Any other paper-anchored line raises.
    ``axes`` maps each y axis id to its matplotlib axes: a line goes on the panel its
    ``yref`` names (``y`` by default), and a ``yref: "paper"`` line crosses every panel.
    """
    for shape in shapes or ():
        if shape.get("type") != "line":
            continue
        yref = shape.get("yref") or "y"
        if yref != "paper" and yref not in axes:
            raise ValueError(f"figrender cannot place shape {shape!r}: its yref {yref!r} "
                             f"names no axis in the layout")
        targets = list(axes.values()) if yref == "paper" else [axes[yref]]
        style = shape.get("line") or {}
        kw = {
            "color": theme(style.get("color")) or "#000000",
            "linewidth": _pt(float(style.get("width") or 1)),
            "linestyle": {"dot": ":", "dash": "--", "dashdot": "-."}.get(
                style.get("dash"), "-"),
            "zorder": 4,
        }
        x0, x1 = shape.get("x0"), shape.get("x1")
        y0, y1 = shape.get("y0"), shape.get("y1")
        if shape.get("xref") == "paper" and y0 == y1:
            for ax in targets:
                ax.axhline(float(y0), **kw)
        elif yref == "paper" and x0 == x1:
            for ax in targets:
                ax.axvline(float(x0), **kw)
        elif shape.get("xref") != "paper" and yref != "paper":
            for ax in targets:
                ax.plot([float(x0), float(x1)], [float(y0), float(y1)], **kw)
        else:
            raise ValueError(
                f"figrender cannot place shape {shape!r}: it is anchored to paper on one "
                f"axis but is not constant on the other. Drawing it in data coordinates "
                f"would put it somewhere the spec did not ask for.")


def _y_axes(layout: Mapping[str, Any]) -> tuple[list[tuple[str, str]], list[float]]:
    """The y axes a layout declares, top first, as ``(trace axis id, layout key)``, and
    each one's share of the height (its domain's span).

    One unless the layout gives ``yaxis2`` (``yaxis3``, ...) a ``domain``: plotly then
    stacks the axes by domain over one shared x, and paper draws one axes per domain,
    sized by its span. An overlaid second axis (no domain) is refused rather than drawn
    on the first one's scale.
    """
    axes = [("y", "yaxis", tuple((layout.get("yaxis") or {}).get("domain") or (0.0, 1.0)))]
    n = 2
    while isinstance(layout.get(f"yaxis{n}"), Mapping):
        domain = layout[f"yaxis{n}"].get("domain")
        if not domain:
            raise ValueError(f"figrender draws a second y axis only as its own panel: "
                             f"yaxis{n} has no domain")
        axes.append((f"y{n}", f"yaxis{n}", tuple(domain)))
        n += 1
    axes.sort(key=lambda a: -a[2][1])
    return [(axis_id, key) for axis_id, key, _ in axes], [d[1] - d[0] for *_, d in axes]


def draw_panel(traces: Sequence[Mapping[str, Any]], layout: Mapping[str, Any], *,
               theme: Theme = SCREEN, figsize: tuple[float, float] = PANEL_SIZE,
               dpi: int = PANEL_DPI):
    """Draw one panel and return its matplotlib ``(figure, axes)``.

    Separate from :func:`render_spec` so tests can check the artists, not pixels.

    Raises:
        ValueError: on a trace type this renderer does not know. figspec is held to the
            same closed set, so this means the two have diverged.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    y_axes, spans = _y_axes(layout)
    if len(y_axes) == 1:
        fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
        panels = [ax]
    else:
        fig, grid = plt.subplots(len(y_axes), 1, sharex=True, figsize=figsize, dpi=dpi,
                                 gridspec_kw={"height_ratios": spans})
        panels = list(grid)
        ax = panels[0]
    by_id = {axis_id: panel for (axis_id, _), panel in zip(y_axes, panels)}
    face = theme(layout.get("plot_bgcolor"))
    if face:
        for panel in panels:
            panel.set_facecolor(face)
    paper = theme(layout.get("paper_bgcolor"))
    if paper:
        fig.patch.set_facecolor(paper)

    # Stacks are per panel: a bar on a lower panel starts from 0.
    stack_base: dict = {}
    bar_stack: dict = {}
    swatches: dict = {}
    for tr in traces:
        if tr.get("visible") is False:
            continue
        axis_id = tr.get("yaxis") or "y"
        target = by_id.get(axis_id)
        if target is None:
            plt.close(fig)
            raise ValueError(
                f"trace {tr.get('name')!r} is on y axis {axis_id!r}, which the layout "
                f"does not declare; drawn on another panel it would sit on a scale it was "
                f"not meant for.")
        kind = tr.get("type", "scatter")
        if kind == "scatter":
            _draw_scatter(target, tr, theme, stack_base.setdefault(axis_id, {}), swatches)
        elif kind == "bar":
            _draw_bar(target, tr, theme, layout, bar_stack.setdefault(axis_id, {}))
        elif kind == "heatmap":
            _draw_heatmap(fig, target, tr, theme)
        else:
            plt.close(fig)
            raise ValueError(
                f"figrender cannot draw trace type {kind!r}. figspec is guarded to "
                f"scatter/bar/heatmap, so this means the spec builders and this renderer "
                f"have diverged.")

    # One x axis, shared: its ticks and title under the bottom panel only.
    for panel in panels:
        _apply_axis(panel, "x", layout.get("xaxis") or {}, theme)
    for panel in panels[:-1]:
        panel.set_xlabel("")
        panel.tick_params(axis="x", labelbottom=False)
    for (axis_id, key), panel in zip(y_axes, panels):
        _apply_axis(panel, "y", layout.get(key) or {}, theme)
    # Equal aspect when the spec asks for it (the shortfall heatmap's 45-degree ruler).
    if (layout.get("yaxis") or {}).get("scaleanchor") == "x":
        ax.set_aspect(float((layout.get("yaxis") or {}).get("scaleratio") or 1))
    _apply_shapes(by_id, layout.get("shapes") or (), theme)

    for panel in panels:
        for side in ("top", "right"):
            panel.spines[side].set_visible(False)

    _size, colour, families = _apply_font(fig, panels, layout, theme)

    handles, names = [], []
    for panel in panels:
        h, n = panel.get_legend_handles_labels()
        handles += h
        names += n
    labels = [t for t in names if t]
    # As in plotly, an unset showlegend means show; drawn for two or more labels.
    if layout.get("showlegend") is not False and len(set(labels)) > 1:
        # Outside the plot on the right, plotly's default, so it never covers a mark.
        # `loc="best"` can land on the data and is slow on panels with many bars.
        legend_font = (layout.get("legend") or {}).get("font") or {}
        # A filled series' swatch is its fill and line overlaid, as plotly draws it; in
        # print the lines alone cannot tell two filled series apart.
        legend = ax.legend(
            [(swatches[h], h) if h in swatches else h for h in handles], names,
            fontsize=float(legend_font.get("size") or 11) * _FONT_PT_PER_SPEC_UNIT,
            loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False,
            borderaxespad=0.0)
        for text in legend.get_texts():
            text.set_color(theme(legend_font.get("color")) or colour)
            text.set_fontfamily(families)
    return fig, ax


def render_spec(spec: Mapping[str, Any], *, theme: Theme = SCREEN,
                figsize: tuple[float, float] = PANEL_SIZE,
                dpi: int = PANEL_DPI) -> list[Panel]:
    """Render a figspec to one SVG per dropdown option."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    panels: list[Panel] = []
    for label, traces, layout in _panels(spec):
        fig, _ = draw_panel(traces, layout, theme=theme, figsize=figsize, dpi=dpi)
        try:
            name, detail = _text(layout.get("title"))
            fig.tight_layout(pad=0.6)
            buf = io.BytesIO()
            with matplotlib.rc_context(_SVG_RC):
                fig.savefig(buf, format="svg", facecolor=fig.get_facecolor(),
                            bbox_inches="tight", metadata={"Date": None})
            panels.append(Panel(label=label, name=name, detail=detail, svg=buf.getvalue()))
        finally:
            plt.close(fig)
    return panels
