"""What the user-facing documents say about the product, held to what the product is.

The content audit of 2026-10-28 found the README calling the product QCRNA after it became
mRNAspect, calling the report a certificate of analysis while the report says it is not one,
promising a modified-U metric Core never computes, and an architecture diagram still drawing
the U-mask modes removed on 2026-10-23. The code had moved; the prose had not, and nothing
looked at the prose.
"""

from __future__ import annotations

import re
from pathlib import Path

from .conftest import NOT_PUBLISHED

ROOT = Path(__file__).resolve().parents[2]
# The documents a user reads: the published ones. A file kept out of the published tree is
# a working record of how the product got here, and quotes old names on purpose.
USER_DOCS = [ROOT / "README.md", ROOT / "gui" / "README.md",
             *sorted(p for p in (ROOT / "docs").glob("*.md") if p not in NOT_PUBLISHED)]


def test_the_documents_are_where_this_test_expects() -> None:
    assert (ROOT / "README.md").exists() and len(USER_DOCS) >= 4, USER_DOCS


def test_the_documents_name_the_product_mrnaspect() -> None:
    """QCRNA survives only as an identifier prefix, e.g. $QCRNA_PYTHON."""
    # Keyed by path, not name: two of these files are README.md, and keyed by name the
    # second silently replaced the first's findings (caught by mutation, 2026-10-28).
    found = {str(p.relative_to(ROOT)): re.findall(r".{0,30}\bQCRNA\b(?!_).{0,30}",
                                                  p.read_text())
             for p in USER_DOCS}
    found = {k: v for k, v in found.items() if v}
    assert not found, f"the product is called QCRNA in: {found}"


def test_the_report_is_never_called_a_certificate_of_analysis() -> None:
    """It says itself that it is not one: no acceptance criteria are set."""
    for p in USER_DOCS:
        text = " ".join(p.read_text().split())
        for m in re.finditer(r"certificate of analysis", text, re.I):
            assert re.search(r"\bnot\b.{0,20}$", text[max(0, m.start() - 25):m.start()]), (
                f"{p.name} calls the output a certificate of analysis: "
                f"...{text[max(0, m.start() - 60):m.end()]}")


def test_the_readme_promises_no_metric_core_does_not_compute() -> None:
    readme = (ROOT / "README.md").read_text()
    table = readme[readme.index("## What it measures"):]
    table = table[:table.index("\n## ", 5)]
    rows = re.findall(r"^\| \d+ \| \*\*(.+?)\*\*", table, re.M)
    # Named as the app and the report name them (2026-11-16, test_report_words.py).
    assert rows == ["Per-base identity", "Poly(A) tail length", "Full length (5' reach)"], rows
    # Full length is reach of the 5' end, not "spanning 5'->3'": a read can start at the
    # 5' end and stop short of the 3' one (terminal_reach).
    assert "spanning" not in table and "starts within 50 nt of the reference's 5' end" in table
    integrator = (ROOT / "mrnaspect" / "analysis" / "modules" / "qc_integrator.py").read_text()
    assert "modified_u" not in integrator


def test_no_document_names_the_old_package() -> None:
    """`import qcrna` stopped working on 2026-10-22 and nothing answers to it. The README
    still taught it -- `python -m qcrna.main`, fixtures under `qcrna/tests/data/` -- and so
    did the GUI contract and the packaging notes, 17 places in all, found preparing the lab
    share: a lab member following them would have stopped at the first command. The
    product-name test above looked at the name, not at the commands."""
    pattern = re.compile(r"\bimport qcrna\b|-m qcrna\b|\bqcrna\.\w|(?<![\w/.-])qcrna/")
    found = {str(p.relative_to(ROOT)): pattern.findall(p.read_text()) for p in USER_DOCS}
    found = {k: v for k, v in found.items() if v}
    assert not found, found


def test_every_module_a_document_runs_exists() -> None:
    """`python -m X` in a document is a command a reader will type."""
    import importlib.util

    missing: dict[str, list[str]] = {}
    for p in USER_DOCS:
        for module in sorted(set(re.findall(r"\bpython3? (?:-I )?-m ([\w.]+)", p.read_text()))):
            if importlib.util.find_spec(module) is None:
                missing.setdefault(str(p.relative_to(ROOT)), []).append(module)
    assert not missing, missing


