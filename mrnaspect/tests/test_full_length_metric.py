"""QC #4 on the way out: the pooled envelope, and the CoA row that must not be a point.

The range is an ASSUMPTION envelope, not a confidence interval. Measured on
``IVT_260407_train4`` the two ends sit ~10 pp apart while each end's own sampling width is
0.04-0.05 pp, so rendering a single number would silently pick one of the two assumptions.
These tests pin that it stays a range WHEN THE TWO ASSUMPTIONS DIFFER, that both
denominators are named, and that the arithmetic has exactly one implementation
(``terminal_reach.pooled_envelope``) shared by ``qc_result.json`` and the CoA —
``reporting`` cannot import ``analysis``, which is how the two came to be able to drift in
the first place.

The qualifier is not a weakening. When every primary read reaches the module the two
assumptions coincide, and the row printed "69.3 – 69.3%" with 200 characters under it
explaining the two ends of a range that has one — a range claimed and not shown, which is
the same fault as a point claimed and not earned, pointing the other way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator
from mrnaspect.analysis.modules.terminal_reach import pooled_envelope
from mrnaspect.reporting.reporter import CoAReporter
from mrnaspect.snapshot_keys import construct_key


def _reach(hit: int, admitted: int, *, excluded: int = 0, thr: int = 50) -> dict:
    return {
        "full_length_count": hit,
        "n_admitted": admitted,
        # Rounded to 6 dp exactly as TerminalReachModule.snapshot does — a fake that
        # is more precise than the real snapshot would hide a rounding mismatch.
        "full_length_fraction": round(hit / admitted, 6) if admitted else None,
        "full_length_5p_max_short_nt": thr,
        "ref_length": 1200,
        "denominator": "reads_assigned_to_construct_admitted",
        "five_prime": {"counts": {"<=10": 0, "11-50": hit, "51-100": 0,
                                  ">100": admitted - hit},
                       "fractions": {}},
        "three_prime": {"counts": {}, "fractions": {}},
        "excluded": {"reverse_strand": 0, "split_mapped_sa": excluded,
                     "split_child_pi": 0, "no_alignment_end": 0},
        "reverse_strand_rate": 0.0,
    }


# --- the envelope arithmetic ----------------------------------------------

def test_lower_never_exceeds_upper() -> None:
    env = pooled_envelope([_reach(80, 100, excluded=5)], total_primary_reads=200)
    assert env["fraction_upper"] == 0.8
    assert env["fraction_lower"] == 0.4
    assert env["fraction_lower"] <= env["fraction_upper"]


def test_the_two_ends_use_different_named_denominators() -> None:
    """A range whose ends share a denominator would be a CI, which this is not."""
    env = pooled_envelope([_reach(80, 100)], total_primary_reads=200)
    assert env["denominator_upper"] == "reads_admitted_to_terminal_reach"
    assert env["denominator_lower"] == "all_primary_reads_in_the_run"
    assert env["bound_type"] == "assumption_envelope_not_confidence_interval"


def test_the_population_names_the_polya_capture_blind_spot() -> None:
    """DRS captures via poly-dT priming, so a molecule with no poly-A is never sequenced.
    Neither end of the range is a statement about molecules."""
    env = pooled_envelope([_reach(1, 1)], total_primary_reads=1)
    assert "poly-A" in env["population"] and "molecules" in env["population"]


def test_constructs_pool_by_summing_counts_not_averaging_fractions() -> None:
    """Averaging per-construct fractions would weight a 10-read construct like a 1M one."""
    env = pooled_envelope([_reach(90, 100), _reach(0, 900)], total_primary_reads=1000)
    assert env["full_length_count"] == 90 and env["n_admitted"] == 1000
    assert env["fraction_upper"] == 0.09          # not (0.9 + 0.0) / 2


def test_a_missing_total_yields_none_not_a_fabricated_bound() -> None:
    env = pooled_envelope([_reach(80, 100)], total_primary_reads=None)
    assert env["fraction_upper"] == 0.8
    assert env["fraction_lower"] is None


def test_no_admitted_reads_yields_none_not_zero() -> None:
    """A fabricated 0.0 would read as 'no read reached the terminus'."""
    env = pooled_envelope([_reach(0, 0)], total_primary_reads=500)
    assert env["fraction_upper"] is None


def test_a_disagreeing_threshold_across_constructs_is_not_summarised() -> None:
    """Edge case: two constructs measured at different thresholds are not comparable,
    so the pooled figure must not claim a single threshold."""
    env = pooled_envelope([_reach(8, 10, thr=50), _reach(8, 10, thr=100)],
                          total_primary_reads=20)
    assert env["five_prime_max_short_nt"] is None


# --- the integrator surface ----------------------------------------------

def _bundle(*reaches) -> dict:
    b = {"run_summary": {"mapping": {"total": 1000, "mapped": 900, "unmapped": 100}}}
    for i, r in enumerate(reaches):
        b[construct_key(f"C-{i}")] = {"n_reads": r["n_admitted"], "terminal_reach": r}
    return b


def test_integrator_reports_the_envelope_at_run_level() -> None:
    res = QCResultIntegrator().integrate(_bundle(_reach(800, 900)))
    fl = res["metrics"]["full_length"]
    assert fl["status"] == "measured"
    assert fl["full_length_fraction_upper"] == round(800 / 900, 6)
    assert fl["full_length_fraction_lower"] == 0.8
    assert fl["five_prime_max_short_nt"] == 50


def test_integrator_reports_it_per_construct_too() -> None:
    """Required per construct: at the locked threshold the constructs span 16.4 pp, so a
    pooled figure rides the mixing ratio and describes none of them."""
    res = QCResultIntegrator().integrate(_bundle(_reach(800, 900), _reach(10, 100)))
    per = res["constructs"]
    assert per["C-0"]["full_length"]["full_length_fraction"] == round(800 / 900, 6)
    assert per["C-1"]["full_length"]["full_length_fraction"] == 0.1
    assert per["C-0"]["full_length"]["denominator"] == (
        "reads_assigned_to_construct_admitted")


def test_integrator_says_not_measured_without_the_module() -> None:
    res = QCResultIntegrator().integrate(
        {"run_summary": {"mapping": {"total": 10, "mapped": 10}}})
    assert res["metrics"]["full_length"]["status"] == "not_measured"


# --- the CoA row ----------------------------------------------------------

def _reporter(tmp_path: Path, *reaches) -> CoAReporter:
    rep = CoAReporter(tmp_path, min_reads=1)
    rep.update("run_summary", {"mapping": {"total": 1000, "mapped": 900, "unmapped": 100}})
    for i, r in enumerate(reaches):
        rep.update(construct_key(f"C-{i}"), {"n_reads": r["n_admitted"],
                                             "terminal_reach": r})
    return rep


def _row(rep: CoAReporter) -> dict:
    rows = [r for r in rep._build_metrics(900) if r["name"] == "Full length (5' reach)"]
    assert len(rows) == 1, "QC #4 must appear exactly once"
    return rows[0]


def test_the_coa_renders_a_range_not_a_single_value(tmp_path: Path) -> None:
    row = _row(_reporter(tmp_path, _reach(800, 900)))
    assert "–" in row["value"], f"expected a range, got {row['value']!r}"
    assert row["value"] == "80.0%–88.9%"


def test_the_coa_prints_one_number_when_the_assumptions_coincide(tmp_path: Path) -> None:
    """Every primary read admitted means the two denominators are the same denominator.

    This printed "69.3 – 69.3%" on the archived fixture, which reads as a formatting
    fault, and explained an envelope underneath it at length. The reason the envelope is
    a point is itself the finding -- nothing excluded, nothing unmapped -- so the method
    text says that instead of going quiet.
    """
    row = _row(_reporter(tmp_path, _reach(800, 1000)))
    assert row["value"] == "80.0%", row["value"]
    assert "–" not in row["value"]
    assert "no range" in row["method"]
    assert "not a confidence interval" not in row["method"], (
        "there is no envelope here to describe the ends of")
    assert "1,000 primary reads" in row["method"], "the denominator is still named"


def test_a_printed_range_is_always_visible(tmp_path: Path) -> None:
    """A range narrower than the displayed precision is a range claimed and not shown.

    Either the ends are far enough apart to render, or the row prints one number. What it
    may not do is print two ends that are the same characters.
    """
    for lo, hi in ((0.80, 0.889), (0.69310, 0.69340), (0.693100, 0.693104),
                   (0.692771, 0.692771)):
        is_point, text = CoAReporter._envelope_text(lo, hi)
        if is_point:
            assert "–" not in text, text
        else:
            left, right = text.split("–")
            assert left != right, f"a range whose ends print the same: {text!r}"


def test_the_number_and_the_sentence_cannot_disagree(tmp_path: Path) -> None:
    """One predicate drives both. A row saying 80.0% under text describing two ends, or a
    range under text saying there is none, is worse than either being wrong alone."""
    for reach in (_reach(800, 900), _reach(800, 1000)):
        row = _row(_reporter(tmp_path, reach))
        says_range = "not a confidence interval" in row["method"]
        shows_range = "–" in row["value"]
        assert says_range == shows_range, (
            f"value {row['value']!r} against method {row['method'][:90]!r}")


def test_no_bound_is_not_a_point_and_not_a_range(tmp_path: Path) -> None:
    """`fraction_upper` is None when no read reached the module, and the method path was
    handing two Nones to a formatter that multiplies them by 100. The VALUE path had
    always guarded it; this is the third branch, and neither envelope sentence applies to
    it."""
    row = _row(_reporter(tmp_path, _reach(0, 0)))
    assert row["value"] == "Not measured", row["value"]
    assert "no bound to report" in row["method"]
    assert "not a confidence interval" not in row["method"] and "no range:" not in row["method"]


def test_the_coa_method_states_threshold_orientation_and_population(tmp_path: Path) -> None:
    method = _row(_reporter(tmp_path, _reach(800, 900)))["method"]
    assert "50 nt" in method                     # the locked threshold
    assert "3'→5'" in method                    # WHY the 5' end
    assert "not a confidence interval" in method                  # the ends are assumptions
    assert "poly(A)" in method                    # the capture blind spot
    assert "1,000 primary reads" in method       # the lower denominator, with its count


def test_the_coa_degrades_to_not_measured_without_the_module(tmp_path: Path) -> None:
    """A report must always render; a missing module is stated, never crashed on."""
    rep = CoAReporter(tmp_path, min_reads=1)
    rep.update("run_summary", {"mapping": {"total": 10, "mapped": 10}})
    assert _row(rep)["value"] == "Not measured"


def test_the_coa_range_equals_the_json_range(tmp_path: Path) -> None:
    """The whole point of sharing pooled_envelope: one arithmetic, two surfaces."""
    reach = _reach(800, 900, excluded=7)
    row = _row(_reporter(tmp_path, reach))
    fl = QCResultIntegrator().integrate(_bundle(reach))["metrics"]["full_length"]
    # Asked of the formatter, not rebuilt here: this line used to hardcode one decimal
    # place, which made it a second source for the display rule and would have gone
    # stale the moment the row needed two to show a narrow range.
    _, expected = CoAReporter._envelope_text(fl["full_length_fraction_lower"],
                                             fl["full_length_fraction_upper"])
    assert row["value"] == expected
