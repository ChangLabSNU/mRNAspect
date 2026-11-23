"""The corrected poly(A) has to reach the page, not just the snapshot.

Found 2026-10-28 by running the app end to end. With an arm declared the module
applied the correction -- QK-09 raw 82.76 -> 91.14 nt -- and the report printed 83 nt in
the construct's row, said nothing about a correction there, and printed "UNCORRECTED:
not applied" on the run-level row. "Corrected to" appeared zero times. Every unit test of
the reporter passed, because each built its input by hand.

So this file goes through the real CLI on a real BAM and reads what a person reads: the
rendered page and the PDF, compared against what the module itself recorded.

Arms are the RECORDED ones, from the constructs' own metadata: QK-09 is
m1psi, HA-WT is U. The fixture is stock rna004_130bps_hac@v5.3.0 (its RG says so), whose
m1psi factor needs delta_pct -- so this also proves the CLI hands the aligner flags to the
module, which a bare AnalysisEngine in a unit test does not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mrnaspect.main import main as offline_main
from mrnaspect.reporting.reporter import CoAReporter

ARMS = "sample:\n  arm: m1psi\n  arms:\n    HA-WT: U\n"
CALLED = "Poly(A) tail length, uncorrected (median)"
CORRECTED = "Poly(A) tail length, corrected (median)"


def _run(tmp_path: Path, bam: Path, ref: Path, config: str | None) -> Path:
    src = tmp_path / "in"
    src.mkdir()
    (src / bam.name).symlink_to(bam)
    bai = bam.with_suffix(bam.suffix + ".bai")
    if bai.exists():
        (src / bai.name).symlink_to(bai)
    argv = ["--input-dir", str(src), "--reference", str(ref), "--output-dir",
            str(tmp_path / "out"), "--output-name", "r", "--mode", "bam",
            "--min-reads", "1"]
    if config is not None:
        cfg = tmp_path / "arms.yaml"
        cfg.write_text(config)
        argv += ["--config", str(cfg)]
    assert offline_main(argv) == 0
    return tmp_path / "out" / "r"


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def _rows(html: str) -> dict[str, list[dict[str, str]]]:
    """Each construct's table as rows keyed by COLUMN HEADER, keyed by construct name.

    By header, not by position: R1 moved these tables from three columns to five, and a
    test reading "the last cell" would have quietly started reading Status.
    """
    out: dict[str, list[dict[str, str]]] = {}
    for block in re.findall(r'<div class="construct-block">(.*?)</table>', html, re.S):
        name = re.search(r'<div class="construct-title">\s*([^ <]+)', block).group(1)
        heads = [_text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", block, re.S)]
        rows = []
        for tr in re.findall(r"<tr>(.*?)</tr>", block, re.S):
            cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if cells:
                assert len(cells) == len(heads), (heads, cells)
                rows.append(dict(zip(heads, cells)))
        out[name] = rows
    return out


def _row(rows: list[dict[str, str]], label: str) -> dict[str, str]:
    hits = [r for r in rows if r["Test"] == label]
    assert len(hits) == 1, f"expected exactly one {label!r} row, found {len(hits)}"
    return hits[0]


@pytest.fixture(scope="module")
def declared(tmp_path_factory, variant_bam, qk_ha_ref) -> Path:
    return _run(tmp_path_factory.mktemp("declared"), variant_bam, qk_ha_ref, ARMS)


@pytest.fixture(scope="module")
def undeclared(tmp_path_factory, variant_bam, qk_ha_ref) -> Path:
    return _run(tmp_path_factory.mktemp("undeclared"), variant_bam, qk_ha_ref, None)


def _module(run_dir: Path, construct: str) -> dict:
    snaps = json.loads((run_dir / "latest_snapshot.json").read_text())["snapshots"]
    return snaps[f"construct:{construct}"]["polya"]


@pytest.mark.parametrize("construct,arm", [("QK-09", "m1psi"), ("HA-WT", "U")])
def test_the_corrected_value_the_module_computed_is_the_one_the_page_prints(
        declared, construct, arm) -> None:
    polya = _module(declared, construct)
    c = polya["correction"]
    assert c["applied"] is True and c["arm"] == arm, c
    rows = _rows((declared / "report_run_end.html").read_text())[construct]
    called, corrected = _row(rows, CALLED), _row(rows, CORRECTED)
    assert called["Result"] == CoAReporter._nt(polya["polya_distribution"]["median"])
    assert corrected["Result"] == CoAReporter._nt(
        polya["polya_distribution_corrected"]["median"])
    assert corrected["Result"] != called["Result"] or arm == "U", (
        "an m1psi construct's corrected median printed equal to its raw one")
    said = {"m1psi": "m1Ψ-substituted", "U": "canonical U"}[arm]
    assert f"modification {said} (declared)" in corrected["Method / specification"]
    assert "The constants are provisional" in corrected["Method / specification"]


def test_an_applied_correction_leaves_no_stale_reason_in_the_record(declared) -> None:
    for construct in ("QK-09", "HA-WT"):
        assert _module(declared, construct)["correction"]["reason"] is None


def test_the_pdf_carries_the_corrected_value_too(declared) -> None:
    """The PDF is the archived artifact, so it is checked, not inferred from the HTML."""
    import pypdf  # not importorskip: without the reader this must fail, not skip
    text = " ".join(p.extract_text() or "" for p in
                    pypdf.PdfReader(str(declared / "report_run_end.pdf")).pages)
    text = re.sub(r"\s+", " ", text)
    assert "corrected (median)" in text
    assert "Corrected to the canonical-equivalent ruler" in text
    want = CoAReporter._nt(_module(declared, "QK-09")["polya_distribution_corrected"]["median"])
    assert want in text


def test_without_an_arm_the_corrected_row_says_why_and_prints_no_number(undeclared) -> None:
    rows = _rows((undeclared / "report_run_end.html").read_text())
    for construct in ("QK-09", "HA-WT"):
        corrected = _row(rows[construct], CORRECTED)
        assert corrected["Result"] == "Not measured"
        method = corrected["Method / specification"]
        assert method.startswith("Not corrected (withheld):")
        assert "sample.arm" in method, "the reason must say how to fix it"


def test_the_run_level_row_is_uncorrected_and_points_at_the_constructs(declared) -> None:
    html = (declared / "report_run_end.html").read_text()
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                     re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)))
    assert CALLED in visible
    assert "applied per construct, in Per-construct results" in visible
    assert "UNCORRECTED dorado ruler: not applied" not in visible


# --- the figures, 2026-10-28: a run-level corrected series, and both per construct --

def _figures(run_dir: Path) -> dict:
    return {f["id"]: f for f in json.loads((run_dir / "figures.json").read_text())}


def test_the_whole_run_figure_draws_a_corrected_series_and_says_whose(declared) -> None:
    """It used to carry a "model-corrected" legend entry over an empty histogram."""
    polya = _figures(declared)["polya"]
    data = polya["spec"]["data"]
    # Each series is its bars (filled) and its curve (a line through them).
    bars = [t for t in data if t.get("fill")]
    curves = [t for t in data if not t.get("fill")]
    # The pooled series says whose reads it holds (2026-11-13).
    assert [t["name"] for t in bars] == ["uncorrected", "corrected (assigned reads)"]
    assert [t["name"] for t in curves] == ["uncorrected", "corrected (assigned reads)"]
    assert sum(y for y in bars[1]["y"] if y) > 0
    assert sum(curves[1]["y"]) > 0
    assert "not the same set of reads" in polya["caption"]
    # One grid: QK-09 and HA-WT are stretched differently, and the pooled series is drawn
    # on the as-called bins so the two can be read against each other.
    called, corrected = bars
    assert corrected["x"] == called["x"]
    assert curves[1]["x"] == curves[0]["x"]
    assert "in proportion to overlap" in polya["caption"]
    assert "Gaussian kernel of fixed 2 nt" in polya["caption"]


def test_without_an_arm_nothing_is_corrected_and_nothing_pretends_to_be(undeclared) -> None:
    polya = _figures(undeclared)["polya"]
    # Bars and curve, both of the one series.
    assert [t["name"] for t in polya["spec"]["data"]] == ["uncorrected", "uncorrected"]
    assert "corrected series" not in polya["caption"]


def test_each_construct_draws_both_with_a_switch(declared) -> None:
    by = _figures(declared)["polya-by-construct"]
    assert len(by["spec"]["layout"]["updatemenus"]) == 2
    assert {t["name"] for t in by["spec"]["data"]} == {"uncorrected", "corrected"}


def test_the_word_is_corrected(declared) -> None:
    html = (declared / "report_run_end.html").read_text()
    assert "model-corrected" not in _text(html[html.index("</head>"):])


def test_no_page_says_as_called(declared) -> None:
    """"As called" became "uncorrected" everywhere a reader looks (2026-11-13): the
    rows, the figure legends, the view buttons and the captions, on screen and in print.
    The JSON keys (``median_length`` beside ``median_length_corrected``) do not move."""
    for name in ("report_run_end.html", "figures.json"):
        text = (declared / name).read_text()
        hits = re.findall(r"[^\n]{0,40}\bas[ -]called\b[^\n]{0,40}", text, re.I)
        assert not hits, f"{name}: {hits[:3]}"
    pdf_html = re.sub(r"\s+", " ", (declared / "report_run_end.html").read_text())
    assert "uncorrected" in pdf_html.lower()
