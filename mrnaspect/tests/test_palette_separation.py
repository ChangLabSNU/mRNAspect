"""The categorical palette, checked the way it is actually read: every pair.

The set was first chosen against ADJACENT pairs, which is the validator's default, and
it passed while slot 4 against slot 1 sat at ΔE 13.7 — under the 15 floor, meaning two
series a full-colour reader cannot reliably tell apart. Adjacency is a property of the
list, not of the chart: the channel-occupancy figure draws "strand" (slot 1) and
"unavailable" (slot 4) as neighbouring bands, so the one pair nobody checked is a pair
a reader sees touching.

The authority is the dataviz skill's `validate_palette.py`, which is not in this repo.
OKLab ΔE is twenty lines and fully specified, so it is implemented here rather than
left to a comment nobody can run — and the expected numbers below are the ones that
script printed, so a drift between the two implementations shows up as a failing number
rather than as silent agreement.
"""

from __future__ import annotations

import itertools
import math

import pytest

from mrnaspect.reporting import figspec

#: Two series below this are hard to tell apart even with full colour vision. The skill
#: calls it a hard floor: secondary encoding does not excuse it, and the remedy for a
#: set that cannot meet it is fewer series, not a louder one.
NORMAL_FLOOR = 15.0
#: Colour-vision-deficient separation. 6 is the floor, 8 the target.
CVD_FLOOR = 8.0

#: Machado 2009 severity-1.0 matrices, as the validator uses them.
_CVD = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216),
               (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413),
               (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602),
               (0.004733, 0.691367, 0.303900)),
}


def _lin(hexcolor: str) -> tuple[float, float, float]:
    def chan(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return tuple(chan(int(hexcolor[i:i + 2], 16) / 255) for i in (1, 3, 5))


def _oklab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    l = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def delta_e(a: str, b: str, kind: str | None = None) -> float:
    """OKLab distance x100, optionally through a colour-vision simulation."""
    def seen(hexcolor: str):
        rgb = _lin(hexcolor)
        if kind is None:
            return rgb
        m = _CVD[kind]
        # Clamped to the displayable range, as the validator does: a saturated colour
        # simulates to negative light, which no screen shows. Unclamped, a vivid violet
        # and blue read ΔE 12.8 apart for a deuteranope where the validator says 7.6
        # (found 2026-11-13 through the pinned numbers below). The previous palettes
        # left the gamut too: clamped, P1's blue-green pair was 7.5 and the dark set's
        # 7.4, under the floor this file sets, which the unclamped version passed.
        return tuple(min(1.0, max(0.0, sum(row[i] * rgb[i] for i in range(3)))) for row in m)
    pa, pb = _oklab(seen(a)), _oklab(seen(b))
    return 100 * math.dist(pa, pb)


def _worst(palette):
    pairs = list(itertools.combinations(palette, 2))
    normal = min((delta_e(a, b), a, b) for a, b in pairs)
    cvd = min((min(delta_e(a, b, k) for k in _CVD), a, b) for a, b in pairs)
    return normal, cvd


def test_every_pair_clears_the_normal_vision_floor():
    (value, a, b), _ = _worst(figspec.CATEGORICAL)
    assert value >= NORMAL_FLOOR, (
        f"{a} and {b} are ΔE {value:.1f} apart, under the {NORMAL_FLOOR} floor. Two "
        f"series this close cannot be told apart by a reader with full colour vision, "
        f"and secondary encoding does not excuse it — re-step the palette or carry "
        f"fewer series.")


def test_every_pair_clears_the_colour_vision_floor():
    _, (value, a, b) = _worst(figspec.CATEGORICAL)
    assert value >= CVD_FLOOR, f"{a} and {b}: ΔE {value:.1f} under simulation"


def test_the_measured_worst_pair_is_the_one_that_was_checked():
    """Pins the numbers the dataviz validator (validate_palette.js, --pairs all) printed
    for the tuned Lagoon set (2026-11-13), so a drift between that script and the
    implementation here shows up rather than passing quietly. It did once: P1's pins
    (blue-plum 18.1, blue-orchid 10.4 CVD) were matched by an implementation that did
    not clamp the simulated colour, as the validator does. The two floors bind on
    different pairs: the lime against the teal in full colour, the blue against the
    violet for a deuteranope."""
    (normal, a, b), (cvd, c, d) = _worst(figspec.CATEGORICAL)
    assert {a, b} == {figspec.CATEGORICAL[1], figspec.CATEGORICAL[3]}
    assert normal == pytest.approx(15.6, abs=0.3)
    assert {c, d} == {figspec.CATEGORICAL[0], figspec.CATEGORICAL[2]}
    assert cvd == pytest.approx(8.4, abs=0.3)


#: Every colour that means a STATUS, as a mark or as text. The surfaces are left out: they
#: are a banner's background, never set beside a series.
_STATUS = {name: getattr(figspec, name) for name in (
    "STATUS_WARNING", "STATUS_WARNING_INK", "STATUS_WARNING_FILL", "STATUS_SERIOUS",
    "STATUS_SERIOUS_INK", "STATUS_SERIOUS_FILL", "STATUS_GOOD", "EMPHASIS")}


@pytest.mark.parametrize("status", sorted(_STATUS))
def test_no_series_can_be_mistaken_for_a_status(status):
    """Why the palette changed on 2026-10-31. The raspberry slot 3 sat ΔE 8.5 from the
    serious text and 9.3 from the serious fill, so a series could read as a warning, and
    EMPHASIS reached only 12.8 against it, a shortfall a test here pinned as deliberate.
    P1 was searched against this bar; EMPHASIS is now 15.3 from the plum, its nearest."""
    colour = _STATUS[status]
    near = min(figspec.CATEGORICAL, key=lambda slot: delta_e(colour, slot))
    assert delta_e(colour, near) >= NORMAL_FLOOR, (
        f"{status} {colour} is ΔE {delta_e(colour, near):.1f} from the series {near}, "
        f"under the {NORMAL_FLOOR} floor")


def test_nothing_marked_as_emphasis_is_identified_by_colour_alone():
    """Colour now tells EMPHASIS from every series; it is still not the only cue. Asserted
    on the specs rather than assumed."""
    snapshot = {"substitution_rate": [0.1, 0.9], "deletion_rate": [0.0, 0.0],
                "insertion_rate": [0.0, 0.0]}
    consensus = {"mismatch_threshold": 0.5,
                 "mismatch_sites": [{"pos": 1, "mismatch_rate": 0.9, "ref_base": "A",
                                     "depth": 10, "base_counts": {"A": 1, "G": 9}}]}
    specs = [figspec.per_position_spec([("A", snapshot, consensus)])]
    marked = [t for spec in specs for t in spec["data"]
              if isinstance(t.get("meta"), dict) and t["meta"].get("emphasis")]
    assert marked, "no trace carries the emphasis tag; this test is checking nothing"
    for trace in marked:
        assert (trace.get("marker") or {}).get("symbol"), (
            f"{trace.get('name')!r} is emphasised by colour alone")
        assert trace.get("name"), (
            f"an emphasised trace with no name has no legend entry: {trace}")
