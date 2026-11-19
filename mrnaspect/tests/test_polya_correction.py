"""What survives the scalar correction's retirement, and what must stay gone until v2 lands.

The scalar ruler correction (`pt_corrected = pt * dorado_speed/c`) was retired
2026-10-23: the v2 correction is a different formula, per-construct and
per-arm, and the two could not both be live without a run's correction depending on which
config route reached it.

This file used to pin that correction's behaviour in 19 tests. Most of them described a
thing that no longer exists. What is left is the part that was never about the scalar:

  * **raw `pt` always ships**, and with no correction available it is all that ships;
  * **the model is read off the RG tag on every run** and its dorado constants adopted
    from the shipped table — v2 needs both, and the mixed-model refusal is still correct;
  * **the retired config surface is gone, not merely ignored.** A config carrying
    `calibration:` or `speed_correction:` must not be silently accepted into a run that
    then reports an uncorrected tail as if nothing was asked for.

The last of those is the one worth having. Retiring a feature by making its knobs
inert is how a knob comes back three months later attached to nothing.
"""

from __future__ import annotations

import pysam
import pytest

from mrnaspect.analysis.modules.polya import (
    DEFAULTS,
    DORADO_POLYA_CONSTANTS,
    PolyAModule,
)

MODEL = "rna004_130bps_hac@v5.3.0"
OTHER_MODEL = "rna004_130bps_sup@v5.3.0"
RG = f"0741d988-979b-4fd1-abf8-d040f567a442_{MODEL}"

_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "ref", "LN": 100}]}
)


def _read(pt=None, *, rg=RG, name="r"):
    """A minimal primary read carrying an optional pt tag and a read group."""
    r = pysam.AlignedSegment(_HEADER)
    r.query_name = name
    r.flag = 0
    r.reference_id = 0
    r.reference_start = 0
    r.query_sequence = "ACGT"
    r.cigartuples = [(0, 4)]
    if pt is not None:
        r.set_tag("pt", int(pt), "i")
    if rg is not None:
        r.set_tag("RG", rg, "Z")
    return r


def _run(module, pts, **kw):
    for i, pt in enumerate(pts):
        module.update(_read(pt, name=f"r{i}", **kw))
    return module.snapshot()


# --- the retired surface is GONE, not inert ---------------------------------

#: Every config key the scalar path accepted. None may survive as an ignored no-op.
RETIRED_KEYS = ("calibration", "speed_correction", "calibration_model")


@pytest.mark.parametrize("key", RETIRED_KEYS)
def test_retired_config_keys_are_not_silently_accepted(key):
    """A config asking for the retired correction must not look like it worked.

    `resolve_params` raises on a key that is not in DEFAULTS, which is what makes this
    loud. If someone re-adds the key to DEFAULTS as a None placeholder "for
    compatibility", this test fails and that is the point: the run would then accept
    `speed_correction: 0.82`, apply nothing, and report a raw tail with no sign that a
    correction had been requested.
    """
    with pytest.raises((ValueError, KeyError, TypeError)):
        PolyAModule({key: 0.82})


@pytest.mark.parametrize("key", RETIRED_KEYS)
def test_retired_keys_are_absent_from_defaults(key):
    assert key not in DEFAULTS, (
        f"{key!r} is back in polya DEFAULTS. The scalar correction was retired "
        f"2026-10-23; if v2 needs a knob it gets its own name, because reusing this one "
        f"would make an old config silently mean something new.")


def test_the_correction_block_does_not_advertise_a_ruler_it_did_not_use():
    """Keys describing the retired calibration must not be reported as None.

    `applies_to: "m1psi_only"` was the worst of them: a property of a ruler, stamped on
    every run including the ones that used no ruler at all.
    """
    snap = _run(PolyAModule(), [90, 95, 100])
    corr = snap["correction"]
    for dead in ("speed_correction", "calibration_key", "calibration_model",
                 "calibration_overlap", "overlap_verified", "applies_to"):
        assert dead not in corr, (
            f"correction block still reports {dead!r}, which describes the retired scalar "
            f"calibration. Absent is the honest shape until v2 defines its own.")


# --- what the run reports while no correction exists ------------------------

def test_raw_pt_ships_whatever_the_correction_does():
    """Raw always ships; the corrected pair sits beside it, never instead of it."""
    snap = _run(PolyAModule(), [90, 95, 100])
    assert snap["polya_length"]["mean"] == pytest.approx(95.0)
    assert snap["correction"]["applied"] is False


def test_an_unapplied_correction_always_says_why():
    """A silent "no correction" and a stated one are different artifacts on a CoA."""
    snap = _run(PolyAModule(), [90])
    assert snap["correction"]["status"] != "applied"
    assert snap["correction"]["reason"], (
        "an unapplied correction must say what is missing, not report an empty reason")


def test_short_tail_is_judged_on_the_raw_ruler_and_says_so():
    snap = _run(PolyAModule({"target_length": 100, "short_fraction": 0.8}), [90, 95, 100])
    assert snap["short_tail"]["judged_on"] == "raw_pt"


# --- the model IS still read, because v2 needs it ---------------------------

def test_the_run_model_and_its_dorado_constants_are_reported():
    """Observed facts about the run, not claims about a correction.

    v2's formula is `(pt + offset) * F - offset - E_nt`, and that `offset` is dorado's
    own `offset_correction` for the run's model — 10.88 on hac@v5.3.0, which is exactly
    what polya_constants.json carries as `shared.offset_correction`. So this table and
    this lookup are load-bearing for v2, not leftovers from the scalar correction.
    """
    snap = _run(PolyAModule(), [90])
    corr = snap["correction"]
    assert corr["run_model"] == MODEL
    assert corr["dorado_speed_correction"] == DORADO_POLYA_CONSTANTS[MODEL]["speed_correction"]
    assert corr["dorado_offset_correction"] == DORADO_POLYA_CONSTANTS[MODEL]["offset_correction"]
    assert corr["dorado_offset_correction"] == 10.88


def test_missing_read_group_leaves_the_model_unknown_rather_than_guessed():
    snap = _run(PolyAModule(), [90], rg=None)
    corr = snap["correction"]
    assert corr["run_model"] is None
    assert corr["dorado_offset_correction"] is None


def test_unknown_model_withholds_the_dorado_constants():
    """A model absent from the table gets no constants — never another model's.

    `rna004_130bps_sup@v5.1.0` is the real case: it emits pt/pa but carries no [poly_a]
    block, so dorado falls back to a default its config does not disclose.
    """
    rg = f"0741d988-979b-4fd1-abf8-d040f567a442_rna004_130bps_sup@v5.1.0"
    snap = _run(PolyAModule(), [90], rg=rg)
    corr = snap["correction"]
    assert corr["run_model"] == "rna004_130bps_sup@v5.1.0"
    assert corr["dorado_speed_correction"] is None
    assert corr["dorado_offset_correction"] is None


def test_mixed_models_in_one_run_raises():
    """Still correct under v2: the constants are per-model in both designs."""
    m = PolyAModule()
    m.update(_read(90, name="a"))
    other_rg = f"0741d988-979b-4fd1-abf8-d040f567a442_{OTHER_MODEL}"
    with pytest.raises(ValueError, match="two basecaller models"):
        m.update(_read(90, name="b", rg=other_rg))


def test_missing_pt_counts_once_and_does_not_reach_the_length_stats():
    m = PolyAModule()
    m.update(_read(90, name="a"))
    m.update(_read(None, name="b"))
    snap = m.snapshot()
    assert snap["missing_pt"] == 1
    assert snap["n_with_pt"] == 1
    assert snap["polya_length"]["n"] == 1
