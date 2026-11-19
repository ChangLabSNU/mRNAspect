"""Item 14a regression — the pod5-name -> cached-BAM checkpoint (Wave 4).

The checkpoint was reframed from a success-only ingestion skip-set (Open Question H2) into a
pod5->BAM cache: in pod5 mode a recorded, still-on-disk BAM lets a restart skip re-basecalling.
It is advisory — a vanished BAM, a legacy single-field line, or a torn final line degrades to a
cache MISS (re-basecall), never a crash, because ingestion is always re-run (H2) so correctness
never depends on the cache.
"""

from __future__ import annotations

from pathlib import Path

from mrnaspect.runtime.checkpoint import Checkpoint


def _bam(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"stub")  # get() only checks existence, not BAM validity
    return p


def test_record_and_get_roundtrip(tmp_path: Path) -> None:
    bam = _bam(tmp_path, "aligned_sample_0.bam")
    ckpt = Checkpoint(tmp_path / "cache.tsv")
    ckpt.record("sample_0.pod5", bam)
    # Fresh instance reads the persisted map back (durability across a restart).
    reloaded = Checkpoint(tmp_path / "cache.tsv")
    assert reloaded.get("sample_0.pod5") == bam
    assert reloaded.get("never_seen.pod5") is None


def test_get_none_when_cached_bam_vanished(tmp_path: Path) -> None:
    bam = _bam(tmp_path, "aligned_gone.bam")
    ckpt = Checkpoint(tmp_path / "cache.tsv")
    ckpt.record("gone.pod5", bam)
    bam.unlink()  # BAM deleted between runs -> must re-basecall, not serve a phantom path
    assert ckpt.get("gone.pod5") is None


def test_last_write_wins(tmp_path: Path) -> None:
    first = _bam(tmp_path, "aligned_a1.bam")
    second = _bam(tmp_path, "aligned_a2.bam")
    ckpt = Checkpoint(tmp_path / "cache.tsv")
    ckpt.record("a.pod5", first)
    ckpt.record("a.pod5", second)
    assert Checkpoint(tmp_path / "cache.tsv").get("a.pod5") == second


def test_malformed_and_legacy_lines_skipped(tmp_path: Path) -> None:
    bam = _bam(tmp_path, "aligned_good.bam")
    state = tmp_path / "cache.tsv"
    # A legacy single-field entry (old skip-set format), a torn/partial line, a blank line, and
    # one valid mapping. Load must keep the valid one and silently drop the rest (no crash).
    state.write_text(
        f"legacy_single_field.pod5\n"
        f"\n"
        f"torn_partial\t\n"
        f"good.pod5\t{bam}\n",
        encoding="utf-8",
    )
    ckpt = Checkpoint(state)
    assert ckpt.get("good.pod5") == bam
    assert ckpt.get("legacy_single_field.pod5") is None
    assert ckpt.get("torn_partial") is None


def test_absent_state_file_is_empty(tmp_path: Path) -> None:
    ckpt = Checkpoint(tmp_path / "does_not_exist.tsv")
    assert ckpt.get("anything.pod5") is None
