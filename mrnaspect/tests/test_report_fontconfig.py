"""The PDF's fonts are configured from the environment Python runs in, wherever that is.

fontconfig reads its configuration from a folder compiled into the library, and conda
writes that folder when the environment is installed. The app carries a COPY of the
environment, so its fontconfig named the build machine's folder: found there, and on any
other Mac missing, where fontconfig reports an error and goes on without the rules that
choose and substitute fonts (2026-11-18, read from the built app's `libfontconfig`).

`reporter._use_this_environments_fontconfig` sets `FONTCONFIG_PATH` to
`<sys.prefix>/etc/fonts` before WeasyPrint is imported. In an environment that was never
moved that is the folder the library already reads.
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

from mrnaspect.reporting import reporter


@pytest.fixture
def unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither variable set, and whatever the function sets is undone after the test.

    Set to "" rather than deleted: monkeypatch then restores the variable's absence, and
    a path into a temporary folder does not stay in the environment of later tests.
    """
    monkeypatch.setenv("FONTCONFIG_PATH", "")
    monkeypatch.setenv("FONTCONFIG_FILE", "")


def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                 configured: bool = True) -> Path:
    fonts = tmp_path / "etc" / "fonts"
    fonts.mkdir(parents=True)
    if configured:
        (fonts / "fonts.conf").write_text("<fontconfig></fontconfig>\n")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    return fonts


def test_fontconfig_is_pointed_at_this_environment(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unset: None) -> None:
    fonts = _environment(tmp_path, monkeypatch)
    assert reporter._use_this_environments_fontconfig() == str(fonts)
    assert os.environ["FONTCONFIG_PATH"] == str(fonts)


@pytest.mark.parametrize("variable", ["FONTCONFIG_PATH", "FONTCONFIG_FILE"])
def test_a_setting_the_operator_made_is_left_alone(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unset: None,
        variable: str) -> None:
    _environment(tmp_path, monkeypatch)
    monkeypatch.setenv(variable, "/somewhere/of/their/own")
    assert reporter._use_this_environments_fontconfig() is None
    assert os.environ[variable] == "/somewhere/of/their/own"
    other = ({"FONTCONFIG_PATH", "FONTCONFIG_FILE"} - {variable}).pop()
    assert os.environ[other] == ""


def test_an_environment_without_a_configuration_changes_nothing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unset: None) -> None:
    """A plain Python with a system fontconfig: its own default is the right one."""
    _environment(tmp_path, monkeypatch, configured=False)
    assert reporter._use_this_environments_fontconfig() is None
    assert os.environ["FONTCONFIG_PATH"] == ""


def test_it_is_set_before_weasyprint_is_imported() -> None:
    """Importing WeasyPrint loads fontconfig, which reads the variable when first used."""
    source = inspect.getsource(reporter.CoAReporter._emit_report)
    assert source.index("_use_this_environments_fontconfig()") \
        < source.index("from weasyprint import HTML")
