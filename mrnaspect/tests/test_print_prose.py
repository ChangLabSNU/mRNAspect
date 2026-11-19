"""Nothing printed on paper may tell the reader to do something paper cannot do.

This leaks in a particular way, which is why it needs a guard rather than care. The
figure specs carry their own text — a title line, a detail line under it — and that text
was written for the screen, where a reader really can drag the plot and move the slider.
Then the same specs started being drawn for print, and the instructions came with them:
"… 126 beyond the default window (drag the slider)" was printed under a figure in the
PDF. It was fixed in the detail line and survived in a caption, which is the shape of a
defect that gets fixed one instance at a time forever.

The remedy is structural rather than editorial: the screen's instructions live ONCE, in
the interactive section's own note, which only the screen render contains. A caption says
what a figure supports and never how to drive it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.reporting.reporter import CoAReporter

pytest.importorskip("jinja2")
pytest.importorskip("matplotlib")

#: Words that name a control the reader of a PDF does not have. Deliberately a short,
#: concrete list: a longer one starts catching ordinary prose ("select" appears in
#: "selected reads"), and a guard that cries wolf gets deleted.
SCREEN_ONLY = ("drag", "slider", "hover", "click", "zoom", "dropdown", "scroll",
               "tooltip", "mouse")


def _print_html(reporter) -> str:
    """The string WeasyPrint is given, which is not the HTML written to disk."""
    from jinja2 import Environment, FileSystemLoader

    from mrnaspect.reporting.reporter import _TEMPLATE_DIR, _TEMPLATE_NAME

    captured = {}
    import weasyprint

    real = weasyprint.HTML

    class Spy(real):
        def __init__(self, *a, **k):
            if "string" in k:
                captured["s"] = k["string"]
            super().__init__(*a, **k)

    weasyprint.HTML = Spy
    try:
        reporter.finalize("run_end", force=True)
    finally:
        weasyprint.HTML = real
    assert "s" in captured, "no PDF was rendered, so nothing was checked"
    return captured["s"]


@pytest.fixture(scope="module")
def printed(tmp_path_factory, fixture_bam: Path, curlcake_ref: Path) -> str:
    out = tmp_path_factory.mktemp("prose")
    reporter = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(reporter, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    return _print_html(reporter)


def _visible_text(html: str) -> str:
    # From the END of <head>, not from the first "<body>" string: a stylesheet comment that
    # mentions the body element is enough to start the cut inside the <style> block, and
    # then CSS comments read as page text. Found 2026-10-28 when one did exactly that.
    body = html[html.index("</head>"):]
    # Scripts and styles are stripped: on screen the page carries the 1.4 MB plotly
    # bundle and 1.9 MB of spec JSON, and a word search over the raw file finds their
    # vocabulary rather than the report's.
    body = re.sub(r"<style.*?</style>|<script.*?</script>", " ", body, flags=re.S)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    body = re.sub(r'src="data:[^"]*"', " ", body)
    return re.sub(r"<[^>]+>", " ", body)


def test_the_pdf_tells_nobody_to_drag_anything(printed) -> None:
    text = _visible_text(printed).lower()
    found = sorted({w for w in SCREEN_ONLY if w in text})
    assert not found, (
        f"the print render says {found}. A caption says what a figure supports, never "
        f"how to drive it — the screen's instructions belong in the interactive note, "
        f"which only the screen render carries.")


def test_the_screen_keeps_its_instructions(printed, tmp_path_factory, fixture_bam: Path,
                                           curlcake_ref: Path) -> None:
    """The converse, so the guard above cannot be satisfied by deleting the help."""
    out = tmp_path_factory.mktemp("prose_screen")
    reporter = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(reporter, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    reporter.finalize("run_end", force=True)
    # The report's OWN prose. The screen HTML embeds the plotly bundle and the spec
    # JSON, and both are full of "hover" — searching the raw file finds those and says
    # nothing about what the page tells a reader. An earlier version of this test did
    # exactly that and passed with the instructions deleted.
    screen = _visible_text((out / "report_run_end.html").read_text()).lower()
    assert "hover" in screen and "selector" in screen, (
        "the screen must still say how to drive the figures")
    assert "drag" in screen


def test_the_instructions_are_in_one_place_not_per_figure(printed, tmp_path_factory,
                                                          fixture_bam: Path,
                                                          curlcake_ref: Path) -> None:
    """Per-figure instructions are both redundant and medium-specific, which is how one
    of them ended up on paper."""
    out = tmp_path_factory.mktemp("prose_once")
    reporter = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(reporter, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    for fig in reporter._interactive_figures():
        caption = fig["caption"].lower()
        found = sorted({w for w in SCREEN_ONLY if w in caption})
        assert not found, f"{fig['id']} instructs in its caption: {found}"


#: The most sentences a caption may carry: one definition, one reading. The bound is
#: the measured maximum (2) plus one, so a caption needing a compound definition has
#: room and a caption drifting into a third claim does not.
MAX_CAPTION_SENTENCES = 3

#: A sentence end, and not a bare `.`: "a value near 1.0" is one sentence, and a guard
#: that counts the decimal point reports two.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def test_every_figure_carries_a_caption() -> None:
    """Five figures shipped with no caption at all.

    qscore, read-start, median-length, end-reason and voltage each rendered a title, an
    axis and nothing saying what the quantity WAS. An empty caption is not a shorter
    caption: "Voltage over the run" does not say whose voltage, over what, or why it is
    in a QC report, and none of that is recoverable from the axes.

    Over the TABLE, not over a built report. Three of the seventeen — end-reason,
    voltage, pore — come from MinKNOW's files rather than the BAM, so the fixture builds
    14 and a report-walking guard passes silently on the rest. The first version of this
    test did exactly that: all four of its mutations passed, because the caption it
    blanked belonged to a figure the fixture never reached.

    It guards PRESENCE and BOUND, which is what failed. It cannot check that a caption
    defines rather than prescribes — that part is read, not tested.
    """
    from mrnaspect.reporting.reporter import _FIGURE_TEXT

    assert _FIGURE_TEXT, "no figure copy at all"
    for fig_id, (title, caption) in _FIGURE_TEXT.items():
        assert title.strip(), f"{fig_id} has no title"
        assert caption.strip(), (
            f"{fig_id} has no caption. A caption names the quantity and the population "
            f"it is over, which is the one thing the axes cannot say.")
        assert caption.rstrip().endswith("."), (
            f"{fig_id} caption is not a sentence: {caption!r}")
        # Wrap damage. These captions are implicit-concatenated literals, and the
        # join is a space: `textwrap` breaking "model-corrected" at the hyphen put
        # "The model- corrected series" on the page. A double space is the same
        # failure with the break landing after a space instead.
        assert not re.search(r"\w- \w", caption), (
            f"{fig_id} caption was wrapped through a hyphen: {caption!r}")
        assert "  " not in caption, f"{fig_id} caption has a double space: {caption!r}"
        n = len(_SENTENCE_END.findall(caption.rstrip()))
        assert n <= MAX_CAPTION_SENTENCES, (
            f"{fig_id} caption runs to {n} sentences: {caption!r}")


def test_the_copy_table_and_the_figures_agree() -> None:
    """Neither side may carry an entry the other does not.

    `add()` raises on an id with no copy, so one direction is enforced at build time.
    The other is not: copy for a figure that no longer exists is dead text nobody reads
    and nothing fails on.
    """
    import ast

    from mrnaspect.reporting import reporter as mod

    tree = ast.parse(Path(mod.__file__).read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_interactive_figures")
    registered = {n.args[0].value for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "add" and n.args
                  and isinstance(n.args[0], ast.Constant)}

    assert registered, "no figures are registered, so nothing was checked"
    assert registered == set(mod._FIGURE_TEXT), (
        f"copy with no figure: {sorted(set(mod._FIGURE_TEXT) - registered)}; "
        f"figure with no copy: {sorted(registered - set(mod._FIGURE_TEXT))}")


#: The report's prose carries no em dash. A house rule, set on 2026-10-16,
#: not a technical constraint: do not "fix" a failure here by deleting the guard.
#:
#: An em dash is four different punctuation marks wearing one glyph. In this file's copy
#: it was standing in for apposition, explanation, consequence and plain separation, and
#: the reader has to work out which each time. Replacing each with the mark that does
#: that job (comma, colon, full stop, parentheses) says it instead. The figure captions
#: were the visible case: a title, a separator and a caption could put three em dashes in
#: one line, as "Read quality -- whole run -- The basecaller's own ... per read -- its
#: confidence in the call".
#:
#: The EN dash stays. It is a numeric range ("63.2 - 71.5%"), which is the one job no
#: other mark does.
EM_DASH = "\u2014"


def _live_strings(path: Path):
    """Every string literal that is not a docstring, with its line number.

    Source rather than a render, because a render only reaches the copy the fixture
    happens to produce: the incomplete-run banner, the sampling notice and the
    insufficient-reads text are the longest prose in the report and none of them appear
    on a run that completed.
    """
    import ast

    tree = ast.parse(path.read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs):
            yield node.lineno, node.value


def test_the_report_prose_carries_no_em_dash() -> None:
    from mrnaspect.reporting import reporter as mod

    pkg = Path(mod.__file__).parent
    found = []
    for path in sorted(pkg.glob("*.py")):
        for lineno, text in _live_strings(path):
            if EM_DASH in text:
                found.append(f"{path.name}:{lineno} {text.strip()[:70]}")
    assert not found, "em dash in report copy:\n  " + "\n  ".join(found)


def test_the_template_carries_no_em_dash() -> None:
    """Jinja and CSS comments are stripped first: they are not copy, and the template's
    own commentary explains the rule in prose that uses the mark."""
    import re

    from mrnaspect.reporting.reporter import _TEMPLATE_DIR, _TEMPLATE_NAME

    body = (Path(_TEMPLATE_DIR) / _TEMPLATE_NAME).read_text()
    body = re.sub(r"\{#.*?#\}", " ", body, flags=re.S)       # Jinja comments
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.S)        # CSS comments
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)       # HTML comments

    hits = [EM_DASH] if EM_DASH in body else []
    if "&mdash;" in body:
        hits.append("&mdash;")
    assert not hits, f"em dash in the template: {hits}"


def test_no_spec_text_instructs_either(printed, tmp_path_factory, fixture_bam: Path,
                                       curlcake_ref: Path) -> None:
    """The title and detail lines travel into the panel label, which is printed.

    This is where it actually happened: `_escaped_note` ended "(drag the slider)".
    """
    out = tmp_path_factory.mktemp("prose_spec")
    reporter = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(reporter, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    def texts(node):
        """Every string a reader can SEE, and no key names.

        `hovermode`, `hovertemplate` and `rangeslider` are layout keys — they are how
        the screen gets its hover and its slider at all. Matching on a serialised layout
        catches those and says nothing about the prose, which is the only thing a reader
        of the PDF ever meets.
        """
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("text", "label", "name", "ticksuffix", "tickprefix"):
                    if isinstance(value, str):
                        yield value
                elif key not in ("hovertemplate", "customdata"):
                    yield from texts(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                yield from texts(item)

    for fig in reporter._interactive_figures():
        for string in texts(fig["spec"]):
            found = sorted({w for w in SCREEN_ONLY if w in string.lower()})
            assert not found, f"{fig['id']} spec text instructs: {found} in {string!r}"
