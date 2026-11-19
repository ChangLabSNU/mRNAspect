"""Shared pytest fixtures for the mrnaspect package tests.

The package is importable either from an editable install or via the repo-level
``conftest.py`` (which puts the repo root on ``sys.path``). This module adds the shared
data fixtures the ported 10+7 checks and the new regression tests need.

Fixture data lives in ``mrnaspect/tests/data/`` — INSIDE the package, and tracked in git
despite the blanket ``*.bam`` ignore rule. It used to live in two places Core did not
own: the BAM in a gitignored directory (so it existed only on the machine that made it)
and the reference on one lab filesystem. Both were guarded with ``pytest.skip``, so on any other host the BAM-backed
tests did not fail — they vanished, and the suite reported green over code nothing had
executed. Core has to be testable off a clone, so the data ships with it and a missing
file is now an error.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


def _lab_private() -> dict:
    """Names that exist only on the developing lab's machines, from ``lab_private.json``
    at the repository root; ``{}`` where the file is absent, which is everywhere else.

    The file is never tracked (.gitignore). It holds ``roots`` (filesystem mounts),
    ``words`` (host and account names) and ``artifacts`` (paths of files a few
    cross-checks read). Tests that keep those names out of the published tree, and the
    few that compare Core against a lab artifact, read them from here, so the names
    themselves are in no tracked file. A check that needs an entry the file lacks skips
    and says so.
    """
    path = Path(__file__).resolve().parents[2] / "lab_private.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} must hold one JSON object")
    return data


#: See `_lab_private`. Empty outside the lab.
LAB_PRIVATE: dict = _lab_private()


def _export_ignored() -> frozenset[Path]:
    """Tracked files that are not published: the paths ``.gitattributes`` marks
    ``export-ignore``. Empty in a published tree, which has no such file because
    ``.gitattributes`` marks itself."""
    root = Path(__file__).resolve().parents[2]
    attrs = root / ".gitattributes"
    if not attrs.exists():
        return frozenset()
    marked = [line.split()[0] for line in attrs.read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.startswith("#") and "export-ignore" in line.split()[1:]]
    return frozenset(root / m for m in marked)


#: See `_export_ignored`.
NOT_PUBLISHED: frozenset[Path] = _export_ignored()


def lab_artifact(name: str) -> Path:
    """The path of a lab-only artifact, or a skip saying which entry is missing."""
    value = (LAB_PRIVATE.get("artifacts") or {}).get(name)
    if not value:
        pytest.skip(f"lab_private.json names no artifact {name!r} (a lab-only check)")
    return Path(value)

# Package-relative anchors. Kept here (one place) so a fixture move is a one-line edit.
_DATA = Path(__file__).parent / "data"
FIXTURE_BAM = _DATA / "small_aligned.bam"
CURLCAKE_REF = _DATA / "curlcake_ref.fasta"
# The fixture above flags no position on any of its four constructs (none reaches depth
# 50), which left the consensus marker layer -- and with it the whole `meta.emphasis` ->
# red-in-print path -- unrendered by any test that walks a built report. This one flags 138.
#
# 130 QK-09 and 140 HA-WT primary records from the IVT_260407 run, basecalled with stock
# `rna004_130bps_hac@v5.3.0` under dorado 2.0.0 and aligned to the 15-contig QK/HA
# reference, then reheadered to the two contigs kept. QK-09 is m1Psi-substituted and the
# basecaller is not, so its flags are real miscalls rather than anything contrived: a U
# read as C in up to 0.803 of the reads is the U->C signature this project exists to
# measure. HA-WT is unmodified. One QK and one HA because they are different lengths
# (1,200 vs 1,900 nt) and different error regimes -- at the default mismatch_threshold
# 0.5, QK-09 flags 137 of 1,158 positions above the depth gate and HA-WT 1 of 1,895; at
# 0.1, 623 and 80 (measured 2026-11-04).
#
# md5 c9925b281f12642033c93433801364d9 (bam), 5c9b472a01f96631a8b764959dd35cdc (fasta). The BAM's header was
# rewritten on 2026-11-16 so that its @PG command lines name files, not the directories of
# the machine that made it; its 270 records are byte-identical (0cc5e4fb… before).
QK_HA_BAM = _DATA / "qk_ha_m1psi.bam"
QK_HA_REF = _DATA / "qk_ha_ref.fasta"
# A REAL MinKNOW pore-activity file (Mk1D, 20.2 h, 1210 minutes x 17 channel states).
# Vendored rather than synthesized because the format is MinKNOW's, not ours, and the
# whole point of this module is to be right about a file we do not control. The run's
# flowcell/acquisition ids are stripped FROM THE FILENAME only — the contents are
# state/minute/sample-count triples and carry nothing identifying.
PORE_ACTIVITY_CSV = _DATA / "pore_activity_FIXTURE_run_acq.csv"
# A 2,000-row slice of a REAL sequencing_summary (same Mk1D run), kept for its exact
# 27-column layout. The rare end reasons are deliberately OVER-SAMPLED so the guards have
# something to count, so its proportions are NOT this run's -- never quote them as a
# measurement. The real distribution is in the module docstring.
SEQ_SUMMARY_TXT = _DATA / "sequencing_summary_FIXTURE_run_acq.txt"
# A slice of the same run's mux-scan file. Unlike the summary fixture above this one IS
# representative: all 13 scans are kept and only the channel set is reduced, so its
# voltage ramp is the run's own (-170 -> -180 mV). It sits under `other_reports/`
# because that is where MinKNOW puts it and the module's glob includes that directory.
PORE_SCAN_CSV = _DATA / "other_reports" / "pore_scan_data_FIXTURE_run_acq.csv"
GOLDEN = Path(__file__).parent / "golden" / "pre_batch_qc_result.json"


def _require(path: Path, what: str) -> Path:
    """Return ``path``, or fail loudly.

    Deliberately not ``pytest.skip``: these files are part of the package, so absence
    means a broken checkout, not a host that happens to lack lab data. A skip here would
    silently retire every BAM-backed test — which is exactly how this suite used to pass
    on a clean clone.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{what} missing: {path}\n"
            "It is tracked in git under mrnaspect/tests/data/. Check out the package fully "
            "(`git checkout -- mrnaspect/tests/data`) rather than running a partial suite.")
    return path


