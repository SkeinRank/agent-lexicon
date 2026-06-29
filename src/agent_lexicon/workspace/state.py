"""SQLite workspace state for local Agent Lexicon workflows.

The workspace database is a local cache for ingest, scout, evidence, and local
review data. It is safe to delete and rebuild from project files; team source of
truth remains lexicon files, review exports, and published snapshots.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from agent_lexicon.ingest import IngestDocument, LocalIngestReport
from agent_lexicon.scout import EvidencePack, EvidencePackReport, ScoutCandidate, ScoutCandidateReport


class WorkspaceError(ValueError):
    """Raised when the local workspace cannot be opened or updated."""


class ReviewDecisionStatus(str, Enum):
    """Local review status for one proposal candidate."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"
    NEEDS_SPLIT = "needs_split"


class ReviewEventType(str, Enum):
    """Review event type stored in the workspace event stream."""

    DECISION_SAVED = "review_decision_saved"


SCHEMA_VERSION = 4
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
    review_decision_count: int = 0
    review_event_count: int = 0
    snapshot_count: int = 0

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable workspace summary."""
        return {
            "root": self.root,
            "db_path": self.db_path,
            "schema_version": self.schema_version,
            "document_count": self.document_count,
            "candidate_count": self.candidate_count,
            "evidence_pack_count": self.evidence_pack_count,
            "review_decision_count": self.review_decision_count,
            "review_event_count": self.review_event_count,
            "snapshot_count": self.snapshot_count,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceReviewDecision:
    """Saved local review decision for one candidate surface."""

    normalized_surface: str
    decision: ReviewDecisionStatus
    note: str
    reviewer: str
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_surface", _clean_text(self.normalized_surface, field_name="normalized_surface"))
        object.__setattr__(self, "decision", ReviewDecisionStatus(self.decision.value if isinstance(self.decision, ReviewDecisionStatus) else str(self.decision)))
        object.__setattr__(self, "note", self.note.strip() if isinstance(self.note, str) else "")
        object.__setattr__(self, "reviewer", _clean_text(self.reviewer, field_name="reviewer"))
        object.__setattr__(self, "created_at", _clean_text(self.created_at, field_name="created_at"))
        object.__setattr__(self, "updated_at", _clean_text(self.updated_at, field_name="updated_at"))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable review decision."""
        return {
            "normalized_surface": self.normalized_surface,
            "decision": self.decision.value,
            "note": self.note,
            "reviewer": self.reviewer,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceReviewEvent:
    """Append-only local review event for JSONL exports."""

    event_id: str
    event_type: ReviewEventType
    normalized_surface: str
    decision: ReviewDecisionStatus
    note: str
    reviewer: str
    created_at: str
    candidate_snapshot: Mapping[str, Any] = field(default_factory=dict)
    evidence_snapshot: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _clean_text(self.event_id, field_name="event_id"))
        object.__setattr__(self, "event_type", ReviewEventType(self.event_type.value if isinstance(self.event_type, ReviewEventType) else str(self.event_type)))
        object.__setattr__(self, "normalized_surface", _clean_text(self.normalized_surface, field_name="normalized_surface"))
        object.__setattr__(self, "decision", ReviewDecisionStatus(self.decision.value if isinstance(self.decision, ReviewDecisionStatus) else str(self.decision)))
        object.__setattr__(self, "note", self.note.strip() if isinstance(self.note, str) else "")
        object.__setattr__(self, "reviewer", _clean_text(self.reviewer, field_name="reviewer"))
        object.__setattr__(self, "created_at", _clean_text(self.created_at, field_name="created_at"))
        if not isinstance(self.candidate_snapshot, Mapping):
            raise WorkspaceError("candidate_snapshot must be a mapping")
        if not isinstance(self.evidence_snapshot, Mapping):
            raise WorkspaceError("evidence_snapshot must be a mapping")
        if not isinstance(self.metadata, Mapping):
            raise WorkspaceError("metadata must be a mapping")
        object.__setattr__(self, "candidate_snapshot", dict(self.candidate_snapshot))
        object.__setattr__(self, "evidence_snapshot", dict(self.evidence_snapshot))
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable review event."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "normalized_surface": self.normalized_surface,
            "decision": self.decision.value,
            "note": self.note,
            "reviewer": self.reviewer,
            "created_at": self.created_at,
            "candidate_snapshot": dict(self.candidate_snapshot),
            "evidence_snapshot": dict(self.evidence_snapshot),
            "metadata": dict(self.metadata),
        }

    def to_json_line(self) -> str:
        """Return this review event as one JSONL row."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshotRecord:
    """Stored local snapshot publication metadata."""

    snapshot_id: str
    created_at: str
    term_count: int
    accepted_count: int
    generated_term_count: int
    skipped_count: int
    output_path: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _clean_text(self.snapshot_id, field_name="snapshot_id"))
        object.__setattr__(self, "created_at", _clean_text(self.created_at, field_name="created_at"))
        object.__setattr__(self, "output_path", _clean_text(self.output_path, field_name="output_path"))
        if self.term_count < 0:
            raise WorkspaceError("term_count must be greater than or equal to 0")
        if self.accepted_count < 0:
            raise WorkspaceError("accepted_count must be greater than or equal to 0")
        if self.generated_term_count < 0:
            raise WorkspaceError("generated_term_count must be greater than or equal to 0")
        if self.skipped_count < 0:
            raise WorkspaceError("skipped_count must be greater than or equal to 0")
        if not isinstance(self.payload, Mapping):
            raise WorkspaceError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot record."""
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "term_count": self.term_count,
            "accepted_count": self.accepted_count,
            "generated_term_count": self.generated_term_count,
            "skipped_count": self.skipped_count,
            "output_path": self.output_path,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class WorkspaceReviewItem:
    """Candidate, evidence, and local review state for the proposal inbox."""

    normalized_surface: str
    surface: str
    candidate_kind: str
    score: float
    jargon_score: float
    background_penalty: float
    occurrence_count: int
    document_count: int
    positive_count: int
    negative_count: int
    candidate_payload: Mapping[str, Any] = field(default_factory=dict)
    evidence_payload: Mapping[str, Any] = field(default_factory=dict)
    review_decision: WorkspaceReviewDecision | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_surface", _clean_text(self.normalized_surface, field_name="normalized_surface"))
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="surface"))
        object.__setattr__(self, "candidate_kind", _clean_text(self.candidate_kind, field_name="candidate_kind"))
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(self, "jargon_score", float(self.jargon_score))
        object.__setattr__(self, "background_penalty", float(self.background_penalty))
        if self.occurrence_count < 0:
            raise WorkspaceError("occurrence_count must be greater than or equal to 0")
        if self.document_count < 0:
            raise WorkspaceError("document_count must be greater than or equal to 0")
        if self.positive_count < 0:
            raise WorkspaceError("positive_count must be greater than or equal to 0")
        if self.negative_count < 0:
            raise WorkspaceError("negative_count must be greater than or equal to 0")
        if not isinstance(self.candidate_payload, Mapping):
            raise WorkspaceError("candidate_payload must be a mapping")
        if not isinstance(self.evidence_payload, Mapping):
            raise WorkspaceError("evidence_payload must be a mapping")
        object.__setattr__(self, "candidate_payload", dict(self.candidate_payload))
        object.__setattr__(self, "evidence_payload", dict(self.evidence_payload))
        if self.review_decision is not None and not isinstance(self.review_decision, WorkspaceReviewDecision):
            raise WorkspaceError("review_decision must be a WorkspaceReviewDecision")

    @property
    def review_status(self) -> str:
        """Return a display-safe review status."""
        if self.review_decision is None:
            return "unreviewed"
        return self.review_decision.decision.value

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable review item."""
        return {
            "normalized_surface": self.normalized_surface,
            "surface": self.surface,
            "candidate_kind": self.candidate_kind,
            "score": self.score,
            "jargon_score": self.jargon_score,
            "background_penalty": self.background_penalty,
            "occurrence_count": self.occurrence_count,
            "document_count": self.document_count,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "candidate_payload": dict(self.candidate_payload),
            "evidence_payload": dict(self.evidence_payload),
            "review_status": self.review_status,
            "review_decision": self.review_decision.to_dict() if self.review_decision else None,
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
                review_decision_count=_table_count(connection, "review_decisions"),
                review_event_count=_table_count(connection, "review_events"),
                snapshot_count=_table_count(connection, "snapshots"),
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

    def save_review_decision(
        self,
        normalized_surface: str,
        decision: ReviewDecisionStatus | str,
        *,
        note: str = "",
        reviewer: str = "local",
    ) -> WorkspaceReviewDecision:
        """Save or replace a local review decision for one candidate."""
        normalized = _clean_text(normalized_surface, field_name="normalized_surface")
        status = ReviewDecisionStatus(decision.value if isinstance(decision, ReviewDecisionStatus) else str(decision))
        reviewer_value = _clean_text(reviewer, field_name="reviewer")
        note_value = note.strip() if isinstance(note, str) else ""
        self.ensure_schema()
        now = _utc_now()
        with _connect(self.db_path) as connection:
            existing = connection.execute(
                "SELECT created_at FROM review_decisions WHERE normalized_surface = ?",
                (normalized,),
            ).fetchone()
            created_at = str(existing[0]) if existing is not None else now
            connection.execute(
                """
                INSERT OR REPLACE INTO review_decisions (
                    normalized_surface,
                    decision,
                    note,
                    reviewer,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized, status.value, note_value, reviewer_value, created_at, now),
            )
            event = _build_review_event(
                connection,
                normalized_surface=normalized,
                decision=status,
                note=note_value,
                reviewer=reviewer_value,
                created_at=now,
            )
            connection.execute(
                """
                INSERT INTO review_events (
                    event_id,
                    event_type,
                    normalized_surface,
                    decision,
                    note,
                    reviewer,
                    created_at,
                    event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type.value,
                    event.normalized_surface,
                    event.decision.value,
                    event.note,
                    event.reviewer,
                    event.created_at,
                    _json_dumps(event.to_dict()),
                ),
            )
        return WorkspaceReviewDecision(
            normalized_surface=normalized,
            decision=status,
            note=note_value,
            reviewer=reviewer_value,
            created_at=created_at,
            updated_at=now,
        )

    def list_review_decisions(self) -> tuple[WorkspaceReviewDecision, ...]:
        """Return saved local review decisions ordered by update time."""
        self.ensure_schema()
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT normalized_surface, decision, note, reviewer, created_at, updated_at
                FROM review_decisions
                ORDER BY updated_at DESC, normalized_surface ASC
                """
            ).fetchall()
        return tuple(_review_decision_from_row(row) for row in rows)

    def list_review_events(
        self,
        *,
        decision: ReviewDecisionStatus | str | None = None,
        limit: int | None = None,
    ) -> tuple[WorkspaceReviewEvent, ...]:
        """Return append-only local review events in chronological order."""
        if limit is not None and limit < 1:
            raise WorkspaceError("limit must be greater than 0")
        status_value: str | None = None
        if decision is not None:
            status_value = ReviewDecisionStatus(decision.value if isinstance(decision, ReviewDecisionStatus) else str(decision)).value
        self.ensure_schema()
        query = """
            SELECT event_json
            FROM review_events
        """
        params: list[object] = []
        if status_value is not None:
            query += " WHERE decision = ?"
            params.append(status_value)
        query += " ORDER BY created_at ASC, rowid ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with _connect(self.db_path) as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_review_event_from_payload(str(row[0])) for row in rows)

    def export_review_events_jsonl(
        self,
        output_path: str | Path | None = None,
        *,
        decision: ReviewDecisionStatus | str | None = None,
    ) -> str:
        """Export local review events as JSONL and optionally write them to a file."""
        events = self.list_review_events(decision=decision)
        content = "".join(f"{event.to_json_line()}\n" for event in events)
        if output_path is not None:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return content


    def store_snapshot_record(self, snapshot: Any) -> WorkspaceSnapshotRecord:
        """Store metadata for one published local snapshot."""
        snapshot_id = _clean_text(str(getattr(snapshot, "snapshot_id")), field_name="snapshot_id")
        created_at = _clean_text(str(getattr(snapshot, "created_at")), field_name="created_at")
        output_path = _clean_text(str(getattr(snapshot, "output_path")), field_name="output_path")
        payload = snapshot.to_dict(include_lexicon=False)
        record = WorkspaceSnapshotRecord(
            snapshot_id=snapshot_id,
            created_at=created_at,
            term_count=int(getattr(snapshot, "term_count")),
            accepted_count=int(getattr(snapshot, "accepted_count")),
            generated_term_count=int(getattr(snapshot, "generated_term_count")),
            skipped_count=int(getattr(snapshot, "skipped_count")),
            output_path=output_path,
            payload=payload,
        )
        self.ensure_schema()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO snapshots (
                    snapshot_id,
                    created_at,
                    term_count,
                    accepted_count,
                    generated_term_count,
                    skipped_count,
                    output_path,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.snapshot_id,
                    record.created_at,
                    record.term_count,
                    record.accepted_count,
                    record.generated_term_count,
                    record.skipped_count,
                    record.output_path,
                    _json_dumps(record.to_dict()),
                ),
            )
        return record

    def list_snapshots(self, *, limit: int = 20) -> tuple[WorkspaceSnapshotRecord, ...]:
        """Return local snapshot records ordered from newest to oldest."""
        if limit < 1:
            raise WorkspaceError("limit must be greater than 0")
        self.ensure_schema()
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT snapshot_id, created_at, term_count, accepted_count,
                       generated_term_count, skipped_count, output_path, payload_json
                FROM snapshots
                ORDER BY created_at DESC, snapshot_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(_snapshot_record_from_row(row) for row in rows)

    def list_review_items(self, *, limit: int = 100) -> tuple[WorkspaceReviewItem, ...]:
        """Return candidate/evidence rows for the local proposal inbox."""
        if limit < 1:
            raise WorkspaceError("limit must be greater than 0")
        self.ensure_schema()
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    c.normalized_surface,
                    c.surface,
                    c.candidate_kind,
                    c.score,
                    c.jargon_score,
                    c.background_penalty,
                    c.occurrence_count,
                    c.document_count,
                    c.payload_json,
                    COALESCE(e.positive_count, 0),
                    COALESCE(e.negative_count, 0),
                    COALESCE(e.payload_json, '{}'),
                    d.decision,
                    d.note,
                    d.reviewer,
                    d.created_at,
                    d.updated_at
                FROM candidates c
                LEFT JOIN evidence_packs e ON e.normalized_surface = c.normalized_surface
                LEFT JOIN review_decisions d ON d.normalized_surface = c.normalized_surface
                ORDER BY
                    CASE WHEN d.decision IS NULL THEN 0 ELSE 1 END ASC,
                    c.score DESC,
                    c.surface ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(_review_item_from_row(row) for row in rows)

    def get_review_item(self, normalized_surface: str) -> WorkspaceReviewItem | None:
        """Return one candidate/evidence row for the proposal inbox."""
        normalized = _clean_text(normalized_surface, field_name="normalized_surface")
        self.ensure_schema()
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT
                    c.normalized_surface,
                    c.surface,
                    c.candidate_kind,
                    c.score,
                    c.jargon_score,
                    c.background_penalty,
                    c.occurrence_count,
                    c.document_count,
                    c.payload_json,
                    COALESCE(e.positive_count, 0),
                    COALESCE(e.negative_count, 0),
                    COALESCE(e.payload_json, '{}'),
                    d.decision,
                    d.note,
                    d.reviewer,
                    d.created_at,
                    d.updated_at
                FROM candidates c
                LEFT JOIN evidence_packs e ON e.normalized_surface = c.normalized_surface
                LEFT JOIN review_decisions d ON d.normalized_surface = c.normalized_surface
                WHERE c.normalized_surface = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return _review_item_from_row(row)


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


