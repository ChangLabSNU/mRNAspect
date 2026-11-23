"""``run_status.json``: how far a run has got, from its first moment.

``latest_snapshot.json`` is written when an input FINISHES, so until the first input had
been basecalled, aligned and ingested (about 40 s for a full POD5 on one GPU, and
longer on a laptop) a surface had nothing to say. The GUI's run card read *Not measured*
for Inputs, and nothing said whether the folder had been recognised at all
(2026-11-12). This file is written when the run starts and whenever its count moves:

- ``inputs_found``: inputs the run knows of. Offline, the whole set, globbed at the
  start; live, the POD5 files MinKNOW has finished writing so far.
- ``inputs_processed``: inputs done.
- ``inputs_total``: the fixed set's size, or ``None`` on a live run, which has none.
- ``current_input``: the input in progress, or ``None``.
- ``continued``: ``None`` for a new run. For a run that continues an earlier one in the
  same folder (the same run name), what it carries over: the live driver's
  ``inputs_restored`` (inputs replayed from its manifest, not processed again), or the
  offline driver's ``basecalls_cached`` (basecalls on record, reused where the POD5 and
  the model are unchanged; every input is aligned and counted again). Until this field,
  nothing told the operator a run had continued rather than started (2026-11-23).

It carries no QC number. The only reader besides a surface is
:mod:`~mrnaspect.runtime.run_dir_state`, which says how far an earlier attempt got
before a run of the same name continues it.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

RUN_STATUS_NAME = "run_status.json"
#: The keys ``continued`` may hold, one per driver.
CONTINUED_KEYS = ("inputs_restored", "basecalls_cached")


def write_run_status(output_dir: Path | str, *, found: int, processed: int,
                     total: Optional[int], current: Optional[str],
                     continued: Optional[Mapping[str, int]] = None) -> Path:
    """Write the run's progress atomically and return the file's path.

    Atomic (a temp file, then ``os.replace``) so a reader never sees half a file; the
    temp name carries the thread, because the live driver writes from its watcher thread
    and its main thread.

    Raises:
        ValueError: a count below zero, or more done than found; a ``continued`` key
            other than :data:`CONTINUED_KEYS`, or a count in it below zero.
    """
    if found < 0 or processed < 0 or processed > found:
        raise ValueError(f"inconsistent counts: found={found}, processed={processed}")
    if continued is not None:
        unknown = set(continued) - set(CONTINUED_KEYS)
        if unknown or any(int(n) < 0 for n in continued.values()):
            raise ValueError(f"continued must hold counts of {CONTINUED_KEYS}, got "
                             f"{dict(continued)}")
    out = Path(output_dir) / RUN_STATUS_NAME
    payload = {"inputs_found": found, "inputs_processed": processed,
               "inputs_total": total, "current_input": current,
               "continued": dict(continued) if continued is not None else None,
               "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    tmp = out.with_name(f".{RUN_STATUS_NAME}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, out)
    return out
