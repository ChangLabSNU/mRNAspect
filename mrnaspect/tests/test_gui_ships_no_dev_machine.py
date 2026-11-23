"""The shipped GUI must not carry the machine it was written on.

This is not tidiness. Until 2026-10-21 the four path fields opened pre-filled with
paths under the developer's home directory, and the Reference one defaulted to
`curlcake_ref.fasta` -- undoing, at the only surface an end user touches, the guarantee
Core is built around: there is deliberately NO default reference, because a valid FASTA of
the WRONG construct maps 0.00% and reads exactly like a dead flow cell (measured
0/19,992 on IVT_260407). Core refuses to guess; the GUI was guessing for it.

The failure is silent in both directions. A pre-filled dev path still *works* on the
machine that wrote it, and a wrong reference still produces a report -- one that says the
flow cell was dead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .conftest import LAB_PRIVATE

_GUI = Path(__file__).resolve().parents[2] / "gui"
_HTML = _GUI / "index.html"
_MAIN = _GUI / "src" / "main.ts"
_CARGO = _GUI / "src-tauri" / "Cargo.toml"

pytestmark = pytest.mark.skipif(
    not _HTML.exists(), reason="GUI sources are not in this checkout")

#: Roots that only exist on one machine: a developer's home, and the developing lab's
#: own mounts, named in an untracked file (conftest LAB_PRIVATE), not in a tracked one.
_PRIVATE_ROOTS = ("/Users/", "/home/", *LAB_PRIVATE.get("roots", ()))


def _sources() -> list[Path]:
    return [p for p in (_HTML, _MAIN) if p.exists()]


def test_no_private_filesystem_root_is_baked_into_the_gui():
    offenders: list[str] = []
    for path in _sources():
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for root in _PRIVATE_ROOTS:
                if root in line:
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "the GUI ships a path from one machine:\n  " + "\n  ".join(offenders))


def test_no_text_input_opens_pre_filled():
    """`placeholder`, never `value`.

    A `value` on a path field is a default the operator has to notice and clear; a
    placeholder is a hint that cannot be submitted by accident. `<option value=...>` is
    untouched -- that is a choice, not a path.
    """
    html = _HTML.read_text()
    prefilled = re.findall(r'<input\b[^>]*\bvalue\s*=\s*"([^"]*)"[^>]*>', html)
    assert not prefilled, f"input fields open with a value: {prefilled}"


def test_the_reference_field_has_no_default_at_all():
    """The one field where a default is not merely untidy but wrong."""
    html = _HTML.read_text()
    m = re.search(r'<input\b[^>]*\bid="ref"[^>]*>', html)
    assert m, "the reference input is gone"
    tag = m.group(0)
    assert "value=" not in tag, f"the reference field carries a default: {tag}"
    assert "placeholder=" in tag, f"the reference field has no hint either: {tag}"


def test_the_rust_crate_is_not_still_the_scaffold():
    """`tauri init` writes these; they travel in the binary."""
    if not _CARGO.exists():
        pytest.skip("no Cargo.toml in this checkout")
    cargo = _CARGO.read_text()
    for leftover in ('description = "A Tauri App"', 'authors = ["you"]'):
        assert leftover not in cargo, f"Cargo.toml still carries the scaffold's {leftover!r}"


def test_the_app_s_identifier_is_the_lab_s_domain_reversed_not_a_person():
    """macOS files the app's settings under this name and binds a signature to it, so it
    is read by every system the app is installed on. It carried the developer's first
    name until 2026-11-16, then for a day a domain guessed from the LICENSE. It is the
    domain the copyright holder's servers answer at, reversed, then the product's name:
    the form exists so that two publishers' apps cannot collide. Changing it makes a
    different application of a signed one, so a change here is a decision, not a tidy-up."""
    import json

    conf = _GUI / "src-tauri" / "tauri.conf.json"
    if not conf.exists():
        pytest.skip("no tauri.conf.json in this checkout")
    assert json.loads(conf.read_text())["identifier"] == "io.qbio.mrnaspect"


def test_the_crate_s_author_is_the_copyright_holder_not_a_person():
    """Cargo hands `authors` to the build (CARGO_PKG_AUTHORS), so it travels in the app.
    It carried the developer's name and address until 2026-11-17; it now carries the name
    on the LICENSE and no address."""
    if not _CARGO.exists():
        pytest.skip("no Cargo.toml in this checkout")
    authors = re.search(r"^authors\s*=\s*\[(.*)\]\s*$", _CARGO.read_text(), re.M)
    assert authors, "Cargo.toml has no one-line `authors`"
    holder = re.search(r"^Copyright \(c\) \d{4} (.+)$", (_GUI.parent / "LICENSE").read_text(),
                       re.M)
    assert holder, "the LICENSE names no copyright holder"
    assert authors.group(1).strip() == f'"{holder.group(1)}"', authors.group(1)
