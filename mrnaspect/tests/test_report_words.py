"""The words in the report, on screen and in print, checked as one pass (2026-11-16).

The GUI's half is test_gui_words.py; the same six points, read against the HTML report
and the PDF:

1. One rule for capitals: sentence case, as in the GUI. The report had Title Case section
   headings ("Sample & Batch Information", "QC Test Results") and capitals for stress
   inside sentences ("ASSUMPTION", "PROVISIONAL", "TIME SUFFIX", "NOT represented"). The
   label that opens a warning keeps its capitals (INCOMPLETE, SAMPLED, OUTSIDE THE
   FITTED RANGE): that was decided, and is said where `_WARNING_LABELS` is.
2. "Arm" is "modification", the name of the GUI step that sets it, and reads
   m1Ψ-substituted or canonical U.
4. A metric is defined once: the first page's note quoted the flag threshold and depth
   as fixed numbers beside a list that prints the run's own; full length was named and
   written differently from the GUI, and was missing from the per-construct tables,
   where the GUI has it; a figure's quartiles were printed to two decimals beside tables
   that round them; a construct's share is of every read, in the figure as in the
   result file.
5. Nothing only the lab would know: the template's style comments, with who asked for
   what and when, were written into every report; method texts named Core's modules.
6. No sentence that explains what the page already shows. Kept on purpose, each by a
   test of its own: how to supply the sample's details when none were given
   (test_sample_identity.py) and how to drive a figure on screen (test_print_prose.py).
"""

from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator
from mrnaspect.reporting import figspec
from mrnaspect.reporting.reporter import CoAReporter
from mrnaspect.snapshot_keys import construct_key

from .test_print_prose import _print_html

SAMPLE = {"arm": "m1psi", "arms": {"HA-WT": "U"}}
POLYA = {"polya": {"minimap2_flags": "-ax map-ont -k7 -w5 --secondary=no -y"}}


def _visible(page: str) -> str:
    """A page's text as a reader gets it: no script, no style, no markup.

    The licence texts of the software embedded in the page are left out. They are other
    people's words, copied whole, and these rules are for the report's own; the line
    that opens them is the report's and stays in.
    """
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    page = re.sub(r'(<details class="third-party-notices">.*?)<pre>.*?</pre>', r"\1", page,
                  flags=re.S)
    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))


def test_the_licence_texts_left_out_are_only_those_of_the_embedded_software(pages) -> None:
    """One block of the page is left out of these rules, and it holds nothing else."""
    screen = pages["screen"]
    assert screen.count("<pre>") == 1 and "<pre>" not in pages["print"]
    start = screen.index('<details class="third-party-notices">')
    block = screen[start:screen.index("</details>", start)]
    assert block.count("<pre>") == 1
    assert "Third-party software in this file: licence notices" in _visible(screen)
    assert "Licence notices for plotly-cartesian" in block


@pytest.fixture(scope="module")
def pages(tmp_path_factory, variant_bam, qk_ha_ref) -> dict[str, str]:
    """One finished run: the screen's HTML as written, and the string given to the PDF."""
    out = tmp_path_factory.mktemp("words")
    reporter = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(reporter, reference=qk_ha_ref, sample=SAMPLE, module_params=POLYA)
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    reporter.note_run_context(reference=qk_ha_ref, mode="bam", inputs="1 file from /data/run_1")
    printed = _print_html(reporter)
    return {"screen": (out / "report_run_end.html").read_text(encoding="utf-8"),
            "print": printed}


# --- 1. one rule for capitals ---------------------------------------------------------------

#: Words that keep a capital inside a heading: names, acronyms.
_PROPER = {"QC", "MAPQ", "Poly(A)", "MinKNOW", "ID", "PDF", "Q", "U"}


def _title_cased(label: str) -> list[str]:
    words = re.findall(r"[A-Za-zΨ][\w()'-]*", label)
    return [w for w in words[1:] if w[0].isupper() and w not in _PROPER and not w.isupper()
            and not w.startswith("mRNA")]


