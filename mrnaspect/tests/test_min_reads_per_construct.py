"""A construct with too few reads is listed, not given a section and figures.

Decided 2026-10-28 (content audit, item 11): a minimum of 100 assigned reads
per construct, and below it one line -- name, reads, MAPQ 0 share -- with no figures. A
panel reference holds constructs the sample may not, and the aligner puts a few reads on
them anyway; on the run-1 test input six such constructs, 1-12 reads each, took a section
and every per-construct figure.

A display rule only. The whole-run numbers keep every construct's reads, so they still
equal qc_result.json's -- which is why these tests compare the run-level table across
minimums, not just the per-construct part.

The fixture's constructs have 130 (QK-09) and 140 (HA-WT) reads, so a minimum of 135
lists QK-09 alone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mrnaspect.main import load_config
from mrnaspect.main import main as offline_main
from mrnaspect.reporting.reporter import CoAReporter

from .test_polya_reaches_the_report import ARMS


def _run(tmp: Path, bam: Path, ref: Path, minimum: int) -> Path:
    src = tmp / "in"
    src.mkdir(parents=True)
    (src / bam.name).symlink_to(bam)
    bai = bam.with_suffix(bam.suffix + ".bai")
    if bai.exists():
        (src / bai.name).symlink_to(bai)
    cfg = tmp / "arms.yaml"
    cfg.write_text(ARMS)
    assert offline_main([
        "--input-dir", str(src), "--reference", str(ref), "--output-dir", str(tmp / "out"),
        "--output-name", "r", "--mode", "bam", "--min-reads", "1", "--config", str(cfg),
        "--min-reads-per-construct", str(minimum)]) == 0
    return tmp / "out" / "r"


@pytest.fixture(scope="module")
def runs(tmp_path_factory, variant_bam, qk_ha_ref) -> dict[int, Path]:
    return {m: _run(tmp_path_factory.mktemp(f"min{m}"), variant_bam, qk_ha_ref, m)
            for m in (0, 135, 1000)}


def _body(run: Path) -> str:
    html = (run / "report_run_end.html").read_text()
    return html[html.index("</head>"):]


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(
        r"<script.*?</script>", " ", fragment, flags=re.S))).strip()


def _sections(run: Path) -> list[str]:
    return re.findall(r'<div class="construct-block">\s*<div class="construct-title">'
                      r'\s*([^ <]+)', _body(run))


def _intro(run: Path) -> str:
    body = _body(run)
    start = body.index('<div class="section-title">Per-construct results</div>')
    return _text(body[start: body.index("</p>", start)])


def _figures(run: Path) -> dict[str, dict]:
    return {f["id"]: f for f in json.loads((run / "figures.json").read_text())}


def _options(figure: dict) -> list[str]:
    menus = figure["spec"]["layout"].get("updatemenus") or []
    return [b["label"] for b in menus[0]["buttons"]] if menus else []


# --- the shipped default -------------------------------------------------------------------

def test_the_shipped_minimum_is_100(qk_ha_ref) -> None:
    assert load_config(None, {"reference": str(qk_ha_ref)})["min_reads_per_construct"] == 100


def test_a_construct_with_exactly_the_minimum_is_reported(tmp_path) -> None:
    """At least the minimum, as the setting's own comment says -- not more than it."""
    r = CoAReporter(tmp_path, min_reads=1, min_reads_per_construct=100)
    r._snapshots = {"construct:A": {"n_reads": 100},
                    "construct:B": {"n_reads": 99, "mapq": {"mapq0_fraction": 0.25,
                                                            "low_mapq_fraction": 0.5}}}
    assert [name for name, _snap in r._reported_constructs()] == ["A"]
    # MAPQ 0 -- ties -- not "low MAPQ": the fixture has 0 of both, so it cannot tell.
    assert r._listed_constructs() == [{"name": "B", "n_reads": 99, "mapq0": "25.0%"}]


def test_a_negative_minimum_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError, match="min_reads_per_construct"):
        CoAReporter(tmp_path, min_reads=1, min_reads_per_construct=-1)


# --- below it: listed, one line, no figures --------------------------------------------------

