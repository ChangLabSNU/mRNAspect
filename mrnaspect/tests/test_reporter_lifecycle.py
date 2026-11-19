"""CoAReporter contract + lifecycle — ported from the reporter prototype's own test script.

The legacy checks lived in a ``main()``-style script; nothing re-covered the
reporter directly once the package assembled `CoAReporter` verbatim. This ports the genuine
gaps as pytest, driving the *package* reporter (``mrnaspect.reporting.reporter``):

  1. Insufficient mapped reads + force=False -> WARNING file, no PDF, can_force=True.
  2. Same reporter, force=True -> PDF rendered, low_reads=True.
  3. Sufficient mapped reads -> non-empty PDF, low_reads=False.
  4. Interims are non-terminal (never freeze); finalize() freezes; update() after raises.
  5. finalize() insufficient + !force stays un-frozen (driver can re-ask); force renders+freezes.
  6. finalize(bad_reason) -> ValueError.
  7. Sparse snapshots (only run_summary) -> still renders ("Not measured"), never crashes.

The sufficiency gate reads primary MAPPED reads (``mapping.mapped``), so the synthetic
snapshots set that key (not the legacy ``mapping.total``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mrnaspect.reporting.reporter import CoAReporter

MIN_READS = 10_000


def _snapshots(mapped: int) -> dict[str, dict]:
    """Synthetic snapshots shaped like the real module snapshot() outputs.

    Only ``run_summary.mapping.mapped`` drives the gate; the rest exercises the
    render path (degrades gracefully when absent — see test 7).
    """
    total = int(mapped / 0.9)
    per_pos = [0.04] * 20 + [0.30] * 10 + [0.04] * 20  # a hotspot around pos 21-30
    return {
        "run_summary": {
            "mapping": {"total": total, "mapped": mapped,
                        "unmapped": total - mapped, "mapping_rate": 0.9},
        },
        "polya": {"polya_distribution": {"median": 118.0, "Q1": 95.0, "Q3": 140.0}},
        "consensus": {"consensus_identity_pct": 97.3, "mismatch_site_count": 4,
                      "mismatch_rate_by_position": per_pos},
        "error_hotspot": {"mean_total_error_rate": 0.061, "max_total_error_rate": 0.34,
                          "substitution_rate": per_pos},
    }


def _load(reporter: CoAReporter, mapped: int) -> None:
    for name, snap in _snapshots(mapped).items():
        reporter.update(name, snap)


def test_insufficient_no_force_warns_no_pdf(tmp_path: Path) -> None:
    r = CoAReporter(tmp_path / "c1", min_reads=MIN_READS)
    _load(r, 500)
    res = r.report("interim_1")
    assert res["status"] == "insufficient"
    assert res["can_force"] is True
    assert not (tmp_path / "c1" / "report_interim_1.pdf").exists()
    assert (tmp_path / "c1" / "WARNING_interim_1.txt").exists()


def test_insufficient_force_renders_low_reads(tmp_path: Path) -> None:
    r = CoAReporter(tmp_path / "c2", min_reads=MIN_READS)
    _load(r, 500)
    res = r.report("interim_1", force=True)
    assert res["status"] == "report"
    assert res["low_reads"] is True
    assert Path(res["path"]).exists() and Path(res["path"]).stat().st_size > 0


def test_sufficient_renders_pdf(tmp_path: Path) -> None:
    r = CoAReporter(tmp_path / "c3", min_reads=MIN_READS)
    _load(r, 15_000)
    res = r.report("interim_2")
    assert res["status"] == "report" and res["low_reads"] is False
    assert Path(res["path"]).stat().st_size > 1000


def test_interims_non_terminal_finalize_freezes(tmp_path: Path) -> None:
    r = CoAReporter(tmp_path / "c4", min_reads=MIN_READS)
    _load(r, 15_000)
    a = r.report("interim_3")
    b = r.report("interim_4")
    assert Path(a["path"]).name != Path(b["path"]).name
    assert r._finalized is False                 # interims never freeze
    fin = r.finalize("run_end")
    assert fin["status"] == "report" and r._finalized is True
    with pytest.raises(RuntimeError):            # update() after finalize() must raise
        r.update("polya", {})


def test_finalize_insufficient_unfrozen_then_force(tmp_path: Path) -> None:
    r = CoAReporter(tmp_path / "c5", min_reads=MIN_READS)
    _load(r, 500)
    f1 = r.finalize("run_end")
    assert f1["status"] == "insufficient" and r._finalized is False
    f2 = r.finalize("run_end", force=True)
    assert f2["status"] == "report" and r._finalized is True


def test_finalize_bad_reason_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        CoAReporter(tmp_path / "c6").finalize("nope")


def test_sparse_snapshots_still_render(tmp_path: Path) -> None:
    r = CoAReporter(tmp_path / "c7", min_reads=1)
    r.update("run_summary", {"mapping": {"total": 5, "mapped": 5, "mapping_rate": 1.0}})
    res = r.report("sparse")
    assert res["status"] == "report" and Path(res["path"]).exists()
