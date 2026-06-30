from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_lexicon import (
    GitDiffAddedLine,
    Lexicon,
    Term,
    build_git_merge_terminology_report,
    parse_git_added_lines,
)
from agent_lexicon.cli import main


def test_parse_git_added_lines_tracks_new_line_numbers() -> None:
    diff_text = """diff --git a/src/auth.py b/src/auth.py
index 0000000..1111111 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,0 +2,2 @@
+accessToken = rotate()
+authToken = rotate()
@@ -5 +8,1 @@
-old
+sessionKey = rotate()
"""

    lines = parse_git_added_lines(diff_text, include_globs=("src/**",))

    assert [(line.path, line.line_number, line.text) for line in lines] == [
        ("src/auth.py", 2, "accessToken = rotate()"),
        ("src/auth.py", 3, "authToken = rotate()"),
        ("src/auth.py", 8, "sessionKey = rotate()"),
    ]


def test_git_merge_report_separates_known_terms_from_near_miss_review() -> None:
    lexicon = Lexicon(
        terms=(
            Term(id="auth.access_token", canonical="access token", scopes=("auth",)),
        )
    )
    lines = (
        GitDiffAddedLine(path="src/auth.py", line_number=10, text="accessToken = rotate()"),
        GitDiffAddedLine(path="src/auth.py", line_number=11, text="authToken = rotate()"),
    )

    report = build_git_merge_terminology_report(
        lexicon,
        lines,
        scopes=("auth",),
        min_confidence=0.42,
    )

    assert report.added_line_count == 2
    assert report.known_occurrence_count == 1
    assert report.known_occurrences[0].term_id == "auth.access_token"
    assert report.needs_review_count == 1
    assert report.needs_review[0].surface == "authToken"
    assert report.needs_review[0].suggestions[0].target_term_id == "auth.access_token"
    assert report.unresolved_unknown_count == 0


def test_git_merge_report_serializes_stable_payload() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    report = build_git_merge_terminology_report(
        lexicon,
        (GitDiffAddedLine(path="src/auth.py", line_number=4, text="authToken = rotate()"),),
    )

    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["needs_review_count"] == 1
    assert payload["needs_review"][0]["surface"] == "authToken"
    assert payload["needs_review"][0]["suggestions"][0]["target_term_id"] == "auth.access_token"


@pytest.mark.skipif(shutil.which("git") is None, reason="git executable is not available")
def test_cli_check_merge_reads_git_diff_and_can_fail_on_review(tmp_path: Path, capsys) -> None:
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
        scopes:
          - id: auth
        terms:
          - id: auth.access_token
            canonical: access token
            scopes: [auth]
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
        "def rotate():\n"
        "    accessToken = rotate_known()\n"
        "    authToken = rotate_unknown()\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature")

    exit_code = main(["check-merge", "--root", str(repo), "--base", "main", "--head", "HEAD"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Git merge terminology check:" in captured.out
    assert "Known terminology:" in captured.out
    assert "Needs review:" in captured.out
    assert "accessToken" in captured.out
    assert "authToken" in captured.out
    assert "auth.access_token" in captured.out

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
