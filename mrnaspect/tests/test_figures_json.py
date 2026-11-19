"""`figures.json` — the file that keeps the GUI and the report drawing one set of charts.

Core builds every plotly spec once, in Python. The report embeds them; the GUI reads this
file. The alternative was a TypeScript port of `figspec`, i.e. two implementations of one
contract, which is the shape of defect this project keeps meeting.

Terminal only, and that is not an optimisation detail a consumer can ignore: the file is
~1.8 MB on a 15-construct run while an interim render happens hundreds of times, so its
absence during a run is the normal state rather than "this run has no figures".
"""

from __future__ import annotations

import json

import pytest

from mrnaspect.reporting.reporter import CoAReporter, FIGURES_JSON_NAME


def _dist(counts, **kw):
    n = len(counts)
    return {"n": sum(counts), "bin_min": 0.0, "bin_max": 10.0 * n, "n_bins": n,
            "bin_width": 10.0, "bin_counts": list(counts), "underflow": 0,
            "overflow": 0, "Q1": kw.get("q1"), "median": kw.get("median"),
            "Q3": kw.get("q3")}


def _reporter(tmp_path, *, reads=50):
    r = CoAReporter(tmp_path, min_reads=1)
    r.update("run_summary", {
        "mapping": {"total": reads, "mapped": reads, "unmapped": 0,
                    "mapping_rate": 1.0},
        "read_length": {"n": reads, "mean": 100.0},
        "read_length_distribution": _dist([1, 2, 3], median=15.0),
        "quality_score_distribution": _dist([2, 3], median=12.0),
    })
    return r


def test_the_terminal_render_writes_it(tmp_path):
    _reporter(tmp_path).finalize("run_end", force=True)
    figures = json.loads((tmp_path / FIGURES_JSON_NAME).read_text())
    assert [f["id"] for f in figures] == ["read-length", "qscore"]


def test_an_interim_render_writes_it_too(tmp_path):
    """The GUI draws the charts during the run (2026-11-09), so every render writes
    the file. Until then it was final-only and every per-construct panel read "After the
    run" for the whole of a live run."""
    out = _reporter(tmp_path).report("interim_1", force=True)
    figures = json.loads((tmp_path / FIGURES_JSON_NAME).read_text())
    assert [f["id"] for f in figures] == ["read-length", "qscore"]
    assert out["figures_path"] == str(tmp_path / FIGURES_JSON_NAME)


def test_an_interim_html_embeds_no_figures(tmp_path):
    """It would inline plotly.js into every interim file; only the final report does."""
    _reporter(tmp_path).report("interim_1", force=True)
    html = (tmp_path / "report_interim_1.html").read_text()
    assert (tmp_path / FIGURES_JSON_NAME).read_text() not in html
    assert "Plotly" not in html


def test_the_final_render_replaces_the_interim_file(tmp_path):
    r = _reporter(tmp_path)
    r.report("interim_1", force=True)
    r.update("run_summary", {**r._snapshots["run_summary"],
                             "read_length_distribution": _dist([4, 5, 6], median=15.0)})
    r.finalize("run_end", force=True)
    spec = {f["id"]: f for f in json.loads((tmp_path / FIGURES_JSON_NAME).read_text())}
    assert spec["read-length"]["spec"]["data"][0]["y"][:3] == [4, 5, 6]


def test_the_file_and_the_html_carry_the_same_specs(tmp_path):
    """One build, two consumers — or the two surfaces drift apart."""
    _reporter(tmp_path).finalize("run_end", force=True)
    on_disk = (tmp_path / FIGURES_JSON_NAME).read_text()
    html = (tmp_path / "report_run_end.html").read_text()
    assert on_disk in html


def test_every_figure_carries_what_a_renderer_needs(tmp_path):
    _reporter(tmp_path).finalize("run_end", force=True)
    for f in json.loads((tmp_path / FIGURES_JSON_NAME).read_text()):
        # `section` and `panel` are part of the contract now: figures.json is what the
        # GUI reads, and it groups by these rather than re-deriving the grouping.
        assert set(f) == {"id", "title", "caption", "section", "panel", "spec"}
        assert set(f["spec"]) >= {"data", "layout"}
        assert f["id"] and f["title"]


