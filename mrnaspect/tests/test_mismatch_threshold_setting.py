"""The flag threshold is a setting the operator can change, and the docs say which are.

Decided 2026-10-28 (content audit, item 8), for the variant threshold: it was
a constructor argument, default 0.10, that no config could reach -- `module_params.consensus`
refused it as an unknown key -- while the report's figure printed "called at >= 0.1" as
though it were a fixed rule, and nothing documented `module_params` at all.

Renamed by the decision of 2026-11-04: `variant_freq_threshold` (one alternative's
share of the reads) became `mismatch_threshold` (the share of reads that do not carry the
reference base), when the variant list and the low-consensus list became one flag list.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mrnaspect.analysis import read_stats
from mrnaspect.analysis.modules import consensus, error_hotspot, mapq, polya, terminal_reach
from mrnaspect.analysis.modules.consensus import ConsensusIdentityModule
from mrnaspect.analysis.shared_pileup import _COL_DEL, SharedPileup

from .test_polya_reaches_the_report import ARMS, _run

ROOT = Path(__file__).resolve().parents[2]
MODULE_DEFAULTS = {
    "consensus": consensus.DEFAULTS, "polya": polya.DEFAULTS,
    "error_hotspot": error_hotspot.DEFAULTS, "mapq": mapq.DEFAULTS,
    "terminal_reach": terminal_reach.DEFAULTS, "run_summary": read_stats.DEFAULTS,
}


def _pileup() -> SharedPileup:
    """'ACGT' at depth 100: position 1 (ref C) has 30 reads calling A, position 2 (ref G)
    has 15 deleted. So mismatch rates of 0.30 and 0.15 there, 0 elsewhere."""
    p = SharedPileup("synthetic", "ACGT")
    p.depth[:] = 100
    p.counts[0, 0] = 100
    p.counts[1, 1], p.counts[1, 0] = 70, 30
    p.counts[2, 2], p.counts[2, _COL_DEL] = 85, 15
    p.counts[3, 3] = 100
    p._read_count = 100
    return p


def _flagged(config=None) -> list[tuple[int, float]]:
    snap = ConsensusIdentityModule(_pileup(), config=config).snapshot()
    return [(s["pos"], s["mismatch_rate"]) for s in snap["mismatch_sites"]]


def test_the_default_is_a_majority() -> None:
    """0.5: below a majority, nanopore basecalling error dominates."""
    assert consensus.DEFAULTS["mismatch_threshold"] == 0.5
    assert _flagged() == []
    assert _flagged({"mismatch_threshold": 0.1}) == [(1, 0.3), (2, 0.15)]


def test_the_config_sets_it() -> None:
    assert _flagged({"mismatch_threshold": 0.2}) == [(1, 0.3)]
    snap = ConsensusIdentityModule(_pileup(), config={"mismatch_threshold": 0.2}).snapshot()
    assert snap["mismatch_threshold"] == 0.2, "the snapshot states the value it used"


@pytest.mark.parametrize("value", [0, -0.1, 1.5, 10])
def test_a_threshold_outside_0_to_1_is_refused(value) -> None:
    """10 is the likely slip -- a percentage where a fraction belongs -- and flags nothing;
    0 would flag every position."""
    with pytest.raises(ValueError, match="mismatch_threshold"):
        ConsensusIdentityModule(_pileup(), config={"mismatch_threshold": value})


def test_it_reaches_the_module_and_the_figure_through_the_cli(
        tmp_path, variant_bam, qk_ha_ref) -> None:
    """Through --config on a real BAM: QK-09 flags more positions at 0.1 than at the
    default 0.5, every default one at or above 0.5, and the figure states 0.5."""
    (tmp_path / "default").mkdir()
    (tmp_path / "lowered").mkdir()
    default = _run(tmp_path / "default", variant_bam, qk_ha_ref, ARMS)
    lowered = _run(tmp_path / "lowered", variant_bam, qk_ha_ref,
                   ARMS + "module_params:\n  consensus:\n    mismatch_threshold: 0.1\n")

    def qk09(run: Path) -> dict:
        snaps = json.loads((run / "latest_snapshot.json").read_text())["snapshots"]
        return snaps["construct:QK-09"]["consensus"]

    low, at_default = qk09(lowered), qk09(default)
    assert any(0.1 <= s["mismatch_rate"] < 0.5 for s in low["mismatch_sites"]), (
        "the fixture must have a position only the lowered threshold flags, or this "
        "proves nothing")
    assert at_default["mismatch_threshold"] == 0.5
    assert all(s["mismatch_rate"] >= 0.5 for s in at_default["mismatch_sites"])
    assert len(at_default["mismatch_sites"]) < len(low["mismatch_sites"])
    figure = {f["id"]: f for f in json.loads((default / "figures.json").read_text())}
    assert "at mismatch ≥ 0.5" in json.dumps(figure["per-position"]["spec"], ensure_ascii=False)


def test_the_report_calls_it_user_adjustable(tmp_path, variant_bam, qk_ha_ref) -> None:
    run = _run(tmp_path, variant_bam, qk_ha_ref, ARMS)
    caption = {f["id"]: f for f in json.loads((run / "figures.json").read_text())}[
        "per-position"]["caption"]
    assert "user-adjustable" in caption
    assert "module_params.consensus.mismatch_threshold" in caption


# --- the documentation names only settings that exist ------------------------------------

def _section(text: str, heading: str) -> str:
    start = text.index(f"## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start: end if end != -1 else len(text)]


def test_every_setting_the_docs_name_is_one_a_module_has() -> None:
    """A documented key the module does not have is refused at run time -- the operator
    would follow the docs into an error."""
    doc = _section((ROOT / "docs" / "config_reference.md").read_text(), "module_params")
    named = re.findall(r"`(\w+)\.(\w+)`", doc)
    named = [(m, k) for m, k in named if m in MODULE_DEFAULTS]
    assert ("consensus", "mismatch_threshold") in named
    missing = [f"{m}.{k}" for m, k in named if k not in MODULE_DEFAULTS[m]]
    assert not missing, missing


def test_the_documented_defaults_are_the_modules_own() -> None:
    doc = _section((ROOT / "docs" / "config_reference.md").read_text(), "module_params")
    rows = re.findall(r"^\| `(\w+)\.(\w+)` \| ([^|]+) \|", doc, re.M)
    assert rows
    for module, key, default in rows:
        want = MODULE_DEFAULTS[module][key]
        shown = default.strip()
        assert (shown == "none") if want is None else float(shown) == float(want), (
            module, key, shown, want)


def test_the_shipped_config_points_at_the_docs() -> None:
    """The comment that introduces module_params, not the file's header, which names the
    same document for every other setting."""
    lines = (ROOT / "mrnaspect" / "config" / "run_config.yaml").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("# module_params:"))
    block = []
    for line in lines[start:]:
        if not line.startswith("#"):
            break
        block.append(line)
    block = "\n".join(block)
    assert 'docs/config_reference.md, "module_params"' in block
    assert "mismatch_threshold" in block
