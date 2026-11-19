"""The flow-cell panel must survive telemetry that arrives only when the run ends.

This is the measured sequence, not a hypothetical. On the real Mk1D watch log
(`watch_log_20260406_120809.txt`, 20 h) `pore_activity_*.csv` has exactly TWO events in
the entire run: CREATED at 08:19:56.517 at its full 438,303 bytes — one second AFTER the
run-end marker at 08:19:55.424 — and DELETED two hours later. `sequencing_summary` is the
same: it grows to 916 MB under `reads/tmp/.../*.txt.tmp`, a staging path Core never reads,
and is published to the run root by atomic rename in the same millisecond as the marker.

So every per-input refresh during the run sees nothing, and the panel is decided entirely
by whether anything re-reads after the run has ended. It did not, and the three metrics
asked for were reported unavailable on every real run — they looked fine in testing
only because every fixture had the files staged before the run started.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mrnaspect.main import OfflinePipeline, load_config


def _run_with_late_telemetry(tmp_path: Path, fixture_bam: Path, curlcake_ref: Path,
                             *, deliver: bool, in_subdir: bool = False) -> dict:
    """Process inputs with no telemetry, then deliver it between the last input and
    finalize — exactly where MinKNOW puts it.

    ``in_subdir`` puts the inputs one level below the telemetry, which is the real
    offline pod5 layout: Core is given `<run>/pod5/` while `pore_activity_*.csv` sits at
    `<run>/`. It is a separate case because when the two coincide, a search that has
    given up and fallen back to `input_dir` still lands on the right directory by
    accident.
    """
    run_root = tmp_path / "run"
    run_root.mkdir()
    input_dir = run_root / "pod5" if in_subdir else run_root
    input_dir.mkdir(exist_ok=True)
    shutil.copy(fixture_bam, input_dir / fixture_bam.name)

    src = Path(__file__).resolve().parent / "data" / "pore_activity_FIXTURE_run_acq.csv"
    assert src.exists(), src

    out = tmp_path / "out"
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "min_reads": 1})
    pipe = OfflinePipeline(input_dir, out, cfg)

    # The delivery hook, placed where MinKNOW actually publishes: immediately after the
    # last input is processed and before anything that finalizes. Hooking `finalize_all`
    # instead would be too late — the end-of-run refresh under test runs before it, and
    # the test would fail against correct code.
    real_process = pipe._process_one_input

    def process_then_deliver(*a, **kw):
        result = real_process(*a, **kw)
        if deliver:
            shutil.copy(src, run_root / src.name)
        return result

    pipe._process_one_input = process_then_deliver              # type: ignore[method-assign]
    pipe.run()
    return json.loads((out / "qc_result.json").read_text(encoding="utf-8"))


def test_telemetry_delivered_at_run_end_still_reaches_the_report(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    doc = _run_with_late_telemetry(tmp_path, fixture_bam, curlcake_ref, deliver=True)
    pa = doc["snapshots"].get("pore_activity") or {}
    assert pa.get("available") is True, (
        f"pore activity unavailable although the file was delivered before finalize: "
        f"{pa.get('unavailable_reason')!r} — nothing re-read it after the run ended")
    assert pa.get("n_minutes"), "the block is present but carries no measurement"


def test_it_is_found_one_level_up_when_it_arrives_late(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The offline pod5 layout: inputs in `<run>/pod5/`, telemetry at `<run>/`.

    The run-root search used to be cached on its FIRST attempt — which, because these
    files do not exist during the run, is always a failure. The cached failure pinned
    every later search to `input_dir`, so the file was never found even once it appeared
    one level up. Invisible whenever inputs and telemetry share a directory.
    """
    doc = _run_with_late_telemetry(tmp_path, fixture_bam, curlcake_ref,
                                   deliver=True, in_subdir=True)
    pa = doc["snapshots"].get("pore_activity") or {}
    assert pa.get("available") is True, (
        f"not found one level up: {pa.get('unavailable_reason')!r}")
    assert pa.get("n_minutes")


def test_a_run_whose_telemetry_never_arrives_says_so_rather_than_failing(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The converse, so the test above cannot pass by reporting `available` regardless.

    A plain directory of BAMs was never a MinKNOW run; absence is ordinary and must not
    stop the report.
    """
    doc = _run_with_late_telemetry(tmp_path, fixture_bam, curlcake_ref, deliver=False)
    pa = doc["snapshots"].get("pore_activity") or {}
    assert pa.get("available") is False
    assert pa.get("unavailable_reason"), "absence must carry a reason"
    assert doc["report"]["status"] in {"report", "render_unavailable"}
