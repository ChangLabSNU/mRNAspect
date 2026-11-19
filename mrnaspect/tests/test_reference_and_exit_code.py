"""A run that analyzed nothing must not look like a run that succeeded.

Found by chasing a single surprising ``exit 0``. Feeding ``run_align.sh`` a file that is
not a FASTA produced a valid, indexed BAM with 166 reads, 0.00% mapped, and exit 0. The
cause was a five-link chain, each link individually defensible:

  1. minimap2 indexes a record-less file over ZERO sequences and exits 0 (``#seq: 0``).
  2. ``run_align.sh`` tested only that the reference path existed.
  3. The engine warns-and-degrades when a reference is unusable.
  4. The driver's exit code keyed purely on "did a PDF render", never on failed inputs.
  5. The below-threshold banner blamed loading / pore occupancy / sample integrity —
     wet-lab causes — for what was a mistyped path.

The observable end state: an operator is told their sequencing run failed, when in fact
their config was wrong and no data was ever examined. These tests pin each link.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest

from mrnaspect.tests.conftest import passthrough_align, run_dir_of, write_distinct_bam

from mrnaspect.main import OfflinePipeline, load_config, main
from mrnaspect.runtime.reference_check import ReferenceError, reference_contigs


# ---------------------------------------------------------------------------
# The validator
# ---------------------------------------------------------------------------

def test_valid_reference_returns_its_contigs(curlcake_ref: Path) -> None:
    names = reference_contigs(curlcake_ref)
    assert len(names) == 4
    assert all(n.startswith("cc6m_") for n in names), names
    # Names must be the whitespace-trimmed form samtools/minimap2 put in @SQ SN:, or they
    # will not match read.reference_name downstream.
    assert all(" " not in n for n in names)


def test_a_file_with_no_fasta_records_is_rejected(tmp_path: Path) -> None:
    """The original bug: minimap2 accepts this and maps nothing."""
    bad = tmp_path / "not_a_fasta.fa"
    bad.write_text("this is not a fasta\nsome other text\n")
    with pytest.raises(ReferenceError, match="no FASTA records"):
        reference_contigs(bad)


@pytest.mark.parametrize("setup,match", [
    (lambda p: None, "does not exist"),
    (lambda p: p.write_bytes(b""), "empty"),
    (lambda p: p.mkdir(), "not a file"),
])
def test_unusable_reference_paths_are_rejected(tmp_path: Path, setup, match) -> None:
    ref = tmp_path / "ref.fa"
    setup(ref)
    with pytest.raises(ReferenceError, match=match):
        reference_contigs(ref)


def test_duplicate_contig_names_are_rejected(tmp_path: Path) -> None:
    """Two records with one name would silently merge their pileups."""
    ref = tmp_path / "dup.fa"
    ref.write_text(">ccA\nACGT\n>ccB\nACGT\n>ccA\nTTTT\n")
    with pytest.raises(ReferenceError, match="duplicate contig name"):
        reference_contigs(ref)


def test_bare_header_is_rejected(tmp_path: Path) -> None:
    ref = tmp_path / "bare.fa"
    ref.write_text(">\nACGT\n")
    with pytest.raises(ReferenceError, match="empty name"):
        reference_contigs(ref)


def test_gzipped_reference_is_accepted(tmp_path: Path) -> None:
    """minimap2 takes gzipped FASTA, so the validator must not reject one."""
    ref = tmp_path / "ref.fa.gz"
    with gzip.open(ref, "wt") as fh:
        fh.write(">ccA desc here\nACGT\n>ccB\nTTTT\n")
    assert reference_contigs(ref) == ["ccA", "ccB"]


def test_too_many_contigs_is_rejected(tmp_path: Path) -> None:
    """Guard against pointing at a transcriptome: one pileup per contig would OOM."""
    ref = tmp_path / "many.fa"
    ref.write_text("".join(f">c{i}\nACGT\n" for i in range(50)))
    assert len(reference_contigs(ref, max_records=100)) == 50
    with pytest.raises(ReferenceError, match="more than 10"):
        reference_contigs(ref, max_records=10)


# ---------------------------------------------------------------------------
# Driver wiring
# ---------------------------------------------------------------------------

def test_driver_refuses_to_start_on_a_bad_reference(tmp_path: Path) -> None:
    """Fail at t=0, before any GPU time — not after the first basecall."""
    bad = tmp_path / "bad.fa"
    bad.write_text("no records here\n")
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    cfg = load_config(None, {"reference": str(bad), "mode": "bam", "min_reads": 1})
    with pytest.raises(ReferenceError, match="no FASTA records"):
        OfflinePipeline(in_dir, tmp_path / "out", cfg)


def test_cli_reports_a_bad_reference_cleanly(tmp_path: Path, capsys) -> None:
    """An operator config error should print one actionable line, not a traceback."""
    bad = tmp_path / "bad.fa"
    bad.write_text("no records here\n")
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    code = main(["--input-dir", str(in_dir), "--output-dir", str(tmp_path / "out"),
                 "--reference", str(bad), "--mode", "bam", "--min-reads", "1"])
    assert code == 1
    assert "no FASTA records" in capsys.readouterr().out


def test_every_input_failing_exits_non_zero(
    tmp_path: Path, curlcake_ref: Path, capsys
) -> None:
    """The headline fix. `run_accounting` was always honest; the exit code was not.

    Snakemake, CI and the GUI see only the exit code, and it used to key purely on
    "did a PDF render" — so a run where nothing was analyzed reported success.
    """
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "broken.bam").write_text("not a bam at all\n")
    out = tmp_path / "out"

    code = main(["--input-dir", str(in_dir), "--output-dir", str(out),
                 "--reference", str(curlcake_ref), "--mode", "bam", "--min-reads", "1"])
    assert code == 1, "a run that analyzed nothing must not exit 0"

    # ...and the artifact still records exactly what happened. A partial report is
    # allowed; a *silently* partial one is not.
    acc = json.loads(
        (run_dir_of(out) / "qc_result.json").read_text())["run_accounting"]
    assert acc["n_processed"] == 0
    assert acc["failed_inputs"] == ["broken.bam"]


def test_partial_failure_still_exits_zero(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """`on_error: skip` is a deliberate "keep going" — partial failure is not an error.

    Only TOTAL failure changes the exit code; the difference in kind is that a partial run
    has a result and a total one has only a rendered shell.
    """
    import shutil

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    shutil.copy(fixture_bam, in_dir / "good.bam")
    (in_dir / "zz_broken.bam").write_text("not a bam\n")
    out = tmp_path / "out"

    code = main(["--input-dir", str(in_dir), "--output-dir", str(out),
                 "--reference", str(curlcake_ref), "--mode", "bam", "--min-reads", "1"])
    assert code == 0
    acc = json.loads(
        (run_dir_of(out) / "qc_result.json").read_text())["run_accounting"]
    assert acc["n_processed"] == 1 and acc["failed_inputs"] == ["zz_broken.bam"]


def test_terminal_banner_does_not_blame_the_wet_lab(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The reporter sees only snapshots, so it cannot distinguish a poor sequencing run
    from a broken reference or failed ingests — and must not pretend otherwise."""
    import shutil

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    shutil.copy(fixture_bam, in_dir / "a.bam")
    out = tmp_path / "out"
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "min_reads": 100_000})
    OfflinePipeline(in_dir, out, cfg).run()

    html = (out / "report_run_end.html").read_text()
    assert "This run finished with" in html
    assert "run_accounting" in html, "the banner must point at the failure record"
    # Scoped to the report's OWN prose. Speculation is a property of what the document
    # says, and the terminal report now embeds the plotly bundle — whose minified source
    # contains "problem downloading your snapshot", which is not the reporter blaming
    # anyone. Searching the raw file made this guard fail the moment a third-party
    # renderer was embedded, which is the guard matching a location rather than the thing
    # it is about.
    prose = re.sub(r"<script\b.*?</script>", "", html, flags=re.S | re.I)
    assert "This run finished with" in prose, "the stripping removed the banner itself"
    for blamed in ("pore occupancy", "sample integrity", "loading"):
        assert blamed not in prose, f"banner speculates about {blamed!r} it cannot know"


