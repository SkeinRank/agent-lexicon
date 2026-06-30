"""Deterministic near-miss suggestions for unknown identifier surfaces.

Near-miss suggestions are Scout metadata, not runtime resolution. They help
reviewers triage unknown code-style terms such as ``authToken`` by pointing to
nearby canonical terms that might deserve an alias proposal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
import re
from typing import Any, Iterable, Mapping

from agent_lexicon.core.models import Alias, Lexicon, Term
from agent_lexicon.text import code_identifier_variants, normalized_fragment_surface, surface_fragments
from agent_lexicon.scout.semantic import (
    NoopSemanticNearMissBackend,
    SemanticNearMissBackend,
    SemanticNearMissCandidate,
    SemanticNearMissRequest,
    SemanticSuggestionSource,
    semantic_escalation_hint,
)


class NearMissError(ValueError):
    """Raised when near-miss scoring receives invalid input."""


class NearMissReason(str, Enum):
    """Stable reason codes attached to near-miss suggestions."""

    SHARED_FRAGMENT = "shared_fragment"
    SAME_PREFIX = "same_prefix"
    SAME_SUFFIX = "same_suffix"
    EDIT_SIMILARITY = "edit_similarity"
    CODE_SHAPE = "code_shape"
    RELATED_FRAGMENT = "related_fragment"
    WEAK_LEXICAL_BRIDGE = "weak_lexical_bridge"


@dataclass(frozen=True, slots=True)
class NearMissSuggestion:
    """A possible canonical target for an unknown identifier surface."""

    surface: str
    normalized_surface: str
    target_term_id: str
    target_canonical: str
    matched_surface: str
    confidence: float
    reasons: tuple[NearMissReason, ...]
    shared_fragments: tuple[str, ...] = ()
    query_fragments: tuple[str, ...] = ()
    target_fragments: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    deprecated: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="surface"))
        object.__setattr__(self, "normalized_surface", _clean_text(self.normalized_surface, field_name="normalized_surface"))
        object.__setattr__(self, "target_term_id", _clean_text(self.target_term_id, field_name="target_term_id"))
        object.__setattr__(self, "target_canonical", _clean_text(self.target_canonical, field_name="target_canonical"))
        object.__setattr__(self, "matched_surface", _clean_text(self.matched_surface, field_name="matched_surface"))
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise NearMissError("confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "reasons", tuple(NearMissReason(reason.value if isinstance(reason, NearMissReason) else str(reason)) for reason in self.reasons))
        object.__setattr__(self, "shared_fragments", _clean_tuple(self.shared_fragments, field_name="shared_fragments"))
        object.__setattr__(self, "query_fragments", _clean_tuple(self.query_fragments, field_name="query_fragments"))
        object.__setattr__(self, "target_fragments", _clean_tuple(self.target_fragments, field_name="target_fragments"))
        object.__setattr__(self, "scopes", _clean_tuple(self.scopes, field_name="scopes"))
        if not isinstance(self.deprecated, bool):
            raise NearMissError("deprecated must be a boolean")
        if not isinstance(self.metadata, Mapping):
            raise NearMissError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable suggestion payload."""
        return {
            "surface": self.surface,
            "normalized_surface": self.normalized_surface,
            "target_term_id": self.target_term_id,
            "target_canonical": self.target_canonical,
            "matched_surface": self.matched_surface,
            "confidence": self.confidence,
            "reasons": [reason.value for reason in self.reasons],
            "shared_fragments": list(self.shared_fragments),
            "query_fragments": list(self.query_fragments),
            "target_fragments": list(self.target_fragments),
            "scopes": list(self.scopes),
            "deprecated": self.deprecated,
            "metadata": dict(self.metadata),
        }

    def to_text(self) -> str:
        """Return a compact human-readable suggestion line."""
        reason_label = ",".join(reason.value for reason in self.reasons) or "shape"
        scope_label = f" scopes={','.join(self.scopes)}" if self.scopes else ""
        deprecated_label = " deprecated" if self.deprecated else ""
        return (
            f"{self.surface!r} -> {self.target_term_id} ({self.target_canonical}) "
            f"confidence={self.confidence:.3f} via {self.matched_surface!r} "
            f"reasons={reason_label}{scope_label}{deprecated_label}"
        )


