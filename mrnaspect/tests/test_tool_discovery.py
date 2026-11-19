"""`resolve_tool` must name the problem where the setting is read.

The ladder exists because the wrappers used to hardcode one machine's absolute paths. Two
gaps in it surfaced on 2026-09-23, both while pointing a Mac at a freshly downloaded ONT
bundle — the first time anyone had configured these tools from outside this lab:

* **An existing path is not a runnable program.** The bundle unpacks to a DIRECTORY whose
  name is the obvious thing to point at, and `DORADO_SERVER=/…/ont-dorado-server` passed
  an `exists()` check, then failed much later as an exec error far from the setting that
  caused it.
* **Only the config key fed the sibling search.** `dorado_server.py` accepts the server
  from `basecall.server.binary` OR `$DORADO_SERVER` and says so, but only the config key
  reached the client's "beside the server" rung — so exporting the env var produced a
  ladder that never tried it and did not list it among the places looked.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mrnaspect.wrappers._shell import resolve_tool


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """An ONT-shaped bundle: a directory with the binaries under bin/."""
    b = tmp_path / "ont-dorado-server" / "bin"
    b.mkdir(parents=True)
    for n in ("dorado_basecall_server", "ont_basecall_client"):
        f = b / n
        f.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        f.chmod(0o755)
    return b.parent


def test_a_directory_is_refused_and_says_where_the_binary_lives(bundle: Path) -> None:
    with pytest.raises(FileNotFoundError) as exc:
        resolve_tool("dorado_basecall_server", str(bundle))
    msg = str(exc.value)
    assert "directory, not a program" in msg
    # The refusal has to carry the fix, not just the diagnosis: this is the exact mistake
    # a first-time user makes, and "bin/" is the whole answer.
    assert "bin/" in msg, msg


def test_a_non_executable_file_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "dorado_basecall_server"
    f.write_text("not a program", encoding="utf-8")
    f.chmod(0o644)
    with pytest.raises(FileNotFoundError, match="not executable"):
        resolve_tool("dorado_basecall_server", str(f))


def test_the_same_rules_apply_to_the_environment_variable(bundle: Path, monkeypatch) -> None:
    """An env var is an explicit setting, so it is refused rather than fallen back from."""
    monkeypatch.setenv("QCRNA_PROBE_TOOL", str(bundle))
    with pytest.raises(FileNotFoundError) as exc:
        resolve_tool("ont_basecall_client", env_var="QCRNA_PROBE_TOOL")
    assert "directory, not a program" in str(exc.value)


def test_the_client_is_found_beside_the_server(bundle: Path) -> None:
    server = bundle / "bin" / "dorado_basecall_server"
    found = resolve_tool("ont_basecall_client", sibling_of=str(server))
    assert Path(found) == bundle / "bin" / "ont_basecall_client"


def test_an_executable_is_accepted(bundle: Path) -> None:
    server = bundle / "bin" / "dorado_basecall_server"
    assert resolve_tool("dorado_basecall_server", str(server)) == str(server)


def test_the_failure_names_every_rung_it_tried(tmp_path: Path, monkeypatch) -> None:
    """A user cannot fix a lookup whose places are not named — which rung to set depends
    entirely on how they installed."""
    monkeypatch.delenv("QCRNA_PROBE_TOOL", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(FileNotFoundError) as exc:
        resolve_tool("definitely_not_a_real_tool", env_var="QCRNA_PROBE_TOOL",
                     hint="the hint must survive too.")
    msg = str(exc.value)
    assert "$QCRNA_PROBE_TOOL (unset)" in msg
    assert "bin/definitely_not_a_real_tool" in msg      # sys.prefix rung
    assert "PATH" in msg
    assert "the hint must survive too." in msg


# --- the two ways a path setting points at the wrong thing ------------------

def test_a_bundle_directory_is_searched_inside_not_beside(bundle: Path) -> None:
    """`sibling_of` was the one rung with no directory guard.

    The bundle directory is the obvious thing to point at — `_usable` says so and the
    GUI's field warns against it — but a warning is not a guard. Taking `.parent` of a
    directory searched one level ABOVE the bundle (wherever it was unpacked) and then
    reported "beside <bundle>", which reads as though it had looked inside.
    """
    found = resolve_tool("ont_basecall_client", sibling_of=str(bundle))
    assert Path(found) == bundle / "bin" / "ont_basecall_client"


def test_the_failure_names_the_paths_it_tried_not_the_relationship(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    with pytest.raises(FileNotFoundError) as exc:
        resolve_tool("ont_basecall_client", sibling_of=str(empty))
    msg = str(exc.value)
    # A path can be pasted into `ls`; "beside <x>" cannot be checked by its reader.
    assert str(empty / "ont_basecall_client") in msg, msg
    assert str(empty / "bin" / "ont_basecall_client") in msg, msg


def test_the_client_configured_as_the_server_is_refused(bundle: Path) -> None:
    """`_usable` cannot catch this: the client is an existing, executable, non-directory
    file. Without the check it starts, rejects the server's flags and dies with a message
    naming neither the setting nor the swap."""
    client = bundle / "bin" / "ont_basecall_client"
    with pytest.raises(FileNotFoundError) as exc:
        resolve_tool("dorado_basecall_server", str(client))
    msg = str(exc.value)
    assert "that is ont_basecall_client, not dorado_basecall_server" in msg, msg
    assert "point this setting at dorado_basecall_server" in msg, msg


def test_the_swap_is_caught_through_the_environment_variable_too(
        bundle: Path, monkeypatch) -> None:
    monkeypatch.setenv("QCRNA_PROBE_TOOL", str(bundle / "bin" / "dorado_basecall_server"))
    with pytest.raises(FileNotFoundError) as exc:
        resolve_tool("ont_basecall_client", env_var="QCRNA_PROBE_TOOL")
    assert "not ont_basecall_client" in str(exc.value)


def test_a_version_suffixed_binary_is_still_accepted(bundle: Path) -> None:
    """The guard is narrow on purpose — a wrapper, a symlink or a versioned name must not
    be refused just because its basename is not exactly the tool name."""
    odd = bundle / "bin" / "dorado_basecall_server_7.13.6"
    odd.write_text("#!/bin/sh\n", encoding="utf-8")
    odd.chmod(0o755)
    assert resolve_tool("dorado_basecall_server", str(odd)) == str(odd)
