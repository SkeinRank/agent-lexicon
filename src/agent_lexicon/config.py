"""Repository configuration for Agent Lexicon workflows.

The configuration file keeps repository scan behavior close to the project
instead of requiring long CLI flags in every local run or CI job.
"""

from __future__ import annotations

import fnmatch
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
from agent_lexicon.ingest.local import (
    DEFAULT_INCLUDE_GLOBS,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_RESPECT_GITIGNORE,
)


DEFAULT_CONFIG_PATH = ".agent-lexicon/config.yaml"
DEFAULT_SCAN_PATHS: tuple[str, ...] = ("README.md", "docs", "src", "app", "packages", "lib", "services")
DEFAULT_SCAN_EXCLUDE_GLOBS: tuple[str, ...] = (
    ".agent-lexicon/**",
    "lexicon/**",
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
    "target/**",
    "coverage/**",
    "vendor/**",
    "**/generated/**",
    "**/migrations/**",
    "**/alembic/**",
    "**/*.lock",
    "**/*.min.js",
    "**/*.map",
)

DEFAULT_CONFIG_TEXT = """scan:
  paths:
    - README.md
    - docs
    - src
    - app
    - packages
    - lib
    - services
  include:
    # Documentation and agent instructions
    - README
    - README.*
    - AGENTS.md
    - CLAUDE.md
    - SKILL.md
    - CHANGELOG
    - CHANGELOG.*
    - docs/*.md
    - docs/*.mdx
    - docs/*.rst
    - docs/*.txt
    - docs/*.json
    - docs/*.yaml
    - docs/*.yml
    - docs/**/*.md
    - docs/**/*.mdx
    - docs/**/*.rst
    - docs/**/*.txt
    - docs/**/*.json
    - docs/**/*.yaml
    - docs/**/*.yml

    # Common implementation languages and project config
    - "**/*.py"
    - "**/*.pyi"
    - "**/*.js"
    - "**/*.jsx"
    - "**/*.mjs"
    - "**/*.cjs"
    - "**/*.ts"
    - "**/*.tsx"
    - "**/*.java"
    - "**/*.kt"
    - "**/*.kts"
    - "**/*.scala"
    - "**/*.go"
    - "**/*.rs"
    - "**/*.c"
    - "**/*.h"
    - "**/*.cc"
    - "**/*.cpp"
    - "**/*.cxx"
    - "**/*.hh"
    - "**/*.hpp"
    - "**/*.cs"
    - "**/*.swift"
    - "**/*.m"
    - "**/*.mm"
    - "**/*.dart"
    - "**/*.rb"
    - "**/*.php"
    - "**/*.lua"
    - "**/*.r"
    - "**/*.pl"
    - "**/*.pm"
    - "**/*.ex"
    - "**/*.exs"
    - "**/*.erl"
    - "**/*.hrl"
    - "**/*.clj"
    - "**/*.cljs"
    - "**/*.sh"
    - "**/*.bash"
    - "**/*.zsh"
    - "**/*.sql"
    - "**/*.tf"
    - "**/*.hcl"
    - "**/*.graphql"
    - "**/*.gql"
    - "**/*.proto"
    - "**/*.json"
    - "**/*.yaml"
    - "**/*.yml"
    - "**/*.toml"
    - "**/*.ini"
    - "**/*.cfg"
    - "**/*.conf"
    - "**/*.env"
    - Dockerfile
    - Containerfile
    - Makefile
    - "*.md"
    - "*.mdx"
    - "*.rst"
    - "*.txt"
    - "*.json"
    - "*.yaml"
    - "*.yml"
    - "*.toml"
  exclude:
    - .agent-lexicon/**
    - lexicon/**
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
    - target/**
    - coverage/**
    - vendor/**
    - "**/generated/**"
    - "**/migrations/**"
    - "**/alembic/**"
    - "**/*.lock"
    - "**/*.min.js"
    - "**/*.map"
  respect_gitignore: true
  max_file_bytes: 1000000

# Optional: activate lexicon scopes by repository path.
# First matching rule wins. Files that do not match a rule use all scopes.
# scope_bindings:
#   - paths: ["pkg/kubelet/**"]
#     scopes: [numa]
#   - paths: ["**"]
#     scopes: [core]
"""

class AgentLexiconConfigError(ValueError):
    """Raised when an Agent Lexicon repository config is invalid."""


@dataclass(frozen=True, slots=True)
class ScanConfig:
    """Repository scan defaults."""

    paths: tuple[str, ...] = DEFAULT_SCAN_PATHS
    include: tuple[str, ...] = DEFAULT_INCLUDE_GLOBS
    exclude: tuple[str, ...] = DEFAULT_SCAN_EXCLUDE_GLOBS
    respect_gitignore: bool = DEFAULT_RESPECT_GITIGNORE
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES

    def __post_init__(self) -> None:
        object.__setattr__(self, "paths", _clean_string_tuple(self.paths, field_name="scan.paths"))
        object.__setattr__(self, "include", _clean_string_tuple(self.include, field_name="scan.include"))
        object.__setattr__(self, "exclude", _clean_string_tuple(self.exclude, field_name="scan.exclude"))
        object.__setattr__(self, "respect_gitignore", _coerce_bool(self.respect_gitignore, field_name="scan.respect_gitignore"))
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
            "respect_gitignore": self.respect_gitignore,
            "max_file_bytes": self.max_file_bytes,
        }