@pytest.mark.parametrize("medium", ["screen", "print"])
def test_every_heading_and_column_is_in_sentence_case(pages, medium) -> None:
    page = pages[medium]
    heads = re.findall(r'<div class="(?:section-title|figure-group-title|flagged-title|'
                       r'signature-line)[^"]*">([^<{]+)', page)
    heads += re.findall(r"<h1>([^<]+)</h1>", page)
    heads += re.findall(r"<th>([^<]+)</th>", page)
    # A results table's first column is the test's name.
    heads += re.findall(r"<tr>\s*<td>([^<]+)</td>\s*<td>", page)
    heads += re.findall(r"<strong>([^<]+)</strong>: _", page)
    heads = [h.strip() for h in heads if h.strip()]
    assert len(heads) > 25, heads
    bad = {h: _title_cased(h) for h in heads if _title_cased(h)}
    assert not bad, bad


def test_the_sections_are_named_as_the_gui_names_them(pages) -> None:
    for medium in ("screen", "print"):
        text = _visible(pages[medium])
        assert "mRNAspect QC report (final)" in text
        assert "Sample and batch" in text and "Sample & Batch" not in text
        assert "Whole-run results" in text and "QC Test Results" not in text
        assert "Per-construct results" in text and "Per-construct Results" not in text
    assert re.search(r"QC reviewer\s*:", _visible(pages["print"]))


@pytest.mark.parametrize("medium", ["screen", "print"])
def test_no_heading_is_set_in_capitals_by_its_style(pages, medium) -> None:
    """On screen the section headings were drawn in capitals by the style sheet, after a
    GUI label that no longer exists; the PDF and the GUI had them in sentence case."""
    own = re.sub(r"<script>.{20000,}?</script>", "", pages[medium], flags=re.S)   # not plotly's
    assert "text-transform" not in own


#: Capitals that are a name, not stress.
_ACRONYMS = {"MAPQ", "FASTA", "HTML", "JSON", "MINKNOW", "UTC+", "UTC"}
#: The label that opens a warning stays in capitals: an out-of-range correction "is
#: shouted, not whispered" (test_polya_ruler_is_stated.py), and a report that is missing
#: inputs leads with the word (test_incomplete_run_banner.py). Nothing inside a sentence.
_WARNING_LABELS = ("INCOMPLETE: ", "SAMPLED: ", "OUTSIDE THE FITTED RANGE: ")


#: A file's path as the report shows it: absolute, from home, relative with dots, with a
#: drive letter, as a URL, or after an option's "=". Its folders are the reader's names.
_PATH = re.compile(r"(?<!\S)(?:\S*?[=:])?(?:~|\.{1,2})?[/\\]\S*")


def _shouted(text: str) -> list[str]:
    for label in _WARNING_LABELS:
        text = text.replace(label, " ")
    text = _PATH.sub(" ", text)
    return [w for w in re.findall(r"\b[A-Z]{4,}\b", text) if w not in _ACRONYMS]


def test_no_sentence_stresses_a_word_in_capitals(pages) -> None:
    for medium in ("screen", "print"):
        assert not _shouted(_visible(pages[medium])), _shouted(_visible(pages[medium]))


def test_a_folder_named_in_capitals_is_the_reader_s_word_not_the_report_s(
        tmp_path, variant_bam, qk_ha_ref) -> None:
    """The report shows the reference's path and the command as they were given. A folder
    called LABDATA is not a stressed word, and this file passed or failed by where the
    checkout sat until 2026-11-17."""
    import shutil

    folder = tmp_path / "LABDATA"
    folder.mkdir()
    reference = Path(shutil.copy(qk_ha_ref, folder / "ref.fa"))
    reporter = CoAReporter(tmp_path / "out", min_reads=1)
    engine = AnalysisEngine(reporter, reference=str(reference), sample=SAMPLE, module_params=POLYA)
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    reporter.note_run_context(reference=str(reference), mode="bam",
                              inputs="1 file from /data/RUNS/run_1")
    reporter.finalize("run_end", force=True)
    seen = _visible((tmp_path / "out" / "report_run_end.html").read_text(encoding="utf-8"))
    assert "LABDATA" in seen, "the page no longer shows the reference's path"
    assert not _shouted(seen), _shouted(seen)
    # and the rule itself: a path is left alone, a word is not
    for path in ("/home/me/QCRNA/ref.fa", "--config=/data/RUNS/c.yaml", "~/DATA/x.bam",
                 "../RUNS/x.bam", r"C:\Users\LABPC\ref.fa", "file:///Volumes/DISK/x"):
        assert not _shouted(f"Reference {path} was read"), path
    assert _shouted("this is VERY wrong") == ["VERY"]
    assert _shouted("counted as READS/SECOND") == ["READS", "SECOND"]


