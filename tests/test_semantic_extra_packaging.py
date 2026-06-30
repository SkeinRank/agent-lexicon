from __future__ import annotations

from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _semantic_extra_block(pyproject_text: str) -> str:
    marker = "semantic = ["
    start = pyproject_text.index(marker)
    end = pyproject_text.index("]", start) + 1
    return pyproject_text[start:end]


def test_semantic_extra_declares_optional_bge_dependency() -> None:
    pyproject = (_project_root() / "pyproject.toml").read_text(encoding="utf-8")

    block = _semantic_extra_block(pyproject)

    assert '"sentence-transformers>=2.6,<4.0"' in block


def test_base_package_stays_dependency_free() -> None:
    pyproject = (_project_root() / "pyproject.toml").read_text(encoding="utf-8")

    assert "dependencies = []" in pyproject


def test_bge_missing_dependency_message_mentions_semantic_extra() -> None:
    source = (_project_root() / "src" / "agent_lexicon" / "scout" / "semantic_bge.py").read_text(
        encoding="utf-8"
    )

    assert "agent-lexicon[semantic]" in source
    assert "poetry install -E semantic" in source
