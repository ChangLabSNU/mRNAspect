"""H2 regression — a restart must report ALL inputs, not a partial subset.

Confirmed bug (UPDATE_PLAN H2): re-running ``run()`` on an output dir with a populated
checkpoint skipped the checkpointed inputs, so the finalized report (stamped run_end,
exit 0) covered only the un-checkpointed ones — a partial paper number presented as
complete. The fix reframes the checkpoint to a pod5-basecall cache and ALWAYS re-ingests
every input into the fresh engine, so the report is always complete.
"""

from __future__ import annotations

import json
from pathlib import Path

from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.runtime.checkpoint import DEFAULT_STATE_NAME


from .conftest import write_distinct_bam

def _two_input_dir(tmp_path: Path, fixture_bam: Path) -> Path:
    d = tmp_path / "input"
    d.mkdir()
    # Two inputs holding DIFFERENT reads, as two real POD5s do. Symlinking one BAM twice
    # gave both the same 166 read IDs; the engine now drops the repeat as a duplicate, so
    # the restart-completeness assertion would be measuring deduplication, not restart.
    write_distinct_bam(fixture_bam, d / "small_aligned_a.bam", "a")
    write_distinct_bam(fixture_bam, d / "small_aligned_b.bam", "b")
    return d


def test_restart_with_seeded_checkpoint_reports_all_inputs(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    in_dir = _two_input_dir(tmp_path, fixture_bam)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    # Pre-seed the checkpoint as if a.bam finished in a prior run (the restart scenario).
    (out_dir / DEFAULT_STATE_NAME).write_text("small_aligned_a.bam\n", encoding="utf-8")

    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam", "min_reads": 1})
    pipe = OfflinePipeline(in_dir, out_dir, cfg)
    pipe.run()

    result = json.loads((out_dir / "qc_result.json").read_text(encoding="utf-8"))
    # Both inputs (166 reads each) are ingested despite a.bam being pre-checkpointed.
    assert result["report"]["mapped_reads"] == 332
    assert result["qc_metrics"]["summary"]["total_reads"] == 332