def test_no_warning_stresses_a_word_in_capitals(tmp_path) -> None:
    """The banners and notes a clean run never shows, each in the state that writes it."""
    rep = CoAReporter(tmp_path, min_reads=1000)
    rep.note_incomplete_inputs(["a.pod5"], aborted_inputs=["b.pod5", "c.pod5"],
                               sampled_inputs=["d.pod5"], total_inputs=10)
    said = [rep._incomplete_notice(), " ".join(rep._signoff_scope().values())]
    only = CoAReporter(tmp_path / "s", min_reads=1000)
    only.note_incomplete_inputs([], sampled_inputs=["d.pod5"], total_inputs=10)
    said += [only._incomplete_notice(), " ".join(only._signoff_scope().values())]
    said.append(CoAReporter._ruler({"correction": {
        "applied": True, "factor": 1.1383, "shift_nt": -2.7, "run_model": "m1psi_tuned_v1",
        "arm": "m1psi", "arm_source": "declared", "range_flags": ["f above the fit"],
        "range_edges": ["u12 at its upper edge"]}}))
    for text in said:
        assert text and not _shouted(text), (_shouted(text), text)
    assert said[0].startswith("INCOMPLETE: ") and said[2].startswith("SAMPLED: ")
    assert "OUTSIDE THE FITTED RANGE: f above the fit" in said[4]


