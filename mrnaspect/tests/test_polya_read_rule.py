"""poly(A) is measured on the read set its ruler was fitted on: forward strand, not chimeric, not split.

Decided 2026-11-02. The ruler constants were fitted on an ``admit_reach``
population -- mapped, forward strand, no ``SA`` tag, no ``pi`` tag -- and Core applied them
to every primary read. On the parity check's inputs, with the median already exact, that left the program
1.0-1.13 nt off the fit in 5 of 65 cells. The three are counted rather than measured,
so the gap between a construct's reads and its poly(A) denominator stays auditable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pysam
import pytest

from mrnaspect.analysis.modules.polya import PolyAModule
from mrnaspect.main import main as offline_main

_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "ref", "LN": 100}]})
_UUID = "0741d988-0000-0000-0000-d040f567a442"
_SA = ("SA", "ref,50,+,4M,60,0;")
_PI = ("pi", "5c2b1f0e-0000-0000-0000-000000000000")


def _read(pt: int, *, name: str, flag: int = 0, tags=(),
          model: str = "rna004_130bps_hac@v5.3.0") -> pysam.AlignedSegment:
    r = pysam.AlignedSegment(_HEADER)
    r.query_name = name
    r.flag = flag
    r.reference_id = 0
    r.reference_start = 0
    r.query_sequence = "ACGT"
    r.cigartuples = [(0, 4)]
    r.set_tag("pt", pt, "i")
    r.set_tag("RG", f"{_UUID}_{model}", "Z")
    for key, value in tags:
        r.set_tag(key, value, "Z")
    return r


def test_the_three_are_counted_and_not_measured():
    m = PolyAModule()
    m.update(_read(100, name="kept"))
    m.update(_read(10, name="reverse", flag=16))
    m.update(_read(20, name="chimera", tags=[_SA]))
    m.update(_read(30, name="split child", tags=[_PI]))
    snap = m.snapshot()

    assert snap["excluded"] == {"reverse_strand": 1, "split_mapped_sa": 1,
                                "split_child_pi": 1}
    # Out of every denominator, not only out of the lengths: 1 of 1, not 1 of 4.
    assert snap["reads_processed"] == 1 and snap["n_with_pt"] == 1
    assert snap["pt_coverage"] == 1.0 and snap["missing_pt"] == 0
    assert snap["polya_distribution"]["median"] == 100.0


def test_a_read_that_is_several_is_counted_once_in_terminal_reach_s_order():
    m = PolyAModule()
    m.update(_read(10, name="all three", flag=16, tags=[_SA, _PI]))
    m.update(_read(20, name="SA and pi", tags=[_SA, _PI]))
    assert m.snapshot()["excluded"] == {"reverse_strand": 1, "split_mapped_sa": 1,
                                        "split_child_pi": 0}


def test_a_chimera_s_supplementary_record_is_not_counted_again():
    """The supplementary record carries SA too; the read is counted on its primary."""
    m = PolyAModule()
    m.update(_read(20, name="chimera", tags=[_SA]))
    m.update(_read(20, name="chimera", flag=2048, tags=[_SA]))
    m.update(_read(20, name="elsewhere", flag=256))
    snap = m.snapshot()
    assert snap["excluded"]["split_mapped_sa"] == 1
    assert snap["reads_processed"] == 0


def test_an_excluded_read_still_names_the_model():
    """The model is a fact about the run, so a second one stops the run even when the
    read that named the first is one the tail statistics leave out."""
    m = PolyAModule()
    m.update(_read(50, name="reverse", flag=16))
    with pytest.raises(ValueError, match="two basecaller models"):
        m.update(_read(100, name="kept", model="rna004_130bps_sup@v5.3.0"))


# --- end to end: the CLI on a real BAM, against a rebuild by the rule ------------------

def _admitted(bam: Path) -> tuple[dict[str, list[int]], dict[str, int]]:
    """pt > 0 of each construct's admitted reads, and each construct's primary reads."""
    pts: dict[str, list[int]] = {}
    routed: dict[str, int] = {}
    with pysam.AlignmentFile(str(bam), "rb") as fh:
        for r in fh.fetch(until_eof=True):
            if r.is_secondary or r.is_supplementary or r.is_unmapped:
                continue
            routed[r.reference_name] = routed.get(r.reference_name, 0) + 1
            if r.is_reverse or r.has_tag("SA") or r.has_tag("pi"):
                continue
            pts.setdefault(r.reference_name, [])
            if r.has_tag("pt") and int(r.get_tag("pt")) > 0:
                pts[r.reference_name].append(int(r.get_tag("pt")))
    return pts, routed


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory, variant_bam, qk_ha_ref) -> Path:
    tmp = tmp_path_factory.mktemp("read_rule")
    src = tmp / "in"
    src.mkdir()
    (src / variant_bam.name).symlink_to(variant_bam)
    assert offline_main(["--input-dir", str(src), "--reference", str(qk_ha_ref),
                         "--output-dir", str(tmp / "out"), "--output-name", "r",
                         "--mode", "bam", "--min-reads", "1"]) == 0
    return tmp / "out" / "r"


def _ingested(run_dir: Path) -> Path:
    """The BAM the engine read: Core's own alignment of the input (since 2026-11-10)."""
    return run_dir / "basecall_cache" / "aligned_qk_ha_m1psi.bam"


