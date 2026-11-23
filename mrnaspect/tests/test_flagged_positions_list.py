"""A construct's flagged positions are listed in the PDF, under its per-position figure.

2026-11-06: the per-position figure marks the flagged positions, but the printed CoA
listed none, so a reviewer could not read off where they are. Each construct's printed
figure block now lists them right under that figure: positions only, 1-based as on the
figure's axis, from the highest mismatch rate down. At most 50 (2026-11-10): past
that, the 50 with the highest mismatch, and a line saying how many more there are. The
full entries, with base counts, stay in qc_result.json's snapshots.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.reporting.reporter import CoAReporter, flagged_positions
from mrnaspect.snapshot_keys import CONSTRUCT_PREFIX, construct_ref

from .test_min_reads_per_construct import _text
from .test_print_prose import _print_html


def _site(pos: int, rate: float) -> dict:
    return {"pos": pos, "ref_base": "A", "depth": 100, "mismatch_rate": rate,
            "base_counts": {"A": round(100 * (1 - rate)), "C": round(100 * rate)}}


def _cons(sites: list[dict], measured: int = 1000) -> dict:
    return {"mismatch_sites": sites, "mismatch_site_count": len(sites),
            "mismatch_threshold": 0.5, "min_depth_threshold": 50,
            "positions_above_min_depth": measured}


# --- the list -------------------------------------------------------------------------------

def test_positions_are_one_based_from_the_highest_mismatch_down() -> None:
    f = flagged_positions(_cons([_site(9, 0.6), _site(1200, 0.95), _site(30, 0.7)]))
    assert f["count"] == 3 and f["positions"] == [1201, 31, 10]


def test_a_tie_goes_to_the_lower_position() -> None:
    f = flagged_positions(_cons([_site(5, 0.7), _site(3, 0.6), _site(1, 0.6)]))
    assert f["positions"] == [6, 2, 4]


def test_at_most_50_are_listed_the_highest_mismatch_ones() -> None:
    sites = [_site(i, 0.5 + i / 1000) for i in range(300)]
    f = flagged_positions(_cons(sites))
    assert f["count"] == 300
    assert f["positions"] == list(range(300, 250, -1))


def test_fifty_are_all_listed() -> None:
    sites = [_site(i, 0.5 + i / 1000) for i in range(50)]
    f = flagged_positions(_cons(sites))
    assert f["count"] == 50 and len(f["positions"]) == 50


def test_nothing_flagged_is_an_empty_list_and_no_consensus_is_none() -> None:
    f = flagged_positions(_cons([], measured=812))
    assert f["count"] == 0 and f["positions"] == [] and f["positions_measured"] == 812
    assert flagged_positions({"consensus_identity_pct": 99.0}) is None
    assert flagged_positions(None) is None


# --- the report, on reads that flag ---------------------------------------------------------

@pytest.fixture(scope="module")
def printed(tmp_path_factory, variant_bam, qk_ha_ref) -> tuple[str, Path, dict]:
    """The print HTML WeasyPrint is given, the PDF written from it, and each construct's
    flagged positions as the snapshot (and so qc_result.json) carries them."""
    out = tmp_path_factory.mktemp("flagged")
    reporter = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(reporter, reference=qk_ha_ref)
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    html_str = _print_html(reporter)
    sites = {construct_ref(k): v["consensus"]["mismatch_sites"]
             for k, v in reporter._snapshots.items() if k.startswith(CONSTRUCT_PREFIX)}
    return html_str, out / "report_run_end.pdf", sites


def _figure_blocks(html_str: str) -> dict[str, str]:
    """Each construct's printed figure block, by name."""
    out = {}
    for part in html_str.split('<div class="construct-figures">')[1:]:
        name = re.search(r'<div class="construct-figures-title">\s*([^ <]+)', part).group(1)
        out[name] = part
    return out


def _ranked(sites: list[dict]) -> list[int]:
    return [s["pos"] + 1 for s in sorted(sites, key=lambda s: (-s["mismatch_rate"], s["pos"]))]


def test_the_fixture_flags_on_both_constructs(printed) -> None:
    """Otherwise the list would go unexercised on real reads."""
    assert all(printed[2].values()), {k: len(v) for k, v in printed[2].items()}


def test_the_list_prints_right_under_the_per_position_figure(printed) -> None:
    """2026-11-06: under each construct's per-position figure, not in its results
    table."""
    html_str, _, sites = printed
    blocks = _figure_blocks(html_str)
    assert set(blocks) == set(sites)
    for name, block in blocks.items():
        figure = block.index('alt="Per-position error')
        listed = block.index('<div class="flagged-title">')
        after = block.find('<div class="printed-figure">', figure + 1)
        assert figure < listed and (after == -1 or listed < after), name
    results = html_str[html_str.index("Per-construct results"):
                       html_str.index('<div class="construct-figures">')]
    assert "flagged-title" not in results


def test_each_construct_lists_its_flags_by_mismatch_at_most_50(printed) -> None:
    html_str, _, every = printed
    capped = 0
    for name, block in _figure_blocks(html_str).items():
        sites = every[name]
        text = html.unescape(_text(block))
        listed = re.search(r'<p class="flagged-list">([^<]*)</p>', block).group(1)
        if len(sites) > 50:
            capped += 1
            assert (f"Flagged positions: {len(sites):,} at mismatch ≥ 0.5, depth ≥ 50; "
                    "the 50 with the highest mismatch, highest first") in text, name
            assert [int(p) for p in listed.split(", ")] == _ranked(sites)[:50], name
            assert (f"The other {len(sites) - 50:,}, and every flagged position's base "
                    "counts, are in qc_result.json.") in text, name
        else:
            assert (f"Flagged positions: {len(sites):,} at mismatch ≥ 0.5, depth ≥ 50, "
                    "highest mismatch first") in text, name
            assert [int(p) for p in listed.split(", ")] == _ranked(sites), name
            assert "The other" not in text, name
    # QK-09 flags 137 and HA-WT 1, so both branches run on real reads.
    assert capped == 1


def test_the_pdf_lists_them(printed) -> None:
    """The PDF is the archived artifact, so it is read, not inferred from the HTML."""
    import pypdf  # not importorskip: without the reader this must fail, not skip
    _, pdf, sites = printed
    text = re.sub(r"\s+", " ", " ".join(
        p.extract_text() or "" for p in pypdf.PdfReader(str(pdf)).pages))
    for name, s in sites.items():
        assert f"Flagged positions: {len(s):,} at mismatch" in text, name
        head = ", ".join(str(p) for p in _ranked(s)[:3])
        assert head in text, (name, head)
        if len(s) > 50:
            assert f"The other {len(s) - 50:,}" in text, name