@dataclass(frozen=True, slots=True)
class NearMissReport:
    """Near-miss suggestions for one input text or surface."""

    surface: str
    suggestions: tuple[NearMissSuggestion, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="surface"))
        if not isinstance(self.suggestions, tuple):
            object.__setattr__(self, "suggestions", tuple(self.suggestions))
        for suggestion in self.suggestions:
            if not isinstance(suggestion, NearMissSuggestion):
                raise NearMissError("suggestions must contain NearMissSuggestion objects")
        if not isinstance(self.metadata, Mapping):
            raise NearMissError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def suggestion_count(self) -> int:
        """Return the number of suggestions."""
        return len(self.suggestions)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report payload."""
        return {
            "surface": self.surface,
            "suggestion_count": self.suggestion_count,
            "suggestions": [suggestion.to_dict() for suggestion in self.suggestions],
            "metadata": dict(self.metadata),
        }


_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:"
    r"[A-Za-z][A-Za-z0-9]*(?:[._:/-][A-Za-z0-9]+)+"
    r"|[A-Za-z]+_[A-Za-z0-9_]+"
    r"|[A-Z][A-Z0-9]{2,}"
    r"|[A-Za-z]+[A-Z][A-Za-z0-9]*"
    r")"
    r"(?![A-Za-z0-9_])"
)
_BACKTICK_PATTERN = re.compile(r"`([^`\n]{2,120})`")
_BROAD_FRAGMENTS = frozenset({
    "api",
    "auth",
    "access",
    "cache",
    "cap",
    "client",
    "config",
    "data",
    "error",
    "event",
    "id",
    "key",
    "limit",
    "log",
    "metric",
    "model",
    "policy",
    "request",
    "response",
    "service",
    "session",
    "state",
    "system",
    "token",
    "url",
    "uri",
    "user",
    "value",
})
_RELATED_FRAGMENTS: Mapping[str, frozenset[str]] = {
    "auth": frozenset({"access", "authentication", "bearer", "credential", "credentials", "session"}),
    "authentication": frozenset({"access", "auth", "bearer", "credential", "credentials", "session"}),
    "access": frozenset({"auth", "authentication", "bearer", "credential", "credentials", "session"}),
    "bearer": frozenset({"access", "auth", "authentication", "credential", "credentials", "session"}),
    "credential": frozenset({"access", "auth", "authentication", "bearer", "credentials", "session"}),
    "credentials": frozenset({"access", "auth", "authentication", "bearer", "credential", "session"}),
    "customer": frozenset({"account", "client"}),
    "account": frozenset({"customer", "client"}),
    "cap": frozenset({"limit", "quota", "threshold"}),
    "limit": frozenset({"cap", "quota", "threshold"}),
    "quota": frozenset({"cap", "limit", "threshold"}),
    "threshold": frozenset({"cap", "limit", "quota"}),
}
_WEAK_SINGLE_BRIDGE_FRAGMENTS = frozenset({
    "access",
    "api",
    "auth",
    "cache",
    "client",
    "config",
    "data",
    "error",
    "event",
    "id",
    "key",
    "limit",
    "log",
    "model",
    "policy",
    "request",
    "response",
    "service",
    "session",
    "state",
    "system",
    "token",
    "url",
    "uri",
    "user",
    "value",
})
_DEFAULT_MIN_CONFIDENCE = 0.42
_DEFAULT_MAX_SUGGESTIONS = 3


@dataclass(frozen=True, slots=True)
class _KnownSurface:
    term_id: str
    canonical: str
    surface: str
    scopes: tuple[str, ...]
    deprecated: bool


def discover_unknown_identifier_surfaces(text: str, *, max_surfaces: int = 10) -> tuple[str, ...]:
    """Extract code-style identifier surfaces that are good near-miss inputs."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if max_surfaces < 1:
        raise NearMissError("max_surfaces must be greater than 0")

    surfaces: list[str] = []
    for raw in _BACKTICK_PATTERN.findall(text):
        cleaned = raw.strip()
        if _is_near_miss_identifier(cleaned):
            surfaces.append(cleaned)
    for match in _IDENTIFIER_PATTERN.finditer(text):
        surfaces.append(match.group(0))

    if not surfaces and _is_near_miss_identifier(text.strip()):
        surfaces.append(text.strip())

    return tuple(dict.fromkeys(surfaces[:max_surfaces]))


