"""The snapshot keys the engine registers and the integrator and reporter look up.

A leaf module with no imports, so the engine and the reporter (which the engine
imports) can share it without an import cycle.
"""

# Run-level modules: one snapshot each, over every read in the run.
RUN_SUMMARY = "run_summary"
POLYA = "polya"
# The primary (most-read) construct's snapshots, also kept flat for the report layout.
# Every construct, this one included, is under CONSTRUCT_PREFIX as well.
CONSENSUS = "consensus"
ERROR_HOTSPOT = "error_hotspot"

# One snapshot per reference construct, plus one for the primary reads that matched no
# construct (unmapped, or mapped to a contig the reference lacks).
CONSTRUCT_PREFIX = "construct:"
UNASSIGNED = "unassigned"

# Instrument snapshots, from MinKNOW's run files rather than reads, produced by the
# pipeline rather than AnalysisEngine. Always registered; without the file, the
# snapshot says ``available: False``.
PORE_ACTIVITY = "pore_activity"
END_REASON = "end_reason"
VOLTAGE = "voltage"


def construct_key(ref_name: str) -> str:
    """Snapshot key for one construct."""
    return CONSTRUCT_PREFIX + ref_name


def construct_ref(key: str) -> str:
    """Inverse of :func:`construct_key`.

    Raises:
        ValueError: If ``key`` is not a construct key.
    """
    if not key.startswith(CONSTRUCT_PREFIX):
        raise ValueError(f"not a construct snapshot key: {key!r}")
    return key[len(CONSTRUCT_PREFIX):]
