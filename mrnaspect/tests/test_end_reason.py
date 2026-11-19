"""Read end reason from MinKNOW's ``sequencing_summary_*.txt``.

The fixture is a 2,000-row slice of a real file, carried for its exact 27-column layout.
Its PROPORTIONS are not the run's -- the rare reasons were over-sampled on purpose so the
guards have something to count -- so nothing here asserts a rate as if it were a
measurement. The real run's distribution (2,400,587 reads) lives in the module docstring
and was checked against an independent awk pass over the 251 MB file.
"""

from __future__ import annotations

import pytest

from mrnaspect.analysis.modules.end_reason import EJECTED, EndReasonModule
from .conftest import SEQ_SUMMARY_TXT, _require

HEADER = "\t".join(["a", "b", "end_reason"])


def _run_dir(tmp_path, rows, name="sequencing_summary_x_y_z.txt", header=HEADER):
    (tmp_path / name).write_text(header + "\n" + "".join(r + "\n" for r in rows))
    return tmp_path


def _loaded(tmp_path, rows, **params):
    m = EndReasonModule(params or None)
    assert m.update_from_run_dir(_run_dir(tmp_path, rows)) is True
    return m.snapshot()


# ------------------------------------------------------------- the real layout

@pytest.fixture(scope="module")
def real():
    m = EndReasonModule()
    assert m.update_from_run_dir(
        _require(SEQ_SUMMARY_TXT, "sequencing-summary fixture").parent) is True
    return m.snapshot()


def test_the_real_column_layout_is_found_and_counted(real):
    """27 columns, `end_reason` last. Located by NAME, never by a hardcoded index."""
    assert real["available"] is True
    assert real["n_reads"] == 2000
    assert sum(v["n"] for v in real["reasons"].values()) == 2000
    assert set(real["reasons"]) == {
        "signal_positive", "unblock_mux_change", "signal_negative"}


def test_the_ejected_headline_is_the_unblock_count(real):
    assert real["ejected_fraction"] == pytest.approx(
        real["reasons"][EJECTED]["n"] / 2000)


def test_fractions_partition_the_reads(real):
    assert sum(v["fraction"] for v in real["reasons"].values()) == pytest.approx(
        1.0, abs=1e-5)


# -------------------------------------------------- incremental == one-shot

def test_reading_a_growing_file_in_pieces_equals_reading_it_whole(tmp_path):
    """The guarantee the whole design rests on.

    The file is ~251 MB here and ~916 MB on another recorded run, so it is read by byte
    offset instead of re-parsed. That is only safe if consuming it in arbitrary pieces --
    including pieces that END MID-LINE -- lands on the same tally as one pass. The split
    below is chosen to fall inside a row, which is exactly what a read landing between
    MinKNOW's appends sees.
    """
    full = _require(SEQ_SUMMARY_TXT, "fixture").read_bytes()
    body_start = full.index(b"\n") + 1
    cut = body_start + (len(full) - body_start) // 2
    assert full[cut - 1:cut] != b"\n", "the split must fall inside a line to be a test"

    whole = EndReasonModule()
    whole.update_from_run_dir(SEQ_SUMMARY_TXT.parent)

    grown = tmp_path / "sequencing_summary_a_b_c.txt"
    piece = EndReasonModule()
    grown.write_bytes(full[:cut])
    piece.update_from_run_dir(tmp_path)
    partial = piece.snapshot()["n_reads"]
    grown.write_bytes(full)                      # MinKNOW appends the rest
    piece.update_from_run_dir(tmp_path)

    assert 0 < partial < 2000, "the first pass must see some rows but not all"
    assert piece.snapshot()["reasons"] == whole.snapshot()["reasons"]


def test_a_row_split_across_two_calls_is_counted_once(tmp_path):
    """Neither dropped nor double-counted — the two ways a torn tail can go wrong."""
    f = tmp_path / "sequencing_summary_a_b_c.txt"
    f.write_text(HEADER + "\n1\t2\tsignal_positive\n1\t2\tunblock_mux")
    m = EndReasonModule()
    m.update_from_run_dir(tmp_path)
    assert m.snapshot()["reasons"] == {
        "signal_positive": {"n": 1, "fraction": 1.0}}, "the torn row must not count yet"
    f.write_text(HEADER + "\n1\t2\tsignal_positive\n1\t2\tunblock_mux_change\n")
    m.update_from_run_dir(tmp_path)
    counts = {k: v["n"] for k, v in m.snapshot()["reasons"].items()}
    assert counts == {"signal_positive": 1, "unblock_mux_change": 1}


def test_an_unchanged_file_adds_nothing(tmp_path):
    d = _run_dir(tmp_path, ["1\t2\tsignal_positive"] * 5)
    m = EndReasonModule()
    m.update_from_run_dir(d)
    first = m.snapshot()
    m.update_from_run_dir(d)
    assert m.snapshot() == first