@dataclass(frozen=True, slots=True)
class ScopeBinding:
    """Path rule that activates a subset of lexicon scopes.

    Bindings are evaluated in order. The first rule matching a relative file
    path wins. Files with no matching rule keep the default behavior: all scopes
    are active.
    """

    paths: tuple[str, ...]
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "paths", _clean_string_tuple(self.paths, field_name="scope_bindings.paths"))
        object.__setattr__(self, "scopes", _clean_string_tuple(self.scopes, field_name="scope_bindings.scopes"))

    def matches(self, path: str | Path) -> bool:
        """Return whether this binding applies to a repository-relative path."""
        normalized = _normalize_repo_path(path)
        return any(_path_matches_scope_binding(normalized, pattern) for pattern in self.paths)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable scope binding."""
        return {
            "paths": list(self.paths),
            "scopes": list(self.scopes),
        }



@dataclass(frozen=True, slots=True)
class AgentLexiconConfig:
    """Project-level Agent Lexicon configuration."""

    scan: ScanConfig = field(default_factory=ScanConfig)
    scope_bindings: tuple[ScopeBinding, ...] = ()
    path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.scan, ScanConfig):
            raise AgentLexiconConfigError("scan must be a ScanConfig")
        if not isinstance(self.scope_bindings, tuple):
            object.__setattr__(self, "scope_bindings", tuple(self.scope_bindings))
        for binding in self.scope_bindings:
            if not isinstance(binding, ScopeBinding):
                raise AgentLexiconConfigError("scope_bindings must contain ScopeBinding objects")
        if self.path is not None:
            object.__setattr__(self, "path", _clean_string(str(self.path), field_name="path"))
        if not isinstance(self.metadata, Mapping):
            raise AgentLexiconConfigError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable project config."""
        return {
            "scan": self.scan.to_dict(),
            "scope_bindings": [binding.to_dict() for binding in self.scope_bindings],
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
        respect_gitignore=_optional_bool(scan_payload.get("respect_gitignore"), default=DEFAULT_RESPECT_GITIGNORE, field_name="scan.respect_gitignore"),
        max_file_bytes=int(scan_payload.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)),
    )
    scope_bindings = _optional_scope_bindings(payload.get("scope_bindings"))
    return AgentLexiconConfig(scan=scan, scope_bindings=scope_bindings, path=str(path), metadata={"source": "file"})


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




def effective_respect_gitignore(
    cli_value: bool | None,
    config: AgentLexiconConfig,
) -> bool:
    """Return CLI gitignore behavior when provided, otherwise config default."""
    if cli_value is not None:
        return bool(cli_value)
    return bool(config.scan.respect_gitignore)

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




def scopes_for_path(config: AgentLexiconConfig, path: str | Path) -> tuple[str, ...] | None:
    """Return active scopes for a path, or None when all scopes should be active."""
    for binding in config.scope_bindings:
        if binding.matches(path):
            return binding.scopes
    return None


def validate_scope_bindings(config: AgentLexiconConfig, known_scope_ids: Iterable[str]) -> None:
    """Validate that configured scope bindings only reference declared scopes."""
    known = set(known_scope_ids)
    for binding in config.scope_bindings:
        for scope_id in binding.scopes:
            if scope_id not in known:
                raise AgentLexiconConfigError(f"scope_bindings reference unknown scope {scope_id!r}")


def _optional_scope_bindings(value: Any) -> tuple[ScopeBinding, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping) or isinstance(value, str) or not isinstance(value, Iterable):
        raise AgentLexiconConfigError("scope_bindings must be a list of mappings")
    bindings: list[ScopeBinding] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise AgentLexiconConfigError(f"scope_bindings[{index}] must be a mapping")
        paths = _optional_string_sequence(item.get("paths"), default=(), field_name=f"scope_bindings[{index}].paths")
        scopes = _optional_string_sequence(item.get("scopes"), default=(), field_name=f"scope_bindings[{index}].scopes")
        bindings.append(ScopeBinding(paths=paths, scopes=scopes))
    return tuple(bindings)


def _normalize_repo_path(path: str | Path) -> str:
    normalized = str(path).replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _path_matches_scope_binding(path: str, pattern: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/").strip()
    if normalized_pattern.startswith("/"):
        normalized_pattern = normalized_pattern[1:]
    if not normalized_pattern:
        return False
    if normalized_pattern == "**":
        return True
    return fnmatch.fnmatch(path, normalized_pattern) or fnmatch.fnmatch(Path(path).name, normalized_pattern)



def _optional_bool(value: Any, *, default: bool, field_name: str) -> bool:
    if value is None:
        return bool(default)
    return _coerce_bool(value, field_name=field_name)


def _coerce_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    raise AgentLexiconConfigError(f"{field_name} must be a boolean")

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
    "ScopeBinding",
    "effective_exclude_globs",
    "effective_include_globs",
    "effective_max_file_bytes",
    "effective_respect_gitignore",
    "effective_scan_paths",
    "scopes_for_path",
    "validate_scope_bindings",
    "init_project_config",
    "load_project_config",
    "project_config_path",
]
