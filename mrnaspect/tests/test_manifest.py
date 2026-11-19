"""IngestManifest — the replay log, and the ways it must refuse to lie.

Two behaviours carry the weight here:

  * a **missing** aligned BAM truncates the replay and is otherwise harmless (the manifest
    is a prefix optimization, so a short prefix is merely slower);
  * a **present but changed** BAM is fatal, because a report whose numbers disagree with
    its own audit trail is worse than no report.

Everything else tests that the manifest never becomes a skip-set.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mrnaspect.realtime.manifest import (
    IngestManifest,
    ManifestIntegrityError,
    MANIFEST_NAME,
)


class FakeEngine:
    """Counts primary reads per BAM by reading a tiny text stand-in.

    A real BAM is not needed to test the manifest's bookkeeping, and using one would make
    these tests depend on pysam and on alignment behaviour they are not about.
    """

    def __init__(self) -> None:
        self.primary_total = 0
        self.ingested: list[str] = []

    def ingest_bam(self, bam_path: Path) -> int:
        n = int(Path(bam_path).read_text().strip())
        self.primary_total += n
        self.ingested.append(Path(bam_path).name)
        return n


def _bam(root: Path, name: str, n_primary: int) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / name
    p.write_text(str(n_primary))
    return p


def _populate(root: Path, counts: list[int]) -> IngestManifest:
    """Write a manifest describing `counts`, as a live run would have."""
    man = IngestManifest(root / MANIFEST_NAME, root=root)
    total = 0
    for i, n in enumerate(counts):
        bam = _bam(root / "bams", f"aligned_{i}.bam", n)
        total += n
        man.append(pod5_name=f"run_{i}.pod5", aligned_bam=bam,
                   n_records=n + 3, primary_total_after=total)
    return man


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_append_then_read_round_trips(tmp_path: Path) -> None:
    man = _populate(tmp_path, [10, 20, 5])
    entries, torn = man.read()
    assert not torn
    assert [e.seq for e in entries] == [0, 1, 2]
    assert [e.pod5_name for e in entries] == ["run_0.pod5", "run_1.pod5", "run_2.pod5"]
    assert [e.primary_total_after for e in entries] == [10, 30, 35]
    assert [e.n_primary for e in entries] == [10, 20, 5]
    # Relative so the whole output directory can be moved.
    assert not Path(entries[0].aligned_bam).is_absolute()


def test_replay_rebuilds_the_engine_exactly(tmp_path: Path) -> None:
    man = _populate(tmp_path, [10, 20, 5])
    engine = FakeEngine()
    report = man.replay(engine)

    assert report.n_replayed == 3
    assert report.primary_total == engine.primary_total == 35
    assert report.stopped_at is None
    assert engine.ingested == ["aligned_0.bam", "aligned_1.bam", "aligned_2.bam"], \
        "replay must feed the engine in recorded order"


def test_a_reopened_manifest_continues_the_sequence(tmp_path: Path) -> None:
    """A restart appends after the existing lines, it does not start over."""
    _populate(tmp_path, [10, 20])
    reopened = IngestManifest(tmp_path / MANIFEST_NAME, root=tmp_path)
    reopened.read()                       # loads seq/total state
    assert reopened.next_seq == 2

    bam = _bam(tmp_path / "bams", "aligned_2.bam", 7)
    entry = reopened.append(pod5_name="run_2.pod5", aligned_bam=bam,
                            n_records=7, primary_total_after=37)
    assert entry.seq == 2 and entry.n_primary == 7


# ---------------------------------------------------------------------------
# Not a skip-set
# ---------------------------------------------------------------------------

def test_replay_requires_a_fresh_engine(tmp_path: Path) -> None:
    """Replaying into a used engine would double-count. There is no "resume"."""
    man = _populate(tmp_path, [10])
    engine = FakeEngine()
    engine.primary_total = 999
    with pytest.raises(ManifestIntegrityError, match="FRESH engine"):
        man.replay(engine)


def test_deleting_the_manifest_costs_speed_not_correctness(tmp_path: Path) -> None:
    """The completeness authority is the POD5 directory, never this file."""
    man = _populate(tmp_path, [10, 20])
    (tmp_path / MANIFEST_NAME).unlink()

    fresh = IngestManifest(tmp_path / MANIFEST_NAME, root=tmp_path)
    engine = FakeEngine()
    report = fresh.replay(engine)
    assert report.n_replayed == 0 and engine.primary_total == 0
    # ...and it is immediately usable again, from seq 0.
    bam = _bam(tmp_path / "bams", "aligned_0.bam", 10)
    assert fresh.append(pod5_name="run_0.pod5", aligned_bam=bam,
                        n_records=10, primary_total_after=10).seq == 0


# ---------------------------------------------------------------------------
# Missing BAM: benign, truncates the prefix
# ---------------------------------------------------------------------------

def test_a_missing_bam_truncates_replay_without_failing(tmp_path: Path) -> None:
    """Plan test E: an evicted aligned BAM must degrade to "re-derive it", not to a crash."""
    man = _populate(tmp_path, [10, 20, 5])
    (tmp_path / "bams" / "aligned_1.bam").unlink()

    engine = FakeEngine()
    report = man.replay(engine)

    assert report.n_replayed == 1
    assert report.primary_total == 10
    assert report.stopped_at is not None
    assert report.stopped_at.pod5_name == "run_1.pod5"
    assert "missing" in report.stop_reason
    # Everything from the gap onward is the caller's to re-derive — including entry 2,
    # which is still on disk. Replaying it would leave a HOLE in the sequence.
    assert engine.ingested == ["aligned_0.bam"]
    assert report.replayed_pod5s == {"run_0.pod5"}


# ---------------------------------------------------------------------------
# Present but changed: fatal
# ---------------------------------------------------------------------------

def test_a_changed_bam_hard_fails(tmp_path: Path) -> None:
    """Plan test F. A truncated BAM, or a Core change that altered counting, must stop the
    run rather than quietly produce a report that disagrees with its own audit trail."""
    man = _populate(tmp_path, [10, 20, 5])
    (tmp_path / "bams" / "aligned_1.bam").write_text("13")   # was 20

    with pytest.raises(ManifestIntegrityError, match="disagree with its own audit trail"):
        man.replay(FakeEngine())


def test_a_corrupted_total_hard_fails(tmp_path: Path) -> None:
    man = _populate(tmp_path, [10, 20])
    path = tmp_path / MANIFEST_NAME
    lines = path.read_text().splitlines()
    lines[-1] = lines[-1].replace("\t30", "\t31")     # cumulative no longer matches
    path.write_text("\n".join(lines) + "\n")

    with pytest.raises(ManifestIntegrityError, match="disagrees with the running total"):
        IngestManifest(path, root=tmp_path).read()


# ---------------------------------------------------------------------------
# Structural integrity
# ---------------------------------------------------------------------------

def test_a_torn_final_line_is_dropped_not_fatal(tmp_path: Path) -> None:
    """Append-only means only the LAST line can be truncated by a crash."""
    man = _populate(tmp_path, [10, 20])
    path = tmp_path / MANIFEST_NAME
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("2\trun_2.pod5\tbams/aligned_2.bam\t8\t8")   # no trailing newline

    entries, torn = IngestManifest(path, root=tmp_path).read()
    assert torn is True
    assert [e.seq for e in entries] == [0, 1]


def test_a_malformed_middle_line_is_fatal(tmp_path: Path) -> None:
    """Corruption anywhere but the tail cannot be explained by a crash."""
    man = _populate(tmp_path, [10, 20, 5])
    path = tmp_path / MANIFEST_NAME
    lines = path.read_text().splitlines()
    lines[3] = "garbage"                     # 2 header lines, so this is entry 1
    path.write_text("\n".join(lines) + "\n")

    with pytest.raises(ManifestIntegrityError, match="means corruption"):
        IngestManifest(path, root=tmp_path).read()


def test_duplicate_pod5_entries_are_fatal(tmp_path: Path) -> None:
    """Replaying a duplicated line would double-count its reads."""
    man = _populate(tmp_path, [10])
    bam = _bam(tmp_path / "bams", "aligned_1.bam", 10)
    man.append(pod5_name="run_0.pod5", aligned_bam=bam,   # same POD5 name again
               n_records=10, primary_total_after=20)
    with pytest.raises(ManifestIntegrityError, match="more than once"):
        IngestManifest(tmp_path / MANIFEST_NAME, root=tmp_path).read()


def test_non_monotone_total_is_refused_at_append(tmp_path: Path) -> None:
    """Catch it on the way in, not three hours later on replay."""
    man = _populate(tmp_path, [10, 20])
    bam = _bam(tmp_path / "bams", "aligned_2.bam", 5)
    with pytest.raises(ManifestIntegrityError, match="went backwards"):
        man.append(pod5_name="run_2.pod5", aligned_bam=bam,
                   n_records=5, primary_total_after=25)     # < 30


def test_out_of_order_seq_is_fatal(tmp_path: Path) -> None:
    man = _populate(tmp_path, [10, 20, 5])
    path = tmp_path / MANIFEST_NAME
    lines = path.read_text().splitlines()
    lines[2], lines[3] = lines[3], lines[2]        # swap the first two entries
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ManifestIntegrityError, match="seq must be"):
        IngestManifest(path, root=tmp_path).read()


def test_manifest_survives_relocation_of_the_output_dir(tmp_path: Path) -> None:
    """Paths are stored relative, so an output directory can be moved or restored."""
    import shutil

    src = tmp_path / "run_a"
    _populate(src, [10, 20])
    dst = tmp_path / "run_b"
    shutil.copytree(src, dst)

    engine = FakeEngine()
    report = IngestManifest(dst / MANIFEST_NAME, root=dst).replay(engine)
    assert report.n_replayed == 2 and engine.primary_total == 30


def test_a_truncated_replay_reconciles_the_file_to_the_engine(tmp_path: Path) -> None:
    """Regression (found by D7): after a truncated replay the manifest must describe what
    the engine actually holds, not what it held before the crash.

    The log's whole purpose is to describe the sequence the engine was fed. If the
    un-replayed tail lines survive, the next ``append`` computes its delta against a total
    the engine never reached and raises "primary_total went backwards" — a spurious
    integrity failure on a *routine* eviction. The dropped entries are not lost; their
    inputs are re-derived and re-appended.
    """
    man = _populate(tmp_path, [10, 20, 5])
    (tmp_path / "bams" / "aligned_1.bam").unlink()

    engine = FakeEngine()
    report = man.replay(engine)
    assert report.n_replayed == 1 and engine.primary_total == 10

    # The file now holds exactly the replayed prefix...
    entries, _ = IngestManifest(tmp_path / MANIFEST_NAME, root=tmp_path).read()
    assert [e.seq for e in entries] == [0]
    assert entries[0].primary_total_after == 10

    # ...so the very next append continues from 10, not from the stale 35.
    bam = _bam(tmp_path / "bams", "aligned_redone.bam", 20)
    entry = man.append(pod5_name="run_1.pod5", aligned_bam=bam,
                       n_records=20, primary_total_after=30)
    assert entry.seq == 1 and entry.n_primary == 20


def test_a_torn_tail_is_also_reconciled(tmp_path: Path) -> None:
    """Same reconciliation for the crash-mid-append case, so the torn line does not
    reappear on the next read."""
    man = _populate(tmp_path, [10, 20])
    path = tmp_path / MANIFEST_NAME
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("2\trun_2.pod5\tbams/aligned_2.bam\t8\t8")   # no newline

    report = IngestManifest(path, root=tmp_path).replay(FakeEngine())
    assert report.torn_tail and report.n_replayed == 2
    assert path.read_text().endswith("\n"), "the torn line must be gone from the file"
    entries, torn = IngestManifest(path, root=tmp_path).read()
    assert not torn and [e.seq for e in entries] == [0, 1]
