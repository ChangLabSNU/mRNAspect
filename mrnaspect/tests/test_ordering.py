"""Input discovery must be in acquisition order, not lexicographic order (C1).

The bug this guards is silent by construction: it cannot be seen on any fixture with
fewer than 10 inputs, which is every fixture in this suite and the 9-file replay set.
So the tests build the >= 10 case explicitly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mrnaspect.tests.conftest import pod5_ready
from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.runtime.ordering import natural_key, natural_sorted


def test_lexicographic_order_is_actually_wrong() -> None:
    """Pin the bug itself, so the fix cannot be reverted without a failure.

    If this ever passes, ``sorted`` became natural and the helper is redundant.
    """
    names = [f"SIM_run_acq_{i}.pod5" for i in range(12)]
    assert sorted(names) != names
    # Lexicographic gives _0, _1, _10, _11, _2, ... — the 11th batch of the run is
    # processed 3rd, and the 2nd is processed 5th.
    assert sorted(names)[2] == "SIM_run_acq_10.pod5"
    assert sorted(names).index("SIM_run_acq_2.pod5") == 4


def test_natural_sorted_recovers_acquisition_order() -> None:
    names = [f"SIM_run_acq_{i}.pod5" for i in range(12)]
    shuffled = [Path(n) for n in sorted(names)]
    assert [p.name for p in natural_sorted(shuffled)] == names


def test_natural_key_never_compares_int_to_str() -> None:
    """Mixed shapes must sort without a TypeError.

    ``re.split`` on a capturing group guarantees the str/int alternation the key relies
    on; this is the test that would catch a "clever" rewrite that breaks it.
    """
    messy = ["a", "1", "a1", "1a", "", "x10y2", "x2y10", "_0.pod5", "10", "007", "7"]
    assert len(sorted(messy, key=natural_key)) == len(messy)  # no TypeError


def test_zero_padding_is_deterministic() -> None:
    """``_02`` and ``_2`` tie on the numeric part; the raw-name tiebreaker decides."""
    a = natural_sorted([Path("x_02.bam"), Path("x_2.bam")])
    b = natural_sorted([Path("x_2.bam"), Path("x_02.bam")])
    assert [p.name for p in a] == [p.name for p in b]


@pytest.mark.parametrize("mode,ext", [("pod5", "pod5"), ("bam", "bam")])
def test_discover_inputs_is_natural_for_both_modes(
    tmp_path: Path, curlcake_ref: Path, mode: str, ext: str
) -> None:
    """The driver itself — not just the helper — returns acquisition order."""
    in_dir = tmp_path / "input"
    in_dir.mkdir()
    for i in range(12):
        (in_dir / f"SIM_run_acq_{i}.{ext}").write_bytes(b"")

    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": mode, "min_reads": 1})
    if mode == "pod5":
        pod5_ready(cfg)   # dorado client + model have no defaults (bucket A-3)
    pipe = OfflinePipeline(in_dir, tmp_path / "out", cfg)
    assert [p.name for p in pipe._discover_inputs()] == [
        f"SIM_run_acq_{i}.{ext}" for i in range(12)]


def test_discover_inputs_still_fails_loudly_when_empty(
    tmp_path: Path, curlcake_ref: Path
) -> None:
    in_dir = tmp_path / "input"
    in_dir.mkdir()
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam", "min_reads": 1})
    pipe = OfflinePipeline(in_dir, tmp_path / "out", cfg)
    with pytest.raises(FileNotFoundError, match="No .*bam"):
        pipe._discover_inputs()
