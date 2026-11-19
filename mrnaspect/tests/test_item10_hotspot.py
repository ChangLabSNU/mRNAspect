"""Item 10, as decided on 2026-10-28: positions are RANKED by error, not flagged.

Until then a position was a "hotspot" when its total error beat a per-k-mer null by 0.05,
and the only null was a flat 0.05 dummy -- so a hotspot was any position with error above
0.10: 290 of QK-10's 1,166 rankable positions on run 1 under v1. Decided: judge against
the fine-tuned model's own expected error, and until that exists, rank.

Without a null the deep-enough positions are ranked by observed total error. With one --
registered by (released model, arm) in NULL_TABLES, its header naming the same model and
arm -- by the residual over it. None is registered yet, so the null half is exercised with
obviously synthetic tables, written to tmp_path and never shipped.
"""

from __future__ import annotations

import itertools
import json

import pytest

from mrnaspect.analysis.modules import error_hotspot
from mrnaspect.analysis.modules.error_hotspot import (
    NULL_TABLES, ErrorHotspotModule, _load_null_table)
from mrnaspect.analysis.shared_pileup import SharedPileup

from .test_polya_reaches_the_report import ARMS, _run

MODEL, ARM = "m1psi_tuned_v1", "m1psi"
STOCK = "rna004_130bps_hac@v5.3.0"


def _pileup() -> SharedPileup:
    """Ref 'AACGTAA' (7 nt), depth 20 everywhere.

    pos3 (ref G, 5-mer ACGTA): all 20 reads call A -> total error 1.0.
    pos2 (ref C, 5-mer AACGT): 10 of 20 call A     -> total error 0.5.
    Every other position matches. Centered 5-mers exist only for positions 2, 3, 4.
    """
    p = SharedPileup("synthetic", "AACGTAA")
    for pos, col in {0: 0, 1: 0, 4: 3, 5: 0, 6: 0}.items():
        p.counts[pos, col] = 20
        p.depth[pos] = 20
    p.counts[3, 0] = 20          # ref G, called A
    p.depth[3] = 20
    p.counts[2, 1] = 10          # ref C: half C ...
    p.counts[2, 0] = 10          # ... half A
    p.depth[2] = 20
    p._read_count = 20
    return p


def _module(config=None, **kw) -> ErrorHotspotModule:
    return ErrorHotspotModule(
        _pileup(), config={"min_depth": 1, "hotspot_min_depth": 1, **(config or {})}, **kw)


# --- without a null: a ranking by observed error ----------------------------------------

def test_positions_are_ranked_by_observed_total_error() -> None:
    snap = _module().snapshot()
    assert snap["ranked_by"] == "observed_total_error"
    top = snap["top_positions"]
    assert [(h["pos"], h["observed_error_rate"]) for h in top[:2]] == [(3, 1.0), (2, 0.5)]
    assert len(top) == 7, "every deep-enough position is ranked, the edges included"
    assert "residual" not in top[0]


def test_nothing_is_flagged_and_nothing_counts_hotspots() -> None:
    snap = _module().snapshot()
    for gone in ("hotspots", "hotspot_count", "hotspot_threshold", "null_table"):
        assert gone not in snap, gone


def test_top_n_caps_the_list() -> None:
    assert [h["pos"] for h in _module({"hotspot_top_n": 1}).snapshot()["top_positions"]] == [3]
    assert _module({"hotspot_top_n": 0}).snapshot()["top_positions"] == []


@pytest.mark.parametrize("key,value", [("hotspot_threshold", 0.05),
                                       ("null_table", "/x/flat_0.05.k5.tsv")])
def test_a_config_that_still_sets_the_old_knobs_is_refused(key, value) -> None:
    """Ignored quietly, a threshold the operator set would read as applied."""
    with pytest.raises(ValueError, match="unknown parameter"):
        _module({key: value})


def test_without_a_model_the_reason_says_so() -> None:
    snap = _module().snapshot()
    assert snap["null"] is None
    assert snap["null_reason"] == "the reads name no basecall model, so no null can be chosen"


def test_no_null_is_registered_for_any_model_yet() -> None:
    """The premise of every report today. Registering one should fail this on purpose."""
    assert NULL_TABLES == {}
    snap = _module(model_of=lambda: MODEL, arm=ARM).snapshot()
    assert snap["null"] is None
    assert snap["null_reason"] == f"no null is registered for {MODEL}"


# --- the null slot, with synthetic tables -------------------------------------------------

