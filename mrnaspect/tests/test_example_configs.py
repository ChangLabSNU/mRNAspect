"""The stock-vs-FT example configs name the models that were released and checked.

Until 2026-10-28 both were headed STALE: pinned to train #4 -- never released -- and to
hac@v5.2.0 to match it, and the fine-tuned side was left unpinned because nobody had
checked that dorado-server 7.13.6 loads the released model. It does (one run-1 POD5,
1,275 reads, all with pt, RG naming the v1 folder), so the pair now names stock
hac@v5.3.0 and that folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mrnaspect.analysis.polya_calibration import resolve_model
from mrnaspect.main import load_config

CONFIG = Path(__file__).resolve().parents[1] / "config"
PAIR = {"compare_stock_m1psi.yaml": "rna004_130bps_hac@v5.3.0",
        "compare_ft_m1psi.yaml": "m1psi_tuned_v1"}


@pytest.mark.parametrize("name,released", sorted(PAIR.items()))
def test_each_side_names_a_registered_model(name, released, qk_ha_ref) -> None:
    cfg = load_config([str(CONFIG / name)], {"reference": str(qk_ha_ref)})
    folder = Path(cfg["basecall"]["model"]).name
    canonical, constants = resolve_model(folder)
    assert canonical == released, (name, folder)
    assert constants is not None, "a model with no poly(A) constants gets no correction"


@pytest.mark.parametrize("name", sorted(PAIR))
def test_each_side_declares_the_arm(name, qk_ha_ref) -> None:
    """Without one the corrected poly(A) -- half of what the comparison is for -- is
    withheld on both sides."""
    cfg = load_config([str(CONFIG / name)], {"reference": str(qk_ha_ref)})
    assert cfg["sample"]["arm"] == "m1psi"


@pytest.mark.parametrize("name", sorted(PAIR))
def test_nothing_stale_is_left(name) -> None:
    text = (CONFIG / name).read_text()
    for gone in ("STALE", "train #4", "train4", "v5.2.0"):
        assert gone not in text, (name, gone)


def test_both_sides_align_the_same_way(qk_ha_ref) -> None:
    flags = {name: load_config([str(CONFIG / name)], {"reference": str(qk_ha_ref)})[
        "align"]["minimap2_flags"] for name in PAIR}
    assert len(set(flags.values())) == 1, flags
