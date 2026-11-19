"""Item 1 — the run-level number must be the RUN's, not the biggest construct's.

Before this, ``AnalysisEngine._primary_construct()`` filled the flat ``consensus`` /
``error_hotspot`` slots with whichever single construct had the most reads, and the CoA
rendered those under a run-level label. On the archived 4-curlcake fixture (166 primary
reads, 47/45/41/33) that was **47 reads standing in for 166**.

This file pins the pooling arithmetic. The wiring into the reporter/integrator is tested
separately — here the question is only whether the helper computes the right thing,
because it is what both consumers import (the single-implementation rule from
``terminal_reach.pooled_envelope``). The error profile's position-weighted pooling
(``pooled_error_rate``) went with qc_metrics' error_profile on 2026-11-04: identity's own
breakdown replaced it (test_identity_breakdown.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.analysis.modules.consensus import pooled_identity
from mrnaspect.reporting.reporter import CoAReporter
from mrnaspect.snapshot_keys import CONSTRUCT_PREFIX


def _cons(agree: int, depth: int) -> dict:
    """A consensus snapshot reduced to what pooling reads."""
    return {"identity_agreeing_depth": agree, "identity_scored_depth": depth,
            "consensus_identity_pct": round(agree / depth * 100, 4) if depth else None}


# -- pooled identity ---------------------------------------------------------------

def test_pooled_identity_sums_before_dividing() -> None:
    """(900+100)/(1000+200) — one division over summed ingredients, not a mean of ratios."""
    got = pooled_identity([_cons(900, 1000), _cons(100, 200)])
    assert got["agreeing_depth"] == 1000
    assert got["scored_depth"] == 1200
    assert got["identity_pct"] == pytest.approx(1000 / 1200 * 100, abs=1e-4)
    assert got["n_constructs"] == 2


def test_pooled_identity_is_not_the_mean_of_the_per_construct_percentages() -> None:
    """The distinction the definition turns on, made to bite.

    Per-construct: 90.0 % and 50.0 %. Their unweighted mean is 70.0 %. The pooled value is
    83.33 %, because the 90 % construct carries 5x the coverage. A mean of ratios would let
    a thin construct move the headline number as much as a deep one.
    """
    got = pooled_identity([_cons(900, 1000), _cons(100, 200)])
    assert got["identity_pct"] == pytest.approx(83.3333, abs=1e-3)
    assert got["identity_pct"] != pytest.approx(70.0, abs=1e-3)


def test_pooled_identity_differs_from_the_largest_construct() -> None:
    """The actual regression: the old run-level value was one construct's value."""
    biggest = _cons(4700, 5000)          # 94.0 %, most coverage
    others = [_cons(300, 1000), _cons(400, 1000)]
    got = pooled_identity([biggest, *others])
    assert biggest["consensus_identity_pct"] == pytest.approx(94.0)
    assert got["identity_pct"] == pytest.approx(5400 / 7000 * 100, abs=1e-4)  # 77.14 %
    assert got["identity_pct"] < biggest["consensus_identity_pct"] - 10


def test_pooled_identity_with_nothing_scorable_is_none_not_zero() -> None:
    """A6/M4: no data must not render as a measured 0 % identity."""
    assert pooled_identity([])["identity_pct"] is None
    assert pooled_identity([_cons(0, 0)])["identity_pct"] is None


def test_a_genuine_zero_percent_stays_measured() -> None:
    """0 % with coverage is a real (terrible) result and must stay distinguishable."""
    got = pooled_identity([_cons(0, 500)])
    assert got["identity_pct"] == 0.0
    assert got["scored_depth"] == 500


# -- against the real fixture ------------------------------------------------------

