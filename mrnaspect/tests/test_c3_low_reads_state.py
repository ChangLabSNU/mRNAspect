"""C3 — "short so far" and "ended short" are different results.

Before this, both set ``low_reads=True`` and both rendered the same banner, whose wording
("the metrics below are preliminary") is transient-flavoured. On a *final* report that is
actively misleading: it reads as "wait for more data" when the correct action is "the run
is over and it failed — go investigate the flow cell".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mrnaspect.reporting.reporter import (
    CoAReporter,
    LOW_READS_ACCUMULATING,
    LOW_READS_FINAL,
)

# The renderer imports jinja2/weasyprint lazily; skip cleanly if the stack is absent.
render_stack = pytest.importorskip("weasyprint")


def _reporter(tmp_path: Path, mapped: int, min_reads: int) -> CoAReporter:
    rep = CoAReporter(tmp_path / "out", min_reads=min_reads)
    rep.update("run_summary", {"mapping": {"total": mapped, "mapped": mapped}})
    return rep


def test_interim_below_threshold_is_accumulating(tmp_path: Path) -> None:
    rep = _reporter(tmp_path, mapped=5, min_reads=100)
    result = rep.report("interim_1", force=True)
    assert result["low_reads"] is True
    assert result["low_reads_state"] == LOW_READS_ACCUMULATING

    html = Path(result["html_path"]).read_text()
    assert "still in progress" in html
    assert "will change as more reads arrive" in html


def test_final_below_threshold_is_finish_low_reads(tmp_path: Path) -> None:
    rep = _reporter(tmp_path, mapped=5, min_reads=100)
    result = rep.finalize("run_end", force=True)
    assert result["low_reads"] is True
    assert result["low_reads_state"] == LOW_READS_FINAL

    html = Path(result["html_path"]).read_text()
    assert "This run finished with" in html
    assert "final result" in html
    # The transient wording must NOT appear on a terminal report — that is the whole bug.
    assert "will change as more reads arrive" not in html


def test_sufficient_run_has_no_low_reads_state(tmp_path: Path) -> None:
    rep = _reporter(tmp_path, mapped=500, min_reads=100)
    for result in (rep.report("interim_1"), rep.finalize("run_end")):
        assert result["low_reads"] is False
        assert result["low_reads_state"] is None
        # The class name is always in the stylesheet; the rendered <div> is not.
        assert '<div class="insufficient-banner">' not in Path(
            result["html_path"]).read_text()


def test_unforced_warning_distinguishes_terminal_from_interim(tmp_path: Path) -> None:
    """The no-render path carries the state too, and says what to do about it."""
    rep = _reporter(tmp_path, mapped=5, min_reads=100)

    interim = rep.report("interim_1")
    assert interim["status"] == "insufficient"
    assert interim["low_reads_state"] == LOW_READS_ACCUMULATING
    assert "still in progress" in interim["message"]

    final = rep.finalize("run_end")
    assert final["status"] == "insufficient"
    assert final["low_reads_state"] == LOW_READS_FINAL
    assert "run is OVER" in final["message"]
    # An insufficient finalize must NOT freeze the reporter — the driver may re-call with
    # force=True after asking the operator. (Pre-existing contract; pinned here because
    # C3 touches this path.)
    assert rep.finalize("run_end", force=True)["status"] == "report"


def test_user_abort_below_threshold_is_also_terminal(tmp_path: Path) -> None:
    """An aborted run is over too — waiting for more reads is not an option."""
    rep = _reporter(tmp_path, mapped=5, min_reads=100)
    result = rep.finalize("user_abort", force=True)
    assert result["low_reads_state"] == LOW_READS_FINAL


def test_low_reads_state_reaches_qc_result_json(tmp_path: Path) -> None:
    """The field must survive into the reported artifact, not just the result dict."""
    import json

    from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator
    from mrnaspect.reporting import summary_writer as SW

    rep = _reporter(tmp_path, mapped=5, min_reads=100)
    result = rep.finalize("run_end", force=True)
    out = SW.write_result_json(
        tmp_path / "out", {"run_summary": {"mapping": {"total": 5, "mapped": 5}}},
        QCResultIntegrator(), result=result, inputs=[],
        run_accounting={}, timing={}, params={}, reason="run_end",
    )
    doc = json.loads(out.read_text())
    assert doc["report"]["low_reads_state"] == LOW_READS_FINAL
    assert doc["report"]["low_reads"] is True
