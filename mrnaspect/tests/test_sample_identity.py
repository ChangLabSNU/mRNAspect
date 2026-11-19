"""Who says so, and whether the report can tell.

The sample block used to be six rows hardcoded to an em dash. The table therefore
asserted six facts it had never been told, and on a document headed "certificate" that is
not a cosmetic problem: a reader could not distinguish a declared lot number from a
machine-read flow cell serial, because both were absent in the same way.

Two groups now. What a person declares cannot be checked by anything here. What MinKNOW
recorded, nobody typed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mrnaspect import PRODUCT_NAME
from mrnaspect.analysis.minknow import read_final_summary
from mrnaspect.reporting.reporter import CoAReporter

pytest.importorskip("jinja2")
pytest.importorskip("matplotlib")

FINAL_SUMMARY = """instrument=MD-100797
position=
flow_cell_id=FBA31122
sample_id=
protocol_group_id=20260406_QK_sequencing
protocol=sequencing/sequencing_MIN004RA_RNA:FLO-MIN004RA:SQK-RNA004:130
started=2026-04-06T12:10:30.879806+09:00
acquisition_stopped=2026-04-07T08:19:48.632138+09:00
basecalling_enabled=1
"""


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    (run / "pod5").mkdir(parents=True)
    (run / "final_summary_FBA31122_a3b_036.txt").write_text(FINAL_SUMMARY)
    return run


# --- reading the instrument's own record -------------------------------------

def test_the_summary_is_found_one_level_up_from_the_data(run_dir: Path) -> None:
    """--input-dir <run>/pod5 is the ordinary case; the file sits beside pod5/."""
    assert read_final_summary(run_dir / "pod5")["flow_cell_id"] == "FBA31122"
    assert read_final_summary(run_dir)["flow_cell_id"] == "FBA31122"


def test_an_empty_value_is_absent_not_blank(run_dir: Path) -> None:
    """MinKNOW writes `sample_id=` with nothing after it when the operator left the box
    blank. Carrying that through would put an empty Sample ID row on a certificate,
    which reads as "we looked and it is blank" rather than "nobody filled this in"."""
    got = read_final_summary(run_dir / "pod5")
    assert "sample_id" not in got
    assert "position" not in got


def test_no_summary_is_an_empty_dict_not_a_crash(tmp_path: Path) -> None:
    """A BAM handed over without its run directory is an ordinary case."""
    assert read_final_summary(tmp_path) == {}


def test_a_malformed_line_is_skipped_rather_than_raised_on(tmp_path: Path) -> None:
    """This names the run; it does not produce a number."""
    (tmp_path / "final_summary_x.txt").write_text("good=1\nnonsense\n=orphan\nalso=2\n")
    assert read_final_summary(tmp_path) == {"good": "1", "also": "2"}


# --- the two groups ----------------------------------------------------------

def test_declared_and_recorded_do_not_mix(tmp_path: Path, run_dir: Path) -> None:
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_sample(declared={"product": "QK-mRNA", "batch_id": "LOT-2026-04"},
                  recorded=read_final_summary(run_dir / "pod5"))
    rows = r._sample_rows()
    assert [x["name"] for x in rows["declared"]] == ["Product", "Batch / lot ID"]
    assert "Flow cell" in [x["name"] for x in rows["recorded"]]
    assert "Product" not in [x["name"] for x in rows["recorded"]]


def test_an_unset_field_gets_no_row_rather_than_a_dash(tmp_path: Path) -> None:
    """A dash reads as "we looked and there is nothing there", which is a different
    claim from "nobody told us" — and the second is the true one."""
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_sample(declared={"product": "QK", "manufacturer": None,
                            "batch_id": "", "operator": "   "})
    assert [x["name"] for x in r._sample_rows()["declared"]] == ["Product"]


def test_the_declared_rows_keep_the_order_a_coa_reads_in(tmp_path: Path) -> None:
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_sample(declared={"operator": "o", "product": "p", "manufacturer": "m"})
    assert [x["name"] for x in r._sample_rows()["declared"]] == [
        "Manufacturer", "Product", "Operator"]


# --- what the report says ----------------------------------------------------

def test_a_run_with_nothing_declared_says_so(tmp_path: Path) -> None:
    """It used to show the run label in the batch row and explain, underneath, that the
    row was the run label and not a lot number. Explaining a wrong value is weaker than
    not printing one: the row is gone, and the note states the consequence instead."""
    r = CoAReporter(tmp_path, min_reads=1)
    r.finalize("run_end", force=True)
    html = (tmp_path / "report_run_end.html").read_text()
    assert "No sample or batch details were supplied" in html
    assert "Nothing in\n    this report identifies the material it measures" in html
    assert "--batch-id" in html, "and how to supply them"


def test_declared_details_replace_the_notice(tmp_path: Path) -> None:
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_sample(declared={"product": "QK-mRNA", "batch_id": "LOT-2026-04"})
    r.finalize("run_end", force=True)
    html = (tmp_path / "report_run_end.html").read_text()
    assert "LOT-2026-04" in html
    assert "No sample or batch details were supplied" not in html
    assert "does not verify them" in html, "reported as given, and said to be"


def test_the_instrument_block_is_labelled_as_the_instrument(tmp_path: Path,
                                                            run_dir: Path) -> None:
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_sample(recorded=read_final_summary(run_dir / "pod5"))
    r.finalize("run_end", force=True)
    html = (tmp_path / "report_run_end.html").read_text()
    assert "Recorded by the instrument" in html
    assert "FBA31122" in html and "MD-100797" in html
    assert "Nobody typed these" in html


def test_the_offline_driver_supplies_both(tmp_path: Path, fixture_bam: Path,
                                          curlcake_ref: Path) -> None:
    from mrnaspect.main import OfflinePipeline, load_config

    run = tmp_path / "run"
    (run / "bam_pass").mkdir(parents=True)
    shutil.copy(fixture_bam, run / "bam_pass" / "a.bam")
    (run / "final_summary_FBA31122_a3b_036.txt").write_text(FINAL_SUMMARY)
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "consensus_mode": "unmodified", "min_reads": 1,
                             "sample.product": "QK-mRNA",
                             "sample.batch_id": "LOT-2026-04"})
    OfflinePipeline(run / "bam_pass", tmp_path / "out", cfg).run()
    html = (tmp_path / "out" / "report_run_end.html").read_text()
    assert "QK-mRNA" in html and "LOT-2026-04" in html
    assert "FBA31122" in html, "the instrument's record must be picked up too"

# --- the render label is a process fact, and was being printed as a data fact --------

def _render(tmp_path, fixture_bam, curlcake_ref, *, declared=None, recorded=None,
            terminal=True, label="run_end"):
    from mrnaspect.analysis.engine import AnalysisEngine

    r = CoAReporter(tmp_path, min_reads=1)
    if declared is not None or recorded is not None:
        r.note_sample(declared=declared or {}, recorded=recorded or {})
    engine = AnalysisEngine(r, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    if terminal:
        r.finalize(label, force=True)
    else:
        r.report(label, force=True)
    return r, sorted(tmp_path.glob(f"report_{label}.*"))


def test_the_title_names_the_document_not_the_render_reason(tmp_path, fixture_bam,
                                                            curlcake_ref):
    """"QCRNA QC Report (run_end)" asked a reader to know the reporter's own vocabulary.

    `run_end`, `user_abort` and `interim_3` are why the RENDERER ran. The distinction
    underneath is real and matters -- an interim must never be read as a final result --
    so it stays, in words a reader already has.
    """
    import re

    r, _ = _render(tmp_path / "f", fixture_bam, curlcake_ref)
    html = (tmp_path / "f" / "report_run_end.html").read_text()
    title = re.search(r"<h1>(.*?)</h1>", html).group(1)
    assert title == f"{PRODUCT_NAME} QC report (final)", title
    assert "run_end" not in title

    r, _ = _render(tmp_path / "i", fixture_bam, curlcake_ref,
                   terminal=False, label="interim_3")
    html = (tmp_path / "i" / "report_interim_3.html").read_text()
    title = re.search(r"<h1>(.*?)</h1>", html).group(1)
    assert title == f"{PRODUCT_NAME} QC report (interim)", title
    # The sequence number stays where it is exact.
    assert (tmp_path / "i" / "report_interim_3.html").exists()


def test_the_run_label_never_fills_the_batch_field(tmp_path, fixture_bam, curlcake_ref):
    """With nothing declared, "Batch / lot ID: run_end" put an internal enum in a field
    an auditor reads as a lot number. An absent field gets no row -- the rule this
    report already applies to every other declared field."""
    import re

    _render(tmp_path, fixture_bam, curlcake_ref)
    html = (tmp_path / "report_run_end.html").read_text()
    body = html[html.index("<body>"):]
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S)
    # The provenance panel records the COMMAND that produced the report, which is a
    # process fact and belongs there. It is excluded before the substring check because
    # the check is about a spelling, not about a field: running the suite sharded puts
    # an explicit file list on the command line, and one of those files is
    # `test_instrument_appears_at_run_end.py`. That made this assertion fail on a report
    # that was entirely correct -- a guard watching the spelling of a thing rather than
    # the thing. (Found 2026-10-27 under a 4-shard split; it passes serially because
    # `pytest mrnaspect/tests/` names no files.)
    body = re.sub(r"<table class=\"provenance\".*?</table>", " ", body, flags=re.S)
    body = re.sub(r"<tr><th>Command</th>.*?</tr>", " ", body, flags=re.S)
    assert "Batch / lot ID" not in body, "no batch row when nobody declared one"
    assert "run_end" not in body, "the render label is not data about the sample"
    assert "No sample or batch details were supplied" in body, "and the report says so"


class TestEveryPrintedPageIsAttributable:
    """A report is 17 pages on four constructs and 12 of them are figures. The footer
    said "Page 4 of 17" and nothing else, so a page pulled out of the set did not say
    what it measured."""

    @staticmethod
    def _footers(pdf):
        """(page count, per-page text) from the rendered PDF.

        pypdf, not poppler's `pdftotext`, since 2026-10-23 — a licence change, not a
        technical one: poppler is GPL-2.0-or-later and shipped 33 MB of binaries into the
        environment the .app carries, for a test. Verified equivalent before the swap on
        three real report PDFs (15 pages each): identical page counts, and identical
        answers for every assertion below, the negative one included.

        **Membership only.** pypdf extracts in content-stream order, not physical order,
        so an assertion about WHERE on the page something sits, or about the order of two
        columns, would be meaningless here even if it passed. Assert that a string is on
        a page, not where.
        """
        from pypdf import PdfReader

        reader = PdfReader(str(pdf))
        return len(reader.pages), [page.extract_text() for page in reader.pages]

    def test_declared_identifiers_lead(self, tmp_path, fixture_bam, curlcake_ref):
        pytest.importorskip("weasyprint")
        _render(tmp_path, fixture_bam, curlcake_ref,
                declared={"batch_id": "LOT-4471", "sample_id": "QK-09"})
        n, pages = self._footers(next(tmp_path.glob("*.pdf")))
        assert n > 1, "a one-page report would not test anything"
        missing = [i for i, t in enumerate(pages, 1) if "batch LOT-4471" not in t]
        assert not missing, f"pages with no sample identity: {missing}"

    def test_minknow_identifiers_when_nothing_was_declared(self, tmp_path, fixture_bam,
                                                           curlcake_ref):
        """Nobody typed these, which is the whole reason they are worth printing."""
        pytest.importorskip("weasyprint")
        _render(tmp_path, fixture_bam, curlcake_ref,
                recorded={"sample_id": "QK-09", "flow_cell_id": "FAV12345"})
        n, pages = self._footers(next(tmp_path.glob("*.pdf")))
        missing = [i for i, t in enumerate(pages, 1) if "flow cell FAV12345" not in t]
        assert not missing, f"pages with no sample identity: {missing}"

    def test_it_says_so_rather_than_falling_back_on_the_run_label(
            self, tmp_path, fixture_bam, curlcake_ref):
        """The fallback that existed was the run label, which is how "run_end" came to be
        printed in a field headed Batch / lot ID. Silence is not the answer either: a QC
        lab seeing "no sample identifier recorded" learns something true."""
        pytest.importorskip("weasyprint")
        _render(tmp_path, fixture_bam, curlcake_ref)
        n, pages = self._footers(next(tmp_path.glob("*.pdf")))
        missing = [i for i, t in enumerate(pages, 1)
                   if "no sample identifier recorded" not in t]
        assert not missing, f"pages that say nothing about identity: {missing}"
        # The footer, not the whole page: the provenance panel legitimately prints the
        # command that produced the report, and under a sharded run that command names
        # `test_instrument_appears_at_run_end.py`. Scanning every page for the substring
        # therefore failed on a correct report. What this test is about is the FOOTER
        # falling back to the render label, which the positive assertion above already
        # pins -- so the remaining check is that no footer carries it.
        footers = [t.rsplit("\n", 4)[-1] for t in pages]
        assert not any("run_end" in f for f in footers), (
            "a page footer fell back to the render label")
