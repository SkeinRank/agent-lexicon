"""Local ingest helpers for Agent Lexicon."""

from __future__ import annotations

from .local import (
    DEFAULT_EXCLUDE_DIRS,
    DEFAULT_DOCUMENTATION_GLOBS,
    DEFAULT_EXCLUDE_GLOBS,
    DEFAULT_INCLUDE_GLOBS,
    DEFAULT_LANGUAGE_GLOBS,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_RESPECT_GITIGNORE,
    GitIgnoreRule,
    IngestDocument,
    IngestSourceKind,
    LocalIngestError,
    LocalIngestReport,
    classify_source_kind,
    discover_local_files,
    ingest_local_paths,
    load_gitignore_rules,
    path_matches_gitignore,
    read_local_document,
    relative_path_matches_gitignore,
)

__all__ = [
    "DEFAULT_EXCLUDE_DIRS",
    "DEFAULT_DOCUMENTATION_GLOBS",
    "DEFAULT_EXCLUDE_GLOBS",
    "DEFAULT_INCLUDE_GLOBS",
    "DEFAULT_LANGUAGE_GLOBS",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_RESPECT_GITIGNORE",
    "GitIgnoreRule",
    "IngestDocument",
    "IngestSourceKind",
    "LocalIngestError",
    "LocalIngestReport",
    "classify_source_kind",
    "discover_local_files",
    "ingest_local_paths",
    "load_gitignore_rules",
    "path_matches_gitignore",
    "read_local_document",
    "relative_path_matches_gitignore",
]
