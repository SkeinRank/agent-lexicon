"""Local file ingestion for Agent Lexicon.

This module reads local repository files into small, deterministic document
objects. It is intentionally dependency-free so it can run in local agent
wrappers, CI jobs, and small projects without a service backend.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping


class LocalIngestError(ValueError):
    """Raised when local ingest receives an invalid path or option."""


class IngestSourceKind(str, Enum):
    """Best-effort source classification for a local document."""

    MARKDOWN = "markdown"
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    TEXT = "text"
    CODE = "code"
    UNKNOWN = "unknown"


DEFAULT_INCLUDE_GLOBS: tuple[str, ...] = (
    "README",
    "README.*",
    "AGENTS.md",
    "CLAUDE.md",
    "SKILL.md",
    "CHANGELOG",
    "CHANGELOG.*",
    "docs/*.md",
    "docs/*.txt",
    "docs/*.json",
    "docs/*.yaml",
    "docs/*.yml",
    "docs/**/*.md",
    "docs/**/*.txt",
    "docs/**/*.json",
    "docs/**/*.yaml",
    "docs/**/*.yml",
    "src/*.py",
    "src/*.pyi",
    "src/*.js",
    "src/*.jsx",
    "src/*.ts",
    "src/*.tsx",
    "src/*.md",
    "src/*.json",
    "src/*.yaml",
    "src/*.yml",
    "src/**/*.py",
    "src/**/*.pyi",
    "src/**/*.js",
    "src/**/*.jsx",
    "src/**/*.ts",
    "src/**/*.tsx",
    "src/**/*.md",
    "src/**/*.json",
    "src/**/*.yaml",
    "src/**/*.yml",
    "*.md",
    "*.txt",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.toml",
)

DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (
    ".agent-lexicon",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "venv",
)

DEFAULT_MAX_FILE_BYTES = 1_000_000

DEFAULT_EXCLUDE_GLOBS: tuple[str, ...] = (
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

_TEXT_EXTENSIONS = {
    ".md",
    ".mdx",
    ".txt",
    ".rst",
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
}


@dataclass(frozen=True, slots=True)
class IngestDocument:
    """One text document read from a local file."""

    source_path: str
    relative_path: str
    text: str
    kind: IngestSourceKind
    size_bytes: int
    line_count: int
    sha256: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_path", _clean_text(self.source_path, field_name="source_path"))
        object.__setattr__(self, "relative_path", _clean_text(self.relative_path, field_name="relative_path"))
        if not isinstance(self.text, str):
            raise LocalIngestError("text must be a string")
        object.__setattr__(self, "kind", IngestSourceKind(_clean_text(self.kind.value if isinstance(self.kind, IngestSourceKind) else str(self.kind), field_name="kind")))
        if self.size_bytes < 0:
            raise LocalIngestError("size_bytes must be greater than or equal to 0")
        if self.line_count < 0:
            raise LocalIngestError("line_count must be greater than or equal to 0")
        object.__setattr__(self, "sha256", _clean_text(self.sha256, field_name="sha256"))
        if not isinstance(self.metadata, Mapping):
            raise LocalIngestError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def preview(self, *, max_chars: int = 160) -> str:
        """Return a compact single-line preview of the document text."""
        if max_chars < 1:
            raise LocalIngestError("max_chars must be greater than 0")
        compact = " ".join(self.text.split())
        if len(compact) <= max_chars:
            return compact
        return f"{compact[: max_chars - 1]}…"

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        """Return a JSON-serializable document representation."""
        payload: dict[str, Any] = {
            "source_path": self.source_path,
            "relative_path": self.relative_path,
            "kind": self.kind.value,
            "size_bytes": self.size_bytes,
            "line_count": self.line_count,
            "sha256": self.sha256,
            "metadata": dict(self.metadata),
        }
        if include_text:
            payload["text"] = self.text
        return payload

    def to_json_line(self, *, include_text: bool = True) -> str:
        """Return this document as one JSONL row."""
        return json.dumps(self.to_dict(include_text=include_text), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class LocalIngestReport:
    """Result returned by local file ingestion."""

    documents: tuple[IngestDocument, ...]
    skipped_paths: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.documents, tuple):
            object.__setattr__(self, "documents", tuple(self.documents))
        for document in self.documents:
            if not isinstance(document, IngestDocument):
                raise LocalIngestError("documents must contain IngestDocument objects")
        object.__setattr__(self, "skipped_paths", tuple(_clean_text(path, field_name="skipped path") for path in self.skipped_paths))
        if not isinstance(self.metadata, Mapping):
            raise LocalIngestError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def document_count(self) -> int:
        """Return the number of ingested documents."""
        return len(self.documents)

    @property
    def total_lines(self) -> int:
        """Return the total number of lines across ingested documents."""
        return sum(document.line_count for document in self.documents)

    @property
    def total_size_bytes(self) -> int:
        """Return the total source byte size across ingested documents."""
        return sum(document.size_bytes for document in self.documents)

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        """Return a JSON-serializable report representation."""
        return {
            "document_count": self.document_count,
            "total_lines": self.total_lines,
            "total_size_bytes": self.total_size_bytes,
            "documents": [document.to_dict(include_text=include_text) for document in self.documents],
            "skipped_paths": list(self.skipped_paths),
            "metadata": dict(self.metadata),
        }


def ingest_local_paths(
    paths: Iterable[str | Path],
    *,
    root: str | Path | None = None,
    include_globs: Iterable[str] | None = None,
    exclude_dirs: Iterable[str] | None = None,
    exclude_globs: Iterable[str] | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> LocalIngestReport:
    """Read local files and directories into a deterministic ingest report.

    Directories are searched recursively with local-project defaults for docs,
    README files, and source files. Individual file paths are read directly when
    they look like text files.
    """
    cleaned_paths = tuple(Path(path) for path in paths)
    if not cleaned_paths:
        raise LocalIngestError("at least one path is required")
    base_root = Path(root).resolve() if root is not None else _common_root(cleaned_paths)
    files = discover_local_files(
        cleaned_paths,
        root=base_root,
        include_globs=include_globs,
        exclude_dirs=exclude_dirs,
        exclude_globs=exclude_globs,
        max_file_bytes=max_file_bytes,
    )
    documents: list[IngestDocument] = []
    skipped_paths: list[str] = []
    for file_path in files:
        try:
            documents.append(read_local_document(file_path, root=base_root, max_file_bytes=max_file_bytes))
        except LocalIngestError:
            skipped_paths.append(_relative_or_absolute(file_path, base_root))
    return LocalIngestReport(
        documents=tuple(documents),
        skipped_paths=tuple(skipped_paths),
        metadata={
            "root": str(base_root),
            "include_globs": list(include_globs or DEFAULT_INCLUDE_GLOBS),
            "exclude_dirs": list(exclude_dirs or DEFAULT_EXCLUDE_DIRS),
            "exclude_globs": list(exclude_globs or DEFAULT_EXCLUDE_GLOBS),
            "max_file_bytes": max_file_bytes,
        },
    )


def discover_local_files(
    paths: Iterable[str | Path],
    *,
    root: str | Path | None = None,
    include_globs: Iterable[str] | None = None,
    exclude_dirs: Iterable[str] | None = None,
    exclude_globs: Iterable[str] | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> tuple[Path, ...]:
    """Return readable local files selected for ingestion."""
    if max_file_bytes < 1:
        raise LocalIngestError("max_file_bytes must be greater than 0")
    cleaned_paths = tuple(Path(path) for path in paths)
    if not cleaned_paths:
        raise LocalIngestError("at least one path is required")
    base_root = Path(root).resolve() if root is not None else _common_root(cleaned_paths)
    includes = tuple(include_globs or DEFAULT_INCLUDE_GLOBS)
    excludes = set(exclude_dirs or DEFAULT_EXCLUDE_DIRS)
    exclude_patterns = tuple(exclude_globs or DEFAULT_EXCLUDE_GLOBS)

    discovered: dict[str, Path] = {}
    for input_path in cleaned_paths:
        path = input_path.resolve()
        if not path.exists():
            raise LocalIngestError(f"local ingest path does not exist: {input_path}")
        if path.is_file():
            if _is_supported_file(path, base_root, includes, max_file_bytes, direct_file=True, exclude_globs=exclude_patterns):
                discovered[str(path)] = path
            continue
        if not path.is_dir():
            continue
        for file_path in _walk_files(path, exclude_dirs=excludes):
            if _is_supported_file(file_path, base_root, includes, max_file_bytes, direct_file=False, exclude_globs=exclude_patterns):
                discovered[str(file_path)] = file_path
    return tuple(sorted(discovered.values(), key=lambda item: _relative_or_absolute(item, base_root)))


def read_local_document(
    path: str | Path,
    *,
    root: str | Path | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> IngestDocument:
    """Read one local text file as an ingest document."""
    if max_file_bytes < 1:
        raise LocalIngestError("max_file_bytes must be greater than 0")
    file_path = Path(path).resolve()
    if not file_path.exists():
        raise LocalIngestError(f"local ingest file does not exist: {path}")
    if not file_path.is_file():
        raise LocalIngestError(f"local ingest path is not a file: {path}")
    size_bytes = file_path.stat().st_size
    if size_bytes > max_file_bytes:
        raise LocalIngestError(f"local ingest file is larger than max_file_bytes: {path}")
    raw = file_path.read_bytes()
    if _looks_binary(raw):
        raise LocalIngestError(f"local ingest file looks binary: {path}")
    text = _decode_text(raw, path=file_path)
    base_root = Path(root).resolve() if root is not None else file_path.parent
    relative_path = _relative_or_absolute(file_path, base_root)
    return IngestDocument(
        source_path=str(file_path),
        relative_path=relative_path,
        text=text,
        kind=classify_source_kind(file_path),
        size_bytes=size_bytes,
        line_count=_line_count(text),
        sha256=hashlib.sha256(raw).hexdigest(),
        metadata={"suffix": file_path.suffix.lower()},
    )


def classify_source_kind(path: str | Path) -> IngestSourceKind:
    """Classify a local source path by extension and conventional filename."""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    name = file_path.name.lower()
    if suffix in {".md", ".mdx", ".rst"} or name in {"readme", "changelog"}:
        return IngestSourceKind.MARKDOWN
    if suffix in {".py", ".pyi"}:
        return IngestSourceKind.PYTHON
    if suffix in {".ts", ".tsx"}:
        return IngestSourceKind.TYPESCRIPT
    if suffix in {".js", ".jsx"}:
        return IngestSourceKind.JAVASCRIPT
    if suffix == ".json":
        return IngestSourceKind.JSON
    if suffix in {".yaml", ".yml"}:
        return IngestSourceKind.YAML
    if suffix == ".toml":
        return IngestSourceKind.TOML
    if suffix == ".txt":
        return IngestSourceKind.TEXT
    if suffix in _TEXT_EXTENSIONS:
        return IngestSourceKind.CODE
    return IngestSourceKind.UNKNOWN


def _common_root(paths: tuple[Path, ...]) -> Path:
    resolved = tuple(path.resolve() for path in paths)
    if len(resolved) == 1:
        path = resolved[0]
        return path if path.is_dir() else path.parent
    parents = tuple(path if path.is_dir() else path.parent for path in resolved)
    common = Path(os.path.commonpath([str(parent) for parent in parents]))
    return common.resolve()


def _walk_files(root: Path, *, exclude_dirs: set[str]) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in exclude_dirs for part in path.parts):
            continue
        yield path.resolve()


def _is_supported_file(
    path: Path,
    root: Path,
    include_globs: tuple[str, ...],
    max_file_bytes: int,
    *,
    direct_file: bool,
    exclude_globs: tuple[str, ...] = (),
) -> bool:
    if path.stat().st_size > max_file_bytes:
        return False
    if _path_matches_exclude_globs(path, root, exclude_globs):
        return False
    if not _looks_like_text_path(path):
        return False
    if direct_file:
        return True
    relative = _relative_or_absolute(path, root)
    normalized = relative.replace("\\", "/")
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in include_globs)


def _path_matches_exclude_globs(path: Path, root: Path, exclude_globs: tuple[str, ...]) -> bool:
    if not exclude_globs:
        return False
    relative = _relative_or_absolute(path, root).replace("\\", "/")
    name = path.name
    parts = set(path.parts)
    for raw_pattern in exclude_globs:
        pattern = str(raw_pattern).strip().replace("\\", "/")
        if not pattern:
            continue
        if pattern.startswith("/"):
            pattern = pattern[1:]
        if pattern.endswith("/"):
            pattern = pattern + "**"
        if fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(name, pattern):
            return True
        if "/" not in pattern and not any(char in pattern for char in "*?[") and pattern in parts:
            return True
    return False


def _looks_like_text_path(path: Path) -> bool:
    if path.suffix.lower() in _TEXT_EXTENSIONS:
        return True
    return path.name in {"README", "CHANGELOG"}


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False
    sample = raw[:4096]
    if b"\x00" in sample:
        return True
    return False


def _decode_text(raw: bytes, *, path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    try:
        return raw.decode("utf-16")
    except UnicodeDecodeError as exc:
        raise LocalIngestError(f"local ingest file is not valid text: {path}") from exc


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise LocalIngestError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise LocalIngestError(f"{field_name} must not be empty")
    return cleaned


__all__ = [
    "DEFAULT_EXCLUDE_DIRS",
    "DEFAULT_EXCLUDE_GLOBS",
    "DEFAULT_INCLUDE_GLOBS",
    "DEFAULT_MAX_FILE_BYTES",
    "IngestDocument",
    "IngestSourceKind",
    "LocalIngestError",
    "LocalIngestReport",
    "classify_source_kind",
    "discover_local_files",
    "ingest_local_paths",
    "read_local_document",
]
