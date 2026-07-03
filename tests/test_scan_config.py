from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    DEFAULT_CONFIG_PATH,
    AgentLexiconConfigError,
    discover_local_files,
    init_project_config,
    load_project_config,
    run_simple_init,
    run_simple_scan,
)
from agent_lexicon.cli import main


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_init_project_config_writes_default_scan_config(tmp_path: Path) -> None:
    config_path = init_project_config(tmp_path)

    assert config_path == tmp_path / DEFAULT_CONFIG_PATH
    assert config_path.exists()

    config = load_project_config(tmp_path)
    assert config.path == str(config_path)
    assert config.scan.paths == ("README.md", "docs", "src", "app", "packages", "lib", "services")
    assert "node_modules/**" in config.scan.exclude
    assert config.scan.respect_gitignore is True
    assert config.scan.max_file_bytes == 1_000_000


def test_run_simple_init_creates_repository_config(tmp_path: Path) -> None:
    report = run_simple_init(tmp_path)

    assert Path(report.config_path).exists()
    assert report.to_dict()["config_path"] == report.config_path


def test_run_simple_scan_uses_config_paths_and_excludes(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "domain.md", "CustomerCapReview owns customer cap review.\n")
    _write(tmp_path / "README.md", "This root readme should be ignored by config paths.\n")
    _write(tmp_path / "docs" / "generated.md", "GeneratedNoiseTerm should not appear.\n")
    (tmp_path / ".agent-lexicon").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent-lexicon" / "config.yaml").write_text(
        "scan:\n"
        "  paths:\n"
        "    - docs\n"
        "  include:\n"
        "    - docs/**/*.md\n"
        "    - docs/*.md\n"
        "  exclude:\n"
        "    - docs/generated.md\n"
        "  max_file_bytes: 1000000\n",
        encoding="utf-8",
    )

    run_simple_init(tmp_path)
    report = run_simple_scan(root=tmp_path, max_candidates=5, min_score=0.1)

    assert [document.relative_path for document in report.ingest.documents] == ["docs/domain.md"]
    assert report.metadata["config_path"] == str(tmp_path / ".agent-lexicon" / "config.yaml")
    assert report.metadata["exclude_globs"] == ["docs/generated.md"]


def test_cli_scan_uses_config_without_explicit_paths(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "docs" / "domain.md", "CustomerCapReview owns customer cap review.\n")
    assert main(["init", "--root", str(tmp_path)]) == 0
    capsys.readouterr()

    assert main(["scan", "--root", str(tmp_path), "--max-candidates", "3", "--min-score", "0.1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["document_count"] == 1
    assert payload["metadata"]["config_path"].endswith(".agent-lexicon/config.yaml")


def test_discover_local_files_respects_exclude_globs(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "# Demo\n")
    _write(tmp_path / "docs" / "guide.md", "Customer cap means credit limit.\n")
    _write(tmp_path / "docs" / "generated.md", "Generated docs.\n")
    _write(tmp_path / "node_modules" / "pkg" / "index.js", "const ignored = true\n")

    files = discover_local_files([tmp_path], root=tmp_path, exclude_globs=("docs/generated.md", "node_modules/**"))
    relative = [path.relative_to(tmp_path).as_posix() for path in files]

    assert relative == ["README.md", "docs/guide.md"]


def test_load_project_config_rejects_invalid_shape(tmp_path: Path) -> None:
    (tmp_path / ".agent-lexicon").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent-lexicon" / "config.yaml").write_text("scan:\n  paths: docs\n", encoding="utf-8")

    try:
        load_project_config(tmp_path)
    except AgentLexiconConfigError as exc:
        assert "scan.paths" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected AgentLexiconConfigError")


def test_scan_excludes_lexicon_workspace_dir_when_present(tmp_path: Path) -> None:
    # Regression: `scan .` used to ingest the tool's own lexicon/ folder,
    # polluting the review queue with internal terms like "queries.jsonl".
    _write(tmp_path / "code.py", "def get_access_token(user):\n    return user.authToken\n")
    _write(tmp_path / "lexicon" / "README.md", "credential blob and bearer token\n")
    _write(tmp_path / "lexicon" / "queries.jsonl", '{"q": "credential blob"}\n')

    # Explicitly discover both a real source file and the lexicon dir; the
    # lexicon dir must be dropped, the source file kept.
    files = discover_local_files(
        [tmp_path / "code.py", tmp_path / "lexicon"], root=tmp_path
    )
    relative = {path.relative_to(tmp_path).as_posix() for path in files}

    assert "code.py" in relative
    assert not any(p.startswith("lexicon/") for p in relative)


def test_default_exclude_dirs_include_lexicon() -> None:
    from agent_lexicon.ingest.local import DEFAULT_EXCLUDE_DIRS, DEFAULT_EXCLUDE_GLOBS

    assert "lexicon" in DEFAULT_EXCLUDE_DIRS
    assert ".agent-lexicon" in DEFAULT_EXCLUDE_DIRS
    assert "lexicon/**" in DEFAULT_EXCLUDE_GLOBS


def test_default_config_exclude_globs_include_lexicon() -> None:
    # Regression: check-merge and scan share no exclude list; both must drop
    # the tool's own lexicon/ dir so its README/queries don't pollute output.
    from agent_lexicon.config import DEFAULT_SCAN_EXCLUDE_GLOBS, DEFAULT_CONFIG_TEXT

    assert "lexicon/**" in DEFAULT_SCAN_EXCLUDE_GLOBS
    assert ".agent-lexicon/**" in DEFAULT_SCAN_EXCLUDE_GLOBS
    assert "lexicon/**" in DEFAULT_CONFIG_TEXT