def test_a_broken_figure_does_not_cost_the_others(tmp_path):
    """A report must render. One unusable snapshot is a missing chart, not a failed run."""
    r = _reporter(tmp_path)
    # Counts that contradict the declared geometry — histogram_spec refuses it.
    bad = _dist([1, 2, 3], median=15.0)
    bad["n_bins"] = 99
    r.update("polya", {"polya_distribution": bad})
    out = r.finalize("run_end", force=True)
    assert out["status"] == "report"
    ids = [f["id"] for f in json.loads((tmp_path / FIGURES_JSON_NAME).read_text())]
    assert "polya" not in ids
    assert "read-length" in ids


def test_a_skipped_figure_is_logged_rather_than_vanishing(tmp_path, caplog):
    """Silently absent and never-configured look identical to whoever opens the report."""
    r = _reporter(tmp_path)
    bad = _dist([1, 2, 3], median=15.0)
    bad["n_bins"] = 99
    r.update("polya", {"polya_distribution": bad})
    with caplog.at_level("WARNING"):
        r.finalize("run_end", force=True)
    assert any("polya" in m and "skipped" in m for m in caplog.messages), caplog.messages


# --- embedding the specs in a <script> element -----------------------------------

def test_the_embedded_json_is_not_html_escaped(tmp_path):
    """The bug this guards: autoescape turned every quote into `&#34;`.

    The embedded object became a JavaScript syntax error, so the report rendered with its
    charts silently missing and the only evidence was in the browser console. Nothing in
    the Python suite saw it, because the HTML was still perfectly well-formed.
    """
    _reporter(tmp_path).finalize("run_end", force=True)
    html = (tmp_path / "report_run_end.html").read_text()
    script = html[html.index("var figures ="):]
    assert "&#34;" not in script[:400], script[:200]
    assert 'var figures = [{"id":' in script


def test_a_reference_name_cannot_close_the_script_tag(tmp_path):
    """Reference names come from a user-supplied FASTA; `| safe` alone would inject.

    The specs carry construct names verbatim, so a name containing `</script>` would end
    the element early and everything after it would be parsed as HTML.
    """
    r = _reporter(tmp_path)
    hostile = "</script><script>window.__owned=1</script>"
    r.update(f"construct:{hostile}", {
        "ref_name": hostile, "n_reads": 10,
        "error_hotspot": {"substitution_rate": [0.1, 0.2], "deletion_rate": [0.0, 0.0],
                          "insertion_rate": [0.0, 0.0]},
    })
    r.finalize("run_end", force=True)
    html = (tmp_path / "report_run_end.html").read_text()
    # The payload TEXT is of course present — it is a construct name, and it is data.
    # What matters is that it cannot close the element: the whole JSON lives on one line,
    # and that line must contain no literal `</script>`.
    line = next(l for l in html.splitlines() if l.lstrip().startswith("var figures ="))
    assert "</script>" not in line
    assert "\\u003c/script\\u003e" in line


def test_the_escaped_form_still_parses_as_json(tmp_path):
    """Unicode escapes are valid JSON — the GUI decodes the same object the HTML runs."""
    _reporter(tmp_path).finalize("run_end", force=True)
    figures = json.loads((tmp_path / FIGURES_JSON_NAME).read_text())
    assert [f["id"] for f in figures] == ["read-length", "qscore"]


# --- the full set ----------------------------------------------------------------

