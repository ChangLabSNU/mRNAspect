"""Flow-cell bias voltage from MinKNOW's mux-scan file.

The fixture is a slice of a real run's `pore_scan_data_*.csv` that keeps **every scan**
and reduces only the channel set, so unlike the sequencing-summary fixture its numbers are
the run's own and may be asserted as such: 13 scans, -170 mV ramping to -180 mV.
"""

from __future__ import annotations

import csv

import pytest

from mrnaspect.analysis.modules.voltage import VoltageModule
from .conftest import PORE_SCAN_CSV, _require

COLS = ["channel", "well", "scan_number", "scan_running_voltage_mv",
        "scan_offset_voltage_mv", "seconds_since_start_of_run"]


def _run_dir(tmp_path, rows, cols=COLS, name="pore_scan_data_x_y_z.csv"):
    """A run directory with the mux-scan file where MinKNOW actually puts it."""
    d = tmp_path / "other_reports"
    d.mkdir(exist_ok=True)
    with (d / name).open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    return tmp_path


def _loaded(tmp_path, rows, **params):
    m = VoltageModule(params or None)
    assert m.update_from_run_dir(_run_dir(tmp_path, rows)) is True
    return m.snapshot()


# --------------------------------------------------------------- the real run

@pytest.fixture(scope="module")
def real():
    m = VoltageModule()
    assert m.update_from_run_dir(
        _require(PORE_SCAN_CSV, "pore-scan fixture").parent.parent) is True
    return m.snapshot()


def test_the_run_ramps_from_minus_170_to_minus_180(real):
    assert real["available"] is True
    assert real["n_scans"] == 13
    assert real["first_running_mv"] == -170.0
    assert real["latest_running_mv"] == -180.0


def test_the_ramp_is_signed_so_the_direction_is_never_guessed(real):
    """RNA004 bias voltage is negative: working HARDER is MORE negative, not larger."""
    assert real["ramp_mv"] == -10.0


def test_the_scans_are_ordered_and_spaced_like_mux_scans(real):
    t = [s["t_hours"] for s in real["series"]]
    assert t == sorted(t)
    gaps = [b - a for a, b in zip(t, t[1:])]
    assert all(1.4 < g < 1.7 for g in gaps), f"mux scans ~1.55 h apart, got {gaps}"


def test_the_per_scan_assumption_holds_on_the_real_file(real):
    """Every one of the 13 scans has a single voltage across all its channels and wells."""
    assert real["inconsistent_scans"] == []


# ------------------------------------- the assumption is checked, not trusted

def test_a_scan_whose_wells_disagree_is_reported_not_averaged(tmp_path):
    """The one case this panel would have something to say about must not be smoothed.

    Averaging would turn a flow cell whose wells are being driven differently into a
    plausible single number, which is the failure mode the whole module exists to avoid.
    """
    snap = _loaded(tmp_path, [
        [1, 1, 1, -170, 10, 0],
        [2, 1, 1, -250, 10, 0],      # same scan, different voltage
        [1, 1, 2, -175, 5, 5580],
    ])
    assert snap["inconsistent_scans"] == [1]
    assert snap["series"][0]["running_mv"] in (-170.0, -250.0), "a real value, not a mean"


def test_a_consistent_run_reports_no_inconsistency(tmp_path):
    snap = _loaded(tmp_path, [[c, w, 1, -170, 10, 0] for c in (1, 2) for w in (1, 2)])
    assert snap["inconsistent_scans"] == []
    assert snap["n_scans"] == 1


# ----------------------------------------------------------------- reporting

def test_a_scan_is_timestamped_by_its_earliest_row(tmp_path):
    snap = _loaded(tmp_path, [[1, 1, 1, -170, 10, 900], [2, 1, 1, -170, 10, 300]])
    assert snap["series"][0]["t_hours"] == pytest.approx(300 / 3600, abs=1e-3)


def test_capping_keeps_the_most_recent_scans_but_the_headline_spans_the_run(tmp_path):
    """A live panel is about where the flow cell is NOW; the ramp is about the whole run."""
    rows = [[1, 1, s, -170 - s, 10, s * 5580] for s in range(1, 11)]
    snap = _loaded(tmp_path, rows, max_scans_reported=3)
    assert snap["n_scans"] == 10 and snap["scans_reported"] == 3
    assert [s["scan"] for s in snap["series"]] == [8, 9, 10]
    assert snap["first_running_mv"] == -171.0      # scan 1, though it is not in the series
    assert snap["ramp_mv"] == -9.0                 # -180 - -171


def test_re_reading_is_idempotent(tmp_path):
    d = _run_dir(tmp_path, [[1, 1, 1, -170, 10, 0]])
    m = VoltageModule()
    m.update_from_run_dir(d)
    first = m.snapshot()
    m.update_from_run_dir(d)
    assert m.snapshot() == first


# ------------------------------------------------------------------- guards

def test_a_missing_column_fails_loudly(tmp_path):
    cols = [c for c in COLS if c != "scan_running_voltage_mv"]
    with pytest.raises(ValueError, match="missing column"):
        VoltageModule().update_from_run_dir(
            _run_dir(tmp_path, [[1, 1, 1, 10, 0]], cols=cols))


def test_an_unparseable_value_names_the_line(tmp_path):
    with pytest.raises(ValueError, match=":2:"):
        VoltageModule().update_from_run_dir(
            _run_dir(tmp_path, [[1, 1, 1, "not_a_voltage", 10, 0]]))


def test_absent_file_is_reported_rather_than_raised(tmp_path):
    """Legitimate early in a run: the first mux scan has not happened yet."""
    m = VoltageModule()
    assert m.update_from_run_dir(tmp_path) is False
    snap = m.snapshot()
    assert snap["available"] is False
    assert "no mux scan has happened yet" in snap["unavailable_reason"]


def test_two_files_refuse_to_be_merged(tmp_path):
    d = _run_dir(tmp_path, [[1, 1, 1, -170, 10, 0]], name="pore_scan_data_a.csv")
    _run_dir(tmp_path, [[1, 1, 1, -250, 10, 0]], name="pore_scan_data_b.csv")
    with pytest.raises(ValueError, match="Expected exactly one"):
        VoltageModule().update_from_run_dir(d)


def test_update_after_finalize_is_refused(tmp_path):
    m = VoltageModule()
    m.update_from_run_dir(_run_dir(tmp_path, [[1, 1, 1, -170, 10, 0]]))
    m.finalize()
    with pytest.raises(RuntimeError, match="after finalize"):
        m.update_from_run_dir(tmp_path)


def test_the_snapshot_does_not_depend_on_where_the_run_lives(tmp_path):
    """Same rule as pore_activity and end_reason — see their path-independence tests."""
    rows = [[1, 1, 1, -170, 10, 0]]
    snaps = []
    for sub in ("a", "b/deeper"):
        d = tmp_path / sub
        d.mkdir(parents=True)
        m = VoltageModule()
        m.update_from_run_dir(_run_dir(d, rows, name="pore_scan_data_FC_run_acq.csv"))
        snaps.append(m.snapshot())
    assert snaps[0] == snaps[1]
    assert snaps[0]["source_file"] == "pore_scan_data_FC_run_acq.csv"