def test_every_package_path_a_document_names_exists() -> None:
    missing: dict[str, list[str]] = {}
    for p in USER_DOCS:
        for path in sorted(set(re.findall(r"`(mrnaspect/[\w/.@-]+)`", p.read_text()))):
            if not (ROOT / path.rstrip("/")).exists():
                missing.setdefault(str(p.relative_to(ROOT)), []).append(path)
    assert not missing, missing


def test_the_u_mask_is_described_only_as_removed() -> None:
    """Removed 2026-10-23 with consensus_mode; a diagram or comment still drawing its modes
    reads as current behaviour."""
    arch = (ROOT / "docs" / "architecture.md").read_text()
    assert "U masked" not in arch and "m1psi_ft" not in arch
    consensus = (ROOT / "mrnaspect" / "analysis" / "modules" / "consensus.py").read_text()
    assert "U-masked mode" not in consensus and "m1psi_ft" not in consensus
    assert "the mode's U-mask" not in consensus


# --- held since the audit before the GitLab share of 2026-11-14 ------------------------------
#
# The same finding as 2026-10-28, eighteen days of GUI work later: the window had moved
# (Settings › Display, a Modification step, a smaller headline size) and the documents a
# lab member opens first had not. Each check below reads the name from the code and looks
# for it in the prose, so the prose cannot fall behind silently again.

def _main_ts() -> str:
    return (ROOT / "gui" / "src" / "main.ts").read_text()


def _step_names() -> list[str]:
    block = re.search(r"const STEP_NAMES: Record<StepId, string> = \{(.*?)\};", _main_ts(), re.S)
    return re.findall(r'\w+: "([^"]+)"', block.group(1))


def test_the_gui_readme_describes_the_window_as_it_is() -> None:
    from mrnaspect.reporting import figspec

    text = (ROOT / "gui" / "README.md").read_text()
    ladder = " / ".join(str(v) for v in sorted(figspec.GUI_TYPE_PX.values())) + " px"
    assert ladder in text, f"the type ladder is {ladder}"
    for name in _step_names():
        assert name in text, f"the start flow has a {name!r} step"
    pages = re.search(r"const SETTINGS_PAGES: Page\[\] = \[([^\]]*)\];", _main_ts()).group(1)
    for page in re.findall(r'"(\w+)"', pages):
        assert page.capitalize() in text, f"Settings has a {page!r} page"
    for gone in ("Appearance", "Uridine", "Core repo"):
        assert gone not in text, f"{gone!r} is no longer in the window"


def test_the_licence_is_stated_where_tools_read_it() -> None:
    """LICENSE says MIT (test_gui_help_about.py); a package index and cargo read fields."""
    assert (ROOT / "LICENSE").read_text().startswith("MIT License")
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert re.search(r'^license = "MIT"$', pyproject, re.M)
    # This project's own licence file first; any other entry is the notices of software
    # the package carries (the plotly bundle's), never a second licence for this code.
    files = re.search(r'^license-files = \[(.*)\]$', pyproject, re.M)
    assert files, "pyproject.toml names no licence files"
    listed = re.findall(r'"([^"]+)"', files.group(1))
    assert listed[0] == "LICENSE", listed
    assert all(name.endswith(".LICENSES.txt") for name in listed[1:]), listed
    cargo = (ROOT / "gui" / "src-tauri" / "Cargo.toml").read_text()
    assert re.search(r'^license = "MIT"$', cargo, re.M)


def test_the_development_guide_says_what_is_settled() -> None:
    text = (ROOT / "docs" / "DEVELOPMENT.md").read_text()
    status = text[text.index("## Status"):text.index("## Environment")]
    assert "the licence," not in status, "the licence is MIT since 2026-11-13"
    assert "preserves the environment's symlinks" not in status, "answered on 2026-11-14: it does not"
    build = text[text.index("## Building the desktop app"):text.index("## Layout")]
    for needed in ("npm ci", "Rust", "Node", "Xcode", "--bundles app"):
        assert needed in build, needed