def _full_snapshots():
    """One snapshot per module, minimal but complete enough to draw every figure."""
    d = _dist([1, 2, 3], q1=5.0, median=15.0, q3=25.0)
    pos = {"bin_min": 0.0, "bin_max": 30.0, "n_bins": 3, "bin_width": 10.0,
           "bin_counts": [1, 2, 9], "n": 12, "underflow": 0, "overflow": 0}
    return {
        "run_summary": {
            # 12 on the construct and 3 unmapped: the buckets below add up to the total,
            # as the share figure requires of a real run.
            "mapping": {"total": 15, "mapped": 12, "unmapped": 3, "mapping_rate": 0.8},
            "read_length": {"n": 12, "mean": 100.0},
            "read_length_distribution": d,
            "quality_score_distribution": _dist([2, 3], median=12.0),
        },
        "polya": {"polya_distribution": d, "polya_distribution_corrected": d},
        # The real shape: the template formats every one of these, so a fixture that
        # carries only n_reads would exercise a snapshot the module never emits.
        "unassigned": {"n_reads": 3, "unmapped": 3, "off_reference": 0,
                       "unprocessable": 0, "off_reference_contigs": {}},
        "construct:A": {
            "ref_name": "A", "n_reads": 12,
            "error_hotspot": {"substitution_rate": [0.1, 0.2],
                              "deletion_rate": [0.0, 0.0], "insertion_rate": [0.0, 0.0]},
            "read_stats": {"read_length_distribution": d},
            # A construct track carries its OWN poly(A), under the same module name the
            # run-level block uses. Without it the fixture was not a complete run: one of
            # the two per-construct distributions had no figure and the pinned list said
            # the figure set was correct anyway.
            "polya": {"polya_distribution": d},
            "mapq": {"distribution": {"0": 1, "60": 11}},
            "terminal_reach": {"ref_length": 30,
                               "start_position_distribution": pos,
                               "end_position_distribution": pos,
                               "joint_shortfall_distribution": {
                                   "axes": ["five_prime_short_nt",
                                            "three_prime_short_nt"],
                                   "n_bins": 3, "bin_width": 10.0, "n": 12,
                                   "cells": [[0, 0, 12]], "out_of_range": 0}},
        },
        "end_reason": {"reasons": {"signal_positive": {"n": 10, "fraction": 0.83}}},
        "voltage": {"series": [{"scan": 1, "t_hours": 0.0, "running_mv": -185.0,
                                "offset_mv": -5.0}]},
        "pore_activity": {"series": {"t_minutes": [0, 60], "coverage": [1.0, 1.0],
                                     "fractions": {"strand": [0.9, 0.8]}}},
    }


#: Every figure a complete run produces, in render order. Pinned so that a snapshot key
#: renamed upstream shows up HERE rather than as a chart that quietly stopped appearing:
#: `_interactive_figures` skips missing data by design, because a report must render.
EXPECTED_FIGURE_IDS = [
    "read-length", "polya", "read-length-by-construct", "polya-by-construct",
    "qscore", "per-position", "read-end", "read-start",
    "shortfall", "share", "identity-by-construct", "median-length", "mapq", "end-reason",
    "voltage", "pore",
]


def test_a_complete_run_draws_every_figure(tmp_path):
    r = CoAReporter(tmp_path, min_reads=1)
    for name, snap in _full_snapshots().items():
        r.update(name, snap)
    r.finalize("run_end", force=True)
    ids = [f["id"] for f in json.loads((tmp_path / FIGURES_JSON_NAME).read_text())]
    assert ids == EXPECTED_FIGURE_IDS


# --- the GUI panel ---------------------------------------------------------------

def test_every_figure_id_has_a_panel_and_every_panel_is_known():
    """One map per grouping, keyed by the same ids: a figure added to the report's
    sections but not to the GUI's panels would reach the GUI with nowhere to go."""
    from mrnaspect.reporting.reporter import (FIGURE_PANELS, _FIGURE_PANEL,
                                              _FIGURE_SECTION)

    assert set(_FIGURE_PANEL) == set(_FIGURE_SECTION)
    assert len(_FIGURE_PANEL) == 16
    assert set(_FIGURE_PANEL.values()) <= set(FIGURE_PANELS)
    # Every panel holds a figure; an empty one would be a tab with nothing behind it.
    assert set(_FIGURE_PANEL.values()) == set(FIGURE_PANELS)
    assert FIGURE_PANELS == ("read-length", "polya", "quality", "constructs",
                             "identity", "full-length", "flow-cell")


def test_an_unknown_figure_id_has_no_panel():
    """As with `_section_of`: a figure with no panel is a programming error, raised."""
    from mrnaspect.reporting.reporter import _panel_of, _section_of

    with pytest.raises(KeyError):
        _panel_of("no-such-figure")
    with pytest.raises(KeyError):
        _section_of("no-such-figure")