def run_dir_of(output_dir: Path) -> Path:
    """The single ``run_*`` subdirectory the CLI created under ``output_dir``.

    The CLI (never the pipeline classes) gives each run its own subdirectory, so a test
    that drives ``main()`` and then reads an artifact by path must go through here. It
    fails on zero or more than one rather than picking: a test that silently read the
    wrong run's ``qc_result.json`` would assert against the wrong numbers, and a test
    that finds none is the "artifact moved and nobody noticed" failure this helper exists
    to make loud.
    """
    from mrnaspect.main import RUN_DIR_PREFIX

    found = sorted(d for d in Path(output_dir).glob(f"{RUN_DIR_PREFIX}*") if d.is_dir())
    assert len(found) == 1, (
        f"expected exactly one {RUN_DIR_PREFIX}* directory under {output_dir}, "
        f"found {[d.name for d in found]}")
    return found[0]


@pytest.fixture(scope="session")
def fixture_bam() -> Path:
    """The 166-read / 4-curlcake aligned BAM used across the suite.

    Its header declares ``cc6m_2244_t7_ecorv`` as LN=2326 while the vendored FASTA is
    2323 nt: the reference it was aligned against had three trailing spaces on that
    line, which minimap2 counts as bases and samtools does not. Harmless here and left
    alone: the furthest read on that contig ends at 2279, so nothing indexes past the
    FASTA, and re-aligning the fixture would move every golden number for no gain.
    """
    return _require(FIXTURE_BAM, "fixture BAM")


@pytest.fixture(scope="session")
def variant_bam() -> Path:
    """A BAM whose consensus flags positions, which `fixture_bam`'s does not.

    Needed by anything that has to see the consensus marker layer drawn -- the markers
    are what `meta.emphasis` turns red in the print theme, so with nothing flagged that
    whole path renders nothing and a test walking the report cannot tell.
    """
    return _require(QK_HA_BAM, "QK/HA m1Psi BAM")


@pytest.fixture(scope="session")
def qk_ha_ref() -> Path:
    """The two contigs `variant_bam` is aligned to, with its `.fai` beside it."""
    return _require(QK_HA_REF, "QK/HA reference")