def _left_out(bam: Path) -> int:
    """Mapped primaries the rule leaves out: reverse, chimeric (SA) or split (pi)."""
    with pysam.AlignmentFile(str(bam), "rb") as fh:
        return sum(1 for r in fh.fetch(until_eof=True)
                   if not (r.is_secondary or r.is_supplementary or r.is_unmapped)
                   and (r.is_reverse or r.has_tag("SA") or r.has_tag("pi")))


def test_each_construct_s_median_is_the_admitted_reads_median(run_dir):
    result = json.loads((run_dir / "qc_result.json").read_text())
    pts, routed = _admitted(_ingested(run_dir))
    checked = excluded_total = 0
    for key, snap in result["snapshots"].items():
        if not key.startswith("construct:"):
            continue
        c, p = key.split(":", 1)[1], snap["polya"]
        excluded = sum(p["excluded"].values())
        excluded_total += excluded
        assert p["reads_processed"] + excluded == routed.get(c, 0), c
        assert p["reads_processed"] == routed.get(c, 0) - excluded, c
        assert p["n_with_pt"] == len(pts.get(c, [])), c
        if pts.get(c):
            assert p["polya_distribution"]["median"] == round(
                float(np.median(pts[c])), 2), c
            checked += 1
    # The chimeras are what make this test bite. Against this two-contig reference Core's
    # alignment gives 3 primaries an SA (6 in the fixture's own alignment against run 1's
    # fifteen constructs).
    assert excluded_total == _left_out(_ingested(run_dir)) == 3
    assert checked >= 2


def test_the_page_and_the_flat_metric_say_what_was_left_out(run_dir):
    result = json.loads((run_dir / "qc_result.json").read_text())
    run_level = result["snapshots"]["polya"]["excluded"]
    assert sum(run_level.values()) == 3
    assert result["qc_metrics"]["metrics"]["polya_tail"]["reads_excluded"] == run_level
    html = (run_dir / "report_run_end.html").read_text()
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    assert "other than 3 reverse-strand, chimeric or Dorado-split ones" in text


# --- a construct whose every read is left out (seen 2026-11-02 on the v1 eval BAMs) -----

def test_when_every_read_is_left_out_the_metric_says_so():
    from mrnaspect.analysis.modules.qc_integrator import _polya_metric

    m = PolyAModule()
    m.update(_read(20, name="chimera", tags=[_SA]))
    m.update(_read(30, name="reverse", flag=16))
    entry = _polya_metric(m.snapshot())
    assert entry["status"] == "no_data"
    assert "left out" in entry["reason"]
    assert entry["reads_processed"] == 0
    assert entry["reads_excluded"] == {"reverse_strand": 1, "split_mapped_sa": 1,
                                       "split_child_pi": 0}


def test_a_measured_read_without_a_tail_keeps_the_old_reason():
    from mrnaspect.analysis.modules.qc_integrator import _polya_metric

    m = PolyAModule()
    r = _read(0, name="no tag")
    r.set_tag("pt", None)                      # removes the tag
    m.update(r)
    m.update(_read(20, name="chimera", tags=[_SA]))
    entry = _polya_metric(m.snapshot())
    assert entry["status"] == "no_data" and entry["reads_processed"] == 1
    assert entry["reason"] == "no reads carried a pt (poly-A) tag"
    assert entry["reads_excluded"]["split_mapped_sa"] == 1


def test_the_page_says_why_nothing_was_measured():
    from mrnaspect.reporting.reporter import CoAReporter

    m = PolyAModule()
    for i in range(3):
        m.update(_read(20, name=f"chimera{i}", tags=[_SA]))
    text = CoAReporter._polya_called(m.snapshot())
    assert "all 3 primary reads were reverse-strand, chimeric or Dorado-split" in text
