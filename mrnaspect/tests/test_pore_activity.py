"""Pore occupancy from MinKNOW's ``pore_activity_*.csv``.

Two kinds of test here, on purpose. The happy path runs against a **real** 20.2 h Mk1D
file (``conftest.PORE_ACTIVITY_CSV``) because the format belongs to MinKNOW, not to us,
and a synthetic fixture can only confirm that the parser agrees with the parser's author.
The guards run against tiny synthetic CSVs, where the discriminating case can be
constructed exactly.
"""

from __future__ import annotations

import pytest

from mrnaspect.analysis.modules.pore_activity import PoreActivityModule
from .conftest import PORE_ACTIVITY_CSV, _require


# Measured from the fixture: 512 channels x 60 s x 4000 Hz. Written out rather than
# recomputed so a change in any factor shows up as a failing number, not a moved goalpost.
FULL_MINUTE_SAMPLES = 122_880_000


def _run_dir(tmp_path, rows, name="pore_activity_x_y_z.csv"):
    """A directory holding one synthetic pore-activity CSV. ``rows`` are raw text lines."""
    (tmp_path / name).write_text(
        "Channel State,Experiment Time (minutes),State Time (samples)\n"
        + "".join(r + "\n" for r in rows))
    return tmp_path


def _loaded(tmp_path, rows, **params):
    m = PoreActivityModule(params or None)
    assert m.update_from_run_dir(_run_dir(tmp_path, rows)) is True
    return m.snapshot()


# --------------------------------------------------------------- the real file

@pytest.fixture(scope="module")
def real():
    m = PoreActivityModule()
    assert m.update_from_run_dir(
        _require(PORE_ACTIVITY_CSV, "pore-activity fixture").parent) is True
    return m.snapshot()


def test_the_denominator_is_measured_total_channel_time(real):
    """The full-minute total is derived from the file, not assumed from hardware.

    It must land on 512ch x 60s x 4000Hz exactly — if it does not, `State Time (samples)`
    is not aggregate channel-time and every fraction this module reports is mis-normalised.
    """
    assert real["samples_per_full_minute"] == FULL_MINUTE_SAMPLES


def test_real_run_shape_and_headline(real):
    assert real["available"] is True
    assert real["n_minutes"] == 1210
    assert (real["first_minute"], real["last_minute"]) == (0, 1209)
    assert len(real["states"]) == 17
    # A healthy run: pores spend most of their time reading a molecule.
    assert real["strand_fraction"] == pytest.approx(0.8777, abs=5e-4)


def test_fractions_partition_the_run(real):
    total = sum(v["fraction"] for v in real["states"].values())
    assert total == pytest.approx(1.0, abs=1e-4)  # 17 independent 6-dp roundings


def test_only_the_final_minute_is_partial(real):
    """The run ended part-way through minute 1209; every earlier minute is whole."""
    assert real["partial_minutes"] == [1209]


def test_the_truncated_final_bin_is_not_rendered_as_a_collapse(real):
    """The trap this module exists to avoid, pinned on real data.

    The last bin holds 4 of 6 minutes and one of those is itself partial, so it carries
    54% of a full bin's channel-time. Dividing by the NOMINAL bin total would scale its
    strand share down by that same 54% and draw a cliff at the right-hand edge of the
    chart — a process fact (the bin is not over) rendered as a data fact (the pores
    emptied). In a live run the newest bin is always in this state, so the cliff would
    appear at every refresh.
    """
    ser = real["series"]
    coverage, strand = ser["coverage"][-1], ser["fractions"]["strand"][-1]
    assert coverage < 0.99, "expected the final bin to be truncated"

    would_be_if_normalised_by_the_constant = strand * coverage
    assert would_be_if_normalised_by_the_constant < 0.5      # the bug is visible...
    assert strand > 0.8                                      # ...and we are not it.
    # The final bin sits at the run's own level, not at a fraction of it.
    assert strand == pytest.approx(real["strand_fraction"], abs=0.06)


def test_series_is_bounded_regardless_of_run_length(real):
    ser = real["series"]
    assert len(ser["t_minutes"]) <= 240
    assert ser["bin_minutes"] == 6          # ceil(1210 / 240)
    for values in ser["fractions"].values():
        assert len(values) == len(ser["t_minutes"])


