"""The product calls itself by its name everywhere a person reads it.

Renamed 2026-10-22 (`qcrna` -> `mrnaspect`). The rename changed the import package and
was verified the way a package rename is verified: nothing imports `qcrna` any more. That
check was clean while the FINAL REPORT'S TITLE still read "QCRNA QC Report", and so did
five sentences of its body, the provenance row, both drivers' --help, and the abort notice
an operator reads while waiting for a run to stop. Found by running the app
end to end on 2026-10-28, not by any test.

A guard watching the spelling of the import rather than the name on the page. These
check the page.

Environment variables keep the QCRNA_ prefix on purpose: they are an interface people
have set, not a name people read, and renaming one silently breaks a working setup.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from mrnaspect import PRODUCT_NAME
from mrnaspect.reporting.reporter import CoAReporter

OLD = "QCRNA"
_TEMPLATE = (Path(__file__).resolve().parents[1]
             / "reporting" / "templates" / "sample_coa.html")


def _final_report(tmp_path: Path, monkeypatch) -> tuple[str, Path]:
    # The report's Command row is sys.argv, which here is the TEST RUNNER's command line
    # and carries the checkout path. Run from a directory whose path contains the old name
    # -- a checkout of the old name, or a scratch directory named after it -- and that row
    # printed it: the clean-clone check failed this way on 2026-10-28. These tests are
    # about the text the product writes, so the command is fixed to a neutral one.
    monkeypatch.setattr(sys, "argv", ["mrnaspect", "--mode", "bam"])
    rep = CoAReporter(tmp_path, min_reads=100)
    rep.update("run_summary", {"mapping": {"total": 5556, "mapped": 5000,
               "unmapped": 556, "mapping_rate": 0.9}})
    rep.update("polya", {"polya_distribution": {"median": 86.0, "Q1": 73.0, "Q3": 103.0}})
    res = rep.finalize("run_end")
    html = Path(res["html_path"]).read_text()
    return html, Path(res["pdf_path"])


def _visible(html: str) -> str:
    """What a reader sees: no scripts, no styles, no tags."""
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def test_the_title_is_the_product_s_name(tmp_path, monkeypatch) -> None:
    html, _ = _final_report(tmp_path, monkeypatch)
    title = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    assert title, "the report has no <h1>"
    assert title.group(1).strip().startswith(PRODUCT_NAME), title.group(1)


def test_the_old_name_appears_nowhere_a_reader_can_see_it(tmp_path, monkeypatch) -> None:
    html, _ = _final_report(tmp_path, monkeypatch)
    hits = [m.start() for m in re.finditer(OLD, _visible(html))]
    assert not hits, (
        f"{OLD!r} appears {len(hits)} time(s) in the rendered report. The product is "
        f"{PRODUCT_NAME}; read it from mrnaspect.PRODUCT_NAME rather than spelling it")


def test_nor_in_the_pdf(tmp_path, monkeypatch) -> None:
    """The PDF is the archived artifact, so it is checked on its own, not inferred."""
    _, pdf = _final_report(tmp_path, monkeypatch)
    pypdf = pytest.importorskip("pypdf")
    text = " ".join(page.extract_text() or "" for page in pypdf.PdfReader(str(pdf)).pages)
    assert OLD not in text
    assert PRODUCT_NAME in text


def test_the_template_spells_no_product_name_at_all() -> None:
    """It reads {{ product_name }}, so the next rename is one constant."""
    src = _TEMPLATE.read_text()
    assert OLD not in src
    assert PRODUCT_NAME not in src, (
        f"the template spells {PRODUCT_NAME!r} itself; use {{{{ product_name }}}}")


@pytest.mark.parametrize("driver", ["mrnaspect.main", "mrnaspect.realtime.main"])
def test_both_command_lines_introduce_themselves_by_name(driver) -> None:
    out = subprocess.run([sys.executable, "-m", driver, "--help"],
                         capture_output=True, text=True, check=True).stdout
    assert OLD not in out
    assert PRODUCT_NAME in out


def test_the_abort_notice_an_operator_waits_on_uses_the_name() -> None:
    from mrnaspect.realtime import main as rt
    src = Path(rt.__file__).read_text()
    body = src[src.index("Abort requested"):src.index("--resume-from-snapshot")]
    assert f"[{OLD}]" not in body
