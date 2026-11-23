"""The report's "Sequencing information" section, end to end, from MinKNOW's own files.

release_work.yaml P3: the section had never rendered in a test. The three sources are
files MinKNOW writes beside the data, not anything in the reads -- `sequencing_summary`
(end reason; ont_basecall_client emits no er:Z tag, so the summary is the only source),
`other_reports/pore_scan_data` (bias voltage per mux scan) and `pore_activity` (channel
states) -- and each module had a unit test of its own parser while nothing showed the
driver finding the files, the figures being built from them, or either medium drawing them.

The fixtures are slices of one real Mk1D run, laid out as MinKNOW lays a run out: the data
in a subdirectory, the run-level files one level up. The end-reason proportions are
over-sampled on purpose (see conftest) -- these tests compare counts with the file they came
from, never with the run's real distribution. The numbers in the figures are checked
against the raw files read here, not against the modules' own snapshots alone.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import pytest

from mrnaspect.main import main as offline_main

from .conftest import PORE_ACTIVITY_CSV, PORE_SCAN_CSV, SEQ_SUMMARY_TXT

_TITLES = ("Read end reason", "Voltage over the run", "Pore occupancy over the run")


def _run(tmp: Path, bam: Path, ref: Path, *, telemetry: bool) -> Path:
    run_root = tmp / "run"
    data = run_root / "bam_pass"
    data.mkdir(parents=True)
    shutil.copy(bam, data / bam.name)
    bai = bam.with_suffix(bam.suffix + ".bai")
    if bai.exists():
        shutil.copy(bai, data / bai.name)
    if telemetry:
        shutil.copy(SEQ_SUMMARY_TXT, run_root / SEQ_SUMMARY_TXT.name)
        shutil.copy(PORE_ACTIVITY_CSV, run_root / PORE_ACTIVITY_CSV.name)
        (run_root / "other_reports").mkdir()
        shutil.copy(PORE_SCAN_CSV, run_root / "other_reports" / PORE_SCAN_CSV.name)
    assert offline_main([
        "--input-dir", str(data), "--reference", str(ref), "--output-dir", str(tmp / "out"),
        "--output-name", "r", "--mode", "bam", "--min-reads", "1",
        # Every construct here has 47 reads or fewer; with the shipped minimum of 100 none
        # would get per-construct figures, and the order check below needs them.
        "--min-reads-per-construct", "0"]) == 0
    return tmp / "out" / "r"


@pytest.fixture(scope="module")
def with_files(tmp_path_factory, fixture_bam, curlcake_ref) -> Path:
    return _run(tmp_path_factory.mktemp("telemetry"), fixture_bam, curlcake_ref,
                telemetry=True)


@pytest.fixture(scope="module")
def without_files(tmp_path_factory, fixture_bam, curlcake_ref) -> Path:
    return _run(tmp_path_factory.mktemp("none"), fixture_bam, curlcake_ref, telemetry=False)


def _snapshots(run: Path) -> dict:
    return json.loads((run / "qc_result.json").read_text())["snapshots"]


def _figures(run: Path) -> dict[str, dict]:
    return {f["id"]: f for f in json.loads((run / "figures.json").read_text())}


# --- found, one level above the data --------------------------------------------------------

def test_all_three_sources_are_found_one_level_up(with_files) -> None:
    snaps = _snapshots(with_files)
    for key in ("end_reason", "voltage", "pore_activity"):
        assert snaps[key]["available"] is True, (key, snaps[key])


def test_the_section_holds_exactly_those_three_figures(with_files) -> None:
    section = [f for f in _figures(with_files).values() if f["section"] == "sequencing"]
    assert [f["title"] for f in section] == list(_TITLES)


# --- the numbers are the files' ------------------------------------------------------------

def test_the_end_reason_bars_count_the_summary_s_column(with_files) -> None:
    """Counted here from the raw TSV: 2,000 rows, every one with an end_reason."""
    with SEQ_SUMMARY_TXT.open(newline="") as fh:
        counted = Counter(row["end_reason"] for row in csv.DictReader(fh, delimiter="\t"))
    bar = _figures(with_files)["end-reason"]["spec"]["data"][0]
    assert dict(zip(bar["y"], bar["x"])) == dict(counted)
    assert sum(bar["x"]) == _snapshots(with_files)["end_reason"]["n_reads"] == 2000


def test_the_voltage_line_is_one_value_per_mux_scan(with_files) -> None:
    """Read here from the raw CSV: the running voltage of each of the 13 scans, which steps
    from -170 to -180 mV over the run."""
    per_scan: dict[int, float] = {}
    with PORE_SCAN_CSV.open(newline="") as fh:
        for row in csv.DictReader(fh):
            per_scan.setdefault(int(row["scan_number"]), float(row["scan_running_voltage_mv"]))
    line = next(t for t in _figures(with_files)["voltage"]["spec"]["data"]
                if t.get("name") == "running")
    assert line["y"] == [per_scan[s] for s in sorted(per_scan)]
    assert len(line["y"]) == 13 and line["y"][0] == -170.0 and line["y"][-1] == -180.0


def test_the_channel_states_are_the_activity_file_s(with_files) -> None:
    """The figure names the commonest states and folds the rest into "other (N states)":
    every named one is a state in the file, and N is the file's states left unnamed."""
    with PORE_ACTIVITY_CSV.open(newline="") as fh:
        in_file = {row["Channel State"] for row in csv.DictReader(fh)}
    drawn = [t.get("name") for t in _figures(with_files)["pore"]["spec"]["data"]]
    other = [n for n in drawn if n.startswith("other (")]
    named = set(drawn) - set(other)
    assert named and named <= in_file, sorted(named - in_file)
    assert len(other) == 1, drawn
    assert other[0] == f"other ({len(in_file - named)} states)"


# --- on screen and on paper ------------------------------------------------------------------

def test_the_screen_report_draws_the_section(with_files) -> None:
    html = (with_files / "report_run_end.html").read_text()
    blob = html[html.index("</head>"):]
    for title in ("Sequencing information", *_TITLES):
        assert title in blob, title


def test_the_pdf_draws_it_after_the_constructs(with_files) -> None:
    """The archived artifact, read on its own. The instrument's record closes the report,
    after the material has been described."""
    import pypdf  # not importorskip: without the reader this must fail, not skip
    text = re.sub(r"\s+", " ", " ".join(
        p.extract_text() or "" for p in pypdf.PdfReader(str(with_files / "report_run_end.pdf")).pages))
    for title in ("Sequencing information", *_TITLES):
        assert title in text, title
    assert text.index("Per construct") < text.index("Sequencing information")


# --- and without the files, absence is said, not drawn ---------------------------------------

def test_without_the_files_nothing_is_drawn_and_each_says_why(without_files) -> None:
    snaps = _snapshots(without_files)
    for key in ("end_reason", "voltage", "pore_activity"):
        assert snaps[key]["available"] is False, key
        assert snaps[key].get("unavailable_reason"), f"{key}: absence must carry a reason"
    assert not [f for f in _figures(without_files).values() if f["section"] == "sequencing"]