def test_figures_json_carries_each_figures_panel(tmp_path):
    """The complete fixture, so every one of the 16 ids reaches figures.json."""
    from mrnaspect.reporting.reporter import FIGURE_PANELS, _FIGURE_PANEL

    r = CoAReporter(tmp_path, min_reads=1)
    for name, snap in _full_snapshots().items():
        r.update(name, snap)
    r.finalize("run_end", force=True)
    figures = json.loads((tmp_path / FIGURES_JSON_NAME).read_text())
    assert [f["id"] for f in figures] == EXPECTED_FIGURE_IDS
    for f in figures:
        assert f["panel"] in FIGURE_PANELS, f"{f['id']}: unknown panel {f['panel']!r}"
        assert f["panel"] == _FIGURE_PANEL[f["id"]]


def test_a_renamed_snapshot_key_loses_its_figure_visibly(tmp_path):
    """The failure mode the list above exists for, made explicit."""
    snaps = _full_snapshots()
    snaps["polya"]["polya_distributionn"] = snaps["polya"].pop("polya_distribution")
    r = CoAReporter(tmp_path, min_reads=1)
    for name, snap in snaps.items():
        r.update(name, snap)
    r.finalize("run_end", force=True)
    ids = [f["id"] for f in json.loads((tmp_path / FIGURES_JSON_NAME).read_text())]
    assert "polya" not in ids
    assert ids == [i for i in EXPECTED_FIGURE_IDS if i != "polya"]


# --- nothing inlined into a <script> may be HTML-escaped --------------------------

def _script_blocks(html: str) -> list[str]:
    import re
    return re.findall(r"<script[^>]*>(.*?)</script>", html, re.S | re.I)


def test_the_bundle_is_inlined_byte_for_byte(tmp_path):
    """The guard that should have existed the first time.

    The template interpolates TWO things into `<script>` elements: the figure specs and
    the plotly bundle. Autoescape was fixed for the first and missed for the second, in
    the same commit — the renderer's own source came out as
    `typeof module === &#34;object&#34;`, a syntax error, so `Plotly` was never defined
    and every one of the thirteen figures rendered as an empty box. The HTML stayed
    perfectly well-formed and the only evidence was in a browser console.

    Byte equality rather than "contains no HTML entities": plotly's own source legitimately
    contains a handful of entity STRINGS in its HTML-handling code, so an entity ban is a
    false positive waiting to happen. Equality cannot be fooled either way.
    """
    from pathlib import Path

    from mrnaspect.reporting import figspec
    from mrnaspect.reporting.figspec import PLOTLY_BUNDLE

    _reporter(tmp_path).finalize("run_end", force=True)
    blocks = _script_blocks((tmp_path / "report_run_end.html").read_text())
    assert len(blocks) >= 2, f"expected the bundle and the specs, found {len(blocks)}"
    vendored = (Path(figspec.__file__).parent / "assets" / PLOTLY_BUNDLE).read_text(
        encoding="utf-8")
    # Selected by CONTENT, not by size. "the biggest block" happens to be the bundle on a
    # small fixture and is the SPEC block on a real run, where 15 constructs of figure
    # JSON come to 1.87 MB against the bundle's 1.42 MB — so a size-based pick quietly
    # compares the wrong thing, and passes for exactly as long as the fixture stays small.
    named = [b for b in blocks if "plotly.js (cartesian" in b]
    assert len(named) == 1, f"expected one bundle block, found {len(named)}"
    inlined = named[0]
    # Compared by DIGEST, and the message is built by hand. Asserting the two 1.4 MB
    # strings directly makes pytest render a diff of them on failure: the first version of
    # this test took 11 minutes to fail. A guard that slow to report is one someone
    # eventually switches off.
    import hashlib
    want = hashlib.sha256(vendored.encode()).hexdigest()
    got = hashlib.sha256(inlined.encode()).hexdigest()
    if got != want:
        where = next((i for i, (a, b) in enumerate(zip(inlined, vendored)) if a != b),
                     min(len(inlined), len(vendored)))
        raise AssertionError(
            f"the inlined bundle differs from the vendored file at offset {where}: "
            f"inlined {inlined[where:where + 40]!r} vs vendored "
            f"{vendored[where:where + 40]!r}. Autoescape has reached JavaScript, and "
            f"Plotly will not be defined — every figure renders as an empty box.")


