"""The run's machine-readable outputs.

* ``write_result_json``: ``qc_result.json``, the reported result (snapshots, integrated
  metrics, run accounting) with a provenance stamp. Written once, at finalize.
* ``dump_latest_snapshot``: ``latest_snapshot.json``, rewritten atomically after every
  input, so a run killed outright still leaves a complete snapshot on disk.
* ``clock_stamp``: the time fields that file and each interim snapshot carry.

Both take the ``{module: snapshot}`` bundle as ``snapshots``.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ..analysis.modules.qc_integrator import QCResultIntegrator
from ..provenance import write_result_json as _prov_write_result_json

LATEST_SNAPSHOT_NAME = "latest_snapshot.json"
RESULT_JSON_NAME = "qc_result.json"


def clock_stamp(started_monotonic: float) -> dict[str, Any]:
    """When a snapshot is written: ``generated_at`` and ``elapsed_s``.

    ``generated_at`` is the local time with its UTC offset (ISO-8601, to the second): the
    user's clock (2026-11-10), while qc_result.json's provenance stamp stays UTC.
    ``elapsed_s`` is seconds since ``started_monotonic``, a ``time.monotonic()`` reading,
    so a clock change mid-run cannot bend it.
    """
    return {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "elapsed_s": round(time.monotonic() - started_monotonic, 1)}


def dump_latest_snapshot(
    output_dir: Path | str,
    snapshots: Mapping[str, Any],
    integrator: QCResultIntegrator,
    *,
    total_reads: int,
    last_input: str,
    inputs_processed: int,
    inputs_total: Optional[int] = None,
    started_monotonic: Optional[float] = None,
) -> Path:
    """Atomically write ``latest_snapshot.json``, the per-input durability point.

    Written to a temp file and moved into place, so a kill mid-write leaves the previous
    complete file or the new one, never a torn one.

    Args:
        output_dir: The run directory.
        snapshots: The ``{module: snapshot}`` bundle.
        integrator: Builds ``qc_metrics`` from the bundle.
        total_reads: Primary reads ingested so far.
        last_input: The input just processed.
        inputs_processed: Inputs processed so far (failed ones not counted).
        inputs_total: How many inputs the run has, when that is fixed (an offline run).
            None for a live run, whose inputs are still arriving; the key is then left
            out of the file, not written as null.
        started_monotonic: ``time.monotonic()`` when the run started; with it the file
            carries ``generated_at`` and ``elapsed_s`` (see ``clock_stamp``).

    Raises:
        ValueError: ``inputs_processed`` is negative, or exceeds ``inputs_total``.
    """
    if inputs_processed < 0:
        raise ValueError(f"inputs_processed must be >= 0, got {inputs_processed!r}")
    if inputs_total is not None and inputs_processed > inputs_total:
        raise ValueError(
            f"inputs_processed ({inputs_processed}) exceeds inputs_total "
            f"({inputs_total})")
    out = Path(output_dir) / LATEST_SNAPSHOT_NAME
    tmp = out.parent / (out.name + ".tmp")
    payload: dict[str, Any] = {
        "last_input": last_input,
        "total_reads": total_reads,
        "inputs_processed": inputs_processed,
    }
    if inputs_total is not None:
        payload["inputs_total"] = inputs_total
    if started_monotonic is not None:
        payload.update(clock_stamp(started_monotonic))
    payload["qc_metrics"] = integrator.integrate(snapshots)
    payload["snapshots"] = dict(snapshots)
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, out)  # atomic on the same filesystem
    return out


def write_result_json(
    output_dir: Path | str,
    snapshots: Mapping[str, Any],
    integrator: QCResultIntegrator,
    *,
    result: Mapping[str, Any],
    inputs: Sequence[Path],
    run_accounting: Mapping[str, Any],
    timing: Mapping[str, Any],
    params: Mapping[str, Any],
    reason: str,
    tools: Optional[Mapping[str, Any]] = None,
) -> Path:
    """Assemble ``qc_result.json``, the reported result, and stamp its provenance.

    ``reason`` (``run_end`` or ``user_abort``) records whether the report covers a full
    run or an aborted one.
    """
    bundle = dict(snapshots)
    payload = {
        "report": {
            "status": result.get("status"),
            "path": result.get("path"),
            "mapped_reads": result.get("mapped_reads"),  # sufficiency gate metric
            "low_reads": result.get("low_reads"),
            # None when reads suffice; "accumulating" (short so far, interim render) or
            # "finish_low_reads" (ended short).
            "low_reads_state": result.get("low_reads_state"),
            # Whether any input failed to process; independent of low_reads.
            "incomplete": result.get("incomplete"),
            "n_failed_inputs": result.get("n_failed_inputs"),
            "reason": reason,  # run_end | user_abort
        },
        "run_accounting": dict(run_accounting),
        "timing": dict(timing),
        "n_inputs": len(inputs),
        "modules": list(bundle.keys()),
        "qc_metrics": integrator.integrate(bundle),
        "snapshots": bundle,
    }
    out = Path(output_dir) / RESULT_JSON_NAME
    # External tool versions go in the stamp beside the code's own commit.
    _prov_write_result_json(
        out, payload, inputs=[str(p) for p in inputs], params=dict(params),
        tools=dict(tools or {}),
    )
    return out
