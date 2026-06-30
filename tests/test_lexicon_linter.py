from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    LexiconLintCode,
    lint_lexicon_file,
    loads_lexicon,
    lint_lexicon,
)
from agent_lexicon.cli import main


def _codes(report) -> set[str]:
    return {finding.code.value for finding in report.findings}


def test_linter_warns_for_missing_version_and_broad_tool_alias(tmp_path: Path) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        """
        terms:
          - id: auth.token
            canonical: access token
            tools: [auth.rotate_access_token]
            aliases:
              - token
              - id
        """,
        encoding="utf-8",
    )

    report = lint_lexicon_file(path)

    assert report.warning_count >= 4
    assert LexiconLintCode.MISSING_VERSION.value in _codes(report)
    assert LexiconLintCode.BROAD_SURFACE.value in _codes(report)
    assert LexiconLintCode.SHORT_SURFACE.value in _codes(report)
    assert LexiconLintCode.TOOL_BROAD_SURFACE.value in _codes(report)


def test_linter_warns_for_deprecated_broad_surfaces() -> None:
    lexicon = loads_lexicon(
        """
        version: 1
        terms:
          - id: auth.old_token
            canonical: token
            deprecated: true
        """,
        document_format="yaml",
    )

    report = lint_lexicon(lexicon)

    assert LexiconLintCode.DEPRECATED_BROAD_SURFACE.value in _codes(report)


def test_linter_detects_unicode_normalized_surface_collisions(tmp_path: Path) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        """
        version: 1
        terms:
          - id: auth.access_token
            canonical: access token
          - id: auth.visual_access_token
            canonical: access​token
        """,
        encoding="utf-8",
    )

    report = lint_lexicon_file(path)

    collisions = [
        finding
        for finding in report.findings
        if finding.code == LexiconLintCode.NORMALIZED_SURFACE_COLLISION
    ]
    assert len(collisions) == 1
    assert collisions[0].metadata["search_surface"] == "access token"
    assert collisions[0].metadata["term_ids"] == (
        "auth.access_token",
        "auth.visual_access_token",
    )


def test_cli_lint_reports_warnings_without_failing_by_default(tmp_path: Path, capsys) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        """
        terms:
          - id: billing.limit
            canonical: credit limit
            tools: [billing.update_credit_limit]
            aliases:
              - limit
        """,
        encoding="utf-8",
    )

    exit_code = main(["lint", str(path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Lexicon lint: warnings" in captured.out
    assert "broad_surface" in captured.out
    assert "tool_broad_surface" in captured.out


def test_cli_lint_strict_fails_on_warnings(tmp_path: Path, capsys) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        """
        terms:
          - id: docs.id
            canonical: id
        """,
        encoding="utf-8",
    )

    exit_code = main(["lint", str(path), "--strict"])

    assert exit_code == 1
    assert "short_surface" in capsys.readouterr().out


def test_cli_lint_json_output(tmp_path: Path, capsys) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        """
        version: 1
        terms:
          - id: docs.token
            canonical: token
        """,
        encoding="utf-8",
    )

    exit_code = main(["lint", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["warning_count"] == 1
    assert payload["findings"][0]["code"] == "broad_surface"


def test_cli_validate_can_include_lint(tmp_path: Path, capsys) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        """
        version: 1
        terms:
          - id: docs.token
            canonical: token
        """,
        encoding="utf-8",
    )

    exit_code = main(["validate", str(path), "--lint"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Valid lexicon:" in captured.out
    assert "Lexicon lint: warnings" in captured.out