# ---------------------------------------------------------------------------
# The reference is REQUIRED (not optional)
# ---------------------------------------------------------------------------

def _engine(tmp_path: Path, reference: Path):
    from mrnaspect.analysis.engine import AnalysisEngine
    from mrnaspect.reporting.reporter import CoAReporter
    return AnalysisEngine(CoAReporter(tmp_path / "out", min_reads=1), reference=reference)


def test_engine_refuses_a_missing_reference_instead_of_degrading(tmp_path: Path) -> None:
    """It used to warn and disable the pileup modules, letting per-read modules run.

    The result was a report with a healthy read count and NO sequence identity — the #1 QC
    metric silently absent. The reference is not optional, so construction now fails.
    """
    with pytest.raises(ReferenceError, match="does not exist"):
        _engine(tmp_path, tmp_path / "nope.fasta")


def test_bam_aligned_to_a_different_reference_is_rejected(
    tmp_path: Path, fixture_bam: Path
) -> None:
    """The nastiest variant: a VALID FASTA that simply is not this BAM's reference.

    Measured before the fix: mapping_rate=1.0, 166 reads, a rendered PDF, exit 0, and
    ``consensus: null``. Everything looks healthy; nothing was actually compared.
    """
    wrong = tmp_path / "wrong.fa"
    wrong.write_text(">totally_different_contig\n" + "ACGT" * 250 + "\n")
    engine = _engine(tmp_path, wrong)
    with pytest.raises(ReferenceError, match="BAM/reference mismatch"):
        engine.ingest_bam(fixture_bam)


