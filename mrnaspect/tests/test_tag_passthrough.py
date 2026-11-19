"""The alignment tag list is a load-bearing config value, so it gets a guard.

A tag absent from the ``samtools fastq -T`` list is DESTROYED at alignment even though
the idiom would have carried it. That is not a tool limit and it is silent: measured
2026-09-04 on ``IVT_260407_train4``, ``ch`` survived on 200,000/200,000 aligned records
because it was listed, while ``er``/``pi``/``sp`` read **0/200,000** purely by omission.
After adding them, a round-trip over the same 200,000 uBAM records recovered
**445/445** distinct ``pi``-carrying reads and ``er`` on 100% of records.

Three tags are guarded here because dropping any one silently breaks a shipped number:

* ``pi``/``sp`` — dorado read-split parent id / split point. Split children carry
  DISTINCT read ids, so the engine's read-id dedup does not collapse them, and each
  child's alignment ends are INTERNAL to the parent molecule — fake termini in the
  3'/5' end panel that measures termini.
* ``er`` — ``end_reason``. Separates "the molecule was short" from "the pore aborted"
  (90.5% of sub-300 nt reads are ``signal_positive``, i.e. NOT aborts), and carries the
  mux-scan schedule the live drift panel needs to avoid alarming on a scheduled event.
* ``ch`` — channel. The sampling unit for every error bar is the channel, not the read,
  so a live channel-clustered SE is impossible without it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Package-relative, not repo-relative: the wrapper is part of THIS package, and a
# repo-root round-trip is what broke when the package moved (2026-09-17).
#
# The other half of this guard — that the analysis workflow's own config agrees — left
# this package on 2026-09-19. It reached `parents[2]` out of the package into a directory
# Core does not own and does not carry, which is the same fault as the fixtures that used
# to live outside the package.
_RUN_ALIGN = Path(__file__).resolve().parents[1] / "wrappers" / "run_align.sh"

# Tags whose loss silently corrupts a reported number (see module docstring).
REQUIRED_TAGS = ("er", "pi", "sp", "ch", "pt")


def _tags_from_run_align() -> list[str]:
    """Parse the ``TAGS=`` assignment out of the shell wrapper.

    Parsed rather than imported because the wrapper is a shell script; the guard has to
    read the same string the pipeline actually passes to ``samtools fastq -T``.
    """
    text = _RUN_ALIGN.read_text(encoding="utf-8")
    match = re.search(r'^TAGS="([^"]*)"', text, re.MULTILINE)
    assert match is not None, f"no TAGS= assignment found in {_RUN_ALIGN}"
    return match.group(1).split(",")


@pytest.mark.parametrize("tag", REQUIRED_TAGS)
def test_run_align_carries_required_tag(tag: str) -> None:
    assert tag in _tags_from_run_align(), (
        f"{tag!r} missing from run_align.sh TAGS — it will be silently destroyed at "
        f"alignment. See realtime_reporting_spec_v0.1.md §4."
    )


def test_mod_tags_are_absent_from_phase2_by_design() -> None:
    """Edge case: the intentional divergence is pinned so it reads as a decision.

    Without this, a later reader sees phase2 missing ``MM``/``ML`` and 'fixes' it. Mod-aware
    basecalling is out of scope (2026-09-04) and the real-time path does not carry the tags.
    If that scope decision is reversed, this test is the thing to delete — deliberately.
    """
    phase2_tags = set(_tags_from_run_align())
    assert "MM" not in phase2_tags and "ML" not in phase2_tags


def test_minus_y_is_present_wherever_tags_are_passed() -> None:
    """``-T`` without ``-y`` puts the tags in a FASTQ comment and then throws them away.

    The two flags are one mechanism; listing tags with no ``-y`` is a silent no-op, which
    is exactly the failure mode this whole guard exists for.
    """
    text = _RUN_ALIGN.read_text(encoding="utf-8")
    assert "fastq -T" in text, "run_align.sh no longer passes tags into the FASTQ"
    align_flags = re.search(r'^ALIGN_FLAGS=\$\{4:-"([^"]*)"\}', text, re.MULTILINE)
    assert align_flags is not None, "no ALIGN_FLAGS default found in run_align.sh"
    assert " -y" in f" {align_flags.group(1)}", (
        "minimap2 -y missing from the default ALIGN_FLAGS: tags would be carried into the "
        "FASTQ comment and then dropped."
    )
