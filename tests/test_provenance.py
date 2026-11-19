"""Tests for src.provenance — the paper-grade result-stamping helper.

Run: pytest tests/test_provenance.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import subprocess
from datetime import datetime, timezone

from pathlib import Path

import pytest

from mrnaspect import provenance


# --------------------------------------------------------------------------- #
# capture_provenance
# --------------------------------------------------------------------------- #
def test_capture_provenance_has_required_keys():
    stamp = provenance.capture_provenance(seed=42)
    for key in ("generated_at", "command", "argv", "git_commit",
                "git_dirty", "code", "software_version", "seed"):
        assert key in stamp
    assert stamp["seed"] == 42
    # generated_at must be a parseable ISO-8601 timestamp.
    datetime.fromisoformat(stamp["generated_at"])


def test_capture_provenance_timestamp_is_injectable():
    fixed = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)
    stamp = provenance.capture_provenance(now=fixed)
    assert stamp["generated_at"] == "2026-06-24T12:00:00+00:00"


def test_capture_provenance_code_is_injectable():
    stamp = provenance.capture_provenance(code={"a.py": "deadbeef"})
    assert stamp["code"] == {"a.py": "deadbeef"}


# --------------------------------------------------------------------------- #
# git_blob_hash / capture_code_versions
# --------------------------------------------------------------------------- #
def test_blob_hash_matches_git_hash_object(tmp_path):
    """Our hashlib implementation must agree with git itself, or the recorded
    id would not be retrievable via `git cat-file blob`."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not available")
    f = tmp_path / "s.py"
    f.write_bytes(b"print('hi')\n")
    expected = subprocess.run(
        [git, "hash-object", str(f)], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert provenance.git_blob_hash(f) == expected


def test_blob_hash_is_stable_and_content_sensitive(tmp_path):
    f = tmp_path / "s.py"
    f.write_bytes(b"x = 1\n")
    first = provenance.git_blob_hash(f)
    assert provenance.git_blob_hash(f) == first  # same bytes -> same id
    f.write_bytes(b"x = 2\n")
    assert provenance.git_blob_hash(f) != first  # one byte -> different id


def test_blob_hash_of_empty_file_is_gits_empty_blob(tmp_path):
    """Edge case: zero-length input must not be special-cased away."""
    f = tmp_path / "empty.py"
    f.write_bytes(b"")
    assert provenance.git_blob_hash(f) == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def test_blob_hash_missing_file_returns_none(tmp_path):
    assert provenance.git_blob_hash(tmp_path / "nope.py") is None


def test_code_versions_include_this_repos_modules_only():
    code = provenance.capture_code_versions()
    # provenance.py is loaded by definition, and is inside the repo.
    assert "mrnaspect/provenance.py" in code
    assert code["mrnaspect/provenance.py"] == provenance.git_blob_hash(
        provenance.__file__
    )
    # Modules OUTSIDE the Core package must still be captured. This is the regression
    # guard for 2026-09-17: `_REPO_ROOT` was derived by counting parents on the assumption
    # that provenance.py sat directly in `src/`, so promoting the package to `mrnaspect/`
    # silently made the root `<repo>/src`. `capture_code_versions` skips anything
    # `relative_to` cannot express, so every module outside the package
    # would have vanished from the stamp with no error — a Tier-A record
    # covering less than it claims. THIS test file is itself outside the package, so its
    # presence is the cheapest possible check that the root is the repo, not the package.
    assert "tests/test_provenance.py" in code, (
        "a module outside the Core package is missing — is _REPO_ROOT the repo root?")
    # Nothing outside the repo, and no absolute paths, may leak in.
    for path in code:
        assert not os.path.isabs(path), path
        assert ".." not in path, path


def test_code_versions_exclude_stdlib_and_site_packages():
    """Regression: this project's pixi env lives at .pixi/envs/ INSIDE the repo,
    so a plain repo-containment test swept the whole stdlib into every stamp
    (63 entries, 2 of them ours). Only first-party code may appear."""
    code = provenance.capture_code_versions()
    for path in code:
        assert not path.startswith(".pixi"), path
        assert "site-packages" not in path, path
    # json/os/pathlib are certainly imported by now; none may be recorded.
    #
    # Matched on the BASENAME, not with endswith. A suffix match also catches
    # first-party files whose names merely end that way — `mrnaspect/tests/
    # test_figures_json.py` ends in "json.py" — so the guard failed on a path it was
    # supposed to allow. It only ever passed because the two files landed in different
    # test shards; adding one test file re-sharded them together and it fired.
    #
    # Worth noting the check was also weaker than it read: stdlib `json` is a PACKAGE, so
    # its file is `json/__init__.py` and `endswith("json.py")` would never have matched it
    # anyway. Both package and module forms are named here.
    names = {Path(p).name for p in code}
    parents = {Path(p).parent.name for p in code}
    for mod in ("json", "os", "pathlib", "subprocess"):
        assert f"{mod}.py" not in names, mod
        assert not (mod in parents and "__init__.py" in names), mod


def test_code_versions_are_sorted_for_stable_diffs():
    code = provenance.capture_code_versions()
    assert list(code) == sorted(code)


def test_capture_provenance_optional_blocks_absent_by_default():
    stamp = provenance.capture_provenance()
    assert "inputs" not in stamp
    assert "params" not in stamp
    assert "tools" not in stamp


def test_capture_provenance_records_params_and_tools():
    stamp = provenance.capture_provenance(
        params={"min_depth": 50, "window_span": 10},
        tools={"minimap2": "2.28"},
    )
    assert stamp["params"] == {"min_depth": 50, "window_span": 10}
    assert stamp["tools"] == {"minimap2": "2.28"}


def test_git_commit_is_hex_or_none():
    # Best-effort: a 40-char hex string inside the repo, None outside one.
    commit = provenance.git_commit()
    assert commit is None or re.fullmatch(r"[0-9a-f]{40}", commit)


# --------------------------------------------------------------------------- #
# input fingerprinting
# --------------------------------------------------------------------------- #
def test_inputs_record_size_for_existing_file(tmp_path):
    f = tmp_path / "reads.bam"
    f.write_bytes(b"0123456789")  # 10 bytes
    stamp = provenance.capture_provenance(inputs=[f])
    (info,) = stamp["inputs"]
    assert info["exists"] is True
    assert info["bytes"] == 10
    assert info["path"] == str(f)
    datetime.fromisoformat(info["mtime_utc"])


def test_inputs_record_missing_file(tmp_path):
    missing = tmp_path / "nope.bam"
    stamp = provenance.capture_provenance(inputs=[missing])
    (info,) = stamp["inputs"]
    assert info["exists"] is False
    assert "bytes" not in info


# --------------------------------------------------------------------------- #
# write_result_json
# --------------------------------------------------------------------------- #
def test_write_result_json_roundtrip_and_structure(tmp_path):
    out = tmp_path / "sub" / "decision.json"  # parent does not exist yet
    payload = {"ref": "QK-10", "pooled_error_rate": 0.0123}
    record = provenance.write_result_json(out, payload, seed=7)

    assert out.exists()
    text = out.read_text()
    assert text.endswith("\n")
    loaded = json.loads(text)

    # payload preserved at top level, provenance nested under one key.
    assert loaded["ref"] == "QK-10"
    assert loaded["pooled_error_rate"] == 0.0123
    assert loaded["provenance"]["seed"] == 7
    assert loaded == record


def test_write_result_json_is_atomic_no_tmp_left(tmp_path):
    out = tmp_path / "r.json"
    provenance.write_result_json(out, {"x": 1})
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_write_result_json_rejects_reserved_key_collision(tmp_path):
    out = tmp_path / "r.json"
    with pytest.raises(ValueError, match="reserved key"):
        provenance.write_result_json(out, {"provenance": "oops"})
    assert not out.exists()  # nothing written on the error path


def test_write_result_json_rejects_both_provenance_and_kwargs(tmp_path):
    out = tmp_path / "r.json"
    with pytest.raises(ValueError, match="either"):
        provenance.write_result_json(
            out, {"x": 1}, provenance={"generated_at": "x"}, seed=1
        )


def test_write_result_json_custom_key(tmp_path):
    out = tmp_path / "r.json"
    record = provenance.write_result_json(out, {"x": 1}, key="_meta", seed=3)
    assert record["_meta"]["seed"] == 3
    assert "provenance" not in record


# --------------------------------------------------------------------------- #
# sidecar for non-JSON results (TSV/plots)
# --------------------------------------------------------------------------- #
def test_sidecar_meta_path_naming():
    p = provenance.sidecar_meta_path("results/summary.tsv")
    assert p.name == "summary.tsv.meta.json"


def test_write_sidecar_meta_creates_file(tmp_path):
    tsv = tmp_path / "summary.tsv"
    tsv.write_text("sample\tvalue\n")
    meta = provenance.write_sidecar_meta(tsv, seed=11)
    assert meta.exists()
    loaded = json.loads(meta.read_text())
    assert loaded["result_file"] == str(tsv)
    assert loaded["provenance"]["seed"] == 11


# ---------------------------------------------------------------------------
# Multi-repository capture (2026-09-21, after Core was split out)
# ---------------------------------------------------------------------------
# Before the split Core and the analyses shared one checkout, so a single repo root
# covered both. After it, `capture_code_versions` — which skips anything `relative_to`
# cannot express, silently — dropped every module of the analysis checkout from the
# stamp. Measured at the time: a duty-cycle script's stamp went from 3 entries to 2,
# losing the very script that produced the number. These pin the fix.

def _repo(tmp_path, name: str, files: dict[str, str]):
    """A throwaway git repo with real commits, so commit/dirty are not mocked."""
    root = tmp_path / name
    root.mkdir(parents=True)
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True,
                       capture_output=True)
    return root


