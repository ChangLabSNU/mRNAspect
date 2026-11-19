"""The Constructs panel accounts for mapped reads on contigs the reference lacks.

Found 2026-11-04 on run1_v1: with a reference missing four of the BAM's contigs, their
9,520 mapped reads went to the unassigned bucket as off-reference, and the panel showed
neither them nor their count. The construct table listed eight constructs whose shares
summed to 100%, so the run looked complete, and a quarter of it had gone unremarked.

Since the redesign (2026-11-06) the table is on the Constructs panel and every share is
Core's: a ratio is a metric, so the shell no longer divides. Since 2026-11-16 the share is
of every read (`share_of_reads`, on each construct and on `unassigned`), as the figure
above the table draws it, so the table's last row is the figure's "(unassigned)" bar and
the off-reference reads are named in that row with their count.

Read from the source, as the other GUI tests are: there is no TypeScript compiler on the
machine the suite runs on.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
RUST = REPO / "gui" / "src-tauri" / "src" / "lib.rs"
TS = REPO / "gui" / "src" / "main.ts"
INTEGRATOR = REPO / "mrnaspect" / "analysis" / "modules" / "qc_integrator.py"


def _constructs_table() -> str:
    body = re.search(r"function renderConstructs\(p: any\) \{(.*?)\n\}\n", TS.read_text(), re.S)
    assert body, "renderConstructs not found in main.ts -- did it move or get renamed?"
    return body.group(1)


def test_the_rust_panel_passes_the_off_reference_count() -> None:
    assert re.search(r'"off_reference":\s*u\["off_reference"\]', RUST.read_text())


def test_the_overview_and_the_table_are_fed_the_unassigned_entry() -> None:
    """The share is read from qc_metrics.unassigned, which Rust forwards as `unassigned`."""
    assert '"share_of_reads": _share_of_reads(' in INTEGRATOR.read_text()
    rust = RUST.read_text()
    assert re.search(r'"unassigned":\s*\{', rust)
    # The row's share was never forwarded while it was `off_reference_share_of_mapped`,
    # so the page would have printed "Not measured" in it.
    assert '"share_of_reads": u["share_of_reads"]' in rust


def test_off_reference_reads_are_named_in_the_unassigned_row_with_core_s_share() -> None:
    body = _constructs_table()
    # Read once, defaulting to 0 when the snapshot predates the count.
    assert re.search(r"const off\b[^=]*= u\.off_reference \?\? 0;", body)
    # The unassigned reads are a row whenever there are any, and the row says how many
    # of them mapped off the reference.
    assert re.search(r"\(u\.n_reads \?\? 0\) > 0 \? `<tr", body)
    assert "off > 0 ? ` · ${num(off)} off reference`" in body
    assert "pct(u.share_of_reads)" in body
    # Every share is Core's: no division by a total the shell summed itself.
    assert "pct(c.share_of_reads)" in body
    assert not re.search(r"/ total\b", body)


def test_construct_names_are_escaped_before_they_reach_the_page() -> None:
    """They come from the user's FASTA and the panel is set through innerHTML."""
    body = _constructs_table()
    assert "<td>${esc(k)}</td>" in body
    assert "<td>${k}</td>" not in body