def save_review_decision(
    state: WorkspaceState,
    normalized_surface: str,
    decision: ReviewDecisionStatus | str,
    *,
    note: str = "",
    reviewer: str = "local",
) -> WorkspaceReviewDecision:
    """Save a local review decision in a workspace."""
    return state.save_review_decision(normalized_surface, decision, note=note, reviewer=reviewer)


def list_review_events(
    state: WorkspaceState,
    *,
    decision: ReviewDecisionStatus | str | None = None,
    limit: int | None = None,
) -> tuple[WorkspaceReviewEvent, ...]:
    """Return append-only local review events from a workspace."""
    return state.list_review_events(decision=decision, limit=limit)


def export_review_events_jsonl(
    state: WorkspaceState,
    output_path: str | Path | None = None,
    *,
    decision: ReviewDecisionStatus | str | None = None,
) -> str:
    """Export local review events from a workspace as JSONL."""
    return state.export_review_events_jsonl(output_path, decision=decision)


def list_snapshots(state: WorkspaceState, *, limit: int = 20) -> tuple[WorkspaceSnapshotRecord, ...]:
    """Return local snapshot records from a workspace."""
    return state.list_snapshots(limit=limit)


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS workspace_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        INSERT OR REPLACE INTO workspace_meta (key, value)
        VALUES ('schema_version', '{SCHEMA_VERSION}');

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

        CREATE TABLE IF NOT EXISTS review_decisions (
            normalized_surface TEXT PRIMARY KEY,
            decision TEXT NOT NULL,
            note TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_review_decisions_decision ON review_decisions (decision);
        CREATE INDEX IF NOT EXISTS idx_review_decisions_updated_at ON review_decisions (updated_at DESC);

        CREATE TABLE IF NOT EXISTS review_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            normalized_surface TEXT NOT NULL,
            decision TEXT NOT NULL,
            note TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            created_at TEXT NOT NULL,
            event_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_review_events_surface ON review_events (normalized_surface);
        CREATE INDEX IF NOT EXISTS idx_review_events_decision ON review_events (decision);
        CREATE INDEX IF NOT EXISTS idx_review_events_created_at ON review_events (created_at ASC);

        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            term_count INTEGER NOT NULL,
            accepted_count INTEGER NOT NULL,
            generated_term_count INTEGER NOT NULL,
            skipped_count INTEGER NOT NULL,
            output_path TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_created_at ON snapshots (created_at DESC);
        """
    )


def _read_schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT value FROM workspace_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        return 0
    return int(row[0])


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    if table_name not in {"documents", "candidates", "evidence_packs", "review_decisions", "review_events", "snapshots"}:
        raise WorkspaceError(f"unsupported workspace table: {table_name}")
    row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0]) if row is not None else 0


def _snapshot_record_from_row(row: sqlite3.Row | tuple[Any, ...]) -> WorkspaceSnapshotRecord:
    return WorkspaceSnapshotRecord(
        snapshot_id=str(row[0]),
        created_at=str(row[1]),
        term_count=int(row[2]),
        accepted_count=int(row[3]),
        generated_term_count=int(row[4]),
        skipped_count=int(row[5]),
        output_path=str(row[6]),
        payload=_json_loads_mapping(str(row[7])),
    )


def _review_item_from_row(row: sqlite3.Row | tuple[Any, ...]) -> WorkspaceReviewItem:
    decision = None
    if row[12] is not None:
        decision = WorkspaceReviewDecision(
            normalized_surface=str(row[0]),
            decision=str(row[12]),
            note=str(row[13] or ""),
            reviewer=str(row[14] or "local"),
            created_at=str(row[15] or ""),
            updated_at=str(row[16] or ""),
        )
    return WorkspaceReviewItem(
        normalized_surface=str(row[0]),
        surface=str(row[1]),
        candidate_kind=str(row[2]),
        score=float(row[3]),
        jargon_score=float(row[4]),
        background_penalty=float(row[5]),
        occurrence_count=int(row[6]),
        document_count=int(row[7]),
        candidate_payload=_json_loads_mapping(str(row[8])),
        positive_count=int(row[9]),
        negative_count=int(row[10]),
        evidence_payload=_json_loads_mapping(str(row[11])),
        review_decision=decision,
    )


def _build_review_event(
    connection: sqlite3.Connection,
    *,
    normalized_surface: str,
    decision: ReviewDecisionStatus,
    note: str,
    reviewer: str,
    created_at: str,
) -> WorkspaceReviewEvent:
    candidate_row = connection.execute(
        "SELECT payload_json FROM candidates WHERE normalized_surface = ?",
        (normalized_surface,),
    ).fetchone()
    evidence_row = connection.execute(
        "SELECT payload_json FROM evidence_packs WHERE normalized_surface = ?",
        (normalized_surface,),
    ).fetchone()
    candidate_snapshot = _json_loads_mapping(str(candidate_row[0])) if candidate_row is not None else {}
    evidence_snapshot = _json_loads_mapping(str(evidence_row[0])) if evidence_row is not None else {}
    return WorkspaceReviewEvent(
        event_id=f"review_evt_{uuid.uuid4().hex}",
        event_type=ReviewEventType.DECISION_SAVED,
        normalized_surface=normalized_surface,
        decision=decision,
        note=note,
        reviewer=reviewer,
        created_at=created_at,
        candidate_snapshot=candidate_snapshot,
        evidence_snapshot=evidence_snapshot,
        metadata={"workspace_schema_version": SCHEMA_VERSION},
    )


def _review_event_from_payload(payload: str) -> WorkspaceReviewEvent:
    data = _json_loads_mapping(payload)
    candidate_snapshot = data.get("candidate_snapshot", {})
    evidence_snapshot = data.get("evidence_snapshot", {})
    metadata = data.get("metadata", {})
    return WorkspaceReviewEvent(
        event_id=str(data.get("event_id", "")),
        event_type=str(data.get("event_type", ReviewEventType.DECISION_SAVED.value)),
        normalized_surface=str(data.get("normalized_surface", "")),
        decision=str(data.get("decision", "")),
        note=str(data.get("note", "")),
        reviewer=str(data.get("reviewer", "local")),
        created_at=str(data.get("created_at", "")),
        candidate_snapshot=candidate_snapshot if isinstance(candidate_snapshot, Mapping) else {},
        evidence_snapshot=evidence_snapshot if isinstance(evidence_snapshot, Mapping) else {},
        metadata=metadata if isinstance(metadata, Mapping) else {},
    )


def _review_decision_from_row(row: sqlite3.Row | tuple[Any, ...]) -> WorkspaceReviewDecision:
    return WorkspaceReviewDecision(
        normalized_surface=str(row[0]),
        decision=str(row[1]),
        note=str(row[2]),
        reviewer=str(row[3]),
        created_at=str(row[4]),
        updated_at=str(row[5]),
    )


def _json_dumps(payload: Mapping[str, object] | dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json_loads_mapping(payload: str) -> dict[str, Any]:
    try:
        loaded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise WorkspaceError(f"invalid workspace JSON payload: {exc}") from exc
    if not isinstance(loaded, dict):
        raise WorkspaceError("workspace JSON payload must be an object")
    return loaded


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


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise WorkspaceError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise WorkspaceError(f"{field_name} must not be empty")
    return cleaned
