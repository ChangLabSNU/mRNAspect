"""The run's results table and every construct's are ONE table, with one set of columns.

They were written twice in the template and the copies drifted: 25/30/17/13/15% against
32/40/28%, three columns against five. Every column boundary jumped between the run-level
table and the first construct's, and the Result column only nearly lined up, by the
coincidence 25+30+17 = 72 = 32+40. Raised twice before it was fixed.

The screen stylesheet had already met this class once, for table WIDTH ("same rules,
different placement, visibly different tables"). Both times the cause was the same: the
table existed twice. Now it exists once, as a macro, with its widths in <col> rules under
table-layout: fixed so a long sentence in one table cannot move its columns.

The sentence between the two tables was also false: "the run-level table above describes
<one construct> only", written when that was true and wrong since the run-level rows were
pooled on 2026-09-17.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mrnaspect.main import main as offline_main

_TEMPLATE = (Path(__file__).resolve().parents[1]
             / "reporting" / "templates" / "sample_coa.html")


def test_the_template_writes_the_results_table_exactly_once() -> None:
    """A second hand-written copy is how the two drifted; the macro is the only one."""
    src = _TEMPLATE.read_text()
    assert src.count('<table class="results">') == 1
    assert src.count("results_table(") == 3, "one definition and two uses expected"


def test_the_columns_are_fixed_and_add_up() -> None:
    src = _TEMPLATE.read_text()
    css = src[src.index("table.results {"): src.index("table.results th, table.results td")]
    assert "table-layout: fixed" in css
    # One width per column the macro declares, so a column added or dropped (two went on
    # 2026-11-09) cannot leave a width behind or go without one.
    cols = re.findall(r'<col class="(c-\w+)">', src)
    widths = dict(re.findall(r"col\.(c-\w+)\s*\{\s*width:\s*([\d.]+)%", css))
    assert sorted(widths) == sorted(cols), (cols, widths)
    assert abs(sum(float(w) for w in widths.values()) - 100) < 1e-9, widths


@pytest.fixture(scope="module")
def html(tmp_path_factory, variant_bam, qk_ha_ref) -> str:
    """A real two-construct report, through the CLI."""
    tmp = tmp_path_factory.mktemp("tables")
    src = tmp / "in"
    src.mkdir()
    (src / variant_bam.name).symlink_to(variant_bam)
    assert offline_main(["--input-dir", str(src), "--reference", str(qk_ha_ref),
                         "--output-dir", str(tmp / "out"), "--output-name", "r",
                         "--mode", "bam", "--min-reads", "1"]) == 0
    return (tmp / "out" / "r" / "report_run_end.html").read_text()


def _tables(html: str) -> list[tuple[str, tuple[str, ...]]]:
    """(colgroup, header texts) for every results table on the page."""
    out = []
    for tbl in re.findall(r'<table class="results">(.*?)</table>', html, re.S):
        cols = re.search(r"<colgroup>(.*?)</colgroup>", tbl, re.S)
        heads = [re.sub(r"<[^>]+>|\s+", " ", h).strip()
                 for h in re.findall(r"<th[^>]*>(.*?)</th>", tbl, re.S)]
        out.append((re.sub(r"\s+", "", cols.group(1)) if cols else "", tuple(heads)))
    return out


def test_every_results_table_on_the_page_has_the_same_columns(html) -> None:
    tables = _tables(html)
    assert len(tables) >= 3, "expected the run's table and one per construct"
    assert len(set(tables)) == 1, (
        f"{len(set(tables))} different column layouts on one page: {set(tables)}")
    # No criterion or status column while no acceptance criterion exists
    # (2026-11-09); test_report_scope pins where the read minimum went.
    assert tables[0][1] == ("Test", "Method / specification", "Result")


def test_the_note_between_them_claims_nothing_the_table_contradicts(html) -> None:
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    # The run's table is headed "Whole-run results" since 2026-11-16, and the sentence
    # that said so ("The table above describes the whole run...") is gone with the need.
    assert "Whole-run results" in visible and "describes the whole run" not in visible
    assert not re.search(r"table above describes \S+ only", visible), (
        "the per-construct note says the run-level table describes one construct; its "
        "rows have pooled every construct since 2026-09-17")
    assert "pooled over 2 constructs" in visible, (
        "the note says each row's method states what it pools -- so one must")