def test_the_spec_block_is_not_html_escaped(tmp_path):
    """Our own content, so here an entity really is an error."""
    _reporter(tmp_path).finalize("run_end", force=True)
    blocks = _script_blocks((tmp_path / "report_run_end.html").read_text())
    specs = [b for b in blocks if "var figures" in b]
    assert len(specs) == 1, f"expected one spec block, found {len(specs)}"
    specs = specs[0]
    for entity in ("&#34;", "&amp;", "&lt;", "&gt;", "&#39;"):
        assert entity not in specs, (
            f"the spec block contains {entity} — autoescape makes it a syntax error")


# --- the palette reaches the figures the REPORTER builds --------------------------

def test_the_reporter_passes_no_colour_of_its_own(tmp_path):
    """figspec's tokens are only the palette if every caller uses them.

    The series colours for the distribution figures are chosen at the CALL SITE, so a
    validated palette inside figspec said nothing about them: three charts kept their old
    hues through a commit that claimed to have replaced the palette.
    """
    import re

    from mrnaspect.reporting import figspec

    r = _reporter(tmp_path)
    r.update("polya", {"polya_distribution": _dist([1, 2], median=15.0)})
    allowed = {c.lower() for c in figspec.CATEGORICAL}
    allowed |= {figspec.INK_PRIMARY, figspec.INK_SECONDARY, figspec.INK_MUTED,
                figspec.GRIDLINE, figspec.AXIS_LINE, figspec.EMPHASIS}
    allowed |= {step[1].lower() for step in figspec.SEQUENTIAL_BLUE}
    allowed = {c.lower() for c in allowed}

    for fig in r._interactive_figures():
        found = {c.lower() for c in
                 re.findall(r"#[0-9a-fA-F]{6}", json.dumps(fig["spec"]))}
        stray = found - allowed
        assert not stray, (
            f"{fig['id']} uses {sorted(stray)}, which is not a figspec token. The palette "
            f"is validated as a set; a hue passed in at the call site is unchecked.")


# --- what each medium shows ------------------------------------------------------

def test_the_screen_hides_nothing_from_the_reader(tmp_path):
    """The HTML is the complete artifact; the PDF is the printable subset of it.

    Decided 2026-10-15, reversing the previous split. The screen stylesheet may
    restyle anything it likes, but it may not remove content: a reader who opens the
    HTML must see everything the PDF carries. Only the opposite direction survives
    (``@media print`` drops the interactive section), because WeasyPrint runs no JS.

    Asserted on the rendered output rather than the template, since a rule reaching the
    reader is the thing that matters.
    """
    _reporter(tmp_path).finalize("run_end", force=True)
    html = (tmp_path / "report_run_end.html").read_text()
    assert "@media screen" in html
    screen = html[html.index("@media screen"):]
    # The block ends at the first line that closes the media query at its own indent.
    screen = screen[:screen.index("\n    }\n") + 6]
    assert "display: none" not in screen, (
        "The screen stylesheet hides content again. The HTML is the complete artifact; "
        "if a static figure duplicates an interactive one, delete the duplicate instead "
        "of hiding it per-medium.\n" + screen[-600:])
    assert "print-only" not in html, (
        "A print-only marker is back. Nothing in the report is print-only any more.")


def test_the_signature_line_is_print_only_but_its_claim_is_not(tmp_path):
    """A signature rule in a browser is a line nobody can put a pen on.

    So it is print-only (decided 2026-10-16). What it ASSERTS is not: the sentence
    saying this is NOT a certificate of analysis is information, and withholding it from
    the medium most people actually read is the wrong way round. The screen carries it
    without the rule.
    """
    _reporter(tmp_path).finalize("run_end", force=True)
    html = (tmp_path / "report_run_end.html").read_text()
    assert '<div class="signoff">' not in html, "no signature rule on screen"
    assert '<div class="section-title">Signoff</div>' not in html
    assert "not a certificate" in html, "the claim survives the block"
    assert "subject to QC manager review" in html


