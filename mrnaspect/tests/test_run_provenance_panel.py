"""The report must say how it was produced, in both media.

A CoA whose numbers cannot be traced to the code and the invocation that made them is not
an audit record. Everything needed was already captured — `qc_result.json` has carried
the command, the commit, the inputs, the params and a hash of every loaded module since
the provenance helper was written — and the report rendered none of it. A reader with the
PDF in front of them had no way to tell which version of the software produced it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mrnaspect import PRODUCT_NAME
from mrnaspect.reporting.reporter import CoAReporter

pytest.importorskip("jinja2")
pytest.importorskip("matplotlib")


def _rows(reporter) -> dict[str, str]:
    return {r["name"]: r["value"] for r in reporter._run_provenance()}


def test_the_process_facts_are_read_not_passed(tmp_path: Path) -> None:
    """The command and the version are properties of THIS process.

    Passing them in would let a caller hand the report one invocation while
    `qc_result.json` recorded another, and nothing would notice.
    """
    rows = _rows(CoAReporter(tmp_path, min_reads=1))
    assert "Command" in rows and rows["Command"]
    assert f"{PRODUCT_NAME} version" in rows


def test_a_dirty_tree_is_stated_rather_than_dropped(tmp_path: Path, monkeypatch) -> None:
    """A commit alone would then describe code that is not what ran.

    That is worse than saying nothing, because it looks precise.
    """
    from mrnaspect import provenance

    monkeypatch.setattr(provenance, "git_commit", lambda short=False: "abc1234")
    monkeypatch.setattr(provenance, "git_is_dirty", lambda: True)
    assert "working tree modified" in _rows(CoAReporter(tmp_path, min_reads=1))["Source commit"]

    monkeypatch.setattr(provenance, "git_is_dirty", lambda: False)
    assert _rows(CoAReporter(tmp_path, min_reads=1))["Source commit"] == "abc1234"


def test_no_commit_means_no_row_rather_than_an_empty_one(tmp_path: Path,
                                                         monkeypatch) -> None:
    """Outside a checkout there is no commit. A row reading "—" would claim the question
    was asked and answered."""
    from mrnaspect import provenance

    monkeypatch.setattr(provenance, "git_commit", lambda short=False: None)
    assert "Source commit" not in _rows(CoAReporter(tmp_path, min_reads=1))


def test_what_only_the_driver_knows_is_passed_in(tmp_path: Path) -> None:
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_run_context(reference="ref.fa", mode="bam", basecall_model=None)
    rows = _rows(r)
    assert rows["Reference"] == "ref.fa"
    assert rows["Mode"] == "bam"
    assert "Basecall model" not in rows, (
        "a run that did not record something must say nothing, not invent a row")


def _with_observed_model(r: CoAReporter, as_written: str, resolved: str) -> None:
    r._snapshots[r.module_names["polya"]] = {
        "correction": {"run_model": as_written, "run_model_resolved": resolved}}


def test_the_basecall_model_is_the_one_the_reads_name_by_its_released_name(
        tmp_path: Path) -> None:
    """In bam mode the reads are the only witness. The BAM's own spelling -- here the
    training name -- is not shown: what ships uses one name (2026-10-28)."""
    r = CoAReporter(tmp_path, min_reads=1)
    # Rows AFTER Mode too: with Mode last, a row appended at the end would also land
    # "after Mode", and the placement check would pass for the wrong reason (it did).
    r.note_run_context(reference="ref.fa", mode="bam", inputs="1 file(s) from in/",
                       minimum_reads="1 mapped")
    _with_observed_model(r, "train8s_hac_5e-6_ep2_w2_ov2500", "m1psi_tuned_v1")
    rows = r._run_provenance()
    named = {row["name"]: row["value"] for row in rows}
    assert named["Basecall model"] == "m1psi_tuned_v1"
    assert not any("train8s" in row["value"] for row in rows)
    order = [row["name"] for row in rows]
    assert order.index("Basecall model") == order.index("Mode") + 1


def test_the_reads_outrank_the_config_s_folder(tmp_path: Path) -> None:
    """pod5 mode passes the configured folder; the reads say what actually ran."""
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_run_context(reference="ref.fa", mode="pod5",
                       basecall_model="/models/rna004_130bps_hac@v5.3.0")
    _with_observed_model(r, "qcrna_m1psi_v1_ov2500", "m1psi_tuned_v1")
    assert _rows(r)["Basecall model"] == "m1psi_tuned_v1"


def test_without_read_groups_the_config_s_folder_names_it(tmp_path: Path) -> None:
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_run_context(reference="ref.fa", mode="pod5",
                       basecall_model="/models/qcrna_m1psi_v1_ov2500")
    assert _rows(r)["Basecall model"] == "m1psi_tuned_v1"


def test_the_panel_reaches_BOTH_media(tmp_path: Path) -> None:
    """The PDF is the archived artifact and the HTML is the one people open. The
    question "which version produced this" is asked of both."""
    from jinja2 import Environment, FileSystemLoader

    from mrnaspect.reporting.reporter import _TEMPLATE_DIR, _TEMPLATE_NAME

    r = CoAReporter(tmp_path, min_reads=1)
    r.note_run_context(reference="ref.fa", mode="bam")
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    template = env.get_template(_TEMPLATE_NAME)
    ctx = {
        "report_title": "t", "test_date": "2026-10-15", "metrics": [], "constructs": [],
        "batch_id": "b", "signatory_title": "QC Reviewer",
        "sample": {"declared": [], "recorded": []},
        "insufficient_notice": None, "incomplete_notice": None, "sampled_run": False,
        "unassigned": None, "auto_refresh_seconds": None, "interactive": [],
        "interactive_json": "[]", "plotly_js": "", "printed": [],
        "print_sections": {"run_level": [], "run_level_bands": [], "constructs": []},
        "run_provenance": r._run_provenance(),
    }
    for for_print in (False, True):
        html = template.render(**ctx, for_print=for_print)
        assert "Provenance" in html, for_print
        assert "ref.fa" in html and "qc_result.json" in html, for_print


def test_the_rendered_report_carries_it_end_to_end(tmp_path: Path) -> None:
    r = CoAReporter(tmp_path, min_reads=1)
    r.note_run_context(reference="curlcake.fa", mode="bam")
    r.update("run_summary", {"mapping": {"total": 1, "mapped": 1, "unmapped": 0,
                                         "mapping_rate": 1.0},
                             "read_length": {"n": 1, "mean": 10.0}})
    r.finalize("run_end", force=True)
    html = (tmp_path / "report_run_end.html").read_text()
    assert "Provenance" in html
    assert "curlcake.fa" in html


def test_the_offline_driver_sets_it_before_the_render(tmp_path: Path, fixture_bam: Path,
                                                      curlcake_ref: Path) -> None:
    """`_finalize` IS the render; a context set afterwards would reach qc_result.json
    and miss the report entirely."""
    from mrnaspect.main import OfflinePipeline, load_config

    inp = tmp_path / "in"
    inp.mkdir()
    (inp / "a.bam").write_bytes(fixture_bam.read_bytes())
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "min_reads": 1})
    pipe = OfflinePipeline(inp, tmp_path / "out", cfg)
    pipe.run()
    html = (tmp_path / "out" / "report_run_end.html").read_text()
    assert "Provenance" in html
    assert curlcake_ref.name in html
    # The fixture's reads name sup@v5.1.0 in their RG; bam mode passes no model at all.
    assert "rna004_130bps_sup@v5.1.0" in html
    assert ">bam<" in html or "bam" in html