def _register(monkeypatch, tmp_path, *, key=(MODEL, ARM), model=MODEL, arm=ARM,
              acgta=0.99) -> None:
    """A synthetic null: 0.0 for every 5-mer but ACGTA, which is expected at ``acgta``."""
    rows = [f"# model: {model}", f"# arm: {arm}", "kmer\texpected_error"]
    for t in itertools.product("ACGT", repeat=5):
        kmer = "".join(t)
        rows.append(f"{kmer}\t{acgta if kmer == 'ACGTA' else 0.0}")
    (tmp_path / "synthetic.k5.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(error_hotspot, "_NULL_DIR", tmp_path)
    monkeypatch.setitem(NULL_TABLES, key, "synthetic.k5.tsv")


def test_with_a_null_positions_are_ranked_by_the_residual_over_it(monkeypatch, tmp_path):
    """pos3 has the higher error, 1.0, but its context is expected at 0.99; pos2's 0.5 is
    all excess. Against the null pos2 comes first -- the reverse of the observed order."""
    _register(monkeypatch, tmp_path)
    snap = _module(model_of=lambda: MODEL, arm=ARM).snapshot()
    assert snap["ranked_by"] == "residual_over_null"
    assert snap["null"] == {"model": MODEL, "arm": ARM, "table": "synthetic.k5.tsv"}
    assert snap["null_reason"] is None
    top = snap["top_positions"]
    assert [h["pos"] for h in top] == [2, 3, 4], "edges have no centered 5-mer"
    assert (top[0]["kmer"], top[0]["residual"]) == ("AACGT", 0.5)
    assert (top[1]["null_expected"], top[1]["residual"]) == (0.99, 0.01)


@pytest.mark.parametrize("model,arm", [(STOCK, ARM), (MODEL, "U")])
def test_a_null_whose_header_names_another_model_or_arm_is_refused(
        monkeypatch, tmp_path, model, arm) -> None:
    _register(monkeypatch, tmp_path, model=model, arm=arm)
    with pytest.raises(ValueError, match="header says"):
        _module(model_of=lambda: MODEL, arm=ARM).snapshot()


def test_a_construct_with_no_arm_gets_no_null_and_is_told_why(monkeypatch, tmp_path):
    _register(monkeypatch, tmp_path)
    snap = _module(model_of=lambda: MODEL, arm=None).snapshot()
    assert snap["null"] is None and snap["ranked_by"] == "observed_total_error"
    assert snap["null_reason"] == "no arm declared for this construct, and a null is per arm"


def test_the_other_arm_s_null_is_not_used(monkeypatch, tmp_path):
    _register(monkeypatch, tmp_path)
    snap = _module(model_of=lambda: MODEL, arm="U").snapshot()
    assert snap["null"] is None
    assert snap["null_reason"] == f"no null is registered for {MODEL} on the U arm"


def test_a_missing_or_ragged_table_fails_loud(monkeypatch, tmp_path):
    monkeypatch.setattr(error_hotspot, "_NULL_DIR", tmp_path)
    monkeypatch.setitem(NULL_TABLES, (MODEL, ARM), "absent.tsv")
    with pytest.raises(FileNotFoundError, match="null table not found"):
        _module(model_of=lambda: MODEL, arm=ARM).snapshot()
    bad = tmp_path / "bad.tsv"
    bad.write_text("kmer\texpected_error\nAAAAA\t0.05\nAAA\t0.05\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ragged"):
        _load_null_table(str(bad))


# --- through the CLI: the engine hands over the reads' model and the construct's arm ---

def test_the_engine_passes_the_reads_model_and_each_construct_s_arm(
        monkeypatch, tmp_path, variant_bam, qk_ha_ref) -> None:
    """The tests above pass model_of and arm by hand, so they cannot see the engine fail to.
    The fixture's reads name stock hac@v5.3.0; the arms are QK-09 m1psi, HA-WT U. A null
    for (stock, m1psi) must reach QK-09 and only QK-09."""
    nulls = tmp_path / "nulls"
    nulls.mkdir()
    _register(monkeypatch, nulls, key=(STOCK, "m1psi"), model=STOCK, arm="m1psi")
    run = _run(tmp_path, variant_bam, qk_ha_ref, ARMS)
    snaps = json.loads((run / "latest_snapshot.json").read_text())["snapshots"]
    qk, ha = (snaps[f"construct:{c}"]["error_hotspot"] for c in ("QK-09", "HA-WT"))
    assert qk["ranked_by"] == "residual_over_null" and qk["null"]["model"] == STOCK
    assert ha["null"] is None
    assert ha["null_reason"] == f"no null is registered for {STOCK} on the U arm"


def test_a_real_run_carries_a_ranking_and_no_hotspot_count(
        tmp_path, variant_bam, qk_ha_ref) -> None:
    run = _run(tmp_path, variant_bam, qk_ha_ref, ARMS)
    text = (run / "qc_result.json").read_text()
    assert "hotspot_count" not in text and "hotspot_threshold" not in text
    snaps = json.loads((run / "latest_snapshot.json").read_text())["snapshots"]
    qk = snaps["construct:QK-09"]["error_hotspot"]
    assert qk["null_reason"] == f"no null is registered for {STOCK}"
    rates = [h["observed_error_rate"] for h in qk["top_positions"]]
    assert rates and rates == sorted(rates, reverse=True)


# --- the two gates are separate -----------------------------------------------------------

def test_summary_gate_and_ranking_gate_are_independent() -> None:
    """Describe at min_depth, rank at hotspot_min_depth.

    The depth-20 pileup sits between the two gates: deep enough for its rates to be
    REPORTED, not deep enough to be RANKED. A single shared gate cannot express that, and
    both single-gate settings are wrong -- at 50 the whole error profile vanishes on a
    sub-50x run (measured: covered_positions 2460 -> 0 on the 166-read fixture), at 10
    sampling noise wins the top N.
    """
    snap = ErrorHotspotModule(
        _pileup(), config={"min_depth": 1, "hotspot_min_depth": 50}).snapshot()
    assert snap["covered_positions"] > 0
    assert snap["mean_total_error_rate"] is not None
    assert snap["total_error_rate"][3] == 1.0
    assert snap["flaggable_positions"] == 0
    assert snap["top_positions"] == []
    assert snap["hotspot_min_depth"] == 50, "so an empty list can be told from a clean one"


def test_ranking_gate_below_summary_gate_is_refused() -> None:
    with pytest.raises(ValueError, match="hotspot_min_depth"):
        ErrorHotspotModule(_pileup(), config={"min_depth": 20, "hotspot_min_depth": 5})
