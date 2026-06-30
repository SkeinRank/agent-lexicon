from __future__ import annotations

import json

from agent_lexicon import GitDiffAddedLine, Lexicon, Term, build_git_merge_terminology_report


def test_git_merge_default_reports_hidden_unresolved_count() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    report = build_git_merge_terminology_report(
        lexicon,
        (
            GitDiffAddedLine(path="src/auth.py", line_number=10, text="def issue_c():"),
            GitDiffAddedLine(path="src/auth.py", line_number=11, text="tmp_value = build()"),
            GitDiffAddedLine(path="src/auth.py", line_number=12, text="credentialBlob = load()"),
        ),
    )

    assert report.likely_new_term_count == 1
    assert [item.surface for item in report.likely_new_terms] == ["credentialBlob"]
    assert report.unresolved_unknown_count == 0
    assert report.hidden_unresolved_count == 2

    text = report.to_text()
    assert "hidden_unresolved=2" in text
    assert "Hidden unresolved identifiers: 2" in text
    assert "--include-unresolved-unknowns" in text
    assert "issue_c" not in text
    assert "tmp_value" not in text


def test_git_merge_json_exposes_hidden_unresolved_count() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    report = build_git_merge_terminology_report(
        lexicon,
        (GitDiffAddedLine(path="src/auth.py", line_number=10, text="def issue_c():"),),
    )

    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["hidden_unresolved_count"] == 1
    assert payload["unresolved_unknown_count"] == 0
    assert payload["metadata"]["hidden_unresolved_count"] == 1


def test_git_merge_full_audit_moves_hidden_unresolved_into_report() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    report = build_git_merge_terminology_report(
        lexicon,
        (
            GitDiffAddedLine(path="src/auth.py", line_number=10, text="def issue_c():"),
            GitDiffAddedLine(path="src/auth.py", line_number=11, text="tmp_value = build()"),
        ),
        include_unresolved_unknowns=True,
    )

    assert report.hidden_unresolved_count == 0
    assert report.unresolved_unknown_count == 2
    assert {item.surface for item in report.unresolved_unknowns} == {"issue_c", "tmp_value"}

    text = report.to_text()
    assert "hidden_unresolved=0" in text
    assert "Hidden unresolved identifiers" not in text
    assert "Low-signal unknown identifiers:" in text
