"""Canonicalizing comparator for qc_result.json vs a frozen golden snapshot.

The UPDATE_PLAN batch intentionally changes reported outputs (H1 identity, item-5
null-not-0.0, items 6/9/10 error metrics, items 11/13 new keys), so the old-driver
``mode=bam`` parity gate no longer applies. It is replaced by a *golden-snapshot +
explained-diff* discipline (UPDATE_PLAN "Re-validation plan"):

  * Wave 0 freezes ``tests/golden/pre_batch_qc_result.json`` from the CURRENT code.
  * After a parity-*safe* wave, ``diff_files`` against the golden must be EMPTY.
  * After a parity-*breaking* change, every surviving diff must map to an owning item.

Canonicalization strips only run-to-run volatiles (provenance, wall-clock timing,
absolute report path, the run-level ``reason``) — NOT the QC numbers. The strip list is
path-specific on purpose: ``report.reason`` (run_end/user_abort) is volatile, but
``qc_metrics.metrics.modified_u_miscall.reason`` (a methodological note) is a real value
and is kept.

CLI:
    python -m mrnaspect.tests.golden_compare NEW.json [GOLDEN.json]
    (exit 0 = no diffs; exit 1 = diffs printed)
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path
from typing import Any

# Volatile locations, addressed as dotted paths from the document root. A trailing key
# is dropped from its parent dict if present; absence is silently fine (schema evolves).
_STRIP_PATHS: tuple[str, ...] = (
    "provenance",        # generated_at, command, argv, git_*, input mtimes, params paths
    "timing",            # wall-clock seconds — nondeterministic
    "report.path",       # absolute output path
    "report.reason",     # run_end vs user_abort — run-level, not a QC number
)

# Float compare tolerance. Reported values are already rounded in-code (2–6 dp), so this
# only absorbs platform FP noise; a real metric change is far larger than this.
_ABS_TOL = 1e-9
_REL_TOL = 1e-9


def canonicalize(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with the volatile ``_STRIP_PATHS`` removed."""
    out = copy.deepcopy(doc)
    for dotted in _STRIP_PATHS:
        parts = dotted.split(".")
        node: Any = out
        for key in parts[:-1]:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    return out


def _floats_equal(a: float, b: float) -> bool:
    if math.isnan(a) and math.isnan(b):
        return True
    return math.isclose(a, b, abs_tol=_ABS_TOL, rel_tol=_REL_TOL)


def _diff(a: Any, b: Any, path: str, out: list[str]) -> None:
    """Accumulate human-readable ``path: old -> new`` lines into ``out``."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            sub = f"{path}.{key}" if path else key
            if key not in a:
                out.append(f"{sub}: <absent in golden> -> {b[key]!r}")
            elif key not in b:
                out.append(f"{sub}: {a[key]!r} -> <absent in new>")
            else:
                _diff(a[key], b[key], sub, out)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: list len {len(a)} -> {len(b)}")
            return
        for i, (ea, eb) in enumerate(zip(a, b)):
            _diff(ea, eb, f"{path}[{i}]", out)
        return
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        if not _floats_equal(float(a), float(b)):
            out.append(f"{path}: {a!r} -> {b!r}")
        return
    if a != b:
        out.append(f"{path}: {a!r} -> {b!r}")


def diff_docs(golden: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Canonicalize both, return the list of differing paths (empty == identical)."""
    out: list[str] = []
    _diff(canonicalize(golden), canonicalize(new), "", out)
    return out


def diff_files(golden_path: Path | str, new_path: Path | str) -> list[str]:
    golden = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    new = json.loads(Path(new_path).read_text(encoding="utf-8"))
    return diff_docs(golden, new)


GOLDEN_PATH = Path(__file__).parent / "golden" / "pre_batch_qc_result.json"


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: golden_compare NEW.json [GOLDEN.json]", file=sys.stderr)
        return 2
    new_path = Path(argv[0])
    golden_path = Path(argv[1]) if len(argv) > 1 else GOLDEN_PATH
    diffs = diff_files(golden_path, new_path)
    if not diffs:
        print(f"OK: no diffs ({new_path.name} == {golden_path.name} after canonicalize)")
        return 0
    print(f"{len(diffs)} diff(s) vs {golden_path.name}:")
    for line in diffs:
        print(f"  {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