def suggest_near_misses(
    lexicon: Lexicon,
    surface: str,
    *,
    scopes: Iterable[str] | None = None,
    include_deprecated: bool = False,
    max_suggestions: int = _DEFAULT_MAX_SUGGESTIONS,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
    semantic_backend: SemanticNearMissBackend | None = None,
    semantic_confidence_band: tuple[float, float] = (_DEFAULT_MIN_CONFIDENCE, 0.62),
) -> NearMissReport:
    """Suggest likely canonical targets for one unknown identifier surface."""
    if not isinstance(lexicon, Lexicon):
        raise NearMissError("lexicon must be a Lexicon")
    cleaned_surface = _clean_text(surface, field_name="surface")
    requested_scopes = _normalize_scopes(scopes)
    max_suggestions = int(max_suggestions)
    if max_suggestions < 1:
        raise NearMissError("max_suggestions must be greater than 0")
    min_confidence = float(min_confidence)
    if not 0.0 <= min_confidence <= 1.0:
        raise NearMissError("min_confidence must be between 0.0 and 1.0")

    query_fragments = surface_fragments(cleaned_surface)
    normalized_query = normalized_fragment_surface(cleaned_surface)
    if len(query_fragments) < 2:
        return NearMissReport(
            surface=cleaned_surface,
            suggestions=(),
            metadata={"reason": "not_identifier_like", "query_fragments": query_fragments},
        )

    best_by_term: dict[str, NearMissSuggestion] = {}
    for known in _iter_known_surfaces(lexicon, include_deprecated=include_deprecated):
        if not _scope_matches(known.scopes, requested_scopes):
            continue
        suggestion = _score_known_surface(
            query_surface=cleaned_surface,
            normalized_query=normalized_query,
            query_fragments=query_fragments,
            known=known,
            semantic_confidence_band=semantic_confidence_band,
        )
        if suggestion is None or suggestion.confidence < min_confidence:
            continue
        current = best_by_term.get(suggestion.target_term_id)
        if current is None or _suggestion_sort_key(suggestion) < _suggestion_sort_key(current):
            best_by_term[suggestion.target_term_id] = suggestion

    suggestions = tuple(
        sorted(best_by_term.values(), key=_suggestion_sort_key)[:max_suggestions]
    )
    semantic_result = _run_semantic_backend(
        semantic_backend,
        surface=cleaned_surface,
        normalized_surface=normalized_query,
        query_fragments=query_fragments,
        suggestions=suggestions,
    )
    return NearMissReport(
        surface=cleaned_surface,
        suggestions=suggestions,
        metadata={
            "query_fragments": query_fragments,
            "normalized_surface": normalized_query,
            "min_confidence": min_confidence,
            "max_suggestions": max_suggestions,
            "semantic_escalation_recommended_count": _semantic_escalation_recommended_count(suggestions),
            "semantic_backend": semantic_result.to_dict(),
        },
    )


def suggest_near_misses_for_text(
    lexicon: Lexicon,
    text: str,
    *,
    scopes: Iterable[str] | None = None,
    include_deprecated: bool = False,
    max_surfaces: int = 10,
    max_suggestions_per_surface: int = _DEFAULT_MAX_SUGGESTIONS,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
    semantic_backend: SemanticNearMissBackend | None = None,
    semantic_confidence_band: tuple[float, float] = (_DEFAULT_MIN_CONFIDENCE, 0.62),
) -> tuple[NearMissReport, ...]:
    """Return near-miss suggestions for identifier-like surfaces in text."""
    surfaces = discover_unknown_identifier_surfaces(text, max_surfaces=max_surfaces)
    reports: list[NearMissReport] = []
    for surface in surfaces:
        report = suggest_near_misses(
            lexicon,
            surface,
            scopes=scopes,
            include_deprecated=include_deprecated,
            max_suggestions=max_suggestions_per_surface,
            min_confidence=min_confidence,
            semantic_backend=semantic_backend,
            semantic_confidence_band=semantic_confidence_band,
        )
        if report.suggestions:
            reports.append(report)
    return tuple(reports)