def test_partial_contig_overlap_is_allowed(
    tmp_path: Path, fixture_bam: Path, caplog
) -> None:
    """A panel reference a given run didn't fully populate is legitimate — warn, not fail.

    Only a COMPLETELY disjoint header means the wrong reference; a subset just means some
    constructs produced no reads, which is ordinary.
    """
    import pysam

    with pysam.AlignmentFile(fixture_bam, "rb") as bam:
        first = bam.header.references[0]
    partial = tmp_path / "partial.fa"
    partial.write_text(f">{first}\n" + "ACGT" * 600 + "\n")

    engine = _engine(tmp_path, partial)
    with caplog.at_level("WARNING"):
        assert engine.ingest_bam(fixture_bam) > 0
    assert "absent from the reference" in caplog.text


def test_reference_mismatch_is_not_swallowed_by_on_error_skip(
    tmp_path: Path, fixture_bam: Path
) -> None:
    """`on_error: skip` skips a bad INPUT, not a broken configuration.

    A wrong reference is wrong for every input, so skipping would turn one accurate
    message into N misleading ones and then a report over nothing. Core's own alignment
    always names the reference's contigs, so the BAM's header disagrees only through an
    aligner that hands on a BAM aligned elsewhere; the pass-through one stands in.
    """
    import shutil

    wrong = tmp_path / "wrong.fa"
    wrong.write_text(">nope_not_this_one\n" + "ACGT" * 250 + "\n")
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    shutil.copy(fixture_bam, in_dir / "a.bam")
    cfg = load_config(None, {"reference": str(wrong), "mode": "bam", "min_reads": 1,
                             "on_error": "skip"})
    pipe = OfflinePipeline(in_dir, tmp_path / "out", cfg, aligner=passthrough_align)
    with pytest.raises(ReferenceError, match="BAM/reference mismatch"):
        pipe.run()


def test_cli_shuts_down_gracefully_on_a_mismatched_reference(
    tmp_path: Path, fixture_bam: Path, qk_ha_ref: Path, capsys
) -> None:
    """Graceful = one actionable line and exit 1, not a traceback and not a fake report.

    Curlcake reads against the QK/HA constructs, through Core's own alignment: none of
    the 166 maps, and 7 distinct copies take the run past the mapping guard's 1,000.
    """
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for i in range(7):
        write_distinct_bam(fixture_bam, in_dir / f"{i}.bam", str(i))
    out = tmp_path / "out"

    code = main(["--input-dir", str(in_dir), "--output-dir", str(out),
                 "--reference", str(qk_ha_ref), "--mode", "bam", "--min-reads", "1"])
    assert code == 1
    assert "mapped to qk_ha_ref.fasta" in capsys.readouterr().out
    # And no report was produced claiming to characterize the run. Checked against the
    # run directory the CLI actually writes into: asserting on `out/` alone would pass
    # merely because the artifacts moved a level down, constraining nothing.
    runs = sorted(d for d in out.glob("run_*") if d.is_dir())
    assert not any((d / "report_run_end.pdf").exists() for d in runs), (
        f"a report was rendered for a run that could not use its reference: {runs}")