# ------------------------------------------------------------- binning is a sum

def test_a_bin_aggregates_samples_rather_than_averaging_fractions(tmp_path):
    """Averaging per-minute fractions would weight a 15-second minute like a full one.

    Minute 0 is all strand but tiny; minute 1 is all `pore` and three times longer.
    Averaging the two fractions gives 0.5; pooling the samples gives the true 0.25.
    """
    snap = _loaded(tmp_path, ["strand,0,100", "pore,0,0",
                              "strand,1,0",   "pore,1,300"],
                   max_series_points=1)
    assert snap["series"]["fractions"]["strand"] == [pytest.approx(0.25)]


def test_a_partial_bin_keeps_its_true_fractions(tmp_path):
    """Synthetic twin of the real-data test above, with the arithmetic in plain sight."""
    snap = _loaded(tmp_path, ["strand,0,90", "pore,0,10",      # full minute
                              "strand,1,9",  "pore,1,1"])      # 10% of a minute
    ser = snap["series"]
    assert snap["samples_per_full_minute"] == 100
    assert ser["coverage"] == [1.0, 0.1]
    assert ser["fractions"]["strand"] == [pytest.approx(0.9), pytest.approx(0.9)]
    assert snap["partial_minutes"] == [1]


def test_the_run_aggregate_needs_no_partial_minute_rule(tmp_path):
    """A short minute contributes less to numerator AND denominator, so it self-corrects."""
    snap = _loaded(tmp_path, ["strand,0,90", "pore,0,10",
                              "strand,1,0",  "pore,1,10"])
    assert snap["strand_fraction"] == pytest.approx(90 / 110, abs=1e-6)


# ------------------------------------------------------------------- the guards

def test_an_unfamiliar_state_is_counted_not_dropped(tmp_path):
    """MinKNOW owns this vocabulary; filtering to a known list would shrink the denominator.

    Dropping the unknown state would report strand as 90/90 = 100% instead of 90/150.
    """
    snap = _loaded(tmp_path, ["strand,0,90", "some_future_state,0,60"])
    assert "some_future_state" in snap["states"]
    assert snap["strand_fraction"] == pytest.approx(0.6)


def test_absent_file_is_reported_rather_than_raised(tmp_path):
    """Core also runs over a plain directory of BAMs that was never a MinKNOW run."""
    m = PoreActivityModule()
    assert m.update_from_run_dir(tmp_path) is False
    snap = m.snapshot()
    assert snap["available"] is False
    assert "pore_activity_*.csv" in snap["unavailable_reason"]


def test_two_files_refuse_to_be_merged(tmp_path):
    """Two positions (or a stale file) would pool two runs into one denominator."""
    d = _run_dir(tmp_path, ["strand,0,10"], name="pore_activity_a_b_c.csv")
    (d / "pore_activity_d_e_f.csv").write_text(
        "Channel State,Experiment Time (minutes),State Time (samples)\nstrand,0,10\n")
    with pytest.raises(ValueError, match="Expected exactly one"):
        PoreActivityModule().update_from_run_dir(d)


def test_a_renamed_column_fails_loudly(tmp_path):
    """A reordered header read at the wrong offset yields a plausible, wrong occupancy."""
    (tmp_path / "pore_activity_a_b_c.csv").write_text(
        "Channel State,State Time (samples),Experiment Time (minutes)\nstrand,10,0\n")
    with pytest.raises(ValueError, match="unexpected header"):
        PoreActivityModule().update_from_run_dir(tmp_path)


@pytest.mark.parametrize("row, match", [
    ("strand,0", "expected 3 fields"),
    ("strand,0,10,extra", "expected 3 fields"),
    ("strand,zero,10", "invalid literal"),
    ("strand,0,-5", "negative state time"),
])
def test_malformed_rows_name_the_line(tmp_path, row, match):
    with pytest.raises(ValueError, match=match) as exc:
        PoreActivityModule().update_from_run_dir(_run_dir(tmp_path, [row]))
    assert ":2:" in str(exc.value), "the failure must name the offending line"


