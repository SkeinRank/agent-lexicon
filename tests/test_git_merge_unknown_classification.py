from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_lexicon import (
    GitDiffAddedLine,
    GitMergeReviewKind,
    Lexicon,
    Term,
    build_git_merge_terminology_report,
)
from agent_lexicon.cli import main


def test_git_merge_classifies_aliases_and_new_terms_by_default() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    report = build_git_merge_terminology_report(
        lexicon,
        (
            GitDiffAddedLine(path="src/auth.py", line_number=10, text="accessToken = rotate()"),
            GitDiffAddedLine(path="src/auth.py", line_number=11, text="authToken = rotate()"),
            GitDiffAddedLine(path="src/auth.py", line_number=12, text="credentialBlob = load()"),
            GitDiffAddedLine(path="src/auth.py", line_number=13, text="sessionKey = load()"),
            GitDiffAddedLine(path="src/auth.py", line_number=14, text="def issue_c():"),
        ),
    )

    assert report.known_occurrence_count == 1
    assert [item.surface for item in report.likely_aliases] == ["authToken"]
    assert report.likely_aliases[0].review_kind == GitMergeReviewKind.LIKELY_ALIAS
    assert {item.surface for item in report.likely_new_terms} == {"credentialBlob", "sessionKey"}
    assert all(item.review_kind == GitMergeReviewKind.LIKELY_NEW_TERM for item in report.likely_new_terms)
    assert report.unresolved_unknowns == ()
    assert report.needs_review_count == 3
    assert report.has_review_items is True


def test_git_merge_can_include_low_signal_unknowns_for_full_audit() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    lines = (
        GitDiffAddedLine(path="src/auth.py", line_number=10, text="def issue_c():"),
        GitDiffAddedLine(path="src/auth.py", line_number=11, text="tmp_value = build()"),
    )

    default_report = build_git_merge_terminology_report(lexicon, lines)
    full_report = build_git_merge_terminology_report(lexicon, lines, include_unresolved_unknowns=True)

    assert default_report.unknown_identifiers == ()
    assert {item.surface for item in full_report.unresolved_unknowns} == {"issue_c", "tmp_value"}
    assert all(item.review_kind == GitMergeReviewKind.UNRESOLVED_IDENTIFIER for item in full_report.unresolved_unknowns)
    assert full_report.needs_review_count == 0
    assert full_report.has_review_items is False


def test_git_merge_report_serializes_review_kind_groups() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    report = build_git_merge_terminology_report(
        lexicon,
        (
            GitDiffAddedLine(path="src/auth.py", line_number=10, text="authToken = rotate()"),
            GitDiffAddedLine(path="src/auth.py", line_number=11, text="quuxHandle = build()"),
        ),
    )

    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["likely_alias_count"] == 1
    assert payload["likely_new_term_count"] == 1
    assert payload["needs_review_count"] == 2
    assert payload["likely_aliases"][0]["review_kind"] == "likely_alias"
    assert payload["likely_new_terms"][0]["review_kind"] == "likely_new_term"
    assert payload["needs_review"][0]["review_kind"] in {"likely_alias", "likely_new_term"}


@pytest.mark.skipif(shutil.which("git") is None, reason="git executable is not available")
def test_cli_check_merge_surfaces_new_terms_without_full_unknown_noise(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "checkout", "-b", "main")
    _git(repo, "config", "user.email", "agent-lexicon@example.test")
    _git(repo, "config", "user.name", "Agent Lexicon")
    lexicon_dir = repo / "lexicon"
    lexicon_dir.mkdir()
    (lexicon_dir / "lexicon.yaml").write_text(
        """
        version: 1
        terms:
          - id: auth.access_token
            canonical: access token
        """,
        encoding="utf-8",
    )
    source_dir = repo / "src"
    source_dir.mkdir()
    source_path = source_dir / "auth.py"
    source_path.write_text("def stable():\n    return None\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "checkout", "-b", "feature")
    source_path.write_text(
        "def stable():\n"
        "    return None\n"
        "\n"
        "def issue_c():\n"
        "    authToken = rotate()\n"
        "    quuxHandle = build()\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature")

    exit_code = main(["check-merge", "--root", str(repo), "--base", "main", "--head", "HEAD"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Likely aliases:" in captured.out
    assert "New terminology candidates:" in captured.out
    assert "authToken" in captured.out
    assert "quuxHandle" in captured.out
    assert "issue_c" not in captured.out

    fail_code = main([
        "check-merge",
        "--root",
        str(repo),
        "--base",
        "main",
        "--head",
        "HEAD",
        "--fail-on-review",
    ])
    assert fail_code == 1


def _git(repo: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
