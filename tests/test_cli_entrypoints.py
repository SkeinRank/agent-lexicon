from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_poetry_dev_entrypoints_are_available() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "[project.scripts]" in pyproject
    assert "[tool.poetry.scripts]" in pyproject

    for script_name in ("agent-lexicon", "alex"):
        entrypoint = f'{script_name} = "agent_lexicon.cli:main"'
        assert pyproject.count(entrypoint) == 2
