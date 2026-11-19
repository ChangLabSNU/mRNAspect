"""dorado's own poly(A) constants, for the model that is actually released.

DORADO_POLYA_CONSTANTS gives the speed and offset correction dorado applied when it called
`pt`; the offset is the shortest tail it can resolve. Until 2026-10-28 the table carried
train #4 -- never released -- and not the released model, and looked names up as written,
not through the aliases the ruler correction uses. Every run basecalled with v1 therefore
logged "not in DORADO_POLYA_CONSTANTS ... the left-censoring boundary is withheld" once per
construct, and qc_result.json's model_offset_correction_nt was null.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mrnaspect.analysis.modules.polya import DORADO_POLYA_CONSTANTS, dorado_constants
from mrnaspect.analysis.polya_calibration import ALIASES, CONSTANTS

from .conftest import lab_artifact



@pytest.mark.parametrize("name", ["m1psi_tuned_v1", *sorted(
    k for k, v in ALIASES.items() if v == "m1psi_tuned_v1")])
def test_every_name_of_the_released_model_resolves(name) -> None:
    """A BAM names the folder it was basecalled with: train8s_..._ov2500 on the test input."""
    assert dorado_constants(name) == {"speed_correction": 0.92, "offset_correction": 10.88}


def test_every_model_with_ruler_constants_has_dorado_constants() -> None:
    """The two tables describe the same models; one without the other is half a model."""
    assert set(CONSTANTS) <= set(DORADO_POLYA_CONSTANTS), (
        set(CONSTANTS) - set(DORADO_POLYA_CONSTANTS))


def test_an_unknown_model_still_gets_none() -> None:
    assert dorado_constants("someone_elses_model") is None
    assert dorado_constants(None) is None


def test_the_v1_values_are_the_model_folder_s_own() -> None:
    """Read from the artifact, not restated from memory. The exported v1 folder is on the
    lab's machines only, so this runs there (conftest ``lab_artifact``)."""
    config = lab_artifact("v1_model_folder") / "config.toml"
    if not config.exists():
        pytest.skip("the exported v1 folder is not on this machine")
    toml = config.read_text()
    block = toml[toml.index("[poly_a]"):]
    block = block[:block.index("\n[", 1)]
    got = {k: float(v) for k, v in re.findall(r"^(speed_correction|offset_correction)\s*=\s*"
                                                r"([0-9.]+)", block, re.M)}
    assert DORADO_POLYA_CONSTANTS["m1psi_tuned_v1"] == got