def test_a_shrinking_file_restarts_rather_than_mixing_two_runs(tmp_path):
    f = tmp_path / "sequencing_summary_a_b_c.txt"
    f.write_text(HEADER + "\n" + "1\t2\tsignal_positive\n" * 10)
    m = EndReasonModule()
    m.update_from_run_dir(tmp_path)
    assert m.snapshot()["n_reads"] == 10
    f.write_text(HEADER + "\n" + "1\t2\tmux_change\n" * 2)      # a different, shorter run
    m.update_from_run_dir(tmp_path)
    counts = {k: v["n"] for k, v in m.snapshot()["reasons"].items()}
    assert counts == {"mux_change": 2}, "the old run's rows must not survive"


# ------------------------------------------------------------------- guards

def test_an_unfamiliar_reason_is_counted_not_dropped(tmp_path):
    snap = _loaded(tmp_path, ["1\t2\tsignal_positive", "1\t2\tsome_future_reason"])
    assert snap["reasons"]["some_future_reason"]["n"] == 1
    assert snap["n_reads"] == 2


def test_a_missing_column_fails_loudly(tmp_path):
    """Read at a hardcoded index this would produce a plausible, wrong tally."""
    with pytest.raises(ValueError, match="no 'end_reason' column"):
        EndReasonModule().update_from_run_dir(
            _run_dir(tmp_path, ["1\t2\t3"], header="a\tb\tc"))


def test_a_short_row_is_not_quietly_skipped(tmp_path):
    """A torn tail never reaches the tally, so a short row here is a real malformation."""
    with pytest.raises(ValueError, match="needs > 2"):
        EndReasonModule().update_from_run_dir(_run_dir(tmp_path, ["1\t2"]))


def test_absent_file_is_reported_rather_than_raised(tmp_path):
    m = EndReasonModule()
    assert m.update_from_run_dir(tmp_path) is False
    snap = m.snapshot()
    assert snap["available"] is False
    assert "sequencing_summary*.txt" in snap["unavailable_reason"]


def test_two_files_refuse_to_be_merged(tmp_path):
    d = _run_dir(tmp_path, ["1\t2\tsignal_positive"], name="sequencing_summary_a.txt")
    (d / "sequencing_summary_b.txt").write_text(HEADER + "\n1\t2\tmux_change\n")
    with pytest.raises(ValueError, match="Expected exactly one"):
        EndReasonModule().update_from_run_dir(d)


def test_the_file_changing_name_mid_run_is_refused(tmp_path):
    """A new acquisition writes a new name; one tally must not span two runs."""
    m = EndReasonModule()
    m.update_from_run_dir(_run_dir(tmp_path, ["1\t2\tsignal_positive"],
                                   name="sequencing_summary_a.txt"))
    (tmp_path / "sequencing_summary_a.txt").unlink()
    (tmp_path / "sequencing_summary_b.txt").write_text(HEADER + "\n1\t2\tmux_change\n")
    with pytest.raises(ValueError, match="changed name mid-run"):
        m.update_from_run_dir(tmp_path)


def test_empty_file_is_an_error_not_an_empty_run(tmp_path):
    (tmp_path / "sequencing_summary_a_b_c.txt").write_text("")
    with pytest.raises(ValueError, match="is empty"):
        EndReasonModule().update_from_run_dir(tmp_path)


def test_update_after_finalize_is_refused(tmp_path):
    m = EndReasonModule()
    m.update_from_run_dir(_run_dir(tmp_path, ["1\t2\tsignal_positive"]))
    m.finalize()
    with pytest.raises(RuntimeError, match="after finalize"):
        m.update_from_run_dir(tmp_path)


# ------------------------- no invocation detail in a reported value (see pore_activity)

def test_the_snapshot_does_not_depend_on_where_the_run_lives(tmp_path):
    rows = ["1\t2\tsignal_positive", "1\t2\tunblock_mux_change"]
    (tmp_path / "a").mkdir()
    (tmp_path / "b" / "deeper").mkdir(parents=True)
    snaps = []
    for d in (tmp_path / "a", tmp_path / "b" / "deeper"):
        (d / "sequencing_summary_FC_run_acq.txt").write_text(
            HEADER + "\n" + "".join(r + "\n" for r in rows))
        m = EndReasonModule()
        m.update_from_run_dir(d)
        snaps.append(m.snapshot())
    assert snaps[0] == snaps[1]
    assert snaps[0]["source_file"] == "sequencing_summary_FC_run_acq.txt"


def test_the_absence_snapshot_is_identical_from_any_directory(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y" / "z").mkdir(parents=True)
    out = []
    for d in (tmp_path / "x", tmp_path / "y" / "z"):
        m = EndReasonModule()
        assert m.update_from_run_dir(d) is False
        out.append(m.snapshot())
    assert out[0] == out[1]
    assert not any(str(tmp_path) in str(v) for v in out[0].values())
