from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    DEFAULT_LANGUAGE_GLOBS,
    DEFAULT_RESPECT_GITIGNORE,
    GitIgnoreRule,
    discover_local_files,
    ingest_local_paths,
    load_gitignore_rules,
    relative_path_matches_gitignore,
)
from agent_lexicon.cli import main
from agent_lexicon.scout import parse_git_added_lines


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_language_defaults_include_common_repository_file_types(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "# Demo\n")
    _write(tmp_path / "services" / "api" / "main.go", "package main\n")
    _write(tmp_path / "packages" / "web" / "app.tsx", "export const CustomerCap = 1\n")
    _write(tmp_path / "crates" / "core" / "src" / "lib.rs", "pub struct CreditLimit;\n")
    _write(tmp_path / "infra" / "main.tf", "resource \"demo\" \"x\" {}\n")
    _write(tmp_path / "notes.tmp", "not selected\n")

    files = discover_local_files([tmp_path], root=tmp_path)
    relative = [path.relative_to(tmp_path).as_posix() for path in files]

    assert "**/*.go" in DEFAULT_LANGUAGE_GLOBS
    assert "**/*.rs" in DEFAULT_LANGUAGE_GLOBS
    assert "**/*.tsx" in DEFAULT_LANGUAGE_GLOBS
    assert relative == [
        "README.md",
        "crates/core/src/lib.rs",
        "infra/main.tf",
        "packages/web/app.tsx",
        "services/api/main.go",
    ]


def test_discovery_respects_root_gitignore_by_default(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "docs/private.md\n*.secret\n")
    _write(tmp_path / "README.md", "# Demo\n")
    _write(tmp_path / "docs" / "public.md", "public term\n")
    _write(tmp_path / "docs" / "private.md", "private term\n")
    _write(tmp_path / "src" / "token.secret", "private token\n")

    files = discover_local_files([tmp_path], root=tmp_path)
    relative = [path.relative_to(tmp_path).as_posix() for path in files]

    assert DEFAULT_RESPECT_GITIGNORE is True
    assert relative == ["README.md", "docs/public.md"]


def test_discovery_can_disable_gitignore_for_explicit_audit(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "docs/private.md\n")
    _write(tmp_path / "docs" / "public.md", "public term\n")
    _write(tmp_path / "docs" / "private.md", "private term\n")

    files = discover_local_files([tmp_path], root=tmp_path, respect_gitignore=False)
    relative = [path.relative_to(tmp_path).as_posix() for path in files]

    assert relative == ["docs/private.md", "docs/public.md"]


def test_gitignore_negation_can_restore_a_file(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "docs/private/\n!docs/private/keep.md\n")
    _write(tmp_path / "docs" / "private" / "drop.md", "drop term\n")
    _write(tmp_path / "docs" / "private" / "keep.md", "keep term\n")

    rules = load_gitignore_rules(tmp_path)

    assert relative_path_matches_gitignore("docs/private/drop.md", rules) is True
    assert relative_path_matches_gitignore("docs/private/keep.md", rules) is False

    files = discover_local_files([tmp_path], root=tmp_path)
    relative = [path.relative_to(tmp_path).as_posix() for path in files]

    assert relative == ["docs/private/keep.md"]


def test_ingest_report_exposes_gitignore_metadata(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "docs/private.md\n")
    _write(tmp_path / "docs" / "public.md", "public term\n")
    _write(tmp_path / "docs" / "private.md", "private term\n")

    report = ingest_local_paths([tmp_path], root=tmp_path)

    assert [document.relative_path for document in report.documents] == ["docs/public.md"]
    assert report.metadata["respect_gitignore"] is True
    assert report.metadata["gitignore_pattern_count"] == 1


def test_parse_git_added_lines_can_apply_gitignore_rules() -> None:
    diff = """diff --git a/docs/public.md b/docs/public.md
+++ b/docs/public.md
@@ -0,0 +1 @@
+CustomerCapReview

diff --git a/docs/private.md b/docs/private.md
+++ b/docs/private.md
@@ -0,0 +1 @@
+PrivateCapReview
"""

    lines = parse_git_added_lines(
        diff,
        include_globs=("docs/**",),
        gitignore_rules=(GitIgnoreRule(pattern="docs/private.md"),),
    )

    assert [(line.path, line.text) for line in lines] == [("docs/public.md", "CustomerCapReview")]


def test_cli_scan_can_disable_gitignore(tmp_path: Path, capsys) -> None:
    _write(tmp_path / ".gitignore", "docs/private.md\n")
    _write(tmp_path / "docs" / "public.md", "CustomerCapReview public\n")
    _write(tmp_path / "docs" / "private.md", "PrivateCapReview private\n")

    assert main(["init", "--root", str(tmp_path)]) == 0
    capsys.readouterr()

    assert main(["scan", "--root", str(tmp_path), "--json", "--max-candidates", "3", "--min-score", "0.1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["document_count"] == 1
    assert payload["metadata"]["respect_gitignore"] is True

    assert main(["scan", "--root", str(tmp_path), "--no-gitignore", "--json", "--max-candidates", "3", "--min-score", "0.1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["document_count"] == 2
    assert payload["metadata"]["respect_gitignore"] is False
