"""Which arm each construct is: fully m1Ψ-substituted (``m1psi``) or canonical (``U``).

The poly(A) correction depends on the arm and no BAM records it, so it is declared:
``sample.arm`` for the run, ``sample.arms`` per construct. An undeclared construct gets
no correction rather than a guessed one. Each ``arms`` entry names its arm instead of
meaning "the opposite of the default", so editing the default cannot invert it. See
``docs/config_reference.md``.
"""

from __future__ import annotations

import difflib
from typing import Any, Mapping, Optional

__all__ = ["ARMS", "ArmError", "normalise_arm", "resolve_arms"]

#: The arms the poly(A) constants are fitted for; a third would need its own constants.
ARMS = ("m1psi", "U")

#: Case-insensitive lookup; the canonical spelling is what is recorded and reported.
_CANONICAL = {a.lower(): a for a in ARMS}


class ArmError(ValueError):
    """A declaration that cannot be acted on. Raised at startup, never mid-run."""


def normalise_arm(value: Any, *, where: str) -> str:
    """One arm value, canonically spelled, or raise naming where it came from."""
    if not isinstance(value, str):
        raise ArmError(
            f"{where}: arm must be one of {list(ARMS)}, got {value!r} "
            f"({type(value).__name__}).")
    key = value.strip().lower()
    if key not in _CANONICAL:
        raise ArmError(
            f"{where}: arm must be one of {list(ARMS)}, got {value!r}. "
            f"'m1psi' means fully m1Ψ-substituted; 'U' means canonical. There is no value "
            f"for 'partially substituted' — the correction is not fitted for one.")
    return _CANONICAL[key]


def resolve_arms(
    sample_cfg: Optional[Mapping[str, Any]],
    contigs: Any,
) -> dict[str, str]:
    """Map every declared construct to its arm; undeclared constructs are absent.

    Args:
        sample_cfg: the config's ``sample`` block, or None. ``arm`` is the run-level
            default and ``arms`` the per-construct overrides; both optional.
        contigs: the reference's contig names.

    Returns:
        ``{construct: arm}``. Empty when nothing was declared, which the caller must
        treat as "withhold", not as a default.

    Raises:
        ArmError: an unknown arm value, an ``arms`` that is not a mapping, or an
            ``arms`` key that is not a contig in the reference (a typo would otherwise
            leave that construct silently on the default).
    """
    cfg = dict(sample_cfg or {})
    default = cfg.get("arm")
    overrides = cfg.get("arms") or {}

    if not isinstance(overrides, Mapping):
        raise ArmError(
            f"sample.arms must be a mapping of construct -> arm, e.g. "
            f"{{construct_B: U}}; got "
            f"{type(overrides).__name__}. A bare LIST of names is not accepted on purpose: "
            f"it would have to mean 'the opposite of sample.arm', which makes every entry "
            f"change meaning when the default is edited.")

    known = sorted(str(c) for c in contigs)
    resolved: dict[str, str] = {}

    if default is not None:
        resolved = {c: normalise_arm(default, where="sample.arm") for c in known}

    for name, value in overrides.items():
        name = str(name)
        if name not in set(known):
            hint = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
            suggestion = f" Did you mean {hint[0]!r}?" if hint else ""
            raise ArmError(
                f"sample.arms names {name!r}, which is not a contig in this reference."
                f"{suggestion} Known: {known if len(known) <= 12 else known[:12] + ['...']}. "
                f"An unmatched name is silent otherwise — the construct would keep the "
                f"default arm and its corrected tail would be wrong by several nt.")
        resolved[name] = normalise_arm(value, where=f"sample.arms[{name}]")

    return resolved
