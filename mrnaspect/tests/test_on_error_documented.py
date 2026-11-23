"""The documents name only the `on_error` values the driver accepts.

The configuration reference told the reader to set `on_error: abort`; the driver takes
`skip` or `raise` and refused `abort` before the run started (exit 2). A document that
names a value is checked against the one tuple the driver validates with.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mrnaspect.main import ON_ERROR_VALUES, OfflinePipeline

_REPO = Path(__file__).resolve().parents[2]
_DOCS = [_REPO / "docs" / "config_reference.md", _REPO / "README.md",
         _REPO / "mrnaspect" / "config" / "run_config.yaml"]


def _named_values(text: str) -> set[str]:
    """Every word written straight after ``on_error:``, in prose or in YAML."""
    return set(re.findall(r"on_error:\s*`?([A-Za-z_]+)", text))


@pytest.mark.parametrize("doc", _DOCS, ids=lambda p: p.name)
def test_documents_name_only_accepted_values(doc: Path) -> None:
    named = _named_values(doc.read_text(encoding="utf-8"))
    assert named <= set(ON_ERROR_VALUES), (
        f"{doc.name} names on_error value(s) the driver refuses: "
        f"{sorted(named - set(ON_ERROR_VALUES))}")


def test_reference_documents_both_values() -> None:
    text = (_REPO / "docs" / "config_reference.md").read_text(encoding="utf-8")
    assert _named_values(text) == set(ON_ERROR_VALUES)


def test_the_parser_sees_a_wrong_value() -> None:
    """The check above must be able to fail: the old sentence's value is found."""
    assert _named_values("set `on_error: abort` to stop") == {"abort"}


def test_driver_refuses_any_other_value(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="on_error must be 'skip' or 'raise'"):
        OfflinePipeline(tmp_path, tmp_path / "out", {"mode": "bam", "on_error": "abort"})
