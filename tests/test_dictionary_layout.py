from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_lexicon import (
    DictionaryLayoutError,
    dictionary_layout_path,
    init_dictionary_layout,
    validate_dictionary_layout,
    write_dictionary_manifest,
)
from agent_lexicon.cli import main


def test_dictionary_layout_path_uses_standard_files(tmp_path: Path) -> None:
    layout = dictionary_layout_path(tmp_path)

    assert Path(layout.layout_path) == tmp_path.resolve() / "lexicon"
    assert Path(layout.lexicon_path).name == "lexicon.yaml"
    assert Path(layout.queries_path).name == "queries.jsonl"
    assert Path(layout.proposals_path).name == "proposals"
    assert Path(layout.snapshots_path).name == "snapshots"
    assert Path(layout.review_events_path).name == "review-events"


def test_init_dictionary_layout_creates_valid_git_tracked_layout(tmp_path: Path) -> None:
    summary = init_dictionary_layout(tmp_path)

    assert summary.valid
    assert summary.metadata["scope_count"] == 1
    assert summary.metadata["term_count"] == 1
    assert summary.metadata["query_count"] == 1
    assert Path(summary.layout.lexicon_path).exists()
    assert Path(summary.layout.queries_path).exists()
    assert (Path(summary.layout.proposals_path) / ".gitkeep").exists()
    assert (Path(summary.layout.snapshots_path) / ".gitkeep").exists()
    assert (Path(summary.layout.review_events_path) / ".gitkeep").exists()

    validated = validate_dictionary_layout(tmp_path)
    assert validated.valid


def test_init_dictionary_layout_preserves_existing_files_without_force(tmp_path: Path) -> None:
    summary = init_dictionary_layout(tmp_path)
    lexicon_path = Path(summary.layout.lexicon_path)
    lexicon_path.write_text("version: 1\nterms: []\n", encoding="utf-8")

    init_dictionary_layout(tmp_path)

    assert lexicon_path.read_text(encoding="utf-8") == "version: 1\nterms: []\n"


def test_init_dictionary_layout_force_overwrites_generated_files(tmp_path: Path) -> None:
    summary = init_dictionary_layout(tmp_path)
    lexicon_path = Path(summary.layout.lexicon_path)
    lexicon_path.write_text("version: 1\nterms: []\n", encoding="utf-8")

    init_dictionary_layout(tmp_path, force=True)

    assert "project.example_term" in lexicon_path.read_text(encoding="utf-8")


def test_validate_dictionary_layout_rejects_missing_layout(tmp_path: Path) -> None:
    with pytest.raises(DictionaryLayoutError, match="layout directory does not exist"):
        validate_dictionary_layout(tmp_path)


def test_validate_dictionary_layout_rejects_invalid_queries(tmp_path: Path) -> None:
    summary = init_dictionary_layout(tmp_path)
    Path(summary.layout.queries_path).write_text("not-json\n", encoding="utf-8")

    with pytest.raises(DictionaryLayoutError, match="invalid queries dataset"):
        validate_dictionary_layout(tmp_path)


def test_write_dictionary_manifest(tmp_path: Path) -> None:
    summary = init_dictionary_layout(tmp_path)
    output_path = tmp_path / "manifest" / "dictionary.json"

    written = write_dictionary_manifest(summary, output_path)

    assert written == output_path
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["valid"] is True
    assert payload["metadata"]["term_count"] == 1


def test_cli_dictionary_init_status_and_validate(tmp_path: Path, capsys) -> None:
    assert main(["dictionary", "init", "--root", str(tmp_path)]) == 0
    init_output = capsys.readouterr().out
    assert "Dictionary layout initialized:" in init_output
    assert "Dictionary status: valid=yes" in init_output

    assert main(["dictionary", "status", "--root", str(tmp_path)]) == 0
    status_output = capsys.readouterr().out
    assert "Dictionary status: valid=yes" in status_output
    assert "lexicon.yaml" in status_output

    manifest_path = tmp_path / "dictionary-manifest.json"
    assert main([
        "dictionary",
        "validate",
        "--root",
        str(tmp_path),
        "--manifest",
        str(manifest_path),
    ]) == 0
    validate_output = capsys.readouterr().out
    assert "Valid dictionary layout:" in validate_output
    assert "Manifest written:" in validate_output
    assert manifest_path.exists()


def test_cli_dictionary_validate_json(tmp_path: Path, capsys) -> None:
    init_dictionary_layout(tmp_path)

    assert main(["dictionary", "validate", "--root", str(tmp_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["metadata"]["query_count"] == 1


def test_cli_dictionary_status_returns_nonzero_when_layout_missing(tmp_path: Path, capsys) -> None:
    assert main(["dictionary", "status", "--root", str(tmp_path)]) == 1
    assert "Dictionary status: valid=no" in capsys.readouterr().out
