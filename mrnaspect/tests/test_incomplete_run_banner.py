"""An incomplete run must SAY SO on the report, not only in run.log.

``on_error: skip`` is the shipped default and it exits 0, so a run that lost inputs used to
produce a clean-looking CoA, a success exit code, and a count buried in JSON. The loss is
not random — an OOM takes the largest POD5s, a basecall failure clusters on abnormal signal
— so the missing reads correlate with the properties being measured, and every rate in the
report describes a filtered population while naming the run.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.reporting.reporter import CoAReporter
from .conftest import write_distinct_bam


def _cfg(ref: Path, **over):
    return load_config(None, {"reference": str(ref), "mode": "bam",
                              "min_reads": 1, **over})


# --- the notice itself ------------------------------------------------------

def test_no_notice_when_every_input_processed(tmp_path):
    r = CoAReporter(tmp_path, min_reads=1)
    assert r._incomplete_notice() is None


def test_notice_names_the_failures_and_the_scope(tmp_path):
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs(["a.pod5", "b.pod5"], total_inputs=10)
    notice = r._incomplete_notice()

    assert notice is not None
    assert "INCOMPLETE" in notice
    assert "a.pod5" in notice and "b.pod5" in notice
    assert "8 of 10" in notice                      # what the report DOES cover
    # The bias direction must be stated, not merely the count — that is the whole point.
    assert "not random" in notice


def test_long_failure_list_is_truncated_with_a_count(tmp_path):
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs([f"p{i}.pod5" for i in range(9)], total_inputs=20)
    notice = r._incomplete_notice()
    assert "+4 more" in notice and "p0.pod5" in notice


def test_scope_degrades_gracefully_without_a_total(tmp_path):
    # The real-time driver may not know the input-set size at render time.
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs(["x.pod5"])
    assert "INCOMPLETE" in r._incomplete_notice()


# --- it reaches the rendered report -----------------------------------------

def test_banner_renders_into_the_html(tmp_path, fixture_bam, curlcake_ref):
    """A bad input alongside a good one: the report renders AND carries the banner."""
    in_dir = tmp_path / "input"
    in_dir.mkdir()
    write_distinct_bam(fixture_bam, in_dir / "a_good.bam", "good")
    (in_dir / "b_broken.bam").write_bytes(b"not a bam at all")

    pipe = OfflinePipeline(in_dir, tmp_path / "out", _cfg(curlcake_ref))
    result = pipe.run()

    assert result["status"] == "report"
    assert result["incomplete"] is True
    assert result["n_failed_inputs"] == 1

    html = (tmp_path / "out" / "report_run_end.html").read_text(encoding="utf-8")
    # The CSS class is always present in the <style> block, so assert on the rendered
    # element and its text, not on the bare class name.
    assert '<div class="incomplete-banner">' in html
    assert "b_broken.bam" in html
    assert "1 of 2" in html


def test_complete_run_renders_no_banner(tmp_path, bam_input_dir, curlcake_ref):
    pipe = OfflinePipeline(bam_input_dir, tmp_path / "out", _cfg(curlcake_ref))
    result = pipe.run()

    assert result["incomplete"] is False
    html = (tmp_path / "out" / "report_run_end.html").read_text(encoding="utf-8")
    assert '<div class="incomplete-banner">' not in html
    assert "INCOMPLETE" not in html


def test_interim_reports_carry_the_banner_too(tmp_path, fixture_bam, curlcake_ref):
    """A failure at input 1 must warn on the interim after input 2, not only at the end —
    an operator watching a live run should not have to wait for the terminal report."""
    in_dir = tmp_path / "input"
    in_dir.mkdir()
    (in_dir / "a_broken.bam").write_bytes(b"nope")
    write_distinct_bam(fixture_bam, in_dir / "b_ok.bam", "b")
    write_distinct_bam(fixture_bam, in_dir / "c_ok.bam", "c")

    OfflinePipeline(in_dir, tmp_path / "out",
                    _cfg(curlcake_ref, **{"cadence.interim_every_n": 1})).run()

    interims = sorted((tmp_path / "out").glob("report_interim_*.html"))
    assert interims, "expected at least one interim report"
    assert '<div class="incomplete-banner">' in interims[0].read_text(encoding="utf-8")


def test_incompleteness_is_recorded_in_qc_result_json(tmp_path, fixture_bam, curlcake_ref):
    in_dir = tmp_path / "input"
    in_dir.mkdir()
    write_distinct_bam(fixture_bam, in_dir / "a_good.bam", "good")
    (in_dir / "b_broken.bam").write_bytes(b"broken")

    OfflinePipeline(in_dir, tmp_path / "out", _cfg(curlcake_ref)).run()
    doc = json.loads((tmp_path / "out" / "qc_result.json").read_text(encoding="utf-8"))

    assert doc["report"]["incomplete"] is True
    assert doc["run_accounting"]["failed_inputs"] == ["b_broken.bam"]


# --- sampled out, which is a different claim ---------------------------------
#
# Decided 2026-10-15: QC does not need every read, so a run that cannot keep up thins
# the sample and stays live. That makes the report a subsample, and a subsample that does
# not announce itself is the failure the whole accounting layer exists to prevent. These
# guard the announcement AND its wording, because the wording is the deliverable here: a
# fixed-stride sample is the one kind of incompleteness that is NOT skewed, and describing
# it in the voice of a basecall failure would be false in the alarming direction.

def test_a_sampled_run_says_so_and_does_not_call_itself_incomplete(tmp_path):
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs([], sampled_inputs=["c.pod5", "d.pod5"], total_inputs=10)
    notice = r._incomplete_notice()

    assert notice is not None
    assert notice.startswith("SAMPLED:"), notice
    assert "INCOMPLETE" not in notice, (
        "A deliberate uniform subsample is not a fault. Leading with the failure word "
        "trains the reader to ignore it on the day it means the other thing.")
    assert "8 of 10" in notice
    assert "run_accounting.sampled_out" in notice


def test_the_sampling_notice_states_the_bias_direction_truthfully(tmp_path):
    """The claim that makes a subsample usable, and it must be the TRUE claim.

    Uniform-in-time admission is unbiased with respect to run time — pore ageing and mux
    turnover drift monotonically, so this is the one property that matters. What it costs
    is precision. Saying "biased" here would be as wrong as saying nothing.
    """
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs([], sampled_inputs=["c.pod5"], total_inputs=4)
    notice = r._incomplete_notice()

    assert "uniform in time" in notice
    assert "precision, not accuracy" in notice
    # The failure mechanism must NOT be borrowed for this bucket.
    assert "not random" not in notice, (
        "that sentence explains FAILURES (an OOM takes the largest POD5s); a stride does "
        "not work that way and attaching it here asserts a mechanism that is not there")


def test_failures_and_sampling_together_keep_their_own_explanations(tmp_path):
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs(["a.pod5"], sampled_inputs=["c.pod5"], total_inputs=10)
    notice = r._incomplete_notice()

    assert notice.startswith("INCOMPLETE:"), "a real failure is present, so lead with it"
    assert "8 of 10" in notice, "both causes count against the scope"
    assert "not random" in notice and "uniform in time" in notice, (
        "each bucket keeps its own bias statement; neither may absorb the other")


def test_only_the_failures_are_named_in_the_banner(tmp_path):
    """A name earns its place when the reader would go open that file.

    For a failure they would: something about it broke. Nothing is wrong with an input
    that was sampled out or left behind when the run stopped, so its name is a string to
    skip past, and on a real run there are many of them differing only in a trailing
    index. Every name stays in `run_accounting` either way.
    """
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs(["broke.pod5"], aborted_inputs=["stopped.pod5"],
                             sampled_inputs=["thinned.pod5"], total_inputs=10)
    notice = r._incomplete_notice()

    assert "broke.pod5" in notice, "a failure names the file; that is where one looks"
    assert "stopped.pod5" not in notice
    assert "thinned.pod5" not in notice
    # The counts must survive the names going: how many is the part that is load-bearing.
    assert "run was stopped (1)" in notice
    assert "sequencer (1)" in notice


def test_the_unbiasedness_claim_is_scoped_to_the_sample(tmp_path):
    """"Unbiased" is a claim about the REPORT, and only sampling can support it.

    Uniform-in-time admission makes the SAMPLE unbiased with respect to run time. That a
    rate in the report is therefore an unbiased estimate of the whole run needs one more
    thing: that nothing else went missing. A failure takes the largest POD5s and a stop
    takes the newest, and neither is uniform in anything.

    Written unconditionally, the banner said both at once. The guard above passed on it,
    because it checks that each bucket keeps its own bias sentence and both sentences
    were present -- the defect was not which bucket owned a sentence but what one of them
    claimed.
    """
    CLAIM = "unbiased estimate of the whole run"

    alone = CoAReporter(tmp_path / "a", min_reads=1)
    alone.note_incomplete_inputs([], sampled_inputs=["c.pod5"], total_inputs=10)
    assert CLAIM in alone._incomplete_notice(), (
        "sampling on its own IS unbiased in run time; withholding that makes a usable "
        "subsample look like a damaged run")

    for label, failed, aborted in (("a failure", ["a.pod5"], []),
                                   ("a stop", [], ["z.pod5"]),
                                   ("both", ["a.pod5"], ["z.pod5"])):
        r = CoAReporter(tmp_path / label.replace(" ", "_"), min_reads=1)
        r.note_incomplete_inputs(failed, aborted_inputs=aborted,
                                 sampled_inputs=["c.pod5"], total_inputs=10)
        notice = r._incomplete_notice()
        assert CLAIM not in notice, (
            f"with {label} also present the report is NOT an unbiased estimate of the "
            f"whole run, and this banner says so in the same breath as naming the bias")
        assert "uniform in time" in notice, (
            "the sample is still uniform in time; that fact does not go away")
        assert "not unbiased estimates of the whole run" in notice, (
            "say what the reader may not conclude, rather than going quiet on it")


def test_the_read_level_share_leads_and_belongs_to_no_single_cause(tmp_path):
    """Two things about the one number that states the size of the loss.

    It LEADS, because a reader takes that size from the first number they meet and a
    file count is not it: POD5s differ in read count, so "1 of 12 input(s)" reads as 8%
    of a run that still holds 40% of its reads. The share used to trail eight sentences
    behind the file count.

    And it sits outside every cause, because it nets all of them. Inside the sampling
    paragraph it would read as the cost of sampling alone, which overstates it whenever
    a failure is also present.
    """
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs(["a.pod5"], sampled_inputs=["c.pod5"],
                             analyzed_read_fraction=0.734, total_inputs=10)
    notice = r._incomplete_notice()

    first_sentence = notice.split(". ")[0]
    assert "73.4%" in first_sentence, (
        f"the share must be the first number the reader meets: {first_sentence!r}")
    assert notice.count("73.4%") == 1, "stated once, or it belongs to something"
    for cause in ("Failed to process", "Sampled out"):
        assert "73.4%" not in notice[notice.index(cause):], (
            f"the share must not sit inside the {cause!r} paragraph, where it reads as "
            f"the cost of that cause alone")
    # Both numbers in the lead describe what the report HAS, so the sentence does not
    # reverse direction against itself: 73.4% of the reads, 8 of 10 inputs.
    assert "8 of 10" in first_sentence


def test_no_read_share_sentence_when_it_could_not_be_measured(tmp_path):
    """No sequencing_summary means we do not know, which is not the same as 100%."""
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs([], sampled_inputs=["c.pod5"], total_inputs=4)
    assert "the instrument recorded" not in r._incomplete_notice()


def test_the_signoff_restates_the_subsample_in_the_printed_record(tmp_path, fixture_bam,
                                                                  curlcake_ref):
    """The banner is a warning; the signoff is the sentence somebody signs under."""
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs([], sampled_inputs=["c.pod5"], total_inputs=4)
    r.finalize("run_end", force=True)
    html = (tmp_path / "report_run_end.html").read_text()
    assert "based on a subsample of the run" in html
    assert "run_accounting.sampled_out" in html


def test_the_signoff_names_every_cause_not_only_the_benign_one(tmp_path):
    """The banner is a warning; this is the sentence somebody signs under.

    It carried only the SAMPLING case: `sampled_run` was `bool(self._sampled_inputs)`, so
    on a run that also lost inputs to a failure or a stop, the signoff explained the
    deliberate, uniform subsample at length and said nothing about the causes that skew.
    A reviewer reading only this block would attribute all of the partialness to the one
    cause that costs no accuracy.
    """
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs(["x.pod5"], aborted_inputs=["z.pod5"],
                             sampled_inputs=["a.pod5"], total_inputs=12)
    scope = r._signoff_scope()

    assert "do not cover the whole run" in scope["lead"]
    for cause in ("failed to process", "left when the run was stopped",
                  "sampled out to keep pace"):
        assert cause in scope["detail"], f"the signoff is silent on: {cause}"
    assert "not an unbiased estimate of the whole run" in scope["detail"], (
        "the claim sampling alone would support must not survive a cause that skews")
    assert "1 was left" in scope["detail"] and "1 were" not in scope["detail"]


def test_the_signoff_keeps_the_subsample_claim_when_sampling_is_the_only_cause(tmp_path):
    """The converse, so the guard above cannot be met by going quiet. A uniform
    subsample IS unbiased with respect to run time, and withholding that would make a
    usable sample read like a damaged run."""
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_incomplete_inputs([], sampled_inputs=["a.pod5"], total_inputs=4)
    scope = r._signoff_scope()

    assert "subsample of the run" in scope["lead"]
    assert "unbiased with respect to run time" in scope["detail"]
    assert "run_accounting.sampled_out" in scope["detail"]
    assert "not an unbiased estimate" not in scope["detail"]


def test_no_signoff_scope_paragraph_on_a_complete_run(tmp_path):
    r = CoAReporter(tmp_path, min_reads=1)
    assert r._signoff_scope() is None


def test_the_signoff_says_the_figures_are_part_of_the_report(tmp_path, fixture_bam,
                                                             curlcake_ref):
    """The signature closed the document with twelve pages of figures after it and no
    statement that they belonged to it. The tables are summaries of what the figures
    show, so a reviewer who signs without them has signed the summary alone."""
    from mrnaspect.analysis.engine import AnalysisEngine

    r = CoAReporter(tmp_path, min_reads=1)
    engine = AnalysisEngine(r, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    # The signature block is PRINT-only since 2026-10-16: a signature rule in a browser
    # is a line nobody can put a pen on. So this is checked on the print render, which is
    # the one that has a signature for the sentence to qualify. The string WeasyPrint is
    # handed is not the HTML written to disk, so it has to be caught at the one finalize
    # rather than produced by a second (`finalize` admits two reasons and neither is a
    # spare).
    import weasyprint

    captured = {}
    real = weasyprint.HTML

    class Spy(real):
        def __init__(self, *a, **k):
            if "string" in k:
                captured["s"] = k["string"]
            super().__init__(*a, **k)

    weasyprint.HTML = Spy
    try:
        r.finalize("run_end", force=True)
    finally:
        weasyprint.HTML = real
    printed = captured["s"]
    signoff = printed[printed.index('<div class="signoff">'):printed.index("Signature")]
    assert "are part of" in signoff and "not an appendix" in signoff
    assert "on the following pages" in signoff, "on paper the figures are overleaf"

    html = (tmp_path / "report_run_end.html").read_text()
    assert '<div class="signoff">' not in html, "and the screen has no signature rule"


def test_a_census_run_makes_no_subsample_claim(tmp_path):
    r = CoAReporter(tmp_path, min_reads=1)
    r.finalize("run_end", force=True)
    html = (tmp_path / "report_run_end.html").read_text()
    assert "subsample" not in html
