from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_lexicon import AgentLexiconLoadError, loads_lexicon
from agent_lexicon.cli import main
from agent_lexicon.path_globs import repo_path_matches


def _write_lexicon(root: Path) -> None:
    lexicon_dir = root / "lexicon"
    lexicon_dir.mkdir(parents=True, exist_ok=True)
    (lexicon_dir / "lexicon.yaml").write_text(
        "version: '1'\n"
        "terms:\n"
        "  - id: core.context_space\n"
        "    canonical: ContextSpace\n"
        "    aliases:\n"
        "      - surface: WorkspaceScope\n"
        "        deprecated: true\n",
        encoding="utf-8",
    )


def _diff(path: str, line: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        f"+{line}\n"
    )


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


def test_repository_globstar_matches_zero_or_more_directories() -> None:
    assert repo_path_matches("main.py", "**/*.py") is True
    assert repo_path_matches("src/main.py", "**/*.py") is True
    assert repo_path_matches("src/main.py", "src/**/*.py") is True
    assert repo_path_matches("src/domain/main.py", "src/**/*.py") is True
    assert repo_path_matches("main.ts", "**/*.py") is False


@pytest.mark.skipif(shutil.which("git") is None, reason="git executable is not available")
def test_lint_diff_staged_scans_deprecated_term_in_root_file(tmp_path: Path, capsys) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "checkout", "-b", "main")
    _git(tmp_path, "config", "user.email", "agent-lexicon@example.test")
    _git(tmp_path, "config", "user.name", "Agent Lexicon")
    _write_lexicon(tmp_path)
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    (tmp_path / "drift_demo.py").write_text("WorkspaceScope = None\n", encoding="utf-8")
    _git(tmp_path, "add", "drift_demo.py")

    code = main(["lint-diff", "--root", str(tmp_path), "--staged", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["scanned_file_count"] == 1
    assert payload["added_line_count"] == 1
    assert payload["fail_count"] == 1
    assert payload["findings"][0]["path"] == "drift_demo.py"


def test_lint_diff_detects_piped_diff_without_stdin_flag(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_lexicon(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(_diff("drift_demo.py", "WorkspaceScope = None")))

    code = main(["lint-diff", "--root", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["scanned_file_count"] == 1
    assert payload["fail_count"] == 1
    assert payload["findings"][0]["surface"] == "WorkspaceScope"


def test_loader_rejects_unknown_alias_field_with_metadata_guidance() -> None:
    with pytest.raises(AgentLexiconLoadError, match="unknown field: 'status'.*metadata"):
        loads_lexicon(
            """
            version: 1
            terms:
              - id: core.context_space
                canonical: ContextSpace
                aliases:
                  - surface: WorkspaceScope
                    status: deprecated
            """,
            document_format="yaml",
        )


def test_loader_rejects_deprecated_lifecycle_shadowed_by_alias_metadata() -> None:
    with pytest.raises(AgentLexiconLoadError, match="metadata.status.*use deprecated: true"):
        loads_lexicon(
            """
            version: 1
            terms:
              - id: core.context_space
                canonical: ContextSpace
                aliases:
                  - surface: WorkspaceScope
                    metadata:
                      status: deprecated
            """,
            document_format="yaml",
        )


def test_validate_command_rejects_shadowed_alias_lifecycle(tmp_path: Path, capsys) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        "version: '1'\n"
        "terms:\n"
        "  - id: core.context_space\n"
        "    canonical: ContextSpace\n"
        "    aliases:\n"
        "      - surface: WorkspaceScope\n"
        "        metadata:\n"
        "          status: deprecated\n",
        encoding="utf-8",
    )

    code = main(["validate", str(path)])
    captured = capsys.readouterr()

    assert code == 1
    assert "metadata.status does not deprecate the alias" in captured.err
    assert "deprecated: true" in captured.err


@pytest.mark.skipif(shutil.which("git") is None, reason="git executable is not available")
def test_check_merge_fail_on_review_blocks_deprecated_root_file(tmp_path: Path, capsys) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "checkout", "-b", "main")
    _git(tmp_path, "config", "user.email", "agent-lexicon@example.test")
    _git(tmp_path, "config", "user.name", "Agent Lexicon")
    _write_lexicon(tmp_path)
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    _git(tmp_path, "checkout", "-b", "feature")
    (tmp_path / "drift_demo.py").write_text("WorkspaceScope = None\n", encoding="utf-8")
    _git(tmp_path, "add", "drift_demo.py")
    _git(tmp_path, "commit", "-m", "add root drift")

    code = main(
        [
            "check-merge",
            "--root",
            str(tmp_path),
            "--base",
            "main",
            "--head",
            "HEAD",
            "--fail-on-review",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["scanned_file_count"] == 1
    assert payload["deprecated_occurrence_count"] == 1
    assert payload["needs_review_count"] == 0
    assert payload["has_review_items"] is True
    assert payload["deprecated_occurrences"][0]["path"] == "drift_demo.py"
