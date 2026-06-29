"""SQLite workspace state for local Agent Lexicon workflows.

The workspace database is a local cache for ingest, scout, and evidence data.
It is safe to delete and rebuild from project files; team source of truth remains
lexicon files, review exports, and published snapshots.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from agent_lexicon.ingest import IngestDocument, LocalIngestReport
from agent_lexicon.scout import EvidencePack, EvidencePackReport, ScoutCandidate, ScoutCandidateReport


class WorkspaceError(ValueError):
    """Raised when the local workspace cannot be opened or updated."""


SCHEMA_VERSION = 1
DEFAULT_WORKSPACE_DIR = ".agent-lexicon"
DEFAULT_DATABASE_NAME = "agent_lexicon.db"


@dataclass(frozen=True, slots=True)
class WorkspaceSummary:
    """Compact status information for a local workspace database."""

    root: str
    db_path: str
    schema_version: int
    document_count: int
    candidate_count: int
    evidence_pack_count: int

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable workspace summary."""
        return {
            "root": self.root,
            "db_path": self.db_path,
            "schema_version": self.schema_version,
            "document_count": self.document_count,
            "candidate_count": self.candidate_count,
            "evidence_pack_count": self.evidence_pack_count,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceState:
    """Handle for a local SQLite workspace database."""

    root: Path
    db_path: Path

    def ensure_schema(self) -> None:
        """Create or migrate the workspace schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.db_path) as connection:
            _create_schema(connection)

    def summary(self) -> WorkspaceSummary:
        """Return current workspace table counts."""
        if not self.db_path.exists():
            raise WorkspaceError(f"workspace database does not exist: {self.db_path}")
        with _connect(self.db_path) as connection:
            _create_schema(connection)
            schema_version = _read_schema_version(connection)
            return WorkspaceSummary(
                root=str(self.root),
                db_path=str(self.db_path),
                schema_version=schema_version,
                document_count=_table_count(connection, "documents"),
                candidate_count=_table_count(connection, "candidates"),
                evidence_pack_count=_table_count(connection, "evidence_packs"),
            )

    def store_documents(self, documents: Iterable[IngestDocument]) -> int:
        """Store ingested documents in the local workspace."""
        document_tuple = tuple(documents)
        for document in document_tuple:
            if not isinstance(document, IngestDocument):
                raise WorkspaceError("documents must contain IngestDocument objects")
        self.ensure_schema()
        now = _utc_now()
        with _connect(self.db_path) as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO documents (
                    relative_path,
                    source_path,
                    kind,
                    sha256,
                    size_bytes,
                    line_count,
                    text,
                    metadata_json,
                    payload_json,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        document.relative_path,
                        document.source_path,
                        document.kind.value,
                        document.sha256,
                        document.size_bytes,
                        document.line_count,
                        document.text,
                        _json_dumps(dict(document.metadata)),
                        _json_dumps(document.to_dict(include_text=True)),
                        now,
                    )
                    for document in document_tuple
                ],
            )
        return len(document_tuple)

    def store_ingest_report(self, report: LocalIngestReport) -> int:
        """Store all documents from a local ingest report."""
        if not isinstance(report, LocalIngestReport):
            raise WorkspaceError("report must be a LocalIngestReport")
        return self.store_documents(report.documents)

    def store_candidates(self, candidates: Iterable[ScoutCandidate]) -> int:
        """Store scout candidates in the local workspace."""
        candidate_tuple = tuple(candidates)
        for candidate in candidate_tuple:
            if not isinstance(candidate, ScoutCandidate):
                raise WorkspaceError("candidates must contain ScoutCandidate objects")
        self.ensure_schema()
        now = _utc_now()
        with _connect(self.db_path) as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO candidates (
                    normalized_surface,
                    surface,
                    candidate_kind,
                    score,
                    jargon_score,
                    background_penalty,
                    occurrence_count,
                    document_count,
                    payload_json,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        candidate.normalized_surface,
                        candidate.surface,
                        candidate.kind.value,
                        candidate.score,
                        candidate.jargon_score,
                        candidate.background_penalty,
                        candidate.occurrence_count,
                        candidate.document_count,
                        _json_dumps(candidate.to_dict()),
                        now,
                    )
                    for candidate in candidate_tuple
                ],
            )
        return len(candidate_tuple)

    def store_candidate_report(self, report: ScoutCandidateReport) -> int:
        """Store all candidates from a scout candidate report."""
        if not isinstance(report, ScoutCandidateReport):
            raise WorkspaceError("report must be a ScoutCandidateReport")
        return self.store_candidates(report.candidates)

    def store_evidence_packs(self, packs: Iterable[EvidencePack]) -> int:
        """Store evidence packs in the local workspace."""
        pack_tuple = tuple(packs)
        for pack in pack_tuple:
            if not isinstance(pack, EvidencePack):
                raise WorkspaceError("packs must contain EvidencePack objects")
        self.ensure_schema()
        now = _utc_now()
        with _connect(self.db_path) as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO evidence_packs (
                    normalized_surface,
                    surface,
                    candidate_kind,
                    score,
                    positive_count,
                    negative_count,
                    payload_json,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        pack.normalized_surface,
                        pack.surface,
                        pack.candidate_kind.value,
                        pack.score,
                        pack.positive_count,
                        pack.negative_count,
                        _json_dumps(pack.to_dict()),
                        now,
                    )
                    for pack in pack_tuple
                ],
            )
        return len(pack_tuple)

    def store_evidence_report(self, report: EvidencePackReport) -> int:
        """Store all packs from an evidence report."""
        if not isinstance(report, EvidencePackReport):
            raise WorkspaceError("report must be an EvidencePackReport")
        return self.store_evidence_packs(report.packs)


def workspace_path(
    root: str | Path = ".",
    *,
    workspace_dir: str = DEFAULT_WORKSPACE_DIR,
    database_name: str = DEFAULT_DATABASE_NAME,
) -> Path:
    """Return the SQLite database path for a local workspace root."""
    clean_workspace_dir = _clean_name(workspace_dir, field_name="workspace_dir")
    clean_database_name = _clean_name(database_name, field_name="database_name")
    return Path(root).resolve() / clean_workspace_dir / clean_database_name


def init_workspace(
    root: str | Path = ".",
    *,
    workspace_dir: str = DEFAULT_WORKSPACE_DIR,
    database_name: str = DEFAULT_DATABASE_NAME,
    reset: bool = False,
) -> WorkspaceState:
    """Create a local SQLite workspace and return its state handle."""
    root_path = Path(root).resolve()
    db_path = workspace_path(root_path, workspace_dir=workspace_dir, database_name=database_name)
    if reset and db_path.exists():
        db_path.unlink()
    state = WorkspaceState(root=root_path, db_path=db_path)
    state.ensure_schema()
    return state


def open_workspace(
    root: str | Path = ".",
    *,
    workspace_dir: str = DEFAULT_WORKSPACE_DIR,
    database_name: str = DEFAULT_DATABASE_NAME,
    create: bool = True,
) -> WorkspaceState:
    """Open a local SQLite workspace, creating it by default."""
    root_path = Path(root).resolve()
    db_path = workspace_path(root_path, workspace_dir=workspace_dir, database_name=database_name)
    state = WorkspaceState(root=root_path, db_path=db_path)
    if not db_path.exists():
        if not create:
            raise WorkspaceError(f"workspace database does not exist: {db_path}")
        state.ensure_schema()
    return state


def store_ingest_report(state: WorkspaceState, report: LocalIngestReport) -> int:
    """Store an ingest report in a workspace."""
    return state.store_ingest_report(report)


def store_candidate_report(state: WorkspaceState, report: ScoutCandidateReport) -> int:
    """Store a candidate report in a workspace."""
    return state.store_candidate_report(report)


def store_evidence_report(state: WorkspaceState, report: EvidencePackReport) -> int:
    """Store an evidence report in a workspace."""
    return state.store_evidence_report(report)


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS workspace_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        INSERT OR REPLACE INTO workspace_meta (key, value)
        VALUES ('schema_version', '1');

        CREATE TABLE IF NOT EXISTS documents (
            relative_path TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            kind TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            line_count INTEGER NOT NULL,
            text TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents (sha256);
        CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents (kind);

        CREATE TABLE IF NOT EXISTS candidates (
            normalized_surface TEXT PRIMARY KEY,
            surface TEXT NOT NULL,
            candidate_kind TEXT NOT NULL,
            score REAL NOT NULL,
            jargon_score REAL NOT NULL,
            background_penalty REAL NOT NULL,
            occurrence_count INTEGER NOT NULL,
            document_count INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_candidates_score ON candidates (score DESC);
        CREATE INDEX IF NOT EXISTS idx_candidates_kind ON candidates (candidate_kind);

        CREATE TABLE IF NOT EXISTS evidence_packs (
            normalized_surface TEXT PRIMARY KEY,
            surface TEXT NOT NULL,
            candidate_kind TEXT NOT NULL,
            score REAL NOT NULL,
            positive_count INTEGER NOT NULL,
            negative_count INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_evidence_packs_score ON evidence_packs (score DESC);
        CREATE INDEX IF NOT EXISTS idx_evidence_packs_kind ON evidence_packs (candidate_kind);
        """
    )


def _read_schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT value FROM workspace_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        return 0
    return int(row[0])


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    if table_name not in {"documents", "candidates", "evidence_packs"}:
        raise WorkspaceError(f"unsupported workspace table: {table_name}")
    row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0]) if row is not None else 0


def _json_dumps(payload: Mapping[str, object] | dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_name(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise WorkspaceError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise WorkspaceError(f"{field_name} must not be empty")
    if "/" in cleaned or "\\" in cleaned:
        raise WorkspaceError(f"{field_name} must be a local name, not a path")
    return cleaned