def _iter_known_surfaces(lexicon: Lexicon, *, include_deprecated: bool) -> Iterable[_KnownSurface]:
    for term in lexicon.terms:
        if term.deprecated and not include_deprecated:
            continue
        yield from _known_surfaces_for_term(term, surface=term.canonical, scopes=term.scopes, deprecated=term.deprecated)
        for alias in term.aliases:
            if (term.deprecated or alias.deprecated) and not include_deprecated:
                continue
            yield from _known_surfaces_for_alias(alias, term=term)


def _known_surfaces_for_alias(alias: Alias, *, term: Term) -> Iterable[_KnownSurface]:
    scopes = alias.scopes or term.scopes
    deprecated = term.deprecated or alias.deprecated
    yield from _known_surfaces_for_term(term, surface=alias.surface, scopes=scopes, deprecated=deprecated)


def _known_surfaces_for_term(
    term: Term,
    *,
    surface: str,
    scopes: tuple[str, ...],
    deprecated: bool,
) -> Iterable[_KnownSurface]:
    values = (surface, *code_identifier_variants(surface))
    for value in dict.fromkeys(values):
        yield _KnownSurface(
            term_id=term.id,
            canonical=term.canonical,
            surface=value,
            scopes=scopes,
            deprecated=deprecated,
        )


def _score_known_surface(
    *,
    query_surface: str,
    normalized_query: str,
    query_fragments: tuple[str, ...],
    known: _KnownSurface,
    semantic_confidence_band: tuple[float, float],
) -> NearMissSuggestion | None:
    target_fragments = surface_fragments(known.surface)
    if len(target_fragments) < 1:
        return None

    shared = tuple(fragment for fragment in query_fragments if fragment in set(target_fragments))
    shared_set = frozenset(shared)
    union = frozenset(query_fragments) | frozenset(target_fragments)
    shared_fragment_score = sum(_fragment_weight(fragment) for fragment in shared_set) / max(
        sum(_fragment_weight(fragment) for fragment in union),
        1.0,
    )
    raw_jaccard = len(shared_set) / max(len(union), 1)
    normalized_target = normalized_fragment_surface(known.surface)
    edit_similarity = SequenceMatcher(None, normalized_query, normalized_target).ratio()
    same_prefix = bool(query_fragments and target_fragments and query_fragments[0] == target_fragments[0])
    same_suffix = bool(query_fragments and target_fragments and query_fragments[-1] == target_fragments[-1])
    related = _related_fragment_matches(query_fragments, target_fragments)
    related_score = min(0.25, len(related) * 0.125)
    shape_score = _code_shape_similarity(query_surface, known.surface, query_fragments, target_fragments)

    confidence = (
        (0.34 * shared_fragment_score)
        + (0.18 * raw_jaccard)
        + (0.24 * edit_similarity)
        + (0.08 if same_prefix else 0.0)
        + (0.10 if same_suffix else 0.0)
        + (0.08 * shape_score)
        + related_score
    )
    precision_adjustments = _precision_adjustments(
        shared_set=shared_set,
        related=related,
        edit_similarity=edit_similarity,
    )
    confidence -= sum(adjustment["penalty"] for adjustment in precision_adjustments)
    confidence = round(max(0.0, min(1.0, confidence)), 4)
    reasons: list[NearMissReason] = []
    if shared:
        reasons.append(NearMissReason.SHARED_FRAGMENT)
    if same_prefix:
        reasons.append(NearMissReason.SAME_PREFIX)
    if same_suffix:
        reasons.append(NearMissReason.SAME_SUFFIX)
    if edit_similarity >= 0.58:
        reasons.append(NearMissReason.EDIT_SIMILARITY)
    if shape_score >= 0.6:
        reasons.append(NearMissReason.CODE_SHAPE)
    if related:
        reasons.append(NearMissReason.RELATED_FRAGMENT)
    if precision_adjustments:
        reasons.append(NearMissReason.WEAK_LEXICAL_BRIDGE)

    if not reasons:
        return None
    return NearMissSuggestion(
        surface=query_surface,
        normalized_surface=normalized_query,
        target_term_id=known.term_id,
        target_canonical=known.canonical,
        matched_surface=known.surface,
        confidence=confidence,
        reasons=tuple(dict.fromkeys(reasons)),
        shared_fragments=shared,
        query_fragments=query_fragments,
        target_fragments=target_fragments,
        scopes=known.scopes,
        deprecated=known.deprecated,
        metadata=_suggestion_metadata(
            shared_fragment_score=shared_fragment_score,
            raw_jaccard=raw_jaccard,
            edit_similarity=edit_similarity,
            shape_score=shape_score,
            related=related,
            precision_adjustments=precision_adjustments,
            confidence=confidence,
            shared_fragments=shared,
            semantic_confidence_band=semantic_confidence_band,
        ),
    )



