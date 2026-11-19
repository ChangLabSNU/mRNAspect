"""The words on the GUI's pages, checked as one pass (2026-11-16, from a screenshot of the app).

Six things were asked of the GUI and the report together; the report's half is
test_report_words.py. Here:

1. One rule for capitals: sentence case. A label that opens its own element takes a
   capital ("Median 465 nt"), a unit or a qualifier after a number does not.
2. The run's "Arm" is "Modification", the name of the step that sets it, and reads as
   that step's two cards do, never the config's raw `m1psi`.
3. On an Overview card the label comes before its number ("Median 465 nt", not
   "465 nt median"), so the large number ends the line, at the card's right edge.
4. A metric has one definition wherever it is named. Found: Help said full length was
   "span the whole construct" while Core measures reach of the 5' end; the Constructs
   table's "Share" (of the mapped reads) sat under a figure of shares of every read, and
   both are now of every read. One group of results had three names (Run health, Flow
   cell, Sequencing information): it is Sequencing information.
5. Nothing only the lab would know: the paste box's example was a lab construct's name,
   three labels said "Core", the repository's own word for the analysis package, and
   the top bar showed the computer's name.
6. No sentence that explains what the page already shows. Kept on purpose, each by a
   test of its own: "A wrong construct maps ~0%" and "Same name continues that run"
   (test_gui_start_flow: they stop a silent wrong result), and the note under the
   poly(A) table on the whole run's corrected curve (test_gui_mac_review3).
"""

from __future__ import annotations

import re
from pathlib import Path

from .conftest import LAB_PRIVATE

_REPO = Path(__file__).resolve().parents[2]
_HTML = _REPO / "gui" / "index.html"
_TS = _REPO / "gui" / "src" / "main.ts"
_RS = _REPO / "gui" / "src-tauri" / "src" / "lib.rs"


def _fn(source: str, head: str) -> str:
    """The source of the function that starts with `head`, to the next top-level one."""
    start = source.index(head)
    nxt = re.search(r"\n(?:async )?function |\n// ====", source[start + len(head):])
    return source[start:start + len(head) + nxt.start()] if nxt else source[start:]


