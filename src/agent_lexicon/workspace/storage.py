"""Workspace storage boundary for Agent Lexicon.

The local workspace uses SQLite by default, but workflow code should depend on
this small boundary rather than on sqlite3 directly. Future deployments can add
another WorkspaceStore implementation without changing resolver, review, or CLI
logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable


class WorkspaceStorageError(ValueError):
    """Raised when a workspace storage backend cannot be selected."""


class WorkspaceStorageBackend(str, Enum):
    """Supported workspace storage backends."""

    SQLITE = "sqlite"


DEFAULT_WORKSPACE_STORAGE_BACKEND = WorkspaceStorageBackend.SQLITE


@dataclass(frozen=True, slots=True)
class WorkspaceStorageConfig:
    """Storage settings for a workspace handle.

    v0.7 keeps SQLite as the only built-in implementation. The config object is
    intentionally backend-neutral so a future PostgreSQL store can reuse the
    same call sites without changing the local CLI workflow.
    """

    backend: WorkspaceStorageBackend | str = DEFAULT_WORKSPACE_STORAGE_BACKEND
    workspace_dir: str = ".agent-lexicon"
    database_name: str = "agent_lexicon.db"

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend", normalize_workspace_storage_backend(self.backend))
        object.__setattr__(self, "workspace_dir", _clean_local_name(self.workspace_dir, field_name="workspace_dir"))
        object.__setattr__(self, "database_name", _clean_local_name(self.database_name, field_name="database_name"))

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable storage config."""
        return {
            "backend": self.backend.value,
            "workspace_dir": self.workspace_dir,
            "database_name": self.database_name,
        }


@runtime_checkable
class WorkspaceStore(Protocol):
    """Protocol implemented by workspace persistence backends.

    The protocol mirrors the methods that workflow, web review, and snapshot
    code use today. It keeps storage-specific details behind one boundary while
    preserving the existing SQLite-backed behavior.
    """

    root: Path
    db_path: Path

    def ensure_schema(self) -> None: ...
    def summary(self) -> Any: ...
    def store_documents(self, documents: Iterable[Any]) -> int: ...
    def store_ingest_report(self, report: Any) -> int: ...
    def store_candidates(self, candidates: Iterable[Any]) -> int: ...
    def store_candidate_report(self, report: Any) -> int: ...
    def store_evidence_packs(self, packs: Iterable[Any]) -> int: ...
    def store_evidence_report(self, report: Any) -> int: ...
    def save_review_decision(self, normalized_surface: str, decision: Any, *, note: str = "", reviewer: str = "local") -> Any: ...
    def append_decision_record(
        self,
        *,
        actor: str,
        action: Any,
        subject: str,
        input_text: str,
        result: str,
        rule_id: str,
        lexicon_snapshot_ref: str = "",
        lexicon_fingerprint: str = "",
        payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Any: ...
    def list_decision_records(self, *, action: Any | None = None, actor: str | None = None, limit: int | None = None) -> tuple[Any, ...]: ...
    def export_decision_records_jsonl(self, output_path: str | Path | None = None, *, action: Any | None = None, actor: str | None = None) -> str: ...
    def list_review_events(self, *, decision: Any | None = None, limit: int | None = None) -> tuple[Any, ...]: ...
    def export_review_events_jsonl(self, output_path: str | Path | None = None, *, decision: Any | None = None) -> str: ...
    def store_snapshot_record(self, snapshot: Any) -> Any: ...
    def list_snapshots(self, *, limit: int = 20) -> tuple[Any, ...]: ...
    def list_review_items(self, *, limit: int = 100) -> tuple[Any, ...]: ...
    def get_review_item(self, normalized_surface: str) -> Any | None: ...


def normalize_workspace_storage_backend(value: WorkspaceStorageBackend | str) -> WorkspaceStorageBackend:
    """Normalize a user-facing storage backend value."""
    if isinstance(value, WorkspaceStorageBackend):
        return value
    cleaned = str(value).strip().lower().replace("_", "-")
    if not cleaned:
        raise WorkspaceStorageError("workspace storage backend must not be empty")
    for backend in WorkspaceStorageBackend:
        if cleaned == backend.value:
            return backend
    supported = ", ".join(backend.value for backend in WorkspaceStorageBackend)
    raise WorkspaceStorageError(f"unsupported workspace storage backend: {value!r}; supported backends: {supported}")


def require_supported_workspace_storage(value: WorkspaceStorageBackend | str) -> WorkspaceStorageBackend:
    """Return the normalized backend when it is available in this build."""
    backend = normalize_workspace_storage_backend(value)
    if backend is not WorkspaceStorageBackend.SQLITE:  # pragma: no cover - future backend guard
        raise WorkspaceStorageError(f"workspace storage backend is not available in this build: {backend.value}")
    return backend


def _clean_local_name(value: str, *, field_name: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise WorkspaceStorageError(f"{field_name} must not be empty")
    if "/" in cleaned or "\\" in cleaned:
        raise WorkspaceStorageError(f"{field_name} must be a local name, not a path")
    return cleaned


__all__ = [
    "DEFAULT_WORKSPACE_STORAGE_BACKEND",
    "WorkspaceStorageBackend",
    "WorkspaceStorageConfig",
    "WorkspaceStorageError",
    "WorkspaceStore",
    "normalize_workspace_storage_backend",
    "require_supported_workspace_storage",
]
