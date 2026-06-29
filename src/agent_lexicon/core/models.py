"""Core data models for Agent Lexicon.

The models in this module are intentionally lightweight and dependency-free.
They define the shared vocabulary objects used by the runtime, local review
workflow, and future integrations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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


class ResolutionStatus(str, Enum):
    """Runtime term resolution status."""

    UNKNOWN = "unknown"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"


class ResolutionAction(str, Enum):
    """Recommended runtime action after resolving terminology."""

    NO_MATCH = "no_match"
    USE_TERMS = "use_terms"
    ASK_CLARIFICATION = "ask_clarification"


class ToolGuardStatus(str, Enum):
    """Safety status for a requested tool call."""

    ALLOWED = "allowed"
    BLOCKED = "blocked"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_MATCH = "no_match"


class ToolGuardAction(str, Enum):
    """Recommended action after checking a requested tool call."""

    PROCEED = "proceed"
    BLOCK = "block"
    ASK_CLARIFICATION = "ask_clarification"



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
    tools: tuple[str, ...] = ()
    deprecated: bool = False
    evidence: tuple[EvidenceSpan, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _clean_text(self.id, field_name="term id"))
        object.__setattr__(self, "canonical", _clean_text(self.canonical, field_name="term canonical"))
        object.__setattr__(self, "description", _clean_optional_text(self.description, field_name="term description"))
        object.__setattr__(self, "scopes", _clean_tuple(self.scopes, field_name="term scopes"))
        object.__setattr__(self, "tags", _clean_tuple(self.tags, field_name="term tags"))
        object.__setattr__(self, "tools", _clean_tuple(self.tools, field_name="term tools"))
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
            "tools": list(self.tools),
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


@dataclass(frozen=True, slots=True)
class ResolutionMatch:
    """A serializable surface occurrence used by a resolution decision."""

    term_id: str
    surface: str
    matched_text: str
    start: int
    end: int
    kind: str
    scopes: tuple[str, ...] = ()
    deprecated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "term_id", _clean_text(self.term_id, field_name="resolution match term_id"))
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="resolution match surface"))
        object.__setattr__(self, "matched_text", _clean_text(self.matched_text, field_name="resolution match matched_text"))
        object.__setattr__(self, "kind", _clean_text(self.kind, field_name="resolution match kind"))
        object.__setattr__(self, "scopes", _clean_tuple(self.scopes, field_name="resolution match scopes"))
        if self.start < 0:
            raise AgentLexiconModelError("resolution match start must be greater than or equal to 0")
        if self.end <= self.start:
            raise AgentLexiconModelError("resolution match end must be greater than start")
        if not isinstance(self.deprecated, bool):
            raise AgentLexiconModelError("resolution match deprecated must be a boolean")

    @property
    def length(self) -> int:
        """Return the number of characters covered by the match."""
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "term_id": self.term_id,
            "surface": self.surface,
            "matched_text": self.matched_text,
            "start": self.start,
            "end": self.end,
            "kind": self.kind,
            "scopes": list(self.scopes),
            "deprecated": self.deprecated,
        }


@dataclass(frozen=True, slots=True)
class ResolutionCandidate:
    """A canonical term candidate returned by the runtime resolver."""

    term_id: str
    canonical: str
    description: str | None = None
    scopes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    matched_surfaces: tuple[str, ...] = ()
    match_count: int = 0
    evidence_count: int = 0
    deprecated: bool = False
    score: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "term_id", _clean_text(self.term_id, field_name="resolution candidate term_id"))
        object.__setattr__(self, "canonical", _clean_text(self.canonical, field_name="resolution candidate canonical"))
        object.__setattr__(self, "description", _clean_optional_text(self.description, field_name="resolution candidate description"))
        object.__setattr__(self, "scopes", _clean_tuple(self.scopes, field_name="resolution candidate scopes"))
        object.__setattr__(self, "tags", _clean_tuple(self.tags, field_name="resolution candidate tags"))
        object.__setattr__(self, "matched_surfaces", _clean_tuple(self.matched_surfaces, field_name="resolution candidate matched_surfaces"))
        if self.match_count < 0:
            raise AgentLexiconModelError("resolution candidate match_count must be greater than or equal to 0")
        if self.evidence_count < 0:
            raise AgentLexiconModelError("resolution candidate evidence_count must be greater than or equal to 0")
        if not isinstance(self.deprecated, bool):
            raise AgentLexiconModelError("resolution candidate deprecated must be a boolean")
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(self, "metadata", _clean_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "term_id": self.term_id,
            "canonical": self.canonical,
            "description": self.description,
            "scopes": list(self.scopes),
            "tags": list(self.tags),
            "matched_surfaces": list(self.matched_surfaces),
            "match_count": self.match_count,
            "evidence_count": self.evidence_count,
            "deprecated": self.deprecated,
            "score": self.score,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ResolutionDecision:
    """Resolver output that can be used by agents, CLI, and tests."""

    text: str
    status: ResolutionStatus
    action: ResolutionAction
    candidates: tuple[ResolutionCandidate, ...] = ()
    matches: tuple[ResolutionMatch, ...] = ()
    message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise AgentLexiconModelError("resolution decision text must be a string")
        object.__setattr__(self, "status", ResolutionStatus(_enum_value(self.status)))
        object.__setattr__(self, "action", ResolutionAction(_enum_value(self.action)))
        if not isinstance(self.candidates, tuple):
            object.__setattr__(self, "candidates", tuple(self.candidates))
        if not isinstance(self.matches, tuple):
            object.__setattr__(self, "matches", tuple(self.matches))
        for candidate in self.candidates:
            if not isinstance(candidate, ResolutionCandidate):
                raise AgentLexiconModelError("resolution decision candidates must contain ResolutionCandidate objects")
        for match in self.matches:
            if not isinstance(match, ResolutionMatch):
                raise AgentLexiconModelError("resolution decision matches must contain ResolutionMatch objects")
        object.__setattr__(self, "message", _clean_optional_text(self.message, field_name="resolution decision message"))
        object.__setattr__(self, "metadata", _clean_metadata(self.metadata))

    @property
    def primary_term_id(self) -> str | None:
        """Return the resolved canonical id when there is exactly one candidate."""
        if self.status != ResolutionStatus.RESOLVED or len(self.candidates) != 1:
            return None
        return self.candidates[0].term_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "status": self.status.value,
            "action": self.action.value,
            "primary_term_id": self.primary_term_id,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "matches": [match.to_dict() for match in self.matches],
            "message": self.message,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ToolGuardDecision:
    """Decision returned when checking whether a tool call is safe."""

    text: str
    tool_name: str
    status: ToolGuardStatus
    action: ToolGuardAction
    resolution: ResolutionDecision
    reason: str
    allowed_tool_names: tuple[str, ...] = ()
    matched_term_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise AgentLexiconModelError("tool guard decision text must be a string")
        object.__setattr__(self, "tool_name", _clean_text(self.tool_name, field_name="tool guard tool_name"))
        object.__setattr__(self, "status", ToolGuardStatus(_enum_value(self.status)))
        object.__setattr__(self, "action", ToolGuardAction(_enum_value(self.action)))
        if not isinstance(self.resolution, ResolutionDecision):
            raise AgentLexiconModelError("tool guard decision resolution must be a ResolutionDecision")
        object.__setattr__(self, "reason", _clean_text(self.reason, field_name="tool guard reason"))
        object.__setattr__(self, "allowed_tool_names", _clean_tuple(self.allowed_tool_names, field_name="tool guard allowed_tool_names"))
        object.__setattr__(self, "matched_term_ids", _clean_tuple(self.matched_term_ids, field_name="tool guard matched_term_ids"))
        object.__setattr__(self, "metadata", _clean_metadata(self.metadata))

    @property
    def is_allowed(self) -> bool:
        """Return whether the tool call may proceed."""
        return self.status in {ToolGuardStatus.ALLOWED, ToolGuardStatus.NO_MATCH}

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "action": self.action.value,
            "is_allowed": self.is_allowed,
            "reason": self.reason,
            "allowed_tool_names": list(self.allowed_tool_names),
            "matched_term_ids": list(self.matched_term_ids),
            "resolution": self.resolution.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Lexicon:
    """A validated terminology document loaded from JSON or YAML."""

    version: str = "1"
    scopes: tuple[Scope, ...] = ()
    terms: tuple[Term, ...] = ()
    proposals: tuple[ProposalCandidate, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", _clean_text(str(self.version), field_name="lexicon version"))
        if self.version != "1":
            raise AgentLexiconModelError("lexicon version must be '1'")
        if not isinstance(self.scopes, tuple):
            object.__setattr__(self, "scopes", tuple(self.scopes))
        if not isinstance(self.terms, tuple):
            object.__setattr__(self, "terms", tuple(self.terms))
        if not isinstance(self.proposals, tuple):
            object.__setattr__(self, "proposals", tuple(self.proposals))
        for scope in self.scopes:
            if not isinstance(scope, Scope):
                raise AgentLexiconModelError("lexicon scopes must contain Scope objects")
        for term in self.terms:
            if not isinstance(term, Term):
                raise AgentLexiconModelError("lexicon terms must contain Term objects")
        for proposal in self.proposals:
            if not isinstance(proposal, ProposalCandidate):
                raise AgentLexiconModelError("lexicon proposals must contain ProposalCandidate objects")
        object.__setattr__(self, "metadata", _clean_metadata(self.metadata))

    @classmethod
    def from_file(cls, path: str | Path, *, document_format: str | None = None) -> "Lexicon":
        """Load a lexicon document from a JSON or YAML file."""
        from .loader import load_lexicon

        return load_lexicon(path, document_format=document_format)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Lexicon":
        """Build a validated lexicon from a mapping."""
        from .loader import lexicon_from_dict

        return lexicon_from_dict(payload)

    def get_term(self, term_id: str) -> Term | None:
        """Return a term by canonical id, or ``None`` when it is unknown."""
        cleaned_term_id = _clean_text(term_id, field_name="term id")
        for term in self.terms:
            if term.id == cleaned_term_id:
                return term
        return None

    def get_scope(self, scope_id: str) -> Scope | None:
        """Return a scope by id, or ``None`` when it is unknown."""
        cleaned_scope_id = _clean_text(scope_id, field_name="scope id")
        for scope in self.scopes:
            if scope.id == cleaned_scope_id:
                return scope
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "scopes": [scope.to_dict() for scope in self.scopes],
            "terms": [term.to_dict() for term in self.terms],
            "proposals": [proposal.to_dict() for proposal in self.proposals],
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
    "Lexicon",
    "ResolutionAction",
    "ResolutionCandidate",
    "ResolutionDecision",
    "ResolutionMatch",
    "ResolutionStatus",
    "RiskLevel",
    "Scope",
    "Term",
    "ToolGuardAction",
    "ToolGuardDecision",
    "ToolGuardStatus",
]
