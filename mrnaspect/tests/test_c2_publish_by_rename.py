"""C2 — a stage artifact at its canonical path is COMPLETE, or it is not there at all.

Both shell wrappers used to write straight to the name the rest of the pipeline trusts:

  * ``run_align.sh``    ``samtools sort -o "$ALIGNED_BAM"``  -> a kill mid-sort leaves a
    truncated BAM where a restart expects a finished one.
  * ``run_basecall.sh`` chunks streamed into ``basecall_<prefix>/`` -> a kill leaves
    partial chunks under a finished-looking name; the restart basecalls into the *same*
    directory and ``run_align.sh``'s ``find | samtools cat`` merges everything it finds.
    Any stale chunk not byte-identically overwritten becomes DUPLICATE READS, with no
    error, no warning, and an inflated depth behind every consensus call.

Offline this was masked (align always re-runs). It stops being masked the moment the
aligned BAM becomes the real-time loop's replay log.

The failure test here is not hypothetical: it demonstrates that a failed align leaves a
perfectly valid, indexable BAM containing HALF the reads. That is the artifact the old
code published.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pysam
import pytest

_WRAPPERS = Path(__file__).resolve().parents[1] / "wrappers"
ALIGN_SCRIPT = _WRAPPERS / "run_align.sh"
BASECALL_SCRIPT = _WRAPPERS / "run_basecall.sh"

# Skip only if the tools are genuinely unreachable — asked the same way the wrapper asks.
# This used to test two ABSOLUTE LAB PATHS for existence, which was wrong in both
# directions once the wrapper stopped hardcoding them: it would skip on a host that has
# perfectly good tools under a different prefix, and it described a coupling the code no
# longer has.
def _missing_tools() -> list[str]:
    from mrnaspect.wrappers._shell import resolve_tool
    missing = []
    for tool in ("minimap2", "samtools"):
        try:
            resolve_tool(tool)
        except FileNotFoundError:
            missing.append(tool)
    return missing


_MISSING = _missing_tools()
pytestmark = pytest.mark.skipif(
    bool(_MISSING), reason=f"run_align.sh needs {', '.join(_MISSING)}")


def _make_ubam_dir(out_dir: Path, fixture_bam: Path, prefix: str, chunks: int = 2) -> Path:
    """Split the fixture into ``chunks`` BAMs, mimicking dorado-server's 4000-read split."""
    dest = out_dir / f"basecall_{prefix}"
    dest.mkdir(parents=True)
    with pysam.AlignmentFile(fixture_bam, "rb") as src:
        header = src.header
        reads = list(src)
    per = -(-len(reads) // chunks)
    for i in range(chunks):
        with pysam.AlignmentFile(dest / f"chunk_0_{i}.bam", "wb", header=header) as out:
            for read in reads[i * per:(i + 1) * per]:
                out.write(read)
    return dest


def _run_align(prefix: str, reference: Path, out_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(ALIGN_SCRIPT), prefix, str(reference), str(out_dir)],
        capture_output=True, text=True,
    )


def test_align_publishes_a_complete_indexed_bam(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Happy path: every read survives, and no ``.partial`` is left behind."""
    _make_ubam_dir(tmp_path, fixture_bam, "smoke")
    proc = _run_align("smoke", curlcake_ref, tmp_path)
    assert proc.returncode == 0, proc.stderr

    bam = tmp_path / "aligned_smoke.bam"
    assert bam.exists() and (tmp_path / "aligned_smoke.bam.bai").exists()
    assert not list(tmp_path.glob("*.partial*")), "publish left staging files behind"

    with pysam.AlignmentFile(fixture_bam, "rb") as f:
        expected = sum(1 for _ in f)
    with pysam.AlignmentFile(bam, "rb") as f:
        assert sum(1 for _ in f) == expected


def test_a_failed_align_leaves_no_canonical_bam(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The regression. A truncated ubam chunk kills the pipeline mid-sort.

    The half-written output is a *valid, readable* BAM — it just holds half the reads.
    Before C2 it landed at the canonical path, so a restart (or a SIGKILL, where there is
    no exit code to catch at all) would ingest 50% of the run and report it as the whole
    thing. Now it is quarantined under ``.partial`` and the canonical path stays absent,
    which the caller reads as a plain cache miss.
    """
    ubam_dir = _make_ubam_dir(tmp_path, fixture_bam, "broken")
    victim = ubam_dir / "chunk_0_1.bam"
    victim.write_bytes(victim.read_bytes()[:3000])  # truncate mid-record

    proc = _run_align("broken", curlcake_ref, tmp_path)
    assert proc.returncode != 0, "a truncated chunk must fail the align, not pass quietly"

    canonical = tmp_path / "aligned_broken.bam"
    assert not canonical.exists(), (
        "a failed align published a canonical BAM — this is exactly the C2 bug")
    assert not (tmp_path / "aligned_broken.bam.bai").exists()

    # The quarantined artifact is genuinely dangerous, which is why it must not be
    # published: it opens cleanly and is silently short.
    partial = tmp_path / "aligned_broken.bam.partial"
    if partial.exists():
        with pysam.AlignmentFile(partial, "rb", check_sq=False) as f:
            short = sum(1 for _ in f)
        with pysam.AlignmentFile(fixture_bam, "rb") as f:
            full = sum(1 for _ in f)
        assert short < full, "the partial should be short — otherwise the test proves nothing"


def test_align_clears_a_stale_partial_from_an_earlier_crash(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """A crash leaves a ``.partial``; the next run must overwrite it, not trip on it."""
    _make_ubam_dir(tmp_path, fixture_bam, "retry")
    (tmp_path / "aligned_retry.bam.partial").write_bytes(b"garbage from a previous crash")
    (tmp_path / "aligned_retry.bam.partial.bai").write_bytes(b"stale index")

    proc = _run_align("retry", curlcake_ref, tmp_path)
    assert proc.returncode == 0, proc.stderr
    with pysam.AlignmentFile(tmp_path / "aligned_retry.bam", "rb") as f:
        assert sum(1 for _ in f) > 0
    assert not list(tmp_path.glob("*.partial*"))


# ---------------------------------------------------------------------------
# Structural tripwires
# ---------------------------------------------------------------------------

def test_align_script_never_sorts_into_the_canonical_path() -> None:
    text = ALIGN_SCRIPT.read_text()
    assert 'sort -o "$PARTIAL_BAM"' in text
    assert 'sort -o "$ALIGNED_BAM"' not in text, (
        "run_align.sh writes the canonical BAM directly again — see C2")


def test_basecall_script_never_streams_into_the_canonical_dir() -> None:
    """``run_basecall.sh`` cannot be executed here — it needs a live dorado-server on
    :5555 and a GPU — so its publish path is pinned structurally instead.

    This is a weaker guarantee than the functional align tests above and is recorded as
    such: the basecall publish must be confirmed on the first real GPU run.
    """
    text = BASECALL_SCRIPT.read_text()
    assert '-s "$PARTIAL_OUT"' in text
    assert '-s "$BASECALL_OUT"' not in text, (
        "run_basecall.sh streams chunks into the canonical dir again — see C2")
    # The rename must be guarded by a prior rm, or `mv A.partial A` NESTS instead of
    # replacing when A already exists.
    assert 'rm -rf "$BASECALL_OUT"' in text
    assert 'mv "$PARTIAL_OUT" "$BASECALL_OUT"' in text
    # And the output must be validated BEFORE it is published, or a failed basecall
    # becomes a poisoned cache entry the next run happily reuses.
    assert text.index('no BAM files') < text.index('mv "$PARTIAL_OUT"')