def test_every_figure_is_sectioned_and_figures_json_carries_it(tmp_path, fixture_bam,
                                                               curlcake_ref):
    """The grouping is ONE decision, made in Core, and it has to reach the GUI.

    figures.json is what the GUI reads, and it was built from a different projection of
    the same list than the template's -- so adding `section` to the context left the
    report grouped and the GUI with fourteen figures and nothing to group them by.
    """
    import json

    from mrnaspect.analysis.engine import AnalysisEngine
    from mrnaspect.reporting.reporter import FIGURE_PANELS, FIGURE_SECTIONS

    rep = CoAReporter(tmp_path, min_reads=1)
    engine = AnalysisEngine(rep, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    rep.finalize("run_end", force=True)

    figures = json.loads((tmp_path / "figures.json").read_text())
    assert figures, "no figures were written, so nothing was checked"
    keys = {k for k, _ in FIGURE_SECTIONS}
    for fig in figures:
        assert "section" in fig, f"{fig['id']} reaches the GUI with no section"
        assert fig["section"] in keys, f"{fig['id']}: unknown section {fig['section']!r}"
        # The GUI's grouping rides the same entry, from the same real-BAM run.
        assert fig.get("panel") in FIGURE_PANELS, f"{fig['id']}: no or unknown panel"


def test_the_gui_prose_carries_no_em_dash_either():
    """The report's rule, applied to the surface beside it.

    An em dash is four punctuation marks wearing one glyph, and the report replaced each
    with the mark that does its job. The GUI kept twelve, which made it the one place a
    reader meets the old habit -- and the GUI and the report are supposed to read as one
    product.

    Comments are stripped first: they are not what anybody sees, and the file's own
    section rules are drawn with them.
    """
    import re

    from pathlib import Path as _P

    root = _P(__file__).resolve().parents[2] / "gui"
    if not (root / "src" / "main.ts").exists():
        pytest.skip("GUI sources are not in this checkout")

    found = []
    for rel in ("src/main.ts", "index.html", "src/styles.css"):
        path = root / rel
        if not path.exists():
            continue
        text = path.read_text()
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)      # block comments
        text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)      # HTML comments
        text = re.sub(r"(?m)^\s*//.*$", " ", text)               # whole-line // comments
        text = re.sub(r"(?m)\s//[^\n`\"']*$", " ", text)         # trailing // comments
        for marker in ("\u2014", "&mdash;"):
            if marker in text:
                line = text[:text.index(marker)].count("\n") + 1
                found.append(f"{rel}:~{line} {marker!r}")
    assert not found, "em dash in GUI text a reader sees:\n  " + "\n  ".join(found)


def test_the_gui_shows_no_em_dash_for_a_missing_value():
    """A dash reads as "we looked and it is blank". That is not what is true.

    The report settled this: an absent field gets a statement, not a mark. The GUI kept
    using "\u2014" as the placeholder in six places, so a run with no mapping rate yet
    showed the same glyph as one whose rate was genuinely zero-width. It now says
    "Not measured", the same words the report uses, so the two surfaces agree.

    Scoped to the PLACEHOLDER. An em dash in the GUI's prose is a different question and
    is not what this guards.
    """
    import re

    from pathlib import Path as _P

    ts = _P(__file__).resolve().parents[2] / "gui" / "src" / "main.ts"
    if not ts.exists():
        pytest.skip("GUI sources are not in this checkout")
    src = ts.read_text()

    # A bare "\u2014" as a whole string literal is the placeholder shape.
    bad = re.findall(r'[:?]\s*"\u2014"', src)
    assert not bad, f"em dash used as a missing-value placeholder: {bad}"
    assert 'const NOT_MEASURED = "Not measured";' in src
    # And it has to be what the formatters actually fall back to.
    for fn in ("const num =", "const pct =", "const withUnit ="):
        i = src.index(fn)
        assert "NOT_MEASURED" in src[i:i + 320], f"{fn} does not fall back to it"


def test_the_gui_and_the_report_agree_on_the_missing_wording():
    """One string for one meaning, across two codebases."""
    from pathlib import Path as _P

    from mrnaspect.reporting.reporter import CoAReporter

    ts = _P(CoAReporter.__module__ and __file__).resolve().parents[2] / "gui" / "src" / "main.ts"
    if not ts.exists():
        pytest.skip("GUI sources are not in this checkout")
    # The reporter's own wording for an unmeasurable row.
    assert 'const NOT_MEASURED = "Not measured";' in ts.read_text()
    rep = CoAReporter.__init__  # noqa: F841 - import side only
    import inspect

    from mrnaspect.reporting import reporter as mod
    assert '"Not measured"' in inspect.getsource(mod), (
        "the report no longer uses this wording, so the GUI is now alone with it")