def test_empty_file_is_an_error_not_an_empty_run(tmp_path):
    (tmp_path / "pore_activity_a_b_c.csv").write_text("")
    with pytest.raises(ValueError, match="is empty"):
        PoreActivityModule().update_from_run_dir(tmp_path)


def test_update_after_finalize_is_refused(tmp_path):
    m = PoreActivityModule()
    m.update_from_run_dir(_run_dir(tmp_path, ["strand,0,10"]))
    m.finalize()
    with pytest.raises(RuntimeError, match="after finalize"):
        m.update_from_run_dir(tmp_path)


def test_re_reading_is_idempotent(tmp_path):
    """The file is re-parsed per input; a second read must not double the counts."""
    d = _run_dir(tmp_path, ["strand,0,90", "pore,0,10"])
    m = PoreActivityModule()
    m.update_from_run_dir(d)
    first = m.snapshot()
    m.update_from_run_dir(d)
    assert m.snapshot() == first


# ------------------------------------------------------- finding the run root

def test_the_run_root_is_found_one_level_above_the_data(tmp_path):
    """The ordinary MinKNOW case: ``--input-dir <run>/pod5``, telemetry at ``<run>/``."""
    from mrnaspect.analysis.modules.pore_activity import find_run_root
    (tmp_path / "pore_activity_a_b_c.csv").write_text("x")
    data = tmp_path / "pod5"
    data.mkdir()
    assert find_run_root(data) == tmp_path


def test_the_run_root_can_be_the_input_dir_itself(tmp_path):
    from mrnaspect.analysis.modules.pore_activity import find_run_root
    (tmp_path / "pore_activity_a_b_c.csv").write_text("x")
    assert find_run_root(tmp_path) == tmp_path


def test_the_search_stops_at_one_level(tmp_path):
    """Two levels up would attribute a NEIGHBOURING run's telemetry to this one.

    Machines that keep runs under a common parent would otherwise always find some file.
    """
    from mrnaspect.analysis.modules.pore_activity import find_run_root
    (tmp_path / "pore_activity_a_b_c.csv").write_text("x")   # grandparent
    data = tmp_path / "some_run" / "pod5"
    data.mkdir(parents=True)
    assert find_run_root(data) is None


# --------------------------------------- no invocation detail in a reported value

def test_the_snapshot_does_not_depend_on_where_the_run_lives(tmp_path):
    """The same run under two directories must produce an identical snapshot.

    This is the defect the wiring shipped with, caught by the suite rather than by these
    tests. Core is entered by two routes that spell the input directory differently --
    offline takes `<run>/pod5`, real-time takes `<run>` -- and a crash-restart check
    re-runs the same data from a different temp root. With the searched directory inside
    `unavailable_reason`, both comparisons reported a CHANGED QC NUMBER for a run whose
    reads were identical: one run described under two roots, which is the same shape of
    defect this project has hit before.

    So nothing here may carry a path. The file NAME stays -- it identifies the flowcell,
    run and acquisition, and survives the run being copied.
    """
    rows = ["strand,0,90", "pore,0,10"]
    (tmp_path / "a").mkdir()
    (tmp_path / "b" / "deeper").mkdir(parents=True)
    snaps = []
    for d in (tmp_path / "a", tmp_path / "b" / "deeper"):
        (d / "pore_activity_FC_run_acq.csv").write_text(
            "Channel State,Experiment Time (minutes),State Time (samples)\n"
            + "".join(r + "\n" for r in rows))
        m = PoreActivityModule()
        m.update_from_run_dir(d)
        snaps.append(m.snapshot())
    assert snaps[0] == snaps[1]
    assert snaps[0]["source_file"] == "pore_activity_FC_run_acq.csv"


def test_the_absence_snapshot_is_identical_from_any_directory(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y" / "z").mkdir(parents=True)
    out = []
    for d in (tmp_path / "x", tmp_path / "y" / "z"):
        m = PoreActivityModule()
        assert m.update_from_run_dir(d) is False
        out.append(m.snapshot())
    assert out[0] == out[1]
    assert not any(str(tmp_path) in str(v) for v in out[0].values())
