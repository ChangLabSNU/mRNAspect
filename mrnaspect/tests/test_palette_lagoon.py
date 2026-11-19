"""The palette chosen on 2026-11-13: "05 Lagoon" from the QC-metric palette sheet
(length sky blue, poly(A) violet, accuracy teal, composition lime), to be fine-tuned
rather than copied.

As drawn, no sheet palette clears this directory's gates (test_palette_separation,
test_gui_dark_theme, test_result_colours): Lagoon's dark teal sat ΔE 12.1 from its sky
blue and 6.4 from the input screen's green check mark, its dark violet 5.2 from the sky
blue for a deuteranope, its light olive 12.3 from its teal. Each colour was moved the
least that clears every gate with a margin (OKLab search, review3/palette/tune*.py in
the session's scratchpad), and the dark check-mark green moved too (#43dd9a ->
#32ab5d), so the teal and the lime did not have to move further. This file holds the
result to the sheet: a later change that drifts far from it is a new choice, to
be made as one. The gates use the validator's CVD measure, which clamps a
simulated colour to the screen's range (test_palette_separation.delta_e).

Two of the validator's style checks stay unmet, both as on the sheet: the light teal's
chroma is 0.095 (floor 0.10; the sheet's was 0.099), and the dark four are lighter than
its dark band (OKLCH L 0.69-0.85 against 0.48-0.67), Lagoon's own bright tone.
"""

from __future__ import annotations

import pytest

from mrnaspect.reporting import figspec
from mrnaspect.tests.test_palette_separation import delta_e

#: The sheet's hexes, in Core's slot order: 1 length, 2 composition, 3 poly(A), 4 accuracy.
LAGOON = {
    "light": ("#0284c7", "#6f8515", "#7c3aed", "#008577"),
    "dark": ("#38bdf8", "#b8d967", "#a78bfa", "#2dd4bf"),
}
#: The largest move the fine-tune took (the dark lime, ΔE 6.2).
MOST = 7.0


@pytest.mark.parametrize("theme, palette", [("light", figspec.CATEGORICAL),
                                            ("dark", figspec.CATEGORICAL_DARK)])
def test_every_slot_is_the_sheet_s_colour_moved_a_little(theme, palette) -> None:
    for slot, (got, want) in enumerate(zip(palette, LAGOON[theme]), 1):
        assert delta_e(got, want) <= MOST, (theme, slot, got, want, round(delta_e(got, want), 1))


def test_the_slots_keep_the_sheet_s_metrics() -> None:
    """The sheet's metric for each colour is the result type wearing it here."""
    rc = figspec.RESULT_COLOURS
    assert rc["read-length"] == rc["full-length"] == figspec.CATEGORICAL[0]
    assert rc["constructs"] == figspec.CATEGORICAL[1]
    assert rc["polya"] == figspec.CATEGORICAL[2]
    assert rc["quality"] == rc["identity"] == figspec.CATEGORICAL[3]