def test_the_gui_knows_the_same_panels_core_does():
    """A cross-language contract, which is why it is checked rather than trusted.

    Since the redesign (2026-11-06) the GUI groups figures by Core's PANEL key, not the
    report's section, and lists the panels itself to draw the tab row. The two lists can
    drift, and the symptom would be a panel whose figures never appear. The full check
    of the run view is test_gui_panels.py; this one sits beside the figures.json tests.
    """
    import re

    from mrnaspect.reporting.reporter import FIGURE_PANELS

    from pathlib import Path as _P
    ts = _P(__file__).resolve().parents[2] / "gui" / "src" / "main.ts"
    if not ts.exists():
        pytest.skip("GUI sources are not in this checkout")
    block = re.search(r"const PANELS: \[string, string\]\[\] = \[(.*?)\];",
                      ts.read_text(), re.S)
    assert block, "the GUI no longer declares PANELS"
    keys = re.findall(r'\["([a-z-]+)",\s*"[^"]+"\]', block.group(1))
    assert keys[1:] == list(FIGURE_PANELS), f"GUI {keys} against Core {FIGURE_PANELS}"


def _render_both(**over):
    """The real template, rendered for each medium from ONE context."""
    from jinja2 import Environment, FileSystemLoader
    from mrnaspect.reporting.reporter import (FIGURE_SECTIONS, _TEMPLATE_DIR,
                                      _TEMPLATE_NAME)

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    template = env.get_template(_TEMPLATE_NAME)
    ctx = {
        "report_title": "t", "test_date": "2026-10-15", "metrics": [], "constructs": [],
        "batch_id": "b", "signatory_title": "QC Reviewer",
        "sample": {"declared": [], "recorded": []},
        "insufficient_notice": None, "incomplete_notice": None, "signoff_scope": None,
        "unassigned": None, "auto_refresh_seconds": None,
        "figures_are_signed": True, "page_identity": "batch B",
        "figure_sections": FIGURE_SECTIONS,
        "interactive": [{"id": "f1", "title": "Read length", "caption": "c",
                         "section": "whole-run"}],
        "interactive_json": "[]", "plotly_js": "/* PLOTLY */",
        "printed": [],
        "print_sections": {
            "before": [{"key": "whole-run", "name": "Whole run",
                        "bands": [{"full_width": True, "figures": [
                            {"id": "f1", "title": "Read length", "caption": "c",
                             "full_width": True,
                             "panel": {"label": None, "name": "", "detail": "n=1",
                                       "url": "data:image/svg+xml;base64,AAAA"}}]}]}],
            "after": [], "construct_key": [], "constructs": [],
            "per_construct_heading": "Per construct",
        },
        **over,
    }
    return template.render(**ctx, for_print=False), template.render(**ctx, for_print=True)


def test_each_medium_is_rendered_for_itself_from_one_context(tmp_path):
    """One template, one context, two passes — not one string with half of it hidden.

    The guarantee that matters is that both describe the same run, and a single context
    dict is what delivers it: nothing in either pass reaches back into a snapshot. What
    the old `@media` split bought instead was a screen HTML carrying several MB of print
    images nobody on screen can see, and a PDF carrying the 1.4 MB plotly bundle it
    cannot run.
    """
    screen, printed = _render_both()

    assert "/* PLOTLY */" in screen
    assert 'id="plot-f1"' in screen
    assert '<img src="data:image/' not in screen, (
        "the screen render is carrying the print images")

    assert "data:image/svg+xml;base64,AAAA" in printed
    assert "/* PLOTLY */" not in printed, (
        "the print render is carrying a renderer WeasyPrint cannot run")
    assert "<script" not in printed
    assert 'id="plot-f1"' not in printed

    # The content that is not a figure is in BOTH — that is the claim. `signatory_title`
    # is no longer among them: it labels the signature rule, which is print-only, while
    # the sentence that rule attests to is in both.
    for shared in ("Read length", "not a certificate", "subject to QC manager review"):
        assert shared in screen and shared in printed, shared
    assert "QC Reviewer" in printed and "QC Reviewer" not in screen


