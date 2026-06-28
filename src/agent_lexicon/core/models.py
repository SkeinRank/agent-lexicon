"""Core data models for Agent Lexicon.

The models in this module are intentionally lightweight and dependency-free.
They define the shared vocabulary objects used by the runtime, local review
workflow, and future integrations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class AgentLexiconModelError(ValueError):
    """Raised when a core model receives invalid data."""


class EvidenceKind(str, Enum):
    """How an evidence span supports a terminology decision."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    CONTEXT = "context"


class ProposalKind(str, Enum):
    """Supported terminology proposal categories."""

    TERM_CANDIDATE = "term_candidate"
    ALIAS_CANDIDATE = "alias_candidate"
    DEPRECATED_SURFACE = "deprecated_surface"
    AMBIGUITY = "ambiguity"
    CANONICAL_MIGRATION = "canonical_migration"


class ProposalStatus(str, Enum):
    """Review lifecycle state for a local proposal candidate."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class RiskLevel(str, Enum):
    """Risk level used to decide whether a proposal can be auto-handled."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise AgentLexiconModelError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise AgentLexiconModelError(f"{field_name} must not be empty")
    return cleaned


def _clean_optional_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _clean_text(value, field_name=field_name)


def _clean_tuple(values: tuple[str, ...] | list[str], *, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise AgentLexiconModelError(f"{field_name} must be a tuple or list of strings")
    return tuple(_clean_text(value, field_name=f"{field_name} item") for value in values)


def _clean_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise AgentLexiconModelError("metadata must be a mapping")
    cleaned: dict[str, Any] = {}
    for key, value in metadata.items():
        cleaned[_clean_text(str(key), field_name="metadata key")] = value
    return cleaned


def _enum_value(value: Enum | str) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


@dataclass(frozen=True, slots=True)
class Scope:
    """A boundary where terminology has a specific meaning.

    Examples include ``billing``, ``api``, ``project.docs``, or ``team.search``.
    """

    id: str
    label: str | None = None
    description: str | None = None
    parents: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _clean_text(self.id, field_name="scope id"))
        object.__setattr__(self, "label", _clean_optional_text(self.label, field_name="scope label"))
        object.__setattr__(self, "description", _clean_optional_text(self.description, field_name="scope description"))
        object.__setattr__(self, "parents", _clean_tuple(self.parents, field_name="scope parents"))
        object.__setattr__(self, "metadata", _clean_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "parents": list(self.parents),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Alias:
    """A surface form that can refer to a canonical term."""

    surface: str
    term_id: str
    scopes: tuple[str, ...] = ()
    case_sensitive: bool = False
    deprecated: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="alias surface"))
        object.__setattr__(self, "term_id", _clean_text(self.term_id, field_name="alias term_id"))
        object.__setattr__(self, "scopes", _clean_tuple(self.scopes, field_name="alias scopes"))
        if not isinstance(self.case_sensitive, bool):
            raise AgentLexiconModelError("alias case_sensitive must be a boolean")
        if not isinstance(self.deprecated, bool):
            raise AgentLexiconModelError("alias deprecated must be a boolean")
        object.__setattr__(self, "metadata", _clean_metadata(self.metadata))

    def normalized_surface(self) -> str:
        """Return the surface normalized for case-insensitive matching."""
        return self.surface if self.case_sensitive else self.surface.casefold()

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "term_id": self.term_id,
            "scopes": list(self.scopes),
            "case_sensitive": self.case_sensitive,
            "deprecated": self.deprecated,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """A source-backed snippet used to justify a terminology decision."""

    source_path: str
    snippet: str
    kind: EvidenceKind = EvidenceKind.CONTEXT
    start_line: int | None = None
    end_line: int | None = None
    source_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_path", _clean_text(self.source_path, field_name="evidence source_path"))
        object.__setattr__(self, "snippet", _clean_text(self.snippet, field_name="evidence snippet"))
        object.__setattr__(self, "kind", EvidenceKind(_enum_value(self.kind)))
        object.__setattr__(self, "source_id", _clean_optional_text(self.source_id, field_name="evidence source_id"))
        if self.start_line is not None and self.start_line < 1:
            raise AgentLexiconModelError("evidence start_line must be greater than 0")
        if self.end_line is not None and self.end_line < 1:
            raise AgentLexiconModelError("evidence end_line must be greater than 0")
        if self.start_line is not None and self.end_line is not None and self.end_line < self.start_line:
            raise AgentLexiconModelError("evidence end_line must be greater than or equal to start_line")
        object.__setattr__(self, "metadata", _clean_metadata(self.metadata))

    def location(self) -> str:
        """Return a compact human-readable source location."""
        if self.start_line is None:
            return self.source_path
        if self.end_line is None or self.end_line == self.start_line:
            return f"{self.source_path}:{self.start_line}"
        return f"{self.source_path}:{self.start_line}-{self.end_line}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "snippet": self.snippet,
            "kind": self.kind.value,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "source_id": self.source_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Term:
    """A canonical domain term and its known surface forms."""

    id: str
    canonical: str
    description: str | None = None
    aliases: tuple[Alias, ...] = ()
    scopes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    deprecated: bool = False
    evidence: tuple[EvidenceSpan, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _clean_text(self.id, field_name="term id"))
        object.__setattr__(self, "canonical", _clean_text(self.canonical, field_name="term canonical"))
        object.__setattr__(self, "description", _clean_optional_text(self.description, field_name="term description"))
        object.__setattr__(self, "scopes", _clean_tuple(self.scopes, field_name="term scopes"))
        object.__setattr__(self, "tags", _clean_tuple(self.tags, field_name="term tags"))
        if not isinstance(self.deprecated, bool):
            raise AgentLexiconModelError("term deprecated must be a boolean")
        if not isinstance(self.aliases, tuple):
            object.__setattr__(self, "aliases", tuple(self.aliases))
        if not isinstance(self.evidence, tuple):
            object.__setattr__(self, "evidence", tuple(self.evidence))
        for alias in self.aliases:
            if not isinstance(alias, Alias):
                raise AgentLexiconModelError("term aliases must contain Alias objects")
            if alias.term_id != self.id:
                raise AgentLexiconModelError("alias term_id must match the owning term id")
        for evidence_span in self.evidence:
            if not isinstance(evidence_span, EvidenceSpan):
                raise AgentLexiconModelError("term evidence must contain EvidenceSpan objects")
        object.__setattr__(self, "metadata", _clean_metadata(self.metadata))

    def surfaces(self, *, include_deprecated: bool = True) -> tuple[str, ...]:
        """Return the canonical surface plus alias surfaces."""
        alias_surfaces = tuple(
            alias.surface
            for alias in self.aliases
            if include_deprecated or not alias.deprecated
        )
        return (self.canonical, *alias_surfaces)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical": self.canonical,
            "description": self.description,
            "aliases": [alias.to_dict() for alias in self.aliases],
            "scopes": list(self.scopes),
            "tags": list(self.tags),
            "deprecated": self.deprecated,
            "evidence": [evidence_span.to_dict() for evidence_span in self.evidence],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ProposalCandidate:
    """A reviewable terminology change suggested by local analysis or an agent."""

    id: str
    kind: ProposalKind
    surface: str
    status: ProposalStatus = ProposalStatus.PENDING
    candidate_term_id: str | None = None
    target_term_id: str | None = None
    confidence: float | None = None
    risk: RiskLevel = RiskLevel.MEDIUM
    scopes: tuple[str, ...] = ()
    evidence: tuple[EvidenceSpan, ...] = ()
    rationale: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _clean_text(self.id, field_name="proposal id"))
        object.__setattr__(self, "kind", ProposalKind(_enum_value(self.kind)))
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="proposal surface"))
        object.__setattr__(self, "status", ProposalStatus(_enum_value(self.status)))
        object.__setattr__(self, "candidate_term_id", _clean_optional_text(self.candidate_term_id, field_name="proposal candidate_term_id"))
        object.__setattr__(self, "target_term_id", _clean_optional_text(self.target_term_id, field_name="proposal target_term_id"))
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise AgentLexiconModelError("proposal confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "risk", RiskLevel(_enum_value(self.risk)))
        object.__setattr__(self, "scopes", _clean_tuple(self.scopes, field_name="proposal scopes"))
        if not isinstance(self.evidence, tuple):
            object.__setattr__(self, "evidence", tuple(self.evidence))
        for evidence_span in self.evidence:
            if not isinstance(evidence_span, EvidenceSpan):
                raise AgentLexiconModelError("proposal evidence must contain EvidenceSpan objects")
        object.__setattr__(self, "rationale", _clean_optional_text(self.rationale, field_name="proposal rationale"))
        object.__setattr__(self, "metadata", _clean_metadata(self.metadata))

    def needs_human_review(self) -> bool:
        """Return whether this proposal should be reviewed by a person."""
        return self.status in {ProposalStatus.PENDING, ProposalStatus.NEEDS_REVIEW} and self.risk != RiskLevel.LOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "surface": self.surface,
            "status": self.status.value,
            "candidate_term_id": self.candidate_term_id,
            "target_term_id": self.target_term_id,
            "confidence": self.confidence,
            "risk": self.risk.value,
            "scopes": list(self.scopes),
            "evidence": [evidence_span.to_dict() for evidence_span in self.evidence],
            "rationale": self.rationale,
            "metadata": dict(self.metadata),
        }


__all__ = [
    "AgentLexiconModelError",
    "Alias",
    "EvidenceKind",
    "EvidenceSpan",
    "ProposalCandidate",
    "ProposalKind",
    "ProposalStatus",
    "RiskLevel",
    "Scope",
    "Term",
]