def test_a_run_short_of_reads_is_told_in_sentence_case(tmp_path, fixture_bam, curlcake_ref) -> None:
    rep = CoAReporter(tmp_path, min_reads=10**9)
    engine = AnalysisEngine(rep, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    rep.finalize("run_end", force=True)
    banner = re.search(r'<div class="insufficient-banner">(.*?)</div>',
                       (tmp_path / "report_run_end.html").read_text(encoding="utf-8"), re.S)
    assert banner and not _shouted(_visible(banner.group(1))), banner
    assert "This run finished with" in _visible(banner.group(1))


def test_a_modified_working_tree_is_said_in_lower_case(monkeypatch, tmp_path) -> None:
    from mrnaspect import provenance

    monkeypatch.setattr(provenance, "git_commit", lambda short=True: "abc1234")
    monkeypatch.setattr(provenance, "git_is_dirty", lambda: True)
    rows = {r["name"]: r["value"] for r in CoAReporter(tmp_path)._run_provenance()}
    assert rows["Source commit"].startswith("abc1234 (working tree modified: ")


# --- 2. modification ------------------------------------------------------------------------

def test_the_report_says_modification_and_spells_it_as_the_gui_does(pages) -> None:
    for medium in ("screen", "print"):
        text = _visible(pages[medium])
        assert not re.search(r"\barms?\b", text), re.findall(r".{40}\barms?\b.{20}", text)
        assert "modification m1Ψ-substituted (declared)" in text
        assert "modification canonical U (declared)" in text
        assert "base type" not in text and "m1psi arm" not in text


def test_a_withheld_correction_asks_for_the_modification(tmp_path, variant_bam, qk_ha_ref) -> None:
    rep = CoAReporter(tmp_path, min_reads=1)
    engine = AnalysisEngine(rep, reference=qk_ha_ref, module_params=POLYA)   # no sample
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    rep.finalize("run_end", force=True)
    text = _visible((tmp_path / "report_run_end.html").read_text(encoding="utf-8"))
    assert "no modification declared for this construct (m1Ψ-substituted or canonical U)" in text
    assert not re.search(r"\bno arm\b", text)
    # The setting keeps its key: that is what a config has to say.
    assert "sample.arm" in text


# --- spelling and signs ---------------------------------------------------------------------

@pytest.mark.parametrize("medium", ["screen", "print"])
def test_one_spelling_and_one_set_of_signs(pages, medium) -> None:
    text = _visible(pages[medium])
    assert "poly-A" not in text, "Poly(A) in the headings, poly-A in the method texts"
    assert "dorado-split" not in text and "Dorado-split" in text
    for ascii_sign in ("->", ">=", "<=", " x 1.", " x 0."):
        assert ascii_sign not in text, ascii_sign
    assert "3'→5'" in text
    assert re.search(r"pt × \d\.\d{4} [+−] \d+\.\d\d nt", text)
    assert not re.search(r"\w\(s\)", text), re.findall(r"\w+\(s\)", text)


def test_a_depth_in_the_thousands_is_grouped_as_a_read_count_is(pages) -> None:
    depths = re.findall(r"Mean depth\s*</td>\s*<td>[^<]*</td>\s*<td class=\"value\">([^<]+)<",
                        pages["screen"])
    assert depths and all(re.fullmatch(r"\d{1,3}(,\d{3})*\.\d×", d.strip()) for d in depths), depths


# --- 4. one definition per metric -----------------------------------------------------------

def test_full_length_is_named_and_written_as_in_the_gui(pages) -> None:
    text = _visible(pages["screen"])
    assert "Full length (5' reach)" in text and "Full-length ratio" not in text
    row = re.search(r"<td>Full length \(5' reach\)</td>\s*<td>(.*?)</td>\s*"
                    r'<td class="value">(.*?)</td>', html_lib.unescape(pages["screen"]), re.S)
    assert row, "no full-length row"
    # The GUI's form: both ends carry the sign, an en dash between, no spaces.
    assert re.fullmatch(r"\d+\.\d+%–\d+\.\d+%|\d+\.\d%", row.group(2).strip())
    method = _visible(row.group(1))
    assert "an assumption envelope, not a confidence interval" in method
    assert "module" not in method


_FL_ROW = re.compile(r"<td>Full length \(5' reach\)</td>\s*<td>(.*?)</td>\s*"
                     r'<td class="value">(.*?)</td>', re.S)


def test_each_construct_s_table_has_its_full_length(pages) -> None:
    """The GUI's Constructs table gives each construct's full length; the report had
    the whole run's only."""
    for medium in ("screen", "print"):
        rows = _FL_ROW.findall(html_lib.unescape(pages[medium]))
        # The whole run's row, then one for each of the two constructs.
        assert len(rows) == 3, (medium, len(rows))
        for method, value in rows[1:]:
            assert re.fullmatch(r"\d+\.\d+%–\d+\.\d+%|\d+\.\d%", value.strip()), value
            said = _visible(method)
            assert "of the 5' terminus" in said
            # The whole run's row explains the range; a construct's row says only what
            # its two ends divide by.
            assert "assumption envelope" not in said
            assert "primary reads" not in said


def test_a_construct_s_full_length_is_the_result_file_s_range(tmp_path, variant_bam, qk_ha_ref) -> None:
    """One number in the report, the result file and the GUI, which reads the file."""
    rep = CoAReporter(tmp_path, min_reads=1)
    engine = AnalysisEngine(rep, reference=qk_ha_ref, sample=SAMPLE, module_params=POLYA)
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    res = QCResultIntegrator().integrate(dict(rep._snapshots))
    sections = {s["name"]: s for s in rep._build_constructs()}
    assert len(sections) == 2
    for name, section in sections.items():
        row = next(r for r in section["rows"] if r["name"] == "Full length (5' reach)")
        fl = res["constructs"][name]["full_length"]
        lo, hi = fl["full_length_fraction_lower"], fl["full_length_fraction"]
        assert lo is not None and hi is not None, name
        assert row["value"] == CoAReporter._envelope_text(lo, hi)[1], name
        assert f"all {section['n_reads']:,} reads assigned to this construct" in row["method"] \
            or CoAReporter._envelope_text(lo, hi)[0], name


def test_a_construct_with_no_reach_measured_says_so_in_its_row(tmp_path) -> None:
    rep = CoAReporter(tmp_path, min_reads=1)
    row = rep._construct_full_length_row({}, 120)
    assert row["value"] == "Not measured" and row["method"] == "Not measured in this run"
    # Reads seen but none admitted: no fraction, and the row says why.
    none = {"full_length_count": 0, "n_admitted": 0, "full_length_fraction": None,
            "full_length_5p_max_short_nt": 50, "excluded": {"reverse_strand": 120}}
    row = rep._construct_full_length_row({"terminal_reach": none}, 120)
    assert row["value"] == "Not measured"
    assert "none of the 120 reads assigned to this construct was admitted" in row["method"]


# --- the share of reads ---------------------------------------------------------------------

def _bars(spec: dict) -> dict[str, float]:
    return dict(zip(spec["data"][0]["y"], spec["data"][0]["x"]))


def test_the_share_figure_divides_by_the_run_s_reads_as_the_result_file_does(
        tmp_path, variant_bam, qk_ha_ref) -> None:
    rep = CoAReporter(tmp_path, min_reads=1)
    engine = AnalysisEngine(rep, reference=qk_ha_ref, sample=SAMPLE, module_params=POLYA)
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    res = QCResultIntegrator().integrate(dict(rep._snapshots))
    bars = _bars(rep._share_spec(rep._construct_snapshots()))
    for name, c in res["constructs"].items():
        assert bars[name] == pytest.approx(100 * c["share_of_reads"], abs=0.00501), name
    if res["unassigned"]["n_reads"]:
        assert bars["(unassigned)"] == pytest.approx(
            100 * res["unassigned"]["share_of_reads"], abs=0.00501)


def test_the_share_figure_s_denominator_is_the_run_s_read_count(tmp_path) -> None:
    """The count the Reads card shows, not the sum of the bars drawn."""
    rep = CoAReporter(tmp_path, min_reads=1)
    rep._snapshots = {construct_key("A"): {"n_reads": 1}, "unassigned": {"n_reads": 1},
                      "run_summary": {"mapping": {"total": 4}}}
    assert _bars(rep._share_spec(rep._construct_snapshots())) == {"A": 25.0, "(unassigned)": 25.0}
    # A snapshot with no run summary has no other count to disagree with.
    del rep._snapshots["run_summary"]
    assert _bars(rep._share_spec(rep._construct_snapshots())) == {"A": 50.0, "(unassigned)": 50.0}
    # More reads in the bars than the run read: the snapshot is broken, and no figure is
    # better than shares above what the result file withholds.
    rep._snapshots["run_summary"] = {"mapping": {"total": 1}}
    with pytest.raises(ValueError, match="more reads than the run"):
        rep._share_spec(rep._construct_snapshots())


def test_no_method_text_names_one_of_core_s_modules(pages) -> None:
    for medium in ("screen", "print"):
        text = _visible(pages[medium])
        assert "Run-summary module" not in text
        assert not re.search(r"[Tt]erminal-reach module|reached the module", text)


def test_the_first_page_quotes_the_run_s_own_flag_rule(tmp_path, variant_bam, qk_ha_ref) -> None:
    """The note under the glance table said "mismatch ≥ 0.5 at depth ≥ 50" whatever the
    run was set to; each construct's list, further on, printed the run's values."""
    rep = CoAReporter(tmp_path, min_reads=1)
    engine = AnalysisEngine(rep, reference=qk_ha_ref, sample=SAMPLE,
                            module_params={**POLYA, "consensus": {"mismatch_threshold": 0.3}})
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    note = re.search(r'<p class="section-note glance-note">(.*?)</p>', _print_html(rep), re.S)
    assert note, "no note under the glance table"
    said = _visible(note.group(1))
    assert "mismatch ≥ 0.3 at depth ≥ " in said and "0.5" not in said


def test_a_figure_s_quartiles_are_written_as_the_tables_write_them() -> None:
    """"Q1 1107.64 · median 1142.97" under a card that says 1,143 nt."""
    dist = {"bin_min": 0.0, "bin_max": 6000.0, "bin_width": 100.0, "n_bins": 60,
            "bin_counts": [0] * 10 + [5, 60, 30] + [0] * 46 + [1], "n": 96,
            "underflow": 0, "overflow": 2, "Q1": 1107.64, "median": 1142.97, "Q3": 1171.39}
    spec = figspec.histogram_spec([("read length", dist, "#158ae0")], x_title="read length (nt)",
                                  window_quantiles=figspec.WINDOW_QUANTILES)
    title = spec["layout"]["title"]["text"]
    assert "Q1 1,108 · median 1,143 · Q3 1,171" in title, title
    assert "2 above 6,000 outside the histogram" in title and "0 below" not in title, title
    small = dict(dist, Q1=11.01, median=12.25, Q3=14.77, overflow=0)
    title = figspec.histogram_spec([("q", small, "#04837b")], x_title="mean read Q")["layout"]["title"]["text"]
    assert "Q1 11.01 · median 12.25 · Q3 14.77" in title, title


def test_read_quality_has_one_unit_on_its_axis() -> None:
    src = (Path(figspec.__file__).parent / "reporter.py").read_text(encoding="utf-8")
    assert 'x_title="mean read Q"' in src and "qscore\")" not in src.replace('rc("qscore")', "")


def test_a_flag_count_is_given_with_the_report_s_sign() -> None:
    line = figspec._per_position_detail({"consensus_identity_pct": 94.8, "mismatch_sites": [1, 2],
                                           "mismatch_threshold": 0.5})
    assert line.endswith("2 positions flagged at mismatch ≥ 0.5"), line


# --- 5. nothing only the lab would know -----------------------------------------------------

@pytest.mark.parametrize("medium", ["screen", "print"])
def test_the_report_s_own_source_carries_no_comment(pages, medium) -> None:
    """The template's style comments said who asked for a change and when, and were
    written into every report. They are template comments now, which are not rendered.
    The vendored plotly bundle and the figures' JSON are not the report's own words."""
    page = re.sub(r"<script>.{20000,}?</script>", "", pages[medium], flags=re.S)
    page = re.sub(r'src="data:[^"]*"', 'src=""', page)       # the PDF's figures, as images
    assert len(page) < 200_000, "the vendored bundle was not left out"
    assert "/*" not in page and "<!--" not in page, re.findall(r"/\*.{0,80}", page)[:3]
    assert not re.search(r"\((?:user|PI)\b", page)
    assert not re.search(r"(?m)^\s*//", page), re.findall(r"(?m)^\s*//.*", page)[:3]


# --- 6. no sentence that explains what the page already shows --------------------------------

def test_a_bar_figure_does_not_repeat_its_title_inside_itself(pages) -> None:
    """Its caption names it. The five bar figures carried the same words a second time:
    inside the figure on screen, under the image in print, and under the card's title in
    the GUI ("Read share by construct", twice)."""
    for spec in (figspec.ranked_bar_spec([("a", 1.0)], x_title="x"),
                 figspec.category_bar_spec({"0": 3}, x_title="MAPQ")):
        assert spec["layout"]["title"]["text"] == ""
    from mrnaspect.reporting.reporter import _FIGURE_TEXT
    titles = {title for title, _caption in _FIGURE_TEXT.values()}
    labels = {html_lib.unescape(x).strip()
              for x in re.findall(r'<div class="panel-label">(.*?)</div>', pages["print"], re.S)}
    assert not labels & titles, labels & titles


def test_the_sentences_that_said_what_the_page_shows_are_gone(pages) -> None:
    screen, printed = _visible(pages["screen"]), _visible(pages["print"])
    for text in (screen, printed):
        # The heading says it now.
        assert "describes the whole run" not in text
        assert "and each is reported below" not in text
    # How to drive a figure stays, once (test_print_prose.py); what it said about the
    # PDF and about acceptance criteria is said where it belongs.
    assert screen.count("Hover for values") == 1 and "drawn for paper" not in screen
    # Said once, in the scope note at the foot of the page.
    assert screen.count("acceptance criteria") == 1
