"""The PDF's first page: what was run, then every construct at a glance (2026-11-10).

Until then page 1 held the title, a sample table that, with no details declared, had one
row (the test date), and the run-level results table with its long method texts. The
per-construct numbers began on page 2, and on a short run the "Per-construct Results"
heading was left alone at the foot of page 1.

Now, in print only: a Run box (reference, input, basecall model; the full record stays
in Provenance), and a table with one row per construct whose values are copied from that
construct's own table, so the two cannot disagree. Per-construct Results opens a page. The
screen is unchanged.

2026-11-13 (from the Mac): beside the Run box the sample table took half the page
and its rows wrapped ("too narrow"), so the two now stack, each the page's width; and the
glance table was the only one whose numbers sat right-aligned, so it reads left like every
other table.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.reporting.reporter import CoAReporter, flagged_positions
from mrnaspect.snapshot_keys import construct_key

from .test_min_reads_per_construct import _text
from .test_print_prose import _print_html

SAMPLE = {"arm": "m1psi", "arms": {"HA-WT": "U"}}
# The flags the pipeline hands the poly(A) module; without them the correction is withheld.
POLYA = {"polya": {"minimap2_flags": "-ax map-ont -k7 -w5 --secondary=no -y"}}
GLANCE_HEADER = ["Construct", "Reads", "Per-base identity", "Poly(A), uncorrected",
                 "Poly(A), corrected", "Flagged positions"]


@pytest.fixture(scope="module")
def printed(tmp_path_factory, variant_bam, qk_ha_ref) -> tuple[str, Path, CoAReporter]:
    out = tmp_path_factory.mktemp("first_page")
    reporter = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(reporter, reference=qk_ha_ref, sample=SAMPLE,
                            module_params=POLYA)
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    reporter.note_run_context(reference=qk_ha_ref, mode="bam",
                              inputs="1 file(s) from /data/run_1")
    return _print_html(reporter), out / "report_run_end.pdf", reporter


def _cells(row: str) -> list[str]:
    return [html.unescape(_text(c))
            for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]


def _glance(html_str: str) -> list[list[str]]:
    table = re.search(r'<table class="results glance">(.*?)</table>', html_str, re.S)
    assert table, "no glance table in the print HTML"
    return [_cells(r) for r in re.findall(r"<tr>(.*?)</tr>", table.group(1), re.S)]


def _pages(pdf: Path) -> list[str]:
    import pypdf  # not importorskip: without the reader this must fail, not skip
    return [re.sub(r"\s+", " ", p.extract_text() or "")
            for p in pypdf.PdfReader(str(pdf)).pages]


def test_every_construct_has_a_row_with_its_own_tables_values(printed) -> None:
    html_str, _, reporter = printed
    header, *body = _glance(html_str)
    assert header == GLANCE_HEADER
    sections = reporter._build_constructs()
    assert [row[0] for row in body] == [c["name"] for c in sections]
    for row, c in zip(body, sections):
        own = {r["name"]: r["value"] for r in c["rows"]}
        flagged = flagged_positions(
            reporter._snapshots[construct_key(c["name"])]["consensus"])
        assert row[1:] == [own["Assigned reads"], own["Per-base identity"],
                           own["Poly(A) tail length, uncorrected (median)"],
                           own["Poly(A) tail length, corrected (median)"],
                           f"{flagged['count']:,}"], c["name"]


def test_the_corrected_column_is_not_a_copy_of_the_called_one(printed) -> None:
    """QK-09 is declared m1Psi, so its correction applies; HA-WT is U and gets its own."""
    _, *body = _glance(printed[0])
    rows = {r[0]: r for r in body}
    assert rows["QK-09"][3] != rows["QK-09"][4]
    assert rows["QK-09"][4].endswith(" nt")


def test_the_run_box_names_the_reference_input_and_model(printed) -> None:
    box = re.search(r'<table class="meta-block run-box">(.*?)</table>', printed[0], re.S)
    assert box, "no run box in the print HTML"
    rows = {r[0]: r[1] for r in map(_cells, re.findall(r"<tr>(.*?)</tr>", box.group(1),
                                                        re.S))}
    assert rows == {"Reference": "qk_ha_ref.fasta",
                    "Input": "1 file(s) from /data/run_1 (BAM)",
                    "Basecall model": "rna004_130bps_hac@v5.3.0"}


def test_page_one_holds_the_run_box_and_every_construct(printed) -> None:
    _, pdf, reporter = printed
    first = _pages(pdf)[0]
    assert "Results at a glance" in first and "qk_ha_ref.fasta" in first
    for c in reporter._build_constructs():
        assert c["name"] in first
    # The section's first sentence; its name also occurs in the run table's prose.
    assert "received reads" not in first


def test_per_construct_results_open_a_page(printed) -> None:
    """Never again a heading alone at the foot of a page."""
    (page,) = [p for p in _pages(printed[1]) if "received reads" in p]
    assert "Per-construct results" in page
    assert "Mapping rate" not in page        # the run table ended on an earlier page


def test_the_screen_is_unchanged(printed) -> None:
    screen = (printed[1].parent / "report_run_end.html").read_text()
    assert '<table class="results glance">' not in screen
    assert '<table class="meta-block run-box">' not in screen
    assert '<table class="id-grid">' not in screen


def test_the_sample_and_the_run_stack_at_the_page_s_width(printed) -> None:
    """The sample's table first, then the run's, each a table of the full text width: no
    two-column grid holding them side by side."""
    html_str = printed[0]
    assert '<table class="id-grid">' not in html_str
    sample = html_str.index("Sample and batch")
    run = html_str.index('<table class="meta-block run-box">')
    glance = html_str.index('<table class="results glance">')
    assert sample < run < glance


def test_the_glance_table_aligns_as_every_other_table_does() -> None:
    """Left, like the results tables beneath it: no rule right-aligns its cells."""
    from mrnaspect.reporting import reporter as R

    template = (Path(R.__file__).parent / "templates" / "sample_coa.html").read_text()
    rules = re.findall(r"table\.results\.glance[^{]*\{[^}]*\}", template)
    assert rules, "the glance table's rules moved; update this test"
    assert not [r for r in rules if "text-align: right" in r or "text-align:right" in r], rules