def test_real_snapshots_carry_the_pooling_ingredients(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """End to end: the ingredients are exposed, and pooling moves the number.

    Guards the failure the synthetic tests cannot see — a snapshot that stops emitting
    ``identity_agreeing_depth``/``identity_scored_depth`` would make every pooled value
    silently 0/0 -> None, i.e. "Not measured" on the CoA rather than a crash.
    """
    engine = AnalysisEngine(CoAReporter(tmp_path / "rep", min_reads=1),
                            reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()

    bundle = dict(engine.reporter._snapshots)
    tracks = [v for k, v in bundle.items() if k.startswith(CONSTRUCT_PREFIX)]
    assert len(tracks) > 1, "fixture is supposed to hold several constructs"

    cons = [t["consensus"] for t in tracks]
    for c in cons:
        assert c["identity_scored_depth"] > 0
        # The exposed pair must reproduce the construct's own published percentage.
        assert c["consensus_identity_pct"] == pytest.approx(
            c["identity_agreeing_depth"] / c["identity_scored_depth"] * 100, abs=1e-4)

    pooled = pooled_identity(cons)
    assert pooled["n_constructs"] == len(tracks)
    assert pooled["scored_depth"] == sum(c["identity_scored_depth"] for c in cons)
    # The flat slot is the largest construct; pooling must consider strictly more coverage.
    flat = bundle["consensus"]
    assert pooled["scored_depth"] > flat["identity_scored_depth"]



# -- item 1b: the two consumers must agree, and the figure must not overclaim -------

def _engine_over_fixture(tmp_path: Path, fixture_bam: Path, curlcake_ref: Path):
    """The curlcake fixture never reaches the error profile's default depth (50), so it
    is reported from depth 10 here, which the per-position figure below needs."""
    from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator

    reporter = CoAReporter(tmp_path / "rep", min_reads=1)
    engine = AnalysisEngine(reporter, reference=curlcake_ref,
                            module_params={"error_hotspot": {"min_depth": 10}})
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    return reporter, dict(reporter._snapshots), QCResultIntegrator()


def test_coa_table_and_qc_result_report_the_same_pooled_numbers(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The single-implementation rule, actually asserted.

    Both consumers import the SAME helper from the measuring module precisely so the CoA
    and ``qc_result.json`` cannot disagree — but nothing checked it. A future edit that
    pooled in one place and not the other would ship a certificate whose headline number is
    absent from, or contradicted by, its own machine-readable record.
    """
    reporter, bundle, integrator = _engine_over_fixture(
        tmp_path, fixture_bam, curlcake_ref)

    mapped = bundle["run_summary"]["mapping"]["mapped"]
    rows = {r["name"]: r for r in reporter._build_metrics(mapped)}
    metrics = integrator.integrate(bundle)["metrics"]

    # Compared as the FORMATTED string, not with a tolerance. The table rounds for a human
    # (79.9857 -> "80.0%"), so a numeric comparison needs slack — and slack is exactly what
    # would hide a genuine divergence. Formatting the JSON value through the reporter's own
    # `_pct` and demanding string equality asserts the real guarantee: the certificate shows
    # the number its machine-readable record holds, with no tolerance at all.
    json_identity = metrics["sequence_identity"]["consensus_identity_pct"]
    assert json_identity is not None
    assert rows["Per-base identity"]["value"] == CoAReporter._pct(
        json_identity, already_pct=True)


def test_the_run_level_numbers_are_not_the_largest_constructs(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The regression item 1 exists to close: run level must not be one construct's value."""
    _, bundle, integrator = _engine_over_fixture(tmp_path, fixture_bam, curlcake_ref)
    metrics = integrator.integrate(bundle)["metrics"]

    flat_cons = bundle["consensus"]
    ident = metrics["sequence_identity"]

    # Pooled over strictly more evidence than the flat slot carried.
    assert ident["scored_depth"] > flat_cons["identity_scored_depth"]
    # And the pooled value is re-derivable from the pair reported beside it.
    assert ident["consensus_identity_pct"] == pytest.approx(
        ident["agreeing_depth"] / ident["scored_depth"] * 100, abs=1e-4)
    # `reference` must be None: a pooled value is not about one reference, and naming one
    # is the misattribution this change removes.
    assert ident["reference"] is None
    assert ident["scope"] == "pooled_over_4_constructs"


def test_the_identity_breakdown_is_pooled_as_a_whole_not_a_mixture(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Every part pools, or the breakdown can contradict itself.

    A pooled identity beside a primary-construct substitution share could sum past 100%.
    So each part must differ from the flat slot's, and the pooled counts must add up.
    """
    _, bundle, integrator = _engine_over_fixture(tmp_path, fixture_bam, curlcake_ref)
    ident = integrator.integrate(bundle)["metrics"]["sequence_identity"]
    flat = bundle["consensus"]

    for key in ("substitution_pct", "deletion_pct", "insertions_per_100_bases"):
        assert ident[key] != pytest.approx(flat[key], abs=1e-9), f"{key} was not pooled"
    assert (ident["agreeing_depth"] + ident["substitution_depth"]
            + ident["deletion_depth"]) == ident["scored_depth"]


def test_the_run_level_figures_are_over_the_whole_run_not_one_construct(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """Backlog item 3, asserted on the rendered report.

    The figure this replaces was a per-position error rate: those cannot be pooled, since
    position 500 of one construct is not position 500 of another, so it showed ONE
    construct under a run-level heading and the interim fix was a label that said so.
    Read length and poly(A) length are run-level quantities — every read has a length
    regardless of which construct it mapped to, or whether it mapped at all — so the
    figures can now carry the denominator they claim.

    Checked on the HTML rather than on the context dict: the claim is about what the
    report says, and the context is an implementation detail of how it gets there.
    """
    reporter, bundle, _ = _engine_over_fixture(tmp_path, fixture_bam, curlcake_ref)
    reporter.finalize("run_end", force=True)
    html = next(iter(sorted((tmp_path / "rep").glob("*.html")))).read_text(encoding="utf-8")

    run_total = bundle["run_summary"]["mapping"]["total"]
    primary_n = reporter._construct_snapshots()[0][1]["n_reads"]
    assert primary_n != run_total, "fixture must have >1 construct or this proves nothing"

    # Asserted on the SPECS rather than on a static section, because that is what the
    # report now draws from. The claim is unchanged: a figure headed "whole run" has to
    # be over the whole run.
    figures = {f["id"]: f for f in reporter._interactive_figures()}
    assert "read-length" in figures and "polya" in figures

    whole_run = figures["read-length"]["spec"]["data"][0]
    drawn_n = sum(whole_run["y"])
    assert drawn_n >= run_total, (
        f"the whole-run read length figure is over {drawn_n} reads, the run has "
        f"{run_total} — a pooled figure showing one construct is the defect this "
        f"replaced, and it looked entirely reasonable")
    assert drawn_n != primary_n, "it is drawing one construct's reads"
    assert "whole run" in figures["read-length"]["title"]
    assert not figures["read-length"]["spec"]["layout"].get("updatemenus"), (
        "a whole-run figure has no construct selector — if it needs one it is not "
        "a whole-run figure")

    # And the per-construct view is not lost; it moved to its own figure, where a
    # reference coordinate and a per-construct denominator both mean something.
    assert "read-length-by-construct" in figures
    assert figures["read-length-by-construct"]["spec"]["layout"]["updatemenus"]
    assert "Per-position error" in {f["title"] for f in figures.values()}


def test_the_identity_method_text_states_the_pooled_denominator(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """A denominator a reader cannot see is a denominator that gets ignored."""
    reporter, bundle, _ = _engine_over_fixture(tmp_path, fixture_bam, curlcake_ref)
    rows = {r["name"]: r for r in
            reporter._build_metrics(bundle["run_summary"]["mapping"]["mapped"])}
    assert "pooled over 4 constructs" in rows["Per-base identity"]["method"]


def test_the_identity_method_names_its_denominator() -> None:
    """The same sentence otherwise describes one construct and four."""
    text = CoAReporter._identity_method(
        pooled_identity([_cons(1, 2), _cons(1, 2)]))
    assert "2 constructs" in text
    assert "every position scored" in text


def test_each_construct_s_identity_method_states_its_own_denominator(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """A construct's row is about that construct, not "no construct tracked"."""
    reporter, _, _ = _engine_over_fixture(tmp_path, fixture_bam, curlcake_ref)
    sections = reporter._build_constructs()
    assert sections
    for section in sections:
        method = {r["name"]: r for r in section["rows"]}["Per-base identity"]["method"]
        assert "no construct tracked" not in method, section["name"]
        assert "base observations" in method and "pooled" not in method, method