def _suggestion_metadata(
    *,
    shared_fragment_score: float,
    raw_jaccard: float,
    edit_similarity: float,
    shape_score: float,
    related: tuple[str, ...],
    precision_adjustments: tuple[dict[str, Any], ...],
    confidence: float,
    shared_fragments: tuple[str, ...],
    semantic_confidence_band: tuple[float, float],
) -> dict[str, Any]:
    hint = semantic_escalation_hint(
        confidence=confidence,
        shared_fragments=shared_fragments,
        related_fragments=related,
        precision_adjustments=precision_adjustments,
        confidence_band=semantic_confidence_band,
    )
    return {
        "shared_fragment_score": round(shared_fragment_score, 4),
        "raw_jaccard": round(raw_jaccard, 4),
        "edit_similarity": round(edit_similarity, 4),
        "shape_score": round(shape_score, 4),
        "related_fragments": related,
        "precision_adjustments": precision_adjustments,
        "suggestion_source": SemanticSuggestionSource.HEURISTIC.value,
        "semantic_escalation": hint.to_dict(),
    }


def _semantic_candidate_from_suggestion(suggestion: NearMissSuggestion) -> SemanticNearMissCandidate:
    return SemanticNearMissCandidate(
        target_term_id=suggestion.target_term_id,
        target_canonical=suggestion.target_canonical,
        matched_surface=suggestion.matched_surface,
        confidence=suggestion.confidence,
        reasons=tuple(reason.value for reason in suggestion.reasons),
        metadata=suggestion.metadata,
    )


def _run_semantic_backend(
    semantic_backend: SemanticNearMissBackend | None,
    *,
    surface: str,
    normalized_surface: str,
    query_fragments: tuple[str, ...],
    suggestions: tuple[NearMissSuggestion, ...],
):
    backend = semantic_backend or NoopSemanticNearMissBackend()
    request = SemanticNearMissRequest(
        surface=surface,
        normalized_surface=normalized_surface,
        query_fragments=query_fragments,
        candidates=tuple(_semantic_candidate_from_suggestion(suggestion) for suggestion in suggestions),
    )
    return backend.rerank(request)


def _semantic_escalation_recommended_count(suggestions: tuple[NearMissSuggestion, ...]) -> int:
    count = 0
    for suggestion in suggestions:
        semantic = suggestion.metadata.get("semantic_escalation")
        if isinstance(semantic, Mapping) and semantic.get("recommended") is True:
            count += 1
    return count