@pytest.fixture(scope="session")
def curlcake_ref() -> Path:
    """The 4-construct curlcake reference the fixture BAM was aligned against.

    The copies it was made from (md5 e2831e8e…) carry three trailing spaces on one sequence line, which the reference
    check now refuses; this copy has them removed (md5 6af784f6…). The ``.fai`` beside
    it was regenerated from THIS copy so it cannot go stale against it.
    """
    return _require(CURLCAKE_REF, "curlcake reference")


@pytest.fixture
def bam_input_dir(tmp_path: Path, fixture_bam: Path) -> Path:
    """A clean input dir containing only the fixture BAM (symlinked)."""
    d = tmp_path / "input"
    d.mkdir()
    (d / fixture_bam.name).symlink_to(fixture_bam)
    return d


def write_distinct_bam(src: Path, dst: Path, tag: str) -> Path:
    """Copy a BAM, re-stamping every read ID with ``tag`` so the copy holds DISTINCT reads.

    Why the tests need this
    -----------------------
    Several tests simulate a multi-input run by copying one fixture BAM under N names. That
    is convenient but not what a real run looks like: distinct POD5s hold distinct reads
    with distinct IDs. While nothing checked read identity the shortcut was harmless, and
    the tests asserted ``166 * N`` reads.

    The engine now rejects a primary read ID it has already seen this run (duplicates
    inflate depth and falsely tighten every depth-gated metric), so the shortcut would make
    every copy after the first vanish — and a test written against it would be asserting the
    double-counting the guard exists to prevent. Re-stamping the IDs restores what each test
    means to assert without weakening the guard.
    """
    import pysam

    with pysam.AlignmentFile(str(src), "rb") as fin:
        with pysam.AlignmentFile(str(dst), "wb", header=fin.header) as fout:
            for read in fin.fetch(until_eof=True):
                read.query_name = f"{tag}:{read.query_name}"
                fout.write(read)
    return dst


def passthrough_align(prefix: str, reference: Path, out_dir: Path, **_: object) -> Path:
    """An aligner that hands a bam-mode input on as it is, its own alignment and all.

    Since 2026-11-10 bam mode aligns every BAM itself (2026-11-03). A test whose
    numbers were fixed on a BAM's own alignment (the golden run: the curlcake fixture was
    aligned with -k14 and minimap2 2.28), or that needs a BAM aligned to another
    reference, passes this as ``aligner=``. It is not a setting: a user cannot turn
    alignment off. Under it the poly(A) guard trusts the config's flags, which then need
    not describe the alignment.
    """
    (staged,) = (Path(out_dir) / f"basecall_{prefix}").glob("*.bam")
    return staged


# --- pod5 prerequisites that deliberately have no default (bucket A-3, 2026-09-19) -----
# pod5 mode now refuses to start without a dorado CLIENT and a MODEL, because neither can
# ship: the client lives in ONT's proprietary bundle and models are downloaded by the user
# into no predictable place. Tests about ordering, aborts or caching have no interest in
# either, so they get stand-ins from here rather than each inventing its own — and the
# stub model is named `rna004_130bps_hac@v5.3.0` so the suite reads as the method reads
# (the version this pipeline is specified for).
_STUB_DIR = Path(tempfile.gettempdir()) / "mrnaspect_test_dorado_stubs"


def dorado_stubs() -> dict[str, str]:
    """Create (once) and return stand-in paths for the dorado client and model."""
    _STUB_DIR.mkdir(parents=True, exist_ok=True)
    client = _STUB_DIR / "ont_basecall_client"
    if not client.exists():
        client.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        client.chmod(0o755)
    model = _STUB_DIR / "rna004_130bps_hac@v5.3.0"
    model.mkdir(exist_ok=True)
    return {"client": str(client), "model": str(model)}


def pod5_ready(cfg: dict) -> dict:
    """Fill a loaded config's pod5 prerequisites with stubs. Returns the same dict."""
    stubs = dorado_stubs()
    cfg.setdefault("basecall", {})["model"] = stubs["model"]
    cfg["basecall"].setdefault("server", {})["client"] = stubs["client"]
    return cfg