def _load(path, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_modules_from_two_repositories_are_both_captured(tmp_path, monkeypatch):
    a = _repo(tmp_path, "repo_a", {"pkg/alpha.py": "VALUE = 1\n"})
    b = _repo(tmp_path, "repo_b", {"scripts/beta.py": "VALUE = 2\n"})
    provenance._root_for.cache_clear()
    _load(a / "pkg" / "alpha.py", "_prov_alpha")
    _load(b / "scripts" / "beta.py", "_prov_beta")
    try:
        code = provenance.capture_code_versions()
        assert "pkg/alpha.py" in code, "the first repo's module vanished"
        assert "scripts/beta.py" in code, (
            "a module from a SECOND repository was dropped — this is the regression "
            "the split introduced, and it fails silently")
        assert code["pkg/alpha.py"] == provenance.git_blob_hash(a / "pkg" / "alpha.py")
    finally:
        for n in ("_prov_alpha", "_prov_beta"):
            sys.modules.pop(n, None)
        provenance._root_for.cache_clear()


def test_code_roots_names_every_repository_and_its_own_commit(tmp_path):
    a = _repo(tmp_path, "roots_a", {"pkg/one.py": "X = 1\n"})
    b = _repo(tmp_path, "roots_b", {"pkg/two.py": "X = 2\n"})
    provenance._root_for.cache_clear()
    _load(a / "pkg" / "one.py", "_prov_one")
    _load(b / "pkg" / "two.py", "_prov_two")
    try:
        roots = provenance.capture_code_roots()
        assert str(a) in roots and str(b) in roots, sorted(roots)
        # Real, distinct commits — the point is that one commit cannot describe both.
        ca, cb = roots[str(a)]["commit"], roots[str(b)]["commit"]
        assert ca and cb and ca != cb, (ca, cb)
        assert roots[str(a)]["dirty"] is False
    finally:
        for n in ("_prov_one", "_prov_two"):
            sys.modules.pop(n, None)
        provenance._root_for.cache_clear()


def test_the_same_relative_path_in_two_repos_does_not_overwrite(tmp_path):
    """Both repos have `conftest.py`. One key would claim a hash for a file that
    did not run, so colliding keys — and only those — are qualified by repo name."""
    a = _repo(tmp_path, "coll_a", {"conftest.py": "X = 'a'\n"})
    b = _repo(tmp_path, "coll_b", {"conftest.py": "X = 'b'\n"})
    provenance._root_for.cache_clear()
    _load(a / "conftest.py", "_prov_ca")
    _load(b / "conftest.py", "_prov_cb")
    try:
        code = provenance.capture_code_versions()
        assert "coll_a::conftest.py" in code and "coll_b::conftest.py" in code, sorted(
            k for k in code if "conftest" in k)
        assert code["coll_a::conftest.py"] != code["coll_b::conftest.py"], (
            "the two files have different content and must hash differently")
    finally:
        for n in ("_prov_ca", "_prov_cb"):
            sys.modules.pop(n, None)
        provenance._root_for.cache_clear()


def test_a_module_in_no_repository_at_all_is_ignored(tmp_path):
    """The shipped product has no .git; a stray script outside every repo is not ours."""
    stray = tmp_path / "loose"
    stray.mkdir()
    (stray / "orphan.py").write_text("X = 0\n", encoding="utf-8")
    provenance._root_for.cache_clear()
    _load(stray / "orphan.py", "_prov_orphan")
    try:
        assert "orphan.py" not in provenance.capture_code_versions()
    finally:
        sys.modules.pop("_prov_orphan", None)
        provenance._root_for.cache_clear()


def test_the_stamp_says_which_repo_its_git_commit_describes():
    """`git_commit` is Core's. Before the split it was also the analyses' commit, so
    the field changed meaning without changing shape; `git_repo` makes that legible."""
    stamp = provenance.capture_provenance()
    assert stamp["git_repo"] == str(provenance._REPO_ROOT)
    assert "code_roots" in stamp
    assert stamp["git_repo"] in stamp["code_roots"]


# --- git is asked only where there is a repository ---------------------------------

def _refuse(*_args, **_kwargs):
    raise AssertionError("git was started")


def test_git_is_not_started_outside_a_repository(tmp_path, monkeypatch):
    """Code inside the desktop app is in no repository. On a Mac without Apple's command
    line developer tools, `/usr/bin/git` is a stand-in that opens a system dialog offering
    to install them, and the report asks for the commit at every render."""
    provenance._root_for.cache_clear()
    monkeypatch.setattr(provenance.subprocess, "run", _refuse)
    installed = tmp_path / "env" / "lib" / "site-packages" / "mrnaspect"
    installed.mkdir(parents=True)
    assert provenance._git_at(installed, "rev-parse", "HEAD") is None
    monkeypatch.setattr(provenance, "_REPO_ANCHOR", installed)
    assert provenance.git_commit(short=True) is None
    assert provenance.git_is_dirty() is None


def test_git_is_still_asked_inside_a_repository(tmp_path, monkeypatch):
    """A `.git` in the folder or above it is what a repository is; a worktree's is a file."""
    provenance._root_for.cache_clear()
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["cwd"]))
        return subprocess.CompletedProcess(command, 0, "abc1234\n", "")

    monkeypatch.setattr(provenance.subprocess, "run", run)
    package = tmp_path / "checkout" / "mrnaspect"
    package.mkdir(parents=True)
    (tmp_path / "checkout" / ".git").write_text("gitdir: elsewhere\n")
    assert provenance._git_at(package, "rev-parse", "--short", "HEAD") == "abc1234"
    assert calls == [(["git", "rev-parse", "--short", "HEAD"], package)]
    provenance._root_for.cache_clear()