def _precision_adjustments(
    *,
    shared_set: frozenset[str],
    related: tuple[str, ...],
    edit_similarity: float,
) -> tuple[dict[str, Any], ...]:
    """Return confidence dampening rules for weak lexical bridges.

    Near-miss is intentionally recall-friendly, but single shared fragments such
    as ``key`` or ``access`` can create noisy review hints when the rest of the
    identifier points elsewhere. Keep high-edit typo cases and explicit related
    fragment bridges, but dampen lower-confidence single-fragment matches.
    """
    if related or len(shared_set) != 1:
        return ()
    fragment = next(iter(shared_set))
    if fragment not in _WEAK_SINGLE_BRIDGE_FRAGMENTS:
        return ()
    if edit_similarity >= 0.78:
        return ()
    return (
        {
            "kind": "weak_single_fragment_bridge",
            "fragment": fragment,
            "penalty": 0.14,
        },
    )


def _fragment_weight(fragment: str) -> float:
    if fragment in _BROAD_FRAGMENTS:
        return 0.55
    return 1.0


def _related_fragment_matches(query_fragments: tuple[str, ...], target_fragments: tuple[str, ...]) -> tuple[str, ...]:
    target_set = frozenset(target_fragments)
    related: list[str] = []
    for fragment in query_fragments:
        neighbors = _RELATED_FRAGMENTS.get(fragment, frozenset())
        hits = sorted(neighbor for neighbor in neighbors if neighbor in target_set)
        related.extend(f"{fragment}:{hit}" for hit in hits)
    return tuple(dict.fromkeys(related))


def _code_shape_similarity(
    query_surface: str,
    known_surface: str,
    query_fragments: tuple[str, ...],
    target_fragments: tuple[str, ...],
) -> float:
    score = 0.0
    if len(query_fragments) == len(target_fragments):
        score += 0.45
    if _surface_shape(query_surface) == _surface_shape(known_surface):
        score += 0.35
    if query_fragments and target_fragments and len(query_fragments[-1]) == len(target_fragments[-1]):
        score += 0.10
    if query_fragments and target_fragments and len(query_fragments[0]) == len(target_fragments[0]):
        score += 0.10
    return round(min(1.0, score), 4)


def _surface_shape(surface: str) -> str:
    if "_" in surface:
        return "snake"
    if "-" in surface:
        return "kebab"
    if "." in surface:
        return "dotted"
    if "/" in surface or ":" in surface:
        return "path"
    if re.search(r"[a-z][A-Z]", surface):
        return "camel"
    if surface.isupper() and any(char.isalpha() for char in surface):
        return "upper"
    if " " in surface:
        return "phrase"
    return "word"


def _is_near_miss_identifier(surface: str) -> bool:
    if not surface:
        return False
    fragments = surface_fragments(surface)
    if len(fragments) < 2:
        return False
    return bool(_IDENTIFIER_PATTERN.search(surface)) or any(separator in surface for separator in ("_", "-", ".", ":", "/"))


def _normalize_scopes(scopes: Iterable[str] | None) -> frozenset[str] | None:
    if scopes is None:
        return None
    cleaned = frozenset(str(scope).strip() for scope in scopes if str(scope).strip())
    return cleaned or None


def _scope_matches(entry_scopes: tuple[str, ...], requested_scopes: frozenset[str] | None) -> bool:
    if requested_scopes is None:
        return True
    if not entry_scopes:
        return True
    return bool(set(entry_scopes) & set(requested_scopes))


def _suggestion_sort_key(suggestion: NearMissSuggestion) -> tuple[float, str, str]:
    return (-suggestion.confidence, suggestion.target_term_id, suggestion.matched_surface)


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise NearMissError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise NearMissError(f"{field_name} must not be empty")
    return cleaned


def _clean_tuple(values: tuple[str, ...] | Iterable[str], *, field_name: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raise NearMissError(f"{field_name} must be an iterable of strings")
    cleaned: list[str] = []
    for value in values:
        cleaned.append(_clean_text(str(value), field_name=f"{field_name} item"))
    return tuple(cleaned)


__all__ = [
    "NearMissError",
    "NearMissReason",
    "NearMissReport",
    "NearMissSuggestion",
    "SemanticNearMissBackend",
    "discover_unknown_identifier_surfaces",
    "suggest_near_misses",
    "suggest_near_misses_for_text",
]
