"""Interim JSON and reports say when they were made: the local clock time with its UTC
offset, and how long after QC started.

Asked for on 2026-11-09 (the interim reports and the JSON should carry the time: the
user's time, and the time elapsed); option 1 on 2026-11-10: the user's time is the clock
on the user's machine, not the CPU user time that `time` reports. Before, interim_<k>_snapshot.json and
latest_snapshot.json carried no time at all, the report gave only the date, and the
elapsed time lived only on the GUI's screen. Only the final qc_result.json had a time
(provenance.generated_at, in UTC).

Elapsed time counts from when Core built its pipeline, the nearest Core gets to the GUI's
own Elapsed (counted from when the GUI spawned it): the live driver starts dorado-server
between building the pipeline and run(), and the user sits through that wait too. It is
monotonic, so a clock change mid-run cannot bend it. How far into the SEQUENCING run a
snapshot reaches (from the POD5 acquisition start) is a separate item.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mrnaspect.main import OfflinePipeline, load_config, resume_from_snapshot
from mrnaspect.reporting.reporter import CoAReporter

from .test_polya_reaches_the_report import _run

#: "Generated 2026-11-10 14:03:27 (UTC+09:00) by mRNAspect, 1 h 12 min after QC started"
_STAMP = r"Generated \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \(UTC[+-]\d{2}:\d{2}\) by mRNAspect"
_ELAPSED = r", (\d+ h \d+ min|\d+ min \d+ s|\d+ s) after QC started"


@pytest.fixture(scope="module")
def run(tmp_path_factory, variant_bam, qk_ha_ref) -> tuple[Path, float]:
    """A real offline run of one input: one interim (min_reads 1), then the final."""
    t0 = time.monotonic()
    run_dir = _run(tmp_path_factory.mktemp("clock"), variant_bam, qk_ha_ref, None)
    return run_dir, time.monotonic() - t0


def _subtitle(html: str) -> str:
    m = re.search(r'<div class="subtitle">(.*?)</div>', html, re.S)
    assert m, "the report has no subtitle"
    return re.sub(r"\s+", " ", m.group(1)).strip()


@pytest.mark.parametrize("name", ["interim_1_snapshot.json", "latest_snapshot.json"])
def test_each_snapshot_says_when_and_how_far_in(run, name) -> None:
    run_dir, wall = run
    data = json.loads((run_dir / name).read_text())
    at = datetime.fromisoformat(data["generated_at"])
    # Local time with its offset: not UTC unless the machine is on UTC, and not naive.
    now = datetime.now().astimezone()
    assert at.utcoffset() == now.utcoffset(), data["generated_at"]
    assert abs(now - at) < timedelta(minutes=10), data["generated_at"]
    assert 0 <= data["elapsed_s"] <= wall + 1, (data["elapsed_s"], wall)


def test_the_interim_comes_after_the_snapshot_it_follows(run) -> None:
    """latest_snapshot.json is written when the input is done, the interim after it."""
    run_dir, _ = run
    latest = json.loads((run_dir / "latest_snapshot.json").read_text())
    interim = json.loads((run_dir / "interim_1_snapshot.json").read_text())
    assert latest["elapsed_s"] <= interim["elapsed_s"]


@pytest.mark.parametrize("name", ["report_interim_1.html", "report_run_end.html"])
def test_each_report_says_when_and_how_far_in(run, name) -> None:
    run_dir, _ = run
    sub = _subtitle((run_dir / name).read_text())
    assert re.fullmatch(_STAMP + _ELAPSED, sub), sub


def test_a_resumed_report_has_the_time_but_no_elapsed(run, tmp_path) -> None:
    """Rendered from latest_snapshot.json with no run behind it: there is no start to
    count from, so it gives none rather than a wrong one."""
    run_dir, _ = run
    (tmp_path / "latest_snapshot.json").write_text(
        (run_dir / "latest_snapshot.json").read_text())
    resume_from_snapshot(tmp_path, min_reads=1)
    sub = _subtitle((tmp_path / "report_user_abort.html").read_text())
    assert re.fullmatch(_STAMP, sub), sub


def test_the_start_is_not_moved_by_a_run_state_reset(qk_ha_ref, tmp_path) -> None:
    """run() zeroes the counters again; the elapsed time still counts from construction,
    and the reporter counts from the same instant as the JSON."""
    (tmp_path / "in").mkdir()
    cfg = load_config(None, {"reference": str(qk_ha_ref), "mode": "bam", "min_reads": 1})
    pipe = OfflinePipeline(tmp_path / "in", tmp_path / "out", cfg)
    start = pipe._started_mono
    assert pipe.reporter.started_monotonic == start
    pipe._reset_run_state()
    assert pipe._started_mono == start


@pytest.mark.parametrize("seconds,text", [
    (0, "0 s"), (59.4, "59 s"), (59.6, "1 min 0 s"), (61, "1 min 1 s"),
    (3599, "59 min 59 s"), (3600, "1 h 0 min"), (4330, "1 h 12 min"),
    (90061, "25 h 1 min"),
])
def test_elapsed_reads_in_its_two_largest_units(seconds, text) -> None:
    assert CoAReporter._elapsed_text(seconds) == text


def test_a_negative_elapsed_time_is_refused() -> None:
    with pytest.raises(ValueError):
        CoAReporter._elapsed_text(-1)


@pytest.mark.parametrize("offset,text", [
    (timedelta(hours=9), "UTC+09:00"), (timedelta(hours=5, minutes=30), "UTC+05:30"),
    (timedelta(hours=-3), "UTC-03:00"), (timedelta(0), "UTC+00:00"),
])
def test_the_clock_time_carries_its_utc_offset(offset, text) -> None:
    at = datetime(2026, 11, 10, 14, 3, 27, tzinfo=timezone(offset))
    assert CoAReporter._clock_text(at) == f"2026-11-10 14:03:27 ({text})"


def test_a_time_without_a_zone_is_refused() -> None:
    with pytest.raises(ValueError):
        CoAReporter._clock_text(datetime(2026, 11, 10, 14, 3, 27))
