"""Local ingest helpers for Agent Lexicon."""

from __future__ import annotations

from .local import (
    DEFAULT_EXCLUDE_DIRS,
    DEFAULT_EXCLUDE_GLOBS,
    DEFAULT_INCLUDE_GLOBS,
    DEFAULT_MAX_FILE_BYTES,
    IngestDocument,
    IngestSourceKind,
    LocalIngestError,
    LocalIngestReport,
    classify_source_kind,
    discover_local_files,
    ingest_local_paths,
    read_local_document,
)

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