def _strings(ts: str) -> list[str]:
    """Every string and template literal in the page's code, comments left out."""
    code = re.sub(r"/\*.*?\*/", "", ts, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    return re.findall(r'"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`', code)


def _visible_html() -> str:
    """index.html's markup, with its comments, styles and scripts left out."""
    html = _HTML.read_text()
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    return re.sub(r"<script.*?</script>", "", html, flags=re.S)


# --- 2. Modification -----------------------------------------------------------------------

def test_the_run_s_arm_is_called_modification_as_its_step_is() -> None:
    """The step that sets it is "Modification"; the review card, the run line and Details
    said "Arm", then "Base type". One name for one setting."""
    ts = _TS.read_text()
    assert '"Arm"' not in ts and '"Base type"' not in ts
    assert 'arm: "Modification"' in ts, "the step's own name"
    assert '<label id="armlabel">Modification</label>' in _HTML.read_text()
    assert ('rows.push(["Modification", modificationText(armValue())]);'
            in _fn(ts, "function reviewRows("))
    assert 'runFacts.push(["Modification", modificationText(armValue())]);' in ts
    assert 'add("Modification",' in _fn(ts, "async function openDetails()")


def test_a_modification_reads_as_the_step_s_two_cards_say_it() -> None:
    """Details printed the config's raw value, `m1psi`, beside a run line that said m1Ψ."""
    ts = _TS.read_text()
    text = _fn(ts, "function modificationText(")
    html = _HTML.read_text()
    for said in ("m1Ψ-substituted", "Canonical U"):
        assert f'"{said}"' in text, said
        assert f"<b>{said}</b>" in html, f"the card no longer says {said}"
    assert ".toLowerCase()" in text, "a config may spell it M1PSI"
    details = _fn(ts, "async function openDetails()")
    assert "modificationText(" in details
    # A config that sets constructs one by one is said, not hidden behind the run's value.
    assert "sample.arms" in details and "set per construct" in details


# --- 3. the label before its number ----------------------------------------------------------

def test_an_overview_card_names_its_number_before_it() -> None:
    ts = _TS.read_text()
    pair = ('<span class="hpair"><span class="hs">${esc(c.headSub)}</span>'
            '<span class="h">${c.head}</span></span>')
    assert pair in _fn(ts, "function topCardHtml(")
    assert ('<span class="hpair"><span class="hs">${esc(headSub)}</span>'
            '<span class="h">${head}</span></span>') in _fn(ts, "function bottomCard(")
    assert '<span class="h">${c.head}</span><span class="hs">' not in ts


def test_the_label_before_a_number_opens_with_a_capital() -> None:
    """It opens its element now, as "Reads", "Mapping rate" and "Median 12.0" do."""
    ts = _TS.read_text()
    subs = re.findall(r'headSub: "([^"]+)"', ts)
    assert subs == ["Median", "Median", "Mean Q"], subs
    overview = _fn(ts, "function renderOverview(")
    assert '"Pooled"' in overview and '"All constructs"' in overview
    assert '"pooled"' not in overview and '"all constructs"' not in overview


# --- 1. one rule for capitals ---------------------------------------------------------------

#: Words that keep their capitals inside a label: names, acronyms and units.
_PROPER = {"MinKNOW", "POD5", "BAM", "BAMs", "FASTA", "QC", "PDF", "IQR", "MAPQ", "Q", "ID",
           "GPU", "CPU", "MIT", "License", "Finder", "Oxford", "Nanopore", "Technologies",
           "Tauri", "Python", "WeasyPrint", "Dorado", "Qbiolab", "Mac", "U", "I", "A",
           "N1-methylpseudouridine", "Poly(A)", "Overview", "Rust", "Apache-2.0", "Copyright"}


def _title_cased(label: str) -> list[str]:
    """The words after the first that carry a capital they have no claim to."""
    words = re.findall(r"[A-Za-zΨ][\w()'-]*", label)
    return [w for w in words[1:]
            if w[0].isupper() and w not in _PROPER and not w.isupper()
            and not re.match(r"m?RNA|mRNAspect", w)]


def test_every_label_in_the_page_is_in_sentence_case() -> None:
    html = _visible_html()
    labels = re.findall(r"<(?:h1|h2|h3|label|button|th|dt|b|summary)\b[^>]*>([^<]{2,60})</", html)
    labels += re.findall(r'<span class="lbl">([^<]+)</span>', html)
    assert len(labels) > 60, "the page's labels were not found"
    bad = {lab: _title_cased(lab) for lab in labels if _title_cased(lab)}
    assert not bad, bad


def test_every_label_the_code_writes_is_in_sentence_case() -> None:
    ts = _TS.read_text()
    labels = re.findall(r'(?:head|title|headSub): "([^"]+)"', ts)
    labels += re.findall(r'(?:add|stat|tileCard|bottomCard|instNone)\("([^"]+)"', ts)
    labels += re.findall(r'<th[^>]*>([^<$]{2,40})</th>', ts)
    labels += re.findall(r'(?:rows|runFacts)\.push\(\["([^"]+)"', ts)
    assert len(labels) > 40, "the code's labels were not found"
    bad = {lab: _title_cased(lab) for lab in labels if _title_cased(lab)}
    assert not bad, bad


def test_nothing_is_set_in_capitals_by_its_style() -> None:
    assert "text-transform" not in _HTML.read_text()


# --- 4. one definition per metric -----------------------------------------------------------

def test_help_defines_full_length_as_core_measures_it() -> None:
    """terminal_reach counts a read whose alignment starts within a set distance of the
    5' end. A read can do that without covering the 3' end, so "span the whole
    construct" described another quantity."""
    html = _visible_html()
    card = html[html.index("<h2>The Overview</h2>"):]
    card = card[:card.index("</div>")]
    assert "span the whole construct" not in card
    assert "reach the construct's 5' end" in card


def test_a_construct_s_share_is_of_every_read_wherever_it_is_shown() -> None:
    """The Constructs figure divides by every read (its axis says "% of reads") and draws
    the unassigned reads as a bar. The table under it and the Overview card divided by
    the mapped reads, so one construct had two shares (3.93% and 4.2%). Every share is
    now Core's `share_of_reads`, over the run's reads."""
    ts = _TS.read_text()
    assert "share_of_mapped" not in ts
    constructs = _fn(ts, "function renderConstructs(")
    assert '<th class="n">Share of reads</th>' in constructs
    assert "pct(c.share_of_reads)" in constructs
    overview = _fn(ts, "function renderOverview(")
    assert "c.share_of_reads * 100" in overview
    assert "${pct(c.share_of_reads)} of reads (${num(c.n_reads)})" in overview
    assert "one.share_of_reads" in overview
    html = _visible_html()
    assert "each construct's share of the reads" in html
    assert "share of the mapped reads" not in html


def test_the_unassigned_reads_are_the_table_s_last_row_as_they_are_the_figure_s_bar() -> None:
    """With every read in the denominator the constructs' shares stop short of 100%;
    the row that completes them is the figure's "(unassigned)"."""
    constructs = _fn(_TS.read_text(), "function renderConstructs(")
    assert "<td>(unassigned)" in constructs
    assert "pct(u.share_of_reads)" in constructs
    rs = _RS.read_text()
    assert '"share_of_reads": u["share_of_reads"]' in rs, "the page is not sent the share"


def test_the_results_from_minknow_s_files_have_one_name() -> None:
    """The Overview's row said "Run health", its tab "Flow cell" and the report's
    section "Sequencing information"."""
    ts = _TS.read_text()
    assert '["flow-cell", "Sequencing information"],' in ts
    assert '<span class="t">Sequencing information</span>' in _fn(ts, "function renderOverview(")
    said = [s for s in _strings(ts) if re.search(r"Run health|Flow cell", s)]
    assert not said, said
    html = _visible_html()
    assert "<dt>Sequencing information</dt>" in html
    assert not re.search(r"Run health|Flow cell", html)
    from mrnaspect.reporting.reporter import FIGURE_SECTIONS
    assert dict(FIGURE_SECTIONS)["sequencing"] == "Sequencing information"


def test_occupancy_is_pore_occupancy_in_the_page_and_on_the_figure() -> None:
    from mrnaspect.reporting.reporter import _FIGURE_TEXT
    assert _FIGURE_TEXT["pore"][0] == "Pore occupancy over the run"
    ts = _TS.read_text()
    assert "Pore occupancy" in ts and "Channel occupancy" not in ts


def test_identity_has_one_name() -> None:
    ts = _TS.read_text()
    assert '{ head: "Identity"' not in ts
    assert '{ head: "Per-base identity", n: true' in _fn(ts, "function tableCols(")


# --- 5. nothing only the lab would know ------------------------------------------------------

def test_no_page_text_names_a_lab_construct_a_lab_path_or_core() -> None:
    # A lab construct's or run's name, and the lab's own machines and mounts; those last
    # are named in an untracked file (conftest LAB_PRIVATE), not in a tracked one.
    lab = re.compile("|".join([r"QK-\d", r"HA-(?:L\d|WT)", r"IVT_\d", "curlcake",
                               *map(re.escape, LAB_PRIVATE.get("words", ()))]), re.I)
    html = _visible_html()
    assert not lab.findall(html), lab.findall(html)
    said = [s for s in _strings(_TS.read_text()) if lab.search(s)]
    assert not said, said
    # "Core" is the repository's name for the analysis package; a reader has never met it.
    core = [s for s in _strings(_TS.read_text()) if re.search(r"\bCore\b", s)]
    assert not core, core
    assert not re.search(r"\bCore\b", html)


def test_the_top_bar_does_not_show_the_computer_s_name() -> None:
    """It was there as MinKNOW shows its host. A screenshot of the app then carries the
    name of whoever took it, and the name tells a reader of the page nothing."""
    html = _HTML.read_text()
    assert 'id="hostname"' not in html and ".host " not in html
    assert "host_name" not in _TS.read_text()
    rs = _RS.read_text()
    assert "host_name" not in rs and "gethostname" not in rs


def test_the_paste_box_s_example_is_nobody_s_construct() -> None:
    box = re.search(r'<textarea id="reftext"[^>]*placeholder="([^"]+)"', _HTML.read_text())
    assert box and box.group(1).startswith("&gt;construct_1&#10;")


def test_the_engine_s_version_has_one_name() -> None:
    """Details said "mRNAspect Core", About "Core (last run)" and "Dorado server (last
    run)" beside a Device page and a Details row that say "Basecall server"."""
    ts = _TS.read_text()
    assert 'add("Analysis engine",' in _fn(ts, "async function openDetails()")
    about = _fn(ts, "async function renderAbout()")
    assert '["Analysis engine (last run)"' in about
    assert '["Basecall server (last run)"' in about


# --- 6. no sentence that explains what the page already shows --------------------------------

def test_the_hints_say_one_thing_each() -> None:
    html = _visible_html()
    for gone in ("Continue to skip", "nothing checks them",
                 "The report and its PDF do not change", "Needed for POD5 runs only"):
        assert gone not in html, gone
    assert '<p class="dim">Optional. The report prints them as given.</p>' in html
    # The field's own placeholder says the config is optional; the line under it is kept
    # for what a loaded config sets, or why it could not be read.
    assert '<p class="dim" id="confighint"></p>' in html
    # Live runs basecall too, so "POD5 runs only" was also wrong.
    assert '<p class="dim">Not needed for BAM files.</p>' in html


# --- seen in the same screenshot --------------------------------------------------------------

def test_an_axis_label_sits_under_its_own_gridline() -> None:
    """"1,0001,500 nt": the last label was right-aligned to its gridline whatever its
    place, so on a window that ends well past the last tick it ran into the one before."""
    ticks = _fn(_TS.read_text(), "function xTicks(")
    assert 'i === vals.length - 1 ? "-100%"' not in ticks
    assert "EDGE" in ticks and '"-50%"' in ticks