# --- basecall model path is validated at startup, like the reference --------

def test_missing_basecall_model_fails_at_startup(tmp_path, curlcake_ref):
    """Same reasoning as the reference check: a run that cannot basecall must say so at
    t=0, not after the engine and reporter are stood up and the first POD5 arrives.

    Caught this for real — a config edit pointed at hac@v5.3.0, which the 7.13.6 bundle
    does not ship (it has v5.2.0 and v6.0.0).
    """
    import pytest
    from mrnaspect.main import OfflinePipeline, load_config

    (tmp_path / "in").mkdir()
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "pod5",
                             "min_reads": 1})
    cfg["basecall"] = {"model": "/no/such/models/rna004_130bps_hac@v9.9.9"}
    with pytest.raises(FileNotFoundError, match="basecall.model does not exist"):
        OfflinePipeline(tmp_path / "in", tmp_path / "out", cfg)


def test_bam_mode_ignores_the_model_path(tmp_path, curlcake_ref):
    """In bam mode nothing basecalls, so a stale model path must not block the run."""
    from mrnaspect.main import OfflinePipeline, load_config

    (tmp_path / "in").mkdir()
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "min_reads": 1})
    cfg["basecall"] = {"model": "/no/such/model"}
    OfflinePipeline(tmp_path / "in", tmp_path / "out", cfg)   # must not raise


# ---------------------------------------------------------------------------
# The wrong-reference guard (2026-09-10)
# ---------------------------------------------------------------------------
# Link 3 above ("the engine warns-and-degrades when a reference is unusable") had a twin
# the original chain missed: a reference that is perfectly USABLE but belongs to another
# construct. `reference_contigs` accepts it, `_check_bam_matches_reference` accepts it
# (header and reference agree — they were built from the same wrong FASTA), and the run
# proceeds to 0.00 % mapped with only a low-reads warning. Observed on the IVT_260407
# replay against the shipped default curlcake reference: 0 / 19,992 mapped over four
# POD5s. These tests pin the guard that stops it. Since bam mode aligns every BAM itself
# (2026-11-10), they reproduce it directly: curlcake reads against the QK/HA constructs,
# of which none of the 166 maps.

def _feed(pipe, in_dir: Path, names: list[str]) -> None:
    for name in names:
        pipe._process_one_input(in_dir / name)


def test_shipped_guard_thresholds_are_the_reviewed_ones() -> None:
    """Pin the two numbers: a silent edit changes what every run is allowed to report."""
    from mrnaspect.main import MAPPING_GUARD_MIN_READS, MAPPING_GUARD_MIN_RATE

    assert MAPPING_GUARD_MIN_READS == 1000
    assert MAPPING_GUARD_MIN_RATE == 0.01


def test_zero_mapped_run_is_stopped_at_the_read_threshold(
    tmp_path: Path, fixture_bam: Path, qk_ha_ref: Path
) -> None:
    """The real failure, at the SHIPPED threshold — 166 x 7 = 1162 > 1000 primary reads."""
    from mrnaspect.main import OfflinePipeline, load_config

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    names = [f"{i}.bam" for i in range(7)]
    for name in names:
        write_distinct_bam(fixture_bam, in_dir / name, Path(name).stem)

    cfg = load_config(None, {"reference": str(qk_ha_ref), "mode": "bam",
                             "min_reads": 1})
    pipe = OfflinePipeline(in_dir, tmp_path / "out", cfg)

    with pytest.raises(ReferenceError, match="mapped to"):
        _feed(pipe, in_dir, names)
    # Both hypotheses must reach the operator; naming only the reference would send
    # someone to re-check a config when their flow cell died.
    try:
        pipe2 = OfflinePipeline(in_dir, tmp_path / "out2", cfg)
        _feed(pipe2, in_dir, names)
    except ReferenceError as exc:
        msg = str(exc)
    assert "reference does not match" in msg
    assert "run / basecall failed" in msg
    assert "read length mean=" in msg and "qscore median=" in msg


