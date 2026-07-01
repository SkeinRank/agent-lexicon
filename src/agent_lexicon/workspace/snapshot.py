"""Publish local workspace review decisions as lexicon snapshots."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from agent_lexicon.core import EvidenceKind, EvidenceSpan, Lexicon, Term, lexicon_runtime_metadata
from agent_lexicon.core.files import atomic_write_text

from .state import ReviewDecisionStatus, WorkspaceError, WorkspaceReviewItem
from .storage import WorkspaceStore


class SnapshotPublishError(ValueError):
    """Raised when a local snapshot cannot be published."""


@dataclass(frozen=True, slots=True)
class PublishedSnapshot:
    """Result returned after publishing accepted local review decisions."""

    snapshot_id: str
    created_at: str
    output_path: str
    lexicon: Lexicon
    accepted_count: int
    generated_term_count: int
    skipped_count: int
    skipped_surfaces: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _clean_text(self.snapshot_id, field_name="snapshot_id"))
        object.__setattr__(self, "created_at", _clean_text(self.created_at, field_name="created_at"))
        object.__setattr__(self, "output_path", _clean_text(self.output_path, field_name="output_path"))
        if not isinstance(self.lexicon, Lexicon):
            raise SnapshotPublishError("lexicon must be a Lexicon")
        if self.accepted_count < 0:
            raise SnapshotPublishError("accepted_count must be greater than or equal to 0")
        if self.generated_term_count < 0:
            raise SnapshotPublishError("generated_term_count must be greater than or equal to 0")
        if self.skipped_count < 0:
            raise SnapshotPublishError("skipped_count must be greater than or equal to 0")
        if not isinstance(self.skipped_surfaces, tuple):
            object.__setattr__(self, "skipped_surfaces", tuple(self.skipped_surfaces))
        object.__setattr__(self, "skipped_surfaces", tuple(_clean_text(surface, field_name="skipped_surface") for surface in self.skipped_surfaces))
        if not isinstance(self.metadata, Mapping):
            raise SnapshotPublishError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def term_count(self) -> int:
        """Return the number of terms in the published lexicon."""
        return len(self.lexicon.terms)

    def to_dict(self, *, include_lexicon: bool = False) -> dict[str, Any]:
        """Return a JSON-serializable snapshot summary."""
        payload: dict[str, Any] = {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "output_path": self.output_path,
            "term_count": self.term_count,
            "accepted_count": self.accepted_count,
            "generated_term_count": self.generated_term_count,
            "skipped_count": self.skipped_count,
            "skipped_surfaces": list(self.skipped_surfaces),
            "metadata": dict(self.metadata),
        }
        if include_lexicon:
            payload["lexicon"] = self.lexicon.to_dict()
        return payload


def publish_local_snapshot(
    state: WorkspaceStore,
    *,
    output_path: str | Path | None = None,
    base_lexicon: Lexicon | None = None,
    snapshot_id: str | None = None,
) -> PublishedSnapshot:
    """Publish accepted local review decisions to a lexicon snapshot JSON file.

    Accepted review items become canonical terms. Rejected, ambiguous, and
    needs-split items remain in the workspace but are not promoted into the
    published lexicon snapshot.
    """
    if not isinstance(state, WorkspaceStore):
        raise SnapshotPublishError("state must implement WorkspaceStore")
    if base_lexicon is not None and not isinstance(base_lexicon, Lexicon):
        raise SnapshotPublishError("base_lexicon must be a Lexicon")

    state.ensure_schema()
    resolved_snapshot_id = _clean_text(snapshot_id, field_name="snapshot_id") if snapshot_id else _new_snapshot_id()
    created_at = _utc_now()
    accepted_items = tuple(
        item for item in state.list_review_items(limit=10_000)
        if item.review_decision is not None and item.review_decision.decision == ReviewDecisionStatus.ACCEPTED
    )
    if not accepted_items:
        raise SnapshotPublishError("no accepted review decisions are available to publish")

    base_terms = tuple(base_lexicon.terms) if base_lexicon is not None else ()
    base_scopes = tuple(base_lexicon.scopes) if base_lexicon is not None else ()
    base_metadata = dict(base_lexicon.metadata) if base_lexicon is not None else {}
    known_term_ids = {term.id for term in base_terms}
    known_surfaces = _known_surfaces(base_lexicon) if base_lexicon is not None else set()

    generated_terms: list[Term] = []
    skipped_surfaces: list[str] = []
    for item in sorted(accepted_items, key=lambda value: value.normalized_surface):
        surface_key = item.surface.casefold()
        if surface_key in known_surfaces:
            skipped_surfaces.append(item.surface)
            continue
        term_id = _unique_term_id(_term_id_from_surface(item.surface), known_term_ids)
        evidence = _evidence_from_item(item, snapshot_id=resolved_snapshot_id)
        generated_terms.append(
            Term(
                id=term_id,
                canonical=item.surface,
                scopes=(),
                tags=("local-scout", item.candidate_kind),
                evidence=evidence,
                metadata={
                    "source": "local_review",
                    "snapshot_id": resolved_snapshot_id,
                    "normalized_surface": item.normalized_surface,
                    "candidate_kind": item.candidate_kind,
                    "score": item.score,
                    "jargon_score": item.jargon_score,
                    "background_penalty": item.background_penalty,
                    "occurrence_count": item.occurrence_count,
                    "document_count": item.document_count,
                    "positive_count": item.positive_count,
                    "negative_count": item.negative_count,
                    "review_decision": item.review_decision.to_dict() if item.review_decision else None,
                },
            )
        )
        known_surfaces.add(surface_key)
        known_term_ids.add(term_id)

    snapshot_metadata = {
        **base_metadata,
        "agent_lexicon_snapshot": {
            "snapshot_id": resolved_snapshot_id,
            "created_at": created_at,
            "source": "local_workspace",
            "accepted_count": len(accepted_items),
            "generated_term_count": len(generated_terms),
            "skipped_count": len(skipped_surfaces),
            "skipped_surfaces": list(skipped_surfaces),
        },
    }
    lexicon = Lexicon(
        version="1",
        scopes=base_scopes,
        terms=(*base_terms, *generated_terms),
        proposals=(),
        metadata=snapshot_metadata,
    )

    resolved_output_path = Path(output_path) if output_path is not None else _default_snapshot_path(state, resolved_snapshot_id)
    atomic_write_text(
        resolved_output_path,
        json.dumps(lexicon.to_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )

    snapshot_runtime_metadata = lexicon_runtime_metadata(lexicon, source_path=resolved_output_path)
    if base_lexicon is not None:
        snapshot_runtime_metadata["base_lexicon_snapshot"] = lexicon_runtime_metadata(base_lexicon)

    snapshot = PublishedSnapshot(
        snapshot_id=resolved_snapshot_id,
        created_at=created_at,
        output_path=str(resolved_output_path),
        lexicon=lexicon,
        accepted_count=len(accepted_items),
        generated_term_count=len(generated_terms),
        skipped_count=len(skipped_surfaces),
        skipped_surfaces=tuple(skipped_surfaces),
        metadata={"base_term_count": len(base_terms), **snapshot_runtime_metadata},
    )
    try:
        state.store_snapshot_record(snapshot)
    except WorkspaceError as exc:
        raise SnapshotPublishError(str(exc)) from exc
    return snapshot


def _evidence_from_item(item: WorkspaceReviewItem, *, snapshot_id: str) -> tuple[EvidenceSpan, ...]:
    evidence_payload = dict(item.evidence_payload)
    snippets = evidence_payload.get("positive_snippets", [])
    evidence: list[EvidenceSpan] = []
    if not isinstance(snippets, list):
        return ()
    for index, snippet in enumerate(snippets):
        if not isinstance(snippet, Mapping):
            continue
        text = str(snippet.get("text", "")).strip()
        source_path = str(snippet.get("document_path", "")).strip()
        if not text or not source_path:
            continue
        start_line = _optional_int(snippet.get("start_line"))
        end_line = _optional_int(snippet.get("end_line"))
        evidence.append(
            EvidenceSpan(
                source_path=source_path,
                snippet=text,
                kind=EvidenceKind.POSITIVE,
                start_line=start_line,
                end_line=end_line,
                metadata={
                    "snapshot_id": snapshot_id,
                    "evidence_index": index,
                    "reason": str(snippet.get("reason", "")).strip(),
                    "matched_surface": str(snippet.get("matched_surface", "")).strip(),
                },
            )
        )
    return tuple(evidence)


def _known_surfaces(lexicon: Lexicon | None) -> set[str]:
    if lexicon is None:
        return set()
    surfaces: set[str] = set()
    for term in lexicon.terms:
        if not term.deprecated:
            surfaces.add(term.canonical.casefold())
        for alias in term.aliases:
            if not alias.deprecated:
                surfaces.add(alias.surface.casefold())
    return surfaces


def _term_id_from_surface(surface: str) -> str:
    cleaned = _clean_text(surface, field_name="surface")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:[._:/-][A-Za-z0-9_]+)*", cleaned):
        return cleaned.replace("/", ".").replace(":", ".").replace("-", "_").casefold()
    tokens = re.findall(r"[A-Za-z0-9]+", cleaned.casefold())
    if not tokens:
        return f"term_{uuid.uuid4().hex[:8]}"
    return "_".join(tokens)


def _unique_term_id(base_term_id: str, known_term_ids: set[str]) -> str:
    candidate = _clean_text(base_term_id, field_name="term_id")
    if candidate not in known_term_ids:
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in known_term_ids:
        suffix += 1
    return f"{candidate}_{suffix}"


def _default_snapshot_path(state: WorkspaceStore, snapshot_id: str) -> Path:
    return state.db_path.parent / "snapshots" / f"{snapshot_id}.json"


def _new_snapshot_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"snapshot_{timestamp}_{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clean_text(value: str | None, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise SnapshotPublishError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise SnapshotPublishError(f"{field_name} must not be empty")
    return cleaned


__all__ = ["PublishedSnapshot", "SnapshotPublishError", "publish_local_snapshot"]