def test_a_construct_below_it_gets_no_section(runs) -> None:
    assert _sections(runs[0]) == ["HA-WT", "QK-09"]
    assert _sections(runs[135]) == ["HA-WT"]


def test_it_is_listed_with_its_reads_and_mapq0_share(runs) -> None:
    body = _body(runs[135])
    listed = body[body.index('<div class="construct-block listed-block">'):]
    listed = listed[: listed.index("</table>")]
    snaps = json.loads((runs[135] / "latest_snapshot.json").read_text())["snapshots"]
    mapq0 = CoAReporter._pct(snaps["construct:QK-09"]["mapq"]["mapq0_fraction"])
    assert "Fewer than 135 reads" in _text(listed)
    rows = re.findall(r"<tr>(.*?)</tr>", listed, re.S)
    assert [_text(r) for r in rows[1:]] == [f"QK-09 130 {mapq0}"]


def test_the_intro_says_which_are_reported_and_which_listed(runs) -> None:
    assert "2 reference constructs received reads." in _intro(runs[0])
    assert ("2 reference constructs received reads: the 1 with at least 135 reads is "
            "reported below, and the 1 with fewer is listed at the end, without figures"
            ) in _intro(runs[135])
    assert ("2 reference constructs received reads, none as many as 1,000, so each is "
            "listed below, without figures") in _intro(runs[1000])


def test_no_per_construct_figure_draws_it(runs) -> None:
    figures = _figures(runs[135])
    per_construct = [f for f in figures.values() if f["section"] == "per-construct"]
    assert per_construct, "the fixture must have per-construct figures to check"
    for f in per_construct:
        assert _options(f) == ["HA-WT"], (f["id"], _options(f))
    for fid in ("identity-by-construct", "median-length"):
        bars = json.dumps(figures[fid]["spec"]["data"])
        assert "QK-09" not in bars and "HA-WT" in bars, fid


def test_with_every_construct_listed_there_is_no_per_construct_figure(runs) -> None:
    assert _sections(runs[1000]) == []
    figures = _figures(runs[1000])
    assert not [f for f in figures.values() if f["section"] == "per-construct"]
    assert "identity-by-construct" not in figures


def test_the_pdf_draws_no_figures_for_it(runs) -> None:
    """The PDF is the archived artifact, so it is read, not inferred from figures.json. A
    construct's figure block opens with its name and the first figure's title."""
    import pypdf  # not importorskip: without the reader this must fail, not skip

    def pdf(run: Path) -> str:
        return re.sub(r"\s+", " ", " ".join(
            p.extract_text() or "" for p in
            pypdf.PdfReader(str(run / "report_run_end.pdf")).pages))

    block = "QK-09 Read length by construct"
    assert block in pdf(runs[0]), "the marker must exist when QK-09 IS drawn"
    listed = pdf(runs[135])
    assert block not in listed
    assert "Fewer than 135 reads" in listed


# --- the whole run is untouched ---------------------------------------------------------------

def _run_level_table(run: Path) -> str:
    body = _body(run)
    start = body.index('<div class="section-title">Whole-run results</div>')
    return _text(body[start: body.index("</table>", start)])


def test_the_whole_run_results_do_not_move(runs) -> None:
    """Listing a construct must not drop its reads from a pooled number: the report's
    run-level table would then disagree with qc_result.json, which keeps every construct."""
    assert _run_level_table(runs[135]) == _run_level_table(runs[0])
    assert _run_level_table(runs[1000]) == _run_level_table(runs[0])
    result = json.loads((runs[135] / "qc_result.json").read_text())
    assert sorted(result["qc_metrics"]["constructs"]) == ["HA-WT", "QK-09"]


def test_whole_run_figures_keep_its_reads(runs) -> None:
    figures = _figures(runs[135])
    assert "pools the 2 constructs" in figures["polya"]["caption"]
    assert "QK-09" in json.dumps(figures["share"]["spec"])


def test_the_provenance_states_the_minimum(runs) -> None:
    text = _text(_body(runs[135]))
    assert "Minimum reads per construct 135 assigned" in text
