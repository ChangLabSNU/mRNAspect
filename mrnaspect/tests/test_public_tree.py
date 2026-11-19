"""What is published carries nothing that only the developing lab could know (2026-11-16).

The published tree is an export of this repository (scripts/make_public_tree.sh): every
tracked file except those ``.gitattributes`` marks ``export-ignore``. The rules are in
scripts/check_public_tree.py, which the export runs on what it made; this file runs the
same rules on the checkout, with the unpublished files left out of the scan.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from .conftest import LAB_PRIVATE, NOT_PUBLISHED

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "check_public_tree.py"


def _rules():
    spec = importlib.util.spec_from_file_location("check_public_tree", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rules():
    return _rules()


@pytest.fixture(scope="module")
def found(rules) -> dict:
    return rules.check(_REPO, not_published=NOT_PUBLISHED,
                       lab_words=LAB_PRIVATE.get("words") or ())


def test_no_comment_or_document_says_who_asked(found) -> None:
    assert not found["who"], "\n".join(found["who"][:20])


def test_no_published_file_names_an_unpublished_one(found) -> None:
    if found["unpublished"] is None:
        pytest.skip("this tree has no unpublished files: it is the published one")
    assert not found["unpublished"], "\n".join(found["unpublished"][:20])


def test_nothing_points_into_the_private_analysis_repository(found) -> None:
    assert not found["research"], "\n".join(found["research"][:20])


def test_no_published_file_names_a_lab_machine_mount_account_or_program(found) -> None:
    if found["lab"] is None:
        pytest.skip("lab_private.json is not here: only the lab can name what to look for")
    assert not found["lab"], "\n".join(found["lab"][:20])


def test_a_fixture_bam_s_header_names_files_not_directories(found) -> None:
    assert not found["bam"], "\n".join(found["bam"])


def test_no_published_file_carries_an_e_mail_address(found) -> None:
    """An address names a person. The project is reached through its repository."""
    assert not found["mail"], "\n".join(found["mail"][:20])


def test_the_scan_covers_the_tree_and_leaves_the_unpublished_out(rules) -> None:
    files = set(rules.published_files(_REPO, NOT_PUBLISHED))
    assert len(files) > 100
    assert _REPO / "README.md" in files and _REPO / "gui" / "src" / "main.ts" in files
    assert not files & set(NOT_PUBLISHED)
    assert any(p.suffix == ".bam" for p in files), "the fixture BAMs are published"


def test_the_patterns_catch_what_was_removed(rules) -> None:
    """The marks as they were written, so a rule that stops matching is seen."""
    role = "user"
    for said in (f"({role}, 2026-11-13)", f"({role}, 2026-11-13: one bar)", "(PI, 2026-11-12).",
                 f"(option A, {role} 2026-11-12)", f"decided 2026-11-02 ({role})",
                 f"since 2026-11-13 ({role}: beside the run", f"(the {role}'s, 11-06)",
                 "User, 2026-11-09: the views", "User decision 2026-10-15: they"):
        assert rules.MARK.search(said), said
    for kept in ("the user's FASTA", "a first-time user", "user_abort", "(user time, and",
                 "shipped YAML -> user --config -> CLI"):
        assert not rules.MARK.search(kept), kept
    for said in ("see the research repo", "in findings.md", "(F8a)", "F21 was retired",
                 "F21's cell", "analysis 20's population", "docs/specs/x.md",
                 "ported from phase1/step4", "every module in analyses/ and finetuning/",
                 "`workflows/config.yaml`"):
        assert rules.RESEARCH.search(said), said
    for kept in ("#F5F5F5", 'key="F1"', "an analysis of 20 reads", "the changelog.md",
                 ".github/workflows/ci.yml"):
        assert not rules.RESEARCH.search(kept), kept
    assert rules.ABSOLUTE.search("samtools sort -o /data/run/primary.bam -")
    assert not rules.ABSOLUTE.search("minimap2 -ax map-ont -k7 ref.fa -")
    # written in pieces, so this file carries no address itself
    address = "jane.doe" + "@" + "example.org"
    for said in (f'authors = ["Jane Doe <{address}>"]', f"mail {address}.", f"({address})"):
        assert rules.MAIL.search(said), said
    for kept in ('"icons/128x128@2x.png",', "npm install plotly.js@2.35.2", "typescript@latest",
                 "git" + "@" + "github.com:org/repo.git", '"@tauri-apps/api": "^2"'):
        assert not rules.MAIL.search(kept), kept


def test_a_pointer_is_found_by_name_and_by_its_name_as_words(rules) -> None:
    pattern = rules.unpublished_pattern(["docs/working_record.md", ".hidden", "NOTES.md"])
    assert pattern.search("see working_record.md") and pattern.search("the Working Record says")
    assert pattern.search("in NOTES.md") and not pattern.search("some notes")
    assert rules.unpublished_pattern([".hidden"]) is None
