"""Repository configuration for Agent Lexicon workflows.

The configuration file keeps repository scan behavior close to the project
instead of requiring long CLI flags in every local run or CI job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # pragma: no cover - exercised only when PyYAML is installed
    import yaml
except Exception:  # pragma: no cover - dependency-free fallback path
    yaml = None  # type: ignore[assignment]

from agent_lexicon.core.files import atomic_write_text
from agent_lexicon.core.loader import _load_basic_yaml
from agent_lexicon.ingest.local import DEFAULT_INCLUDE_GLOBS, DEFAULT_MAX_FILE_BYTES


DEFAULT_CONFIG_PATH = ".agent-lexicon/config.yaml"
DEFAULT_SCAN_PATHS: tuple[str, ...] = ("README.md", "docs", "src")
DEFAULT_SCAN_EXCLUDE_GLOBS: tuple[str, ...] = (
    ".agent-lexicon/**",
    ".git/**",
    ".hg/**",
    ".mypy_cache/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    ".tox/**",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "build/**",
    "dist/**",
    "node_modules/**",
    "site-packages/**",
)

DEFAULT_CONFIG_TEXT = """scan:
  paths:
    - README.md
    - docs
    - src
  include:
    - README
    - README.*
    - AGENTS.md
    - CLAUDE.md
    - SKILL.md
    - CHANGELOG
    - CHANGELOG.*
    - docs/*.md
    - docs/*.txt
    - docs/*.json
    - docs/*.yaml
    - docs/*.yml
    - docs/**/*.md
    - docs/**/*.txt
    - docs/**/*.json
    - docs/**/*.yaml
    - docs/**/*.yml
    - src/*.py
    - src/*.pyi
    - src/*.js
    - src/*.jsx
    - src/*.ts
    - src/*.tsx
    - src/*.md
    - src/*.json
    - src/*.yaml
    - src/*.yml
    - src/**/*.py
    - src/**/*.pyi
    - src/**/*.js
    - src/**/*.jsx
    - src/**/*.ts
    - src/**/*.tsx
    - src/**/*.md
    - src/**/*.json
    - src/**/*.yaml
    - src/**/*.yml
    - "*.md"
    - "*.txt"
    - "*.json"
    - "*.yaml"
    - "*.yml"
    - "*.toml"
  exclude:
    - .agent-lexicon/**
    - .git/**
    - .hg/**
    - .mypy_cache/**
    - .pytest_cache/**
    - .ruff_cache/**
    - .tox/**
    - .venv/**
    - venv/**
    - __pycache__/**
    - "**/__pycache__/**"
    - build/**
    - dist/**
    - node_modules/**
    - site-packages/**
  max_file_bytes: 1000000
"""


class AgentLexiconConfigError(ValueError):
    """Raised when an Agent Lexicon repository config is invalid."""


@dataclass(frozen=True, slots=True)
class ScanConfig:
    """Repository scan defaults."""

    paths: tuple[str, ...] = DEFAULT_SCAN_PATHS
    include: tuple[str, ...] = DEFAULT_INCLUDE_GLOBS
    exclude: tuple[str, ...] = DEFAULT_SCAN_EXCLUDE_GLOBS
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES

    def __post_init__(self) -> None:
        object.__setattr__(self, "paths", _clean_string_tuple(self.paths, field_name="scan.paths"))
        object.__setattr__(self, "include", _clean_string_tuple(self.include, field_name="scan.include"))
        object.__setattr__(self, "exclude", _clean_string_tuple(self.exclude, field_name="scan.exclude"))
        max_file_bytes = int(self.max_file_bytes)
        if max_file_bytes < 1:
            raise AgentLexiconConfigError("scan.max_file_bytes must be greater than 0")
        object.__setattr__(self, "max_file_bytes", max_file_bytes)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable scan config."""
        return {
            "paths": list(self.paths),
            "include": list(self.include),
            "exclude": list(self.exclude),
            "max_file_bytes": self.max_file_bytes,
        }


@dataclass(frozen=True, slots=True)
class AgentLexiconConfig:
    """Project-level Agent Lexicon configuration."""

    scan: ScanConfig = field(default_factory=ScanConfig)
    path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.scan, ScanConfig):
            raise AgentLexiconConfigError("scan must be a ScanConfig")
        if self.path is not None:
            object.__setattr__(self, "path", _clean_string(str(self.path), field_name="path"))
        if not isinstance(self.metadata, Mapping):
            raise AgentLexiconConfigError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable project config."""
        return {
            "scan": self.scan.to_dict(),
            "path": self.path,
            "metadata": dict(self.metadata),
        }


def project_config_path(root: str | Path = ".", *, config_path: str | Path | None = None) -> Path:
    """Return the Agent Lexicon config path for a project root."""
    root_path = Path(root).expanduser().resolve()
    if config_path is None:
        return root_path / DEFAULT_CONFIG_PATH
    candidate = Path(config_path).expanduser()
    return candidate if candidate.is_absolute() else root_path / candidate


def init_project_config(root: str | Path = ".", *, force: bool = False) -> Path:
    """Create the default repository config if needed and return its path."""
    path = project_config_path(root)
    if path.exists() and not force:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, DEFAULT_CONFIG_TEXT)
    return path


def load_project_config(root: str | Path = ".", *, config_path: str | Path | None = None) -> AgentLexiconConfig:
    """Load repository configuration, falling back to built-in defaults when absent."""
    path = project_config_path(root, config_path=config_path)
    if not path.exists():
        if config_path is not None:
            raise AgentLexiconConfigError(f"Agent Lexicon config does not exist: {path}")
        return AgentLexiconConfig(path=None, metadata={"source": "defaults"})
    payload = _load_config_payload(path)
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise AgentLexiconConfigError("Agent Lexicon config must be a mapping")
    scan_payload = payload.get("scan", {})
    if scan_payload is None:
        scan_payload = {}
    if not isinstance(scan_payload, Mapping):
        raise AgentLexiconConfigError("scan config must be a mapping")
    scan = ScanConfig(
        paths=_optional_string_sequence(scan_payload.get("paths"), default=DEFAULT_SCAN_PATHS, field_name="scan.paths"),
        include=_optional_string_sequence(scan_payload.get("include"), default=DEFAULT_INCLUDE_GLOBS, field_name="scan.include"),
        exclude=_optional_string_sequence(scan_payload.get("exclude"), default=DEFAULT_SCAN_EXCLUDE_GLOBS, field_name="scan.exclude"),
        max_file_bytes=int(scan_payload.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)),
    )
    return AgentLexiconConfig(scan=scan, path=str(path), metadata={"source": "file"})


def effective_scan_paths(
    cli_paths: Sequence[str | Path] | None,
    config: AgentLexiconConfig,
) -> tuple[str | Path, ...]:
    """Return CLI scan paths when provided, otherwise config defaults."""
    if cli_paths:
        return tuple(cli_paths)
    return config.scan.paths


def effective_include_globs(
    cli_include: Sequence[str] | None,
    config: AgentLexiconConfig,
) -> tuple[str, ...]:
    """Return CLI include globs when provided, otherwise config defaults."""
    if cli_include is not None:
        return tuple(cli_include)
    return config.scan.include


def effective_exclude_globs(
    cli_exclude: Sequence[str] | None,
    config: AgentLexiconConfig,
) -> tuple[str, ...]:
    """Return CLI exclude globs when provided, otherwise config defaults."""
    if cli_exclude is not None:
        return tuple(cli_exclude)
    return config.scan.exclude


def effective_max_file_bytes(cli_value: int | None, config: AgentLexiconConfig) -> int:
    """Return CLI max-file-bytes when provided, otherwise config default."""
    return int(cli_value if cli_value is not None else config.scan.max_file_bytes)


def _load_config_payload(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            return json.loads(text)
        if suffix in {".yaml", ".yml"}:
            if yaml is not None:
                return yaml.safe_load(text)
            return _load_basic_yaml(text)
        if yaml is not None:
            return yaml.safe_load(text)
        return json.loads(text)
    except Exception as exc:
        raise AgentLexiconConfigError(f"unable to load Agent Lexicon config {path}: {exc}") from exc


def _optional_string_sequence(value: Any, *, default: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        raise AgentLexiconConfigError(f"{field_name} must be a list of strings")
    if not isinstance(value, Iterable):
        raise AgentLexiconConfigError(f"{field_name} must be a list of strings")
    return _clean_string_tuple(tuple(str(item) for item in value), field_name=field_name)


def _clean_string_tuple(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        cleaned.append(_clean_string(str(value), field_name=f"{field_name} item"))
    if not cleaned:
        raise AgentLexiconConfigError(f"{field_name} must not be empty")
    return tuple(cleaned)


def _clean_string(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise AgentLexiconConfigError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise AgentLexiconConfigError(f"{field_name} must not be empty")
    return cleaned


__all__ = [
    "AgentLexiconConfig",
    "AgentLexiconConfigError",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_CONFIG_TEXT",
    "DEFAULT_SCAN_EXCLUDE_GLOBS",
    "DEFAULT_SCAN_PATHS",
    "ScanConfig",
    "effective_exclude_globs",
    "effective_include_globs",
    "effective_max_file_bytes",
    "effective_scan_paths",
    "init_project_config",
    "load_project_config",
    "project_config_path",
]
