from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_lexicon import (
    IngestSourceKind,
    LocalIngestError,
    classify_source_kind,
    discover_local_files,
    ingest_local_paths,
    read_local_document,
)
from agent_lexicon.cli import main


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_discover_local_files_reads_project_defaults(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "# Demo\n")
    _write(tmp_path / "docs" / "guide.md", "Customer cap means credit limit.\n")
    _write(tmp_path / "src" / "demo" / "module.py", "class CreditLimit:\n    pass\n")
    _write(tmp_path / "notes.tmp", "not selected\n")
    _write(tmp_path / ".venv" / "ignored.py", "ignored\n")

    files = discover_local_files([tmp_path], root=tmp_path)
    relative = [path.relative_to(tmp_path).as_posix() for path in files]

    assert relative == [
        "README.md",
        "docs/guide.md",
        "src/demo/module.py",
    ]


def test_ingest_local_paths_returns_documents_with_metadata(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "# Demo\n")
    _write(tmp_path / "docs" / "guide.md", "Customer cap means credit limit.\n")

    report = ingest_local_paths([tmp_path], root=tmp_path)

    assert report.document_count == 2
    assert report.total_lines == 2
    assert [document.relative_path for document in report.documents] == [
        "README.md",
        "docs/guide.md",
    ]
    assert report.documents[0].kind == IngestSourceKind.MARKDOWN
    assert len(report.documents[0].sha256) == 64
    assert "Demo" in report.documents[0].preview()


def test_read_local_document_supports_explicit_local_file(tmp_path: Path) -> None:
    file_path = _write(tmp_path / "custom.rules", "Use the billing vocabulary.\n")

    document = read_local_document(file_path, root=tmp_path)

    assert document.relative_path == "custom.rules"
    assert document.line_count == 1
    assert document.text == "Use the billing vocabulary.\n"


def test_discover_direct_file_is_not_limited_to_default_globs(tmp_path: Path) -> None:
    file_path = _write(tmp_path / "local_notes.conf", "term=credit limit\n")

    files = discover_local_files([file_path], root=tmp_path)

    assert files == (file_path.resolve(),)


def test_local_ingest_rejects_missing_path(tmp_path: Path) -> None:
    with pytest.raises(LocalIngestError):
        discover_local_files([tmp_path / "missing"], root=tmp_path)


def test_local_ingest_skips_binary_files(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "# Demo\n")
    binary_path = tmp_path / "docs" / "image.txt"
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    binary_path.write_bytes(b"abc\x00def")

    report = ingest_local_paths([tmp_path], root=tmp_path)

    assert report.document_count == 1
    assert report.skipped_paths == ("docs/image.txt",)


def test_classify_source_kind() -> None:
    assert classify_source_kind("README.md") == IngestSourceKind.MARKDOWN
    assert classify_source_kind("src/app.py") == IngestSourceKind.PYTHON
    assert classify_source_kind("openapi.json") == IngestSourceKind.JSON
    assert classify_source_kind("config.yaml") == IngestSourceKind.YAML


def test_cli_ingest_reports_local_documents(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "README.md", "# Demo\n")
    _write(tmp_path / "docs" / "guide.md", "Customer cap means credit limit.\n")

    exit_code = main(["ingest", str(tmp_path), "--root", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Local ingest: 2 documents" in captured.out
    assert "README.md" in captured.out
    assert "docs/guide.md" in captured.out


def test_cli_ingest_can_emit_jsonl(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "README.md", "# Demo\n")

    exit_code = main(["ingest", str(tmp_path), "--root", str(tmp_path), "--jsonl"])
    captured = capsys.readouterr()

    assert exit_code == 0
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert rows[0]["relative_path"] == "README.md"
    assert rows[0]["text"] == "# Demo\n"