def test_guard_does_not_fire_below_the_read_threshold(
    tmp_path: Path, fixture_bam: Path, qk_ha_ref: Path
) -> None:
    """Edge case: 0 % mapped on 166 reads is not yet evidence — 166 < 1000.

    The guard must not convert "too early to tell" into an abort, or a run whose first
    POD5 is a small ramp-up partial dies on a verdict it had no power to reach.
    """
    from mrnaspect.main import OfflinePipeline, load_config

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    write_distinct_bam(fixture_bam, in_dir / "a.bam", "a")

    cfg = load_config(None, {"reference": str(qk_ha_ref), "mode": "bam",
                             "min_reads": 1})
    pipe = OfflinePipeline(in_dir, tmp_path / "out", cfg)

    pipe._process_one_input(in_dir / "a.bam")       # must not raise
    assert pipe.engine.run_summary.snapshot()["mapping"]["mapped"] == 0
    assert pipe.engine.primary_total < 1000
    assert pipe._mapping_guard_done is False        # verdict deferred, not passed


def test_correctly_referenced_run_passes_the_guard(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Happy path: the same volume of reads, mapped, runs to completion."""
    from mrnaspect.main import OfflinePipeline, load_config

    from .conftest import write_distinct_bam

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    names = [f"{i}.bam" for i in range(7)]
    for name in names:
        write_distinct_bam(fixture_bam, in_dir / name, Path(name).stem)

    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "min_reads": 1})
    pipe = OfflinePipeline(in_dir, tmp_path / "out", cfg)

    _feed(pipe, in_dir, names)                      # must not raise

    assert pipe.engine.primary_total >= 1000
    assert pipe._mapping_guard_done is True         # evaluated, and passed


@pytest.mark.parametrize("seq, bad", [
    ("ACGTNACGT", "N"), ("ACGTRYACGT", "R"), ("ACGTXACGT", "X"), ("ACG-TACGT", "-"),
    ("ACGT*ACGT", "*"), ("1 acgtacgt", "1"), ("MKVLAAGIVG", "M"), ("ACGT ACGT", " "),
    ("ACGTACGT   ", " "),
])
def test_a_sequence_character_other_than_acgtu_is_refused(
    tmp_path: Path, seq: str, bad: str
) -> None:
    """Only fully specified bases: anything else is unmatchable or read differently by
    minimap2 (which counts a space as a base) and samtools (which does not)."""
    ref = tmp_path / "ref.fa"
    ref.write_text(f">c1\nACGTACGT\n{seq}\n")
    with pytest.raises(ReferenceError, match=f"line 3, column {seq.index(bad) + 1}: "
                                             f"{re.escape(repr(bad))} is not A, C, G, T or U"):
        reference_contigs(ref)


def test_lowercase_u_and_crlf_line_ends_are_accepted(tmp_path: Path) -> None:
    ref = tmp_path / "ref.fa"
    ref.write_bytes(b">c1\r\nacguACGU\r\n\r\nACGT\r\n>c2 description\nGGCC\n")
    assert reference_contigs(ref) == ["c1", "c2"]


def test_a_record_without_sequence_is_refused(tmp_path: Path) -> None:
    ref = tmp_path / "ref.fa"
    ref.write_text(">c1\nACGT\n>c2\n>c3\nGGCC\n")
    with pytest.raises(ReferenceError, match="record 'c2' has no sequence"):
        reference_contigs(ref)


def test_a_bad_character_in_a_gzipped_reference_is_refused(tmp_path: Path) -> None:
    ref = tmp_path / "ref.fa.gz"
    with gzip.open(ref, "wt") as fh:
        fh.write(">c1\nACGTNACGT\n")
    with pytest.raises(ReferenceError, match="'N' is not A, C, G, T or U"):
        reference_contigs(ref)
