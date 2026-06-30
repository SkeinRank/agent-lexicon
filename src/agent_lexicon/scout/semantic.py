"""Semantic escalation interfaces for near-miss review.

The default Agent Lexicon runtime stays deterministic and dependency-free. This
module defines the typed handoff point for optional semantic rerankers that may
be added by callers in an offline Scout workflow. The built-in backend is a
no-op: it never changes near-miss suggestions and records that no semantic model
was applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence


class SemanticNearMissError(ValueError):
    """Raised when semantic escalation metadata receives invalid input."""


class SemanticSuggestionSource(str, Enum):
    """Stable source labels for near-miss suggestions."""

    HEURISTIC = "heuristic"
    SEMANTIC = "semantic"
    NONE = "none"


class SemanticEscalationReason(str, Enum):
    """Reasons why a heuristic near-miss item should be escalated."""

    GRAY_ZONE_CONFIDENCE = "gray_zone_confidence"
    WEAK_LEXICAL_BRIDGE = "weak_lexical_bridge"
    SINGLE_FRAGMENT_BRIDGE = "single_fragment_bridge"
    RELATED_FRAGMENT_BRIDGE = "related_fragment_bridge"


@dataclass(frozen=True, slots=True)
class SemanticEscalationHint:
    """Review metadata that marks a near-miss item as semantic-ready."""

    recommended: bool
    reasons: tuple[SemanticEscalationReason, ...] = ()
    confidence: float | None = None
    confidence_band: tuple[float, float] = (0.42, 0.62)
    backend_name: str = "none"
    deterministic: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.recommended, bool):
            raise SemanticNearMissError("recommended must be a boolean")
        object.__setattr__(
            self,
            "reasons",
            tuple(
                SemanticEscalationReason(reason.value if isinstance(reason, SemanticEscalationReason) else str(reason))
                for reason in self.reasons
            ),
        )
        if self.confidence is not None:
            confidence = float(self.confidence)
            if not 0.0 <= confidence <= 1.0:
                raise SemanticNearMissError("confidence must be between 0.0 and 1.0")
            object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "confidence_band", _clean_confidence_band(self.confidence_band))
        object.__setattr__(self, "backend_name", _clean_text(self.backend_name, field_name="backend_name"))
        if not isinstance(self.deterministic, bool):
            raise SemanticNearMissError("deterministic must be a boolean")
        if not isinstance(self.metadata, Mapping):
            raise SemanticNearMissError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable hint payload."""
        return {
            "recommended": self.recommended,
            "reasons": [reason.value for reason in self.reasons],
            "confidence": self.confidence,
            "confidence_band": {
                "min": self.confidence_band[0],
                "max": self.confidence_band[1],
            },
            "backend_name": self.backend_name,
            "deterministic": self.deterministic,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SemanticNearMissCandidate:
    """Backend-neutral candidate payload for optional semantic reranking."""

    target_term_id: str
    target_canonical: str
    matched_surface: str
    confidence: float
    reasons: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_term_id", _clean_text(self.target_term_id, field_name="target_term_id"))
        object.__setattr__(self, "target_canonical", _clean_text(self.target_canonical, field_name="target_canonical"))
        object.__setattr__(self, "matched_surface", _clean_text(self.matched_surface, field_name="matched_surface"))
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise SemanticNearMissError("confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "reasons", _clean_tuple(self.reasons, field_name="reasons"))
        if not isinstance(self.metadata, Mapping):
            raise SemanticNearMissError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable candidate payload."""
        return {
            "target_term_id": self.target_term_id,
            "target_canonical": self.target_canonical,
            "matched_surface": self.matched_surface,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SemanticNearMissRequest:
    """Input passed to an optional semantic near-miss backend."""

    surface: str
    normalized_surface: str
    query_fragments: tuple[str, ...]
    candidates: tuple[SemanticNearMissCandidate, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="surface"))
        object.__setattr__(self, "normalized_surface", _clean_text(self.normalized_surface, field_name="normalized_surface"))
        object.__setattr__(self, "query_fragments", _clean_tuple(self.query_fragments, field_name="query_fragments"))
        if not isinstance(self.candidates, tuple):
            object.__setattr__(self, "candidates", tuple(self.candidates))
        for candidate in self.candidates:
            if not isinstance(candidate, SemanticNearMissCandidate):
                raise SemanticNearMissError("candidates must contain SemanticNearMissCandidate objects")
        if not isinstance(self.metadata, Mapping):
            raise SemanticNearMissError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable request payload."""
        return {
            "surface": self.surface,
            "normalized_surface": self.normalized_surface,
            "query_fragments": list(self.query_fragments),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SemanticNearMissResult:
    """Result produced by an optional semantic near-miss backend."""

    source: SemanticSuggestionSource = SemanticSuggestionSource.NONE
    backend_name: str = "none"
    applied: bool = False
    deterministic: bool = True
    candidates: tuple[SemanticNearMissCandidate, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source",
            SemanticSuggestionSource(self.source.value if isinstance(self.source, SemanticSuggestionSource) else str(self.source)),
        )
        object.__setattr__(self, "backend_name", _clean_text(self.backend_name, field_name="backend_name"))
        if not isinstance(self.applied, bool):
            raise SemanticNearMissError("applied must be a boolean")
        if not isinstance(self.deterministic, bool):
            raise SemanticNearMissError("deterministic must be a boolean")
        if not isinstance(self.candidates, tuple):
            object.__setattr__(self, "candidates", tuple(self.candidates))
        for candidate in self.candidates:
            if not isinstance(candidate, SemanticNearMissCandidate):
                raise SemanticNearMissError("candidates must contain SemanticNearMissCandidate objects")
        if not isinstance(self.metadata, Mapping):
            raise SemanticNearMissError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable result payload."""
        return {
            "source": self.source.value,
            "backend_name": self.backend_name,
            "applied": self.applied,
            "deterministic": self.deterministic,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "metadata": dict(self.metadata),
        }


class SemanticNearMissBackend(Protocol):
    """Protocol for optional offline semantic near-miss rerankers."""

    name: str
    deterministic: bool

    def rerank(self, request: SemanticNearMissRequest) -> SemanticNearMissResult:
        """Return semantic reranking metadata for a near-miss request."""


@dataclass(frozen=True, slots=True)
class NoopSemanticNearMissBackend:
    """Dependency-free backend that records no semantic model was applied."""

    name: str = "none"
    deterministic: bool = True

    def rerank(self, request: SemanticNearMissRequest) -> SemanticNearMissResult:
        if not isinstance(request, SemanticNearMissRequest):
            raise SemanticNearMissError("request must be a SemanticNearMissRequest")
        return SemanticNearMissResult(
            source=SemanticSuggestionSource.NONE,
            backend_name=self.name,
            applied=False,
            deterministic=self.deterministic,
            candidates=request.candidates,
            metadata={"reason": "no_semantic_backend_configured"},
        )


def semantic_escalation_hint(
    *,
    confidence: float,
    shared_fragments: Sequence[str] = (),
    related_fragments: Sequence[str] = (),
    precision_adjustments: Sequence[Mapping[str, Any]] = (),
    confidence_band: tuple[float, float] = (0.42, 0.62),
    backend_name: str = "none",
    deterministic: bool = True,
) -> SemanticEscalationHint:
    """Return a deterministic hint for optional semantic escalation.

    The gate is intentionally conservative: it marks the gray zone around the
    heuristic threshold and structurally weak lexical bridges. It does not call a
    model and does not change the heuristic suggestion order.
    """
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        raise SemanticNearMissError("confidence must be between 0.0 and 1.0")
    band = _clean_confidence_band(confidence_band)
    shared = tuple(str(fragment) for fragment in shared_fragments if str(fragment).strip())
    related = tuple(str(fragment) for fragment in related_fragments if str(fragment).strip())
    adjustments = tuple(precision_adjustments)

    reasons: list[SemanticEscalationReason] = []
    if band[0] <= confidence <= band[1]:
        reasons.append(SemanticEscalationReason.GRAY_ZONE_CONFIDENCE)
    if adjustments:
        reasons.append(SemanticEscalationReason.WEAK_LEXICAL_BRIDGE)
    if len(set(shared)) == 1 and not related:
        reasons.append(SemanticEscalationReason.SINGLE_FRAGMENT_BRIDGE)
    if related and len(set(shared)) <= 1:
        reasons.append(SemanticEscalationReason.RELATED_FRAGMENT_BRIDGE)

    return SemanticEscalationHint(
        recommended=bool(reasons),
        reasons=tuple(dict.fromkeys(reasons)),
        confidence=round(confidence, 4),
        confidence_band=band,
        backend_name=backend_name,
        deterministic=deterministic,
        metadata={
            "shared_fragments": list(shared),
            "related_fragments": list(related),
            "precision_adjustment_count": len(adjustments),
        },
    )


def semantic_candidate_from_mapping(payload: Mapping[str, Any]) -> SemanticNearMissCandidate:
    """Build a semantic candidate from a serialized near-miss-like mapping."""
    return SemanticNearMissCandidate(
        target_term_id=str(payload.get("target_term_id", "")),
        target_canonical=str(payload.get("target_canonical", "")),
        matched_surface=str(payload.get("matched_surface", "")),
        confidence=float(payload.get("confidence", 0.0)),
        reasons=tuple(str(reason) for reason in payload.get("reasons", ()) if str(reason).strip()),
        metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), Mapping) else {},
    )


def _clean_confidence_band(value: tuple[float, float] | Sequence[float]) -> tuple[float, float]:
    if len(value) != 2:
        raise SemanticNearMissError("confidence_band must contain two values")
    lower = float(value[0])
    upper = float(value[1])
    if not 0.0 <= lower <= upper <= 1.0:
        raise SemanticNearMissError("confidence_band must be within 0.0..1.0 and ordered")
    return (lower, upper)


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise SemanticNearMissError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise SemanticNearMissError(f"{field_name} must not be empty")
    return cleaned


def _clean_tuple(values: Sequence[str] | tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raise SemanticNearMissError(f"{field_name} must be an iterable of strings")
    cleaned: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            raise SemanticNearMissError(f"{field_name} item must not be empty")
        cleaned.append(text)
    return tuple(cleaned)


__all__ = [
    "NoopSemanticNearMissBackend",
    "SemanticEscalationHint",
    "SemanticEscalationReason",
    "SemanticNearMissBackend",
    "SemanticNearMissCandidate",
    "SemanticNearMissError",
    "SemanticNearMissRequest",
    "SemanticNearMissResult",
    "SemanticSuggestionSource",
    "semantic_candidate_from_mapping",
    "semantic_escalation_hint",
]
