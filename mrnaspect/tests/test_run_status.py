"""run_status.json: a run's progress from its first moment (GUI round 2, 2026-11-12).

Until the first input finished there was no latest_snapshot.json, so the GUI could not
say whether the folder had even been recognised. The drivers now write this file at the
start and whenever the count moves; these tests hold the file's shape, and that both
drivers write it before any input has been processed.
"""

from __future__ import annotations

import json
import threading

import pytest

from mrnaspect.runtime.run_status import RUN_STATUS_NAME, write_run_status


def test_the_file_says_found_processed_total_and_current(tmp_path) -> None:
    path = write_run_status(tmp_path, found=40, processed=0, total=40, current="a_0.pod5")
    assert path == tmp_path / RUN_STATUS_NAME
    d = json.loads(path.read_text())
    assert {k: d[k] for k in ("inputs_found", "inputs_processed", "inputs_total",
                              "current_input")} == {
        "inputs_found": 40, "inputs_processed": 0, "inputs_total": 40,
        "current_input": "a_0.pod5"}
    assert d["updated_at"].endswith("+00:00")


def test_a_live_run_has_no_total(tmp_path) -> None:
    d = json.loads(write_run_status(tmp_path, found=3, processed=1, total=None,
                                    current=None).read_text())
    assert d["inputs_total"] is None and d["current_input"] is None


@pytest.mark.parametrize("found, processed", [(-1, 0), (2, -1), (2, 3)])
def test_inconsistent_counts_are_refused(tmp_path, found, processed) -> None:
    with pytest.raises(ValueError, match="inconsistent counts"):
        write_run_status(tmp_path, found=found, processed=processed, total=None,
                         current=None)


def test_two_threads_never_leave_a_partial_file(tmp_path) -> None:
    """The live driver writes from its watcher thread and its main thread."""
    def write(k: int) -> None:
        for i in range(50):
            write_run_status(tmp_path, found=i + k, processed=i, total=None, current=None)

    threads = [threading.Thread(target=write, args=(k,)) for k in (0, 100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    json.loads((tmp_path / RUN_STATUS_NAME).read_text())   # whole, whoever won
    assert not list(tmp_path.glob("*.tmp")), "a temp file was left behind"
