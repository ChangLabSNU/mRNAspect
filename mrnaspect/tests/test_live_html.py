"""The CoA is now polled by a browser, which changes three things about writing it.

1. **Atomicity.** A plain ``write_text`` has a real window in which a reload returns
   TRUNCATED HTML. Everything else in the project already publishes by rename; the
   human-readable artifact was the one place still missing it.
2. **A stable path.** ``report_{label}.html`` changes every interim, so there is no URL
   to park a browser on. ``report_latest.html`` is republished on every render.
3. **PDF is terminal-only.** Measured 2026-09-04: the interim render is
   matplotlib 10.804 s (92.4%) / WeasyPrint 0.851 s (7.3%) / jinja2 0.033 s (0.3%).
   The PDF is a final deliverable, and writing 400 of them per run is waste — a minor
   win in seconds, but it must never be a file nobody opens.

The auto-refresh interval is deliberately the POD5 cadence and not faster: order
statistics need 5-20 min and means 6.5-11 h to settle, so a fast refresh would
animate sampling noise and read as live movement that is not there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mrnaspect.reporting.reporter import (
    CoAReporter,
    DEFAULT_AUTO_REFRESH_SECONDS,
    LATEST_HTML_NAME,
)

MIN_READS = 100
_REFRESH_TAG = 'http-equiv="refresh"'


def _load(rep: CoAReporter, mapped: int = 5_000) -> None:
    """Minimal snapshots: enough to clear the gate and exercise the render."""
    total = int(mapped / 0.9)
    rep.update("run_summary", {"mapping": {"total": total, "mapped": mapped,
               "unmapped": total - mapped, "mapping_rate": 0.9}})
    rep.update("polya", {"polya_distribution": {"median": 86.0, "Q1": 73.0, "Q3": 103.0}})


def _reporter(tmp_path: Path, **kw) -> CoAReporter:
    r = CoAReporter(tmp_path, min_reads=MIN_READS, **kw)
    _load(r)
    return r


# --- PDF is terminal-only ---------------------------------------------------

def test_interim_writes_no_pdf(tmp_path: Path) -> None:
    res = _reporter(tmp_path).report("interim_1")
    assert res["pdf_path"] is None
    assert not (tmp_path / "report_interim_1.pdf").exists()


def test_terminal_still_writes_the_pdf(tmp_path: Path) -> None:
    res = _reporter(tmp_path).finalize("run_end")
    assert res["pdf_path"] is not None
    assert Path(res["pdf_path"]).exists() and Path(res["pdf_path"]).stat().st_size > 0


def test_path_never_names_a_file_that_does_not_exist(tmp_path: Path) -> None:
    """`path` is consumed by logs and by qc_result.json's report block.

    Leaving it pointing at an unwritten PDF would put a dead path in the reported
    artifact — the exact class of silent lie the provenance rules exist to prevent.
    """
    rep = _reporter(tmp_path)
    for res in (rep.report("interim_1"), rep.finalize("run_end")):
        assert Path(res["path"]).exists(), res["path"]


# --- the stable path --------------------------------------------------------

def test_latest_html_tracks_the_most_recent_render(tmp_path: Path) -> None:
    """The second render must differ because its NUMBERS differ.

    This used to vary `consensus.consensus_identity_pct` between the two renders, and
    99.1 never reached the HTML: the run-level identity row is pooled over the construct
    snapshots, not read from the flat `consensus` slot, so writing that slot changes
    nothing a reader sees. The test passed anyway, on the render label in the title --
    "QCRNA QC Report (interim_1)" against "(interim_2)". With the title naming the
    document rather than the renderer's reason, the two renders came out byte-identical
    and the test finally said so.

    Mapped reads instead: the one number this fixture's snapshots actually drive, and the
    one row in the table with a real criterion.
    """
    rep = _reporter(tmp_path)
    rep.report("interim_1")
    latest = tmp_path / LATEST_HTML_NAME
    assert latest.exists()
    first = latest.read_text(encoding="utf-8")
    assert f"{5_000:,}" in first, "the fixture's own number must be in the render"

    _load(rep, mapped=7_500)
    rep.report("interim_2")
    second = latest.read_text(encoding="utf-8")

    assert second != first, "report_latest.html did not follow the second render"
    assert f"{7_500:,}" in second and f"{5_000:,}" not in second, (
        "it followed the render but not the numbers in it")
    # This fixture has nothing to draw, so the live page and the numbered copy are one
    # render; with figures the live page also draws them (below).
    assert second == (tmp_path / "report_interim_2.html").read_text(encoding="utf-8")


def test_per_label_files_survive_as_the_audit_trail(tmp_path: Path) -> None:
    """The stable path is an addition, not a replacement."""
    rep = _reporter(tmp_path)
    rep.report("interim_1")
    rep.report("interim_2")
    assert (tmp_path / "report_interim_1.html").exists()
    assert (tmp_path / "report_interim_2.html").exists()


# --- atomicity -------------------------------------------------------------

def test_no_temporary_files_are_left_behind(tmp_path: Path) -> None:
    rep = _reporter(tmp_path)
    rep.report("interim_1")
    rep.finalize("run_end")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"publish-by-rename leaked: {leftovers}"


def test_the_writer_replaces_rather_than_truncates(tmp_path: Path) -> None:
    """Edge case: overwriting a LONGER existing file must not leave a tail behind.

    A truncate-and-write would leave the old bytes past the new length if the write were
    short; os.replace cannot, and this pins that we use it.
    """
    from mrnaspect.reporting.reporter import _atomic_write_text
    target = tmp_path / "x.html"
    target.write_text("Z" * 5000, encoding="utf-8")
    _atomic_write_text(target, "short")
    assert target.read_text(encoding="utf-8") == "short"


# --- auto refresh ----------------------------------------------------------

def test_interim_carries_the_meta_refresh(tmp_path: Path) -> None:
    res = _reporter(tmp_path).report("interim_1")
    html = Path(res["html_path"]).read_text(encoding="utf-8")
    assert _REFRESH_TAG in html
    assert f'content="{DEFAULT_AUTO_REFRESH_SECONDS}"' in html


def test_terminal_report_does_not_refresh_itself(tmp_path: Path) -> None:
    """An archived CoA that reloads on a timer is a defect, not a feature."""
    res = _reporter(tmp_path).finalize("run_end")
    html = Path(res["html_path"]).read_text(encoding="utf-8")
    assert _REFRESH_TAG not in html


def test_refresh_can_be_disabled(tmp_path: Path) -> None:
    res = _reporter(tmp_path, auto_refresh_seconds=None).report("interim_1")
    html = Path(res["html_path"]).read_text(encoding="utf-8")
    assert _REFRESH_TAG not in html


def test_refresh_interval_is_the_pod5_cadence_not_faster(tmp_path: Path) -> None:
    """Pins the rationale, not just the number.

    A future "make it feel more live" change to e.g. 5 s would show the estimate wandering
    inside its own sampling error between POD5s, which the content cannot even have
    changed between. If the POD5 cadence itself changes, change this test deliberately.
    """
    assert DEFAULT_AUTO_REFRESH_SECONDS >= 60


# --- per-construct figures are terminal-only -------------------------------

def _with_constructs(tmp_path: Path, n: int = 3) -> CoAReporter:
    from mrnaspect.snapshot_keys import construct_key
    rep = _reporter(tmp_path)
    pp = [0.04 + (0.26 if 40 <= i < 50 else 0.0) for i in range(200)]
    for i in range(n):
        rep.update(construct_key(f"C-{i:02d}"), {
            "n_reads": 1000,
            "consensus": {"consensus_identity_pct": 96.8, "mismatch_site_count": 2,
                          "mismatch_rate_by_position": pp},
            "error_hotspot": {"mean_total_error_rate": 0.06,
                              "max_total_error_rate": 0.31, "substitution_rate": pp},
            "pileup": {"covered_positions": 200, "ref_length": 200}})
    return rep


def test_a_numbered_interim_draws_no_figures(tmp_path: Path) -> None:
    """The numbered copies are the audit trail, one per render: with the figures each
    would be 1.8-4 MB (plotly alone 1.4 MB), and a 20 h run writes hundreds of them.

    Until 2026-11-10 no interim drew figures; the live page now does (below). The
    original reason, a per-input budget, was the matplotlib figures' 10.8 s per render;
    the plotly specs are built on every render anyway, for figures.json.
    """
    res = _with_constructs(tmp_path).report("interim_1")
    html = Path(res["html_path"]).read_text(encoding="utf-8")
    assert '<img src="data:image/' not in html
    assert "Plotly.newPlot" not in html


def test_a_numbered_interim_says_where_the_figures_are(tmp_path: Path) -> None:
    """The same absence means two different things, and only one is a defect.

    An operator who reads "no figures" as "this run produced nothing to plot" is being
    misled by a policy. So the page says where they are, in the page, not in a log.
    """
    res = _with_constructs(tmp_path).report("interim_1")
    html = Path(res["html_path"]).read_text(encoding="utf-8")
    assert "keeps no figures" in html and LATEST_HTML_NAME in html
    assert "qc_result.json" in html, "and where the numbers behind them already are"


# --- the live page draws the figures (2026-11-10) ----------------------

def test_the_live_page_draws_the_figures(tmp_path: Path) -> None:
    """report_latest.html, which the GUI's "Interim report" opens and a browser can be
    parked on, draws the same figures as the final report."""
    res = _with_constructs(tmp_path).report("interim_1")
    latest = (tmp_path / LATEST_HTML_NAME).read_text(encoding="utf-8")
    numbered = Path(res["html_path"]).read_text(encoding="utf-8")
    assert "Plotly.newPlot" in latest and 'class="plot"' in latest
    assert "keeps no figures" not in latest
    assert len(numbered) < 200_000 < len(latest)


def test_the_live_page_refreshes_by_a_timer_a_reader_can_stop(tmp_path: Path) -> None:
    """A meta refresh cannot be stopped, and a reload undoes the reader's zoom."""
    res = _with_constructs(tmp_path).report("interim_1")
    latest = (tmp_path / LATEST_HTML_NAME).read_text(encoding="utf-8")
    assert _REFRESH_TAG not in latest
    assert f"{DEFAULT_AUTO_REFRESH_SECONDS} * 1000" in latest
    assert 'id="refresh-paused"' in latest
    # Nothing to zoom in a numbered copy: it keeps the meta refresh.
    assert _REFRESH_TAG in Path(res["html_path"]).read_text(encoding="utf-8")


def test_the_final_report_neither_refreshes_nor_offers_to(tmp_path: Path) -> None:
    html = Path(_with_constructs(tmp_path).finalize("run_end")["html_path"]).read_text(
        encoding="utf-8")
    assert _REFRESH_TAG not in html
    assert "location.reload" not in html and "refresh-paused" not in html


def test_the_terminal_report_draws_the_figures(tmp_path: Path) -> None:
    res = _with_constructs(tmp_path).finalize("run_end")
    html = Path(res["html_path"]).read_text(encoding="utf-8")
    assert "Plotly.newPlot" in html
    # The screen render carries the specs, not the print images.
    assert '<img src="data:image/' not in html
    assert (Path(res["html_path"]).parent / "report_run_end.pdf").exists()


