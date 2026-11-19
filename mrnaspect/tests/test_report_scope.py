"""The report states measurements, and says so in words rather than in empty columns.

Decided 2026-10-05: this product sets no acceptance criteria, because a criterion is a
decision about the analyte and belongs to whoever owns the specification. The signoff
also stopped calling the document a Certificate of Analysis, a claim about conformance
that a report with no criteria cannot support.

Decided 2026-11-09 (option A of the 11-05 triage): while no criterion exists, the
results tables have no "Acceptance criterion" or "Status" column. Every row but one read
"not set" and "N/A" there, and the one gate Core enforces, the read minimum, sat in those
columns as ">= N" and PASS. The minimum now sits in that row's own words, and a report
forced out below it says so in the row, where FAIL used to.

These tests pin the stance in both directions: the scope is still stated, the CoA claim
stays gone, no row carries a verdict, and the one real gate is not retired with the
columns. That last one matters most -- the cheap way to "report data only" is to stop
stating the gate Core actually enforces.
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path

import pytest

from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.reporting.reporter import CoAReporter
from .conftest import CURLCAKE_REF, FIXTURE_BAM, _require


@pytest.fixture(scope="module")
def report_html(tmp_path_factory) -> str:
    """One real run through the shipped pipeline — these assertions are about the artifact.

    Rendered rather than asserted against the template source: what a reviewer reads is
    the output, and a template variable can be correct while the page it produces is not.
    """
    root = tmp_path_factory.mktemp("scope")
    src, out = root / "in", root / "out"
    src.mkdir()
    (src / "small_aligned.bam").write_bytes(
        _require(FIXTURE_BAM, "fixture BAM").read_bytes())
    cfg = load_config(None, {"reference": str(_require(CURLCAKE_REF, "reference")),
                             "mode": "bam",
                             "min_reads": 1})
    OfflinePipeline(src, out, cfg).run()
    html = sorted(out.glob("report_*.html"))
    assert html, "the pipeline rendered no HTML report"
    return html[0].read_text()


def _text(html: str) -> str:
    """Tag-stripped, entity-decoded, whitespace-collapsed.

    Entities are decoded because the assertions are about what a READER sees: the
    5' in "Full-length ratio (5' reach)" is `5&#39;` in the source.
    """
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", html)))


def _results_text(html: str) -> str:
    """The text of every results table on the page."""
    tables = re.findall(r'<table class="results">(.*?)</table>', html, re.S)
    assert tables, "no results table on the page"
    return _text(" ".join(tables))


def _cells(html: str, test: str) -> list[str]:
    """The cells of the results row whose Test cell is ``test``."""
    for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        cells = [_text(c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if cells and cells[0] == test:
            return cells
    raise AssertionError(f"no {test!r} row on the page")


def test_the_scope_is_stated_in_words(report_html):
    """With no criterion column, the page's own sentence carries the scope."""
    body = _text(report_html).lower()
    assert "no acceptance criteria set" in body
    # And the responsibility, which the removed 10-05 note's last sentence carried.
    assert "subject to qc manager review" in body


def test_it_does_not_claim_to_be_a_certificate_of_analysis(report_html):
    """A certificate asserts conformance; a report with no criteria cannot."""
    body = _text(report_html).lower()
    assert "not a certificate of analysis" in body
    # and nowhere does it assert the opposite
    assert not re.search(r"this certificate of analysis", body)


def test_no_row_carries_a_verdict_or_an_unset_criterion(report_html):
    tables = _results_text(report_html)
    for word in ("Acceptance criterion", "Status", "not set", "PASS", "FAIL", "N/A"):
        assert not re.search(rf"(?<!\w){re.escape(word)}(?!\w)", tables), word


def test_the_read_minimum_is_stated_in_its_row(report_html):
    """The fixture runs with min_reads = 1. Bounded, so that a depth or a percentage in
    the same row cannot stand in for it."""
    _test, method, result = _cells(report_html, "Primary mapped reads")
    assert re.search(r"at least 1(?![\d,.])", method), method
    assert "min_reads" in method, "the reader needs the setting's name to change it"
    assert result.replace(",", "").isdigit(), "a met minimum adds nothing to the count"


@pytest.mark.parametrize("mapped,below", [(5, True), (500, False)])
def test_a_forced_report_below_the_minimum_says_so_in_its_row(tmp_path, mapped, below):
    """FAIL used to say this. The banner above the tables says it too; the row says it
    where a reader of the table looks."""
    rep = CoAReporter(tmp_path / "out", min_reads=100)
    rep.update("run_summary", {"mapping": {"total": mapped, "mapped": mapped}})
    html = Path(rep.finalize("run_end", force=True)["html_path"]).read_text()
    _test, method, result = _cells(html, "Primary mapped reads")
    assert re.search(r"at least 100(?![\d,.])", method), method
    assert result.startswith(f"{mapped:,}")
    assert ("below the minimum" in result) is below, result