def test_the_print_render_draws_every_panel_of_every_figure():
    """A selector is a dropdown on screen; on paper it can only be one figure per option.

    Dropping the extra options is the silent version of this: the PDF would look complete
    while showing one construct out of fifteen.
    """
    _screen, printed = _render_both(print_sections={
        "run_level": [], "run_level_bands": [],
        "constructs": [
            {"name": c, "bands": [{"full_width": True, "figures": [{
                "id": "per-position", "title": "Per-position error", "caption": "cap",
                "full_width": True,
                "panel": {"label": c, "name": c, "detail": f"{c} detail",
                          "url": f"data:image/svg+xml;base64,{c}"}}]}]}
            for c in ("A", "B", "C")],
    })
    assert printed.count("data:image/svg+xml;base64,") == 3
    for c in ("A", "B", "C"):
        assert f"{c} detail" in printed


def test_the_print_render_is_grouped_by_construct():
    """Following ONE construct must not mean seven places in the document."""
    def fig(fid, full):
        return {"id": fid, "title": fid, "caption": "", "full_width": full,
                "panel": {"label": "QK-09", "name": "QK-09", "detail": f"d-{fid}",
                          "url": f"data:image/svg+xml;base64,{fid}"}}

    _screen, printed = _render_both(print_sections={
        "before": [], "after": [], "per_construct_heading": "Per construct",
        "construct_key": [{"title": "Per-position error", "caption": "KEYCAPTION-SENTINEL"}],
        "constructs": [{"name": "QK-09", "bands": [
            {"full_width": False, "figures": [fig("read-length-by-construct", False)]},
            {"full_width": True, "figures": [fig("per-position", True)]},
        ]}],
    })
    # No "Figures:" prefix: every section here is figures, and the word sat in front of
    # the only thing that distinguished them.
    assert "Figures: QK-09" not in printed
    assert "QK-09" in printed and "Per construct" in printed
    # And the captions are said once, in the key, not under each construct. The
    # sentinel is deliberately unlike prose: "said once" also appears in a CSS comment
    # in the template, and the assertion counted that too.
    assert printed.count("KEYCAPTION-SENTINEL") == 1
    assert printed.count("base64,") == 2
    for fid in ("read-length-by-construct", "per-position"):
        assert f"d-{fid}" in printed


def test_a_full_width_figure_leaves_the_grid_rather_than_spanning_it():
    """Measured on WeasyPrint 69.0: an item with `grid-column: 1 / -1` collapses the
    track sizing of the whole container. The same grid lays its cells out at 82 mm
    without a spanning item and at 0-10 mm with one, so the panels came out a few
    millimetres across — a figure that is present, correctly drawn and unreadable.
    """
    def fig(fid, full):
        return {"id": fid, "title": fid, "caption": "", "full_width": full,
                "panel": {"label": None, "name": "", "detail": "",
                          "url": f"data:image/svg+xml;base64,{fid}"}}

    _screen, printed = _render_both(print_sections={
        "run_level": [], "run_level_bands": [],
        "constructs": [{"name": "C", "bands": [
            {"full_width": False, "figures": [fig("narrow", False)]},
            {"full_width": True, "figures": [fig("wide", True)]},
        ]}],
    })
    body = printed[printed.index("<body>"):]
    assert "span-both" not in body, (
        "a figure is spanning the grid again; WeasyPrint mis-sizes every track when "
        "anything does")
    # One grid for the one narrow band, and none for the wide figure.
    assert body.count('class="figure-grid"') == 1
    assert body.index("base64,narrow") < body.index("base64,wide")

    # And the CSS no longer offers a span rule to reach for.
    assert "grid-column" not in printed



def test_the_html_on_disk_carries_no_print_images(tmp_path):
    """End to end, because the context could be right and the wiring wrong."""
    _reporter(tmp_path).finalize("run_end", force=True)
    html = (tmp_path / "report_run_end.html").read_text()
    assert '<img src="data:image/' not in html
    assert "Plotly.newPlot" in html
